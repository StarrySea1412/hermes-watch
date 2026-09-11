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
import os
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


# ---------------------------------------------------------------- DNS 拨测

QTYPES = {"A": 1, "CNAME": 5, "AAAA": 28, "TXT": 16, "MX": 15, "NS": 2}


def _enc_name(name: str) -> bytes:
    out = b""
    for part in (name or "").strip().rstrip(".").split("."):
        if not part:
            continue
        b = part.encode("ascii") if part.isascii() else part.encode("idna")
        out += bytes([len(b)]) + b
    return out + b"\x00"


def _parse_name(buf: bytes, off: int) -> tuple[str, int]:
    """读 DNS 名称（RFC1035 压缩指针），返回 (name, 消耗结束偏移)。"""
    labels: list[str] = []
    pos, end, jumped = off, off, False
    while True:
        ln = buf[pos] if pos < len(buf) else 0
        if ln == 0:
            if not jumped:
                end = pos + 1
            break
        if ln & 0xC0:  # 压缩指针
            if not jumped:
                end = pos + 2
                jumped = True
            pos = ((ln & 0x3F) << 8) | buf[pos + 1]
            continue
        labels.append(buf[pos + 1:pos + 1 + ln].decode("ascii", "replace"))
        pos += 1 + ln
    return ".".join(labels), end


def _parse_rdata(buf: bytes, off: int, rdlen: int, rtype: int) -> str:
    """RDATA → 展示字符串：A/AAAA 点分、NS/CNAME/MX 域名、TXT 文本，其余十六进制。"""
    if rtype == 1 and rdlen == 4:
        return ".".join(str(b) for b in buf[off:off + 4])
    if rtype == 28 and rdlen == 16:
        import ipaddress
        return str(ipaddress.IPv6Address(buf[off:off + 16]))
    if rtype in (2, 5, 12):
        name, _ = _parse_name(buf, off)
        return name
    if rtype == 15:
        pref = int.from_bytes(buf[off:off + 2], "big")
        name, _ = _parse_name(buf, off + 2)
        return f"{pref} {name}".strip()
    if rtype == 16:
        txt, p = [], off
        while p < off + rdlen:
            ln = buf[p]
            txt.append(buf[p + 1:p + 1 + ln].decode("utf-8", "replace"))
            p += 1 + ln
        return "".join(txt)
    return buf[off:off + rdlen].hex()


def _build_query(name: str, qtype: int, qid: int) -> bytes:
    header = qid.to_bytes(2, "big") + b"\x01\x00" + b"\x00\x01\x00\x00\x00\x00\x00\x00"  # RD=1, QD=1
    return header + _enc_name(name) + qtype.to_bytes(2, "big") + b"\x00\x01"


async def dns_lookup(target: str, rrtype: str = "A", resolver: str = "",
                     timeout_s: float = 10.0) -> list[str]:
    """最小 UDP DNS 查询（纯 stdlib，不引 dnspython）→ rdata 字符串列表。

    resolver 形如 "223.5.5.5" 或 "223.5.5.5:5353"（必填：各平台系统解析器无统一直读 API）。
    任何失败（超时/rcode 非 0/响应畸形）抛异常，由 run_probe 归一为拨测失败。"""
    qtype = QTYPES[(rrtype or "A").upper()]
    host, _, port = (resolver or "").partition(":")
    if not host:
        raise ValueError("dns resolver required (e.g. 223.5.5.5)")
    addr = (host, int(port or 53))
    loop = asyncio.get_running_loop()
    qid = int.from_bytes(os.urandom(2), "big")
    fut: asyncio.Future = loop.create_future()

    class _P(asyncio.DatagramProtocol):
        def datagram_received(self, data, _addr):
            if not fut.done():
                fut.set_result(data)

        def error_received(self, exc):
            if not fut.done():
                fut.set_exception(exc)

    transport, _ = await asyncio.wait_for(
        loop.create_datagram_endpoint(lambda: _P(), remote_addr=addr), timeout_s)
    try:
        transport.sendto(_build_query(target, qtype, qid))
        data = await asyncio.wait_for(fut, timeout_s)
    finally:
        transport.close()
    return _parse_response(data, qid)


def _parse_response(data: bytes, qid: int) -> list[str]:
    """解析 DNS 应答 → answer 区 rdata 字符串列表（纯函数，离线可测）。

    校验事务 ID/RCODE；跳过 Question 区，逐条读 Answer 的名称/类型/RDATA。"""
    if len(data) < 12 or data[:2] != qid.to_bytes(2, "big"):
        raise ValueError("malformed dns response")
    flags = int.from_bytes(data[2:4], "big")
    if flags & 0x0F:
        raise ValueError(f"dns rcode={flags & 0x0F}")
    ancount = int.from_bytes(data[6:8], "big")
    off = 12
    _, off = _parse_name(data, off)  # 跳过 Question 的 QNAME
    off += 4                          # QTYPE + QCLASS
    out: list[str] = []
    for _ in range(ancount):
        _, off = _parse_name(data, off)
        rtype = int.from_bytes(data[off:off + 2], "big")
        rdlen = int.from_bytes(data[off + 8:off + 10], "big")
        out.append(_parse_rdata(data, off + 10, rdlen, rtype))
        off += 10 + rdlen
    return [a for a in out if a]


def dns_check(answers: list[str], expected: str) -> tuple[bool, str]:
    """DNS 条件引擎（纯函数）：有答案 + 期望子串命中任一答案（大小写不敏感）。"""
    if not answers:
        return False, "no records"
    exp = (expected or "").strip()
    if exp and not any(exp.lower() in a.lower() for a in answers):
        return False, f"expected '{exp[:60]}' not in answers"
    return True, ""


async def run_probe(p: dict) -> tuple[bool, float | None, str]:
    """执行一次拨测 → (ok, latency_ms, error)。任何异常都归一为失败，不外抛。

    URL 拨测带条件引擎（url_check）：状态码 / 关键词 / 证书天数 / 响应时间。
    DNS 拨测查解析器（记录类型 + 期望包含）。push 型不轮询（由 sweep_push 清扫）。"""
    target = (p["target"] or "").strip()
    timeout_s = max(1.0, float(p["timeout_s"] or 10))
    started = time.perf_counter()
    try:
        if p["kind"] == "dns":
            answers = await dns_lookup(target, p["dns_type"] or "A",
                                       (p["dns_resolver"] or "").strip(), timeout_s)
            latency = (time.perf_counter() - started) * 1000
            ok, err = dns_check(answers, p["dns_expected"] or "")
            return ok, latency, err
        if p["kind"] == "push":
            return True, None, "push targets are swept, not dialed"  # 不会走到（run_due 已跳过）
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
    if flip:
        await _emit_flip(p, flip, error)


async def _emit_flip(p: dict, flip: str, error: str) -> None:
    """翻转出口（handle_result 与 push_miss 共用）：事件落库 + 通知外发 + last_flip_ts。

    事件/时间线文案走内置双语（tr_probe_message 读出口兜底）；外呼文本走可自定义模板
    （notify_tpl_probe_down / notify_tpl_probe_up，空 = 与事件同文案）。"""
    name, target = p["name"], p["target"]
    if flip == "down":
        msg = i18n.t(f"拨测下线: {name}（{target}）— {error}" if error
                     else f"拨测下线: {name}（{target}）",
                     f"Probe down: {name} ({target})" + (f" — {error}" if error else ""))
        kind_word = i18n.t("拨测告警", "Probe alert")
        text = notify.tpl("notify_tpl_probe_down",
                          "拨测下线: {name}（{target}）— {error}",
                          "Probe down: {name} ({target}) — {error}",
                          name=name, target=target, error=error)
    else:
        dur = _fmt_dur(db.now() - (p["last_flip_ts"] or p["created_at"] or db.now()))
        msg = i18n.t(f"拨测恢复: {name}（{target}，持续 {dur}）",
                     f"Probe recovered: {name} ({target}, was down for {dur})")
        kind_word = i18n.t("拨测恢复", "Probe recovered")
        text = notify.tpl("notify_tpl_probe_up",
                          "拨测恢复: {name}（{target}，持续 {dur}）",
                          "Probe recovered: {name} ({target}, was down for {dur})",
                          name=name, target=target, dur=dur)
    if _notify_broadcast:
        await _notify_broadcast("probe", msg, {"probe_id": p["id"]})
    db.execute("INSERT INTO events(ts,host_id,kind,message,data) VALUES(?,?,?,?,?)",
               (db.now(), None, "probe", msg, db.j({"probe_id": p["id"]})))
    await notify.send(kind_word, text)
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


# ---------------------------------------------------------------- Push 拨测

def push_report(token: str) -> int | None:
    """按 token 定位 push 型目标；无效 token 返回 None（端点转 404）。"""
    p = db.query_one("SELECT id FROM probes WHERE kind='push' AND push_token=?", (token or "",))
    return p["id"] if p else None


async def push_miss(p: dict, grace: int) -> None:
    """push 超时判定：容忍窗口已过仍未收到上报 → 直接判 down（grace 即防抖）。

    心跳留痕 up=0 后强制置 down，事件/通知走与轮询翻转相同的出口。"""
    error = i18n.t(f"push 超时（>{grace}s 未上报）", f"push missed (no report for > {grace}s)")
    p = {**p, "_latency": None, "_error": error}
    db.execute("INSERT INTO probe_log(probe_id,ts,up,latency,error) VALUES(?,?,?,?,?)",
               (p["id"], db.now(), 0, None, error[:160]))
    db.execute("UPDATE probes SET up=0, succ_streak=0, last_error=? WHERE id=?",
               (error[:160], p["id"]))
    await _emit_flip(p, "down", error)


async def sweep_push() -> int:
    """push 型目标活性清扫（随拨测循环每轮执行）：超窗口未上报 → down。"""
    n = 0
    for p in db.query("SELECT * FROM probes WHERE kind='push' AND up=1"):
        grace = max(30, int(p["push_grace_s"] or 0) or 600)
        stale = (p["last_ts"] and db.now() - p["last_ts"] > grace) or \
                (not p["last_ts"] and p["created_at"] and db.now() - p["created_at"] > grace)
        if stale:
            await push_miss(p, grace)
            n += 1
    return n


# ---------------------------------------------------------------- 调度

# 拨测并发上限：一串 10s 超时目标串行会拖住整轮（目标多了跑不过拨测周期）
DIAL_SEMA = asyncio.Semaphore(8)


async def run_due() -> int:
    """跑一轮：push 型只做超时清扫（不轮询），其余到期目标并发拨测。

    每条可用 interval_s 覆盖全局 probe_interval（0=用全局）；
    单目标异常不拖垮同轮其他目标（gather 隔离）。"""
    global_interval = _setting_int("probe_interval", 30, minimum=15)
    due = []
    for p in db.query("SELECT * FROM probes"):
        if p["kind"] == "push":
            continue
        interval = max(15, int(p["interval_s"] or 0)) or global_interval
        if (p["last_ts"] or 0) + interval > db.now() + 0.5:
            continue
        due.append(dict(p))

    async def _one(p: dict) -> None:
        async with DIAL_SEMA:
            ok, latency, error = await run_probe(p)
        await handle_result(p, ok, latency, error)

    await asyncio.gather(*(_one(p) for p in due), return_exceptions=True)
    return len(due) + await sweep_push()


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
