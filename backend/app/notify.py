"""多渠道告警通知：企业微信 / 钉钉 / 飞书 / Telegram / Server酱 / 通用 Webhook。

配置全部存 settings 表（webhook_url / notify_channel / telegram_chat_id /
quiet_hours）。免打扰时段（quiet_hours，如 "23:00-08:00"）内只记事件不发外呼。
发送失败静默返回错误串，由调用方决定是否留痕，绝不阻塞巡检主流程。
"""
import datetime
import httpx

from . import db

LABELS = {
    "wecom": "企业微信",
    "dingtalk": "钉钉",
    "feishu": "飞书",
    "telegram": "Telegram",
    "serverchan": "Server酱",
    "webhook": "通用 Webhook",
}


def conf() -> dict:
    rows = {r["key"]: r["value"] for r in db.query("SELECT key,value FROM settings")}
    return {
        "channel": rows.get("notify_channel") if rows.get("notify_channel") in LABELS else "wecom",
        "url": (rows.get("webhook_url") or "").strip(),
        "chat_id": (rows.get("telegram_chat_id") or "").strip(),
        "quiet_hours": (rows.get("quiet_hours") or "").strip(),
    }


def configured() -> bool:
    return bool(conf()["url"])


def in_quiet_hours(spec: str) -> bool:
    """quiet_hours 如 "23:00-08:00"（支持跨午夜）；"08:00-22:00" 为日间窗口。"""
    import re
    m = re.match(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$", spec.strip())
    if not m:
        return False
    h1, m1, h2, m2 = (int(x) for x in m.groups())
    if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59:
        return False
    now = datetime.datetime.now()
    cur = now.hour * 60 + now.minute
    start, end = h1 * 60 + m1, h2 * 60 + m2
    if start <= end:
        return start <= cur < end
    return cur >= start or cur < end  # 跨午夜窗口


async def send(kind: str, text: str) -> tuple[bool, str]:
    """按渠道发一条通知并落 notify_log 留痕。返回 (ok, 错误说明)；
    未配置返回 (False, '未配置')，免打扰时段返回 (False, 'quiet')——事件照常落库，只是不外呼。"""
    c = conf()
    if not c["url"]:
        _log(kind, text, c["channel"], False, "未配置")
        return False, "未配置"
    if c["quiet_hours"] and in_quiet_hours(c["quiet_hours"]):
        _log(kind, text, c["channel"], False, "quiet（免打扰时段拦截）")
        return False, "quiet"
    tag = f"[Hermes Watch] {kind}"
    ch, url = c["channel"], c["url"]
    try:
        async with httpx.AsyncClient(timeout=8) as cli:
            if ch == "feishu":
                r = await cli.post(url, json={"msg_type": "text", "content": {"text": f"{tag}: {text}"}})
            elif ch == "telegram":
                r = await cli.post(f"https://api.telegram.org/bot{url}/sendMessage",
                                   json={"chat_id": c["chat_id"], "text": f"{tag}: {text}"})
            elif ch == "serverchan":
                r = await cli.post(f"https://sctapi.ftqq.com/{url}.send",
                                   data={"title": tag, "desp": text})
            elif ch == "webhook":  # 通用：扁平 JSON，方便自建中转
                r = await cli.post(url, json={"source": "hermes-watch", "kind": kind, "text": text})
            else:  # wecom / dingtalk 文本消息同构
                r = await cli.post(url, json={"msgtype": "text", "text": {"content": f"{tag}: {text}"}})
        if r.status_code >= 400:
            _log(kind, text, ch, False, f"HTTP {r.status_code}: {(r.text or '')[:120]}")
            return False, f"HTTP {r.status_code}: {(r.text or '')[:120]}"
        _log(kind, text, ch, True, "")
        return True, ""
    except Exception as e:
        err = f"{type(e).__name__}: {e}"[:200]
        _log(kind, text, ch, False, err)
        return False, err


def _log(kind: str, text: str, channel: str, ok: bool, error: str) -> None:
    """发送留痕（含静默拦截与失败），排障与审计用；上限由保留策略清理。"""
    try:
        db.execute("INSERT INTO notify_log(ts,kind,text,channel,ok,error) VALUES(?,?,?,?,?,?)",
                   (db.now(), kind, text[:300], channel, 1 if ok else 0, error))
    except Exception:
        pass  # 留痕失败绝不影响通知主流程
