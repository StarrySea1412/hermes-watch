"""Service probes: URL / TCP dial checks independent of host inspection.

拨测监控（对标 Uptime Kuma 拨测 / Gatus 状态机，补「只管主机不管服务」的维度缺口）：
- 目标：URL（GET，接受 http/https）或 TCP（host:port 连通）
- 状态机（Gatus 式双阈值防抖）：
  连续 fail_threshold 次失败 → down；down 中连续 success_threshold 次成功 → up
- 心跳：probe_log 逐次留痕（up/down + 延迟），前端画 UK 式心跳条
- 通知：翻转才发（down 告警 / up 恢复带持续时长），走既有 notify（免打扰/留痕复用）
- 调度：独立 15s 步进循环（拨测周期 probe_interval 默认 30s，与 60s 主机巡检解耦）
- 文案：i18n.t(zh,en) 双语 + tr_probe_message 读出口兜底历史行

设计取舍：拨测失败不进 findings（那是主机指标域），只落 events + probe_log；
面板独立「拨测」页展示，Fleet 健康分不被拨测污染。
"""
import asyncio
import re
import time

import httpx

from . import db, i18n, notify

# 状态机默认阈值（Gatus: failure-threshold=3 / success-threshold=2）
DEFAULT_FAIL_THRESHOLD = 3
DEFAULT_SUCCESS_THRESHOLD = 2

_notify_broadcast = None  # set by main.py at startup（复用 SSE 广播，避免循环导入）


def set_broadcaster(fn):
    global _notify_broadcast
    _notify_broadcast = fn


def _setting_int(key: str, default: int, minimum: int = 5) -> int:
    r = db.query_one("SELECT value FROM settings WHERE key=?", (key,))
    try:
        return max(minimum, int(float(r["value"])))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- 拨测执行

def url_check(p: dict, status: int, body: str, latency_ms: float,
              cert_days: int | None) -> tuple[bool, str]:
    """URL 拨测条件引擎（Gatus 式条件的轻量子集），纯函数便于测试。

    依次判定：状态码 → 关键词包含 → 证书剩余天数 → 响应时间上限。
    返回 (ok, error)；error 为首个未满足条件的原因（存 last_error 并进事件）。"""
    if status >= 400:
        return False, f"HTTP {status}"
    kw = (p.get("keyword") or "").strip()
    if kw and kw not in (body or ""):
        return False, f"keyword missing: {kw[:80]}"
    min_days = int(p.get("cert_days_min") or 0)
    if min_days > 0:
        if cert_days is None:
            return False, "cert: could not read peer certificate"
        if cert_days < min_days:
            return False, f"cert expires in {cert_days}d (< {min_days}d)"
    max_ms = int(p.get("max_latency_ms") or 0)
    if max_ms > 0 and latency_ms > max_ms:
        return False, f"latency {round(latency_ms)}ms > {max_ms}ms"
    return True, ""


async def cert_days_left(host: str, port: int, timeout_s: float) -> int | None:
    """TLS 握手取对端证书剩余天数；任何异常返回 None（调用方按条件判定失败）。"""
    import ssl
    try:
        ctx = ssl.create_default_context()
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=ctx, server_hostname=host), timeout_s)
        cert = writer.get_extra_info("peercert")
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        if not cert or "notAfter" not in cert:
            return None
        return max(0, int((ssl.cert_time_to_seconds(cert["notAfter"]) - time.time()) // 86400))
    except Exception:
        return None


async def run_probe(p: dict) -> tuple[bool, float | None, str]:
    """执行一次拨测 → (ok, latency_ms, error)。任何异常都归一为失败，不外抛。

    URL 拨测带条件引擎（url_check）：状态码 / 关键词 / 证书天数 / 响应时间。"""
    target = (p["target"] or "").strip()
    timeout_s = max(1.0, float(p["timeout_s"] or 10))
    started = time.perf_counter()
    try:
        if p["kind"] == "tcp":
            host, _, port = target.rpartition(":")
            if not host or not port.isdigit():
                return False, None, "invalid tcp target (expected host:port)"
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, int(port)), timeout_s)
            latency = (time.perf_counter() - started) * 1000
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True, latency, ""
        # URL（默认）：GET 一次 + 条件引擎；自签证书常年在拨测目标里（verify=False）
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True,
                                     verify=False) as cli:
            r = await cli.get(target)
            latency = (time.perf_counter() - started) * 1000
        cert_days = None
        if target.startswith("https://") and int(p.get("cert_days_min") or 0) > 0:
            from urllib.parse import urlsplit
            sp = urlsplit(target)
            cert_days = await cert_days_left(sp.hostname, sp.port or 443, timeout_s)
        ok, err = url_check(p, r.status_code, r.text, latency, cert_days)
        return ok, latency, err
    except Exception as e:
        return False, None, f"{type(e).__name__}: {e}"[:160]


# ---------------------------------------------------------------- 状态机

def evaluate(p: dict, ok: bool) -> str | None:
    """推进单条拨测的状态机并落库状态，返回翻转动作（"down"/"up"/None）。

    连续 fail_threshold 次失败才打翻（防抖），恢复须连续 success_threshold 次成功。
    p 需带本次结果：_latency（成功时 ms）与 _error（失败时原因），见 handle_result。"""
    fail_streak = 0 if ok else (p["fail_streak"] or 0) + 1
    succ_streak = (p["succ_streak"] or 0) + 1 if ok else 0
    fail_th = p["fail_threshold"] or DEFAULT_FAIL_THRESHOLD
    succ_th = p["success_threshold"] or DEFAULT_SUCCESS_THRESHOLD
    latency = p.get("_latency")
    error = p.get("_error") or ""

    flip = None
    if p["up"] and fail_streak >= fail_th:
        flip = "down"          # up 中连续失败达阈值 → 下线
    elif not p["up"] and succ_streak >= succ_th:
        flip = "up"            # down 中连续成功达阈值 → 恢复

    now_up = (p["up"] == 1 and flip is None) or flip == "up"
    # 计数归零只跟本次结果走：失败累计 fail_streak（与是否翻转无关），成功累计 succ_streak
    db.execute(
        "UPDATE probes SET up=?, fail_streak=?, succ_streak=?, last_ts=?, "
        "last_latency=?, last_error=? WHERE id=?",
        (1 if now_up else 0,
         0 if ok else fail_streak,
         succ_streak if ok else 0,
         db.now(), round(latency) if ok and latency else None,
         "" if ok else error[:160],
         p["id"]))
    return flip


async def handle_result(p: dict, ok: bool, latency: float | None, error: str) -> None:
    """落一次拨测心跳（probe_log）并处理翻转（事件 + 通知）。"""
    p = {**p, "_latency": latency, "_error": error}
    db.execute("INSERT INTO probe_log(probe_id,ts,up,latency,error) VALUES(?,?,?,?,?)",
               (p["id"], db.now(), 1 if ok else 0, round(latency) if latency else None,
                (error or "")[:160]))
    flip = evaluate(p, ok)
    if not flip:
        return
    name, target = p["name"], p["target"]
    if flip == "down":
        msg = i18n.t(f"拨测下线: {name}（{target}）— {error}" if error
                     else f"拨测下线: {name}（{target}）",
                     f"Probe down: {name} ({target})" + (f" — {error}" if error else ""))
        kind_word = i18n.t("拨测告警", "Probe alert")
    else:
        dur = _fmt_dur(db.now() - (p["last_flip_ts"] or p["created_at"] or db.now()))
        msg = i18n.t(f"拨测恢复: {name}（{target}，持续 {dur}）",
                     f"Probe recovered: {name} ({target}, was down for {dur})")
        kind_word = i18n.t("拨测恢复", "Probe recovered")
    if _notify_broadcast:
        await _notify_broadcast("probe", msg, {"probe_id": p["id"]})
    db.execute("INSERT INTO events(ts,host_id,kind,message,data) VALUES(?,?,?,?,?)",
               (db.now(), None, "probe", msg, db.j({"probe_id": p["id"]})))
    await notify.send(kind_word, msg)
    db.execute("UPDATE probes SET last_flip_ts=? WHERE id=?", (db.now(), p["id"]))


def _fmt_dur(sec: float) -> str:
    m = int(sec // 60)
    zh = i18n.lang() != "en"
    if m < 60:
        return f"{m} 分钟" if zh else f"{m} min"
    h = m // 60
    if h < 24:
        return (f"{h} 小时 {m % 60} 分" if m % 60 else f"{h} 小时") if zh \
            else (f"{h} h {m % 60} min" if m % 60 else f"{h} h")
    d = h // 24
    return (f"{d} 天 {h % 24} 小时" if h % 24 else f"{d} 天") if zh \
        else (f"{d} d {h % 24} h" if h % 24 else f"{d} d")


# ---------------------------------------------------------------- 调度

async def run_due() -> int:
    """跑一轮所有到期拨测；每条可用 interval_s 覆盖全局 probe_interval（0=用全局）。"""
    global_interval = _setting_int("probe_interval", 30, minimum=15)
    n = 0
    for p in db.query("SELECT * FROM probes"):
        interval = max(15, int(p["interval_s"] or 0)) or global_interval
        if (p["last_ts"] or 0) + interval > db.now() + 0.5:
            continue
        ok, latency, error = await run_probe(p)
        await handle_result(p, ok, latency, error)
        n += 1
    return n


async def loop():
    """拨测独立循环：15s 步进，跑到期目标；异常不静默（与主机巡检同纪律）。"""
    while True:
        try:
            await run_due()
        except Exception:
            import traceback
            traceback.print_exc()
        await asyncio.sleep(15)


# ---------------------------------------------------------------- 读出口兜底

_RE_RECOVER_PAREN = re.compile(r"（(.+?)，持续 (.+?)）")


def tr_probe_message(message: str | None) -> str:
    """EN 面板下翻译历史拨测事件（模板前缀匹配，未命中原样保留）。

    恢复消息嵌套的中文时长（「持续 5 分钟」）一并归一为英文单位。"""
    if i18n.lang() != "en" or not message:
        return message or ""
    if message.startswith("拨测下线: "):
        return "Probe down: " + message[len("拨测下线: "):]
    if message.startswith("拨测恢复: "):
        body = message[len("拨测恢复: "):]
        # 「name（target，持续 5 分钟）」→「name (target, was down for 5 min)」整体换写法
        body = _RE_RECOVER_PAREN.sub(
            lambda m: f" ({m.group(1)}, was down for {i18n._tr_dur(m.group(2))})", body)
        return "Probe recovered: " + body
    return message
