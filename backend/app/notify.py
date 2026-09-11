"""多渠道告警通知：企业微信 / 钉钉 / 飞书 / Telegram / Server酱 / 通用 Webhook /
Discord / Slack / ntfy / SMTP 邮件。

配置全部存 settings 表（webhook_url / notify_channel / telegram_chat_id /
quiet_hours）。免打扰时段（quiet_hours，如 "23:00-08:00"）内只记事件不发外呼。
发送失败静默返回错误串，由调用方决定是否留痕，绝不阻塞巡检主流程。

SMTP 用 Shoutrrr 式单字段打包进 webhook_url（与拨测 URL 同一输入框，UI 不加字段）：
    smtp://user:pass@smtp.gmail.com:587?to=me@example.com&from=alert@example.com
    端口 465 走 SMTP_SSL，其余端口 STARTTLS。
"""
import datetime
import httpx

from . import db, i18n

LABELS = {
    "wecom": "企业微信",
    "dingtalk": "钉钉",
    "feishu": "飞书",
    "telegram": "Telegram",
    "serverchan": "Server酱",
    "webhook": "通用 Webhook",
    "discord": "Discord",
    "slack": "Slack",
    "ntfy": "ntfy",
    "smtp": "Email (SMTP)",
}

# 通知模板（settings 键 → 说明），tpl() 渲染 {var} 占位符；空 = 内置双语默认文案
TPL_KEYS = {
    "notify_tpl_finding": "发现告警（crit 聚合）：{host} {n} {list}",
    "notify_tpl_ongoing": "持续告警：{host} {title} {minutes}",
    "notify_tpl_probe_down": "拨测下线：{name} {target} {error}",
    "notify_tpl_probe_up": "拨测恢复：{name} {target} {dur}",
}


def tpl(key: str, zh: str, en: str, **vars) -> str:
    """自定义通知模板：settings[key] 非空则按其渲染（{var} 占位替换），否则内置双语默认。

    变量替换为纯字符串替换；模板里写了未提供的变量则原样保留 {var}（便于发现配错）。"""
    raw = (db.query_one("SELECT value FROM settings WHERE key=?", (key,)) or {}).get("value") or ""
    text = raw.strip() or i18n.t(zh, en)
    for k, v in vars.items():
        text = text.replace("{" + k + "}", str(v))
    return text


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
        if ch == "smtp":
            err = await _smtp_send(url, tag, text)
            if err:
                _log(kind, text, ch, False, err)
                return False, err
            _log(kind, text, ch, True, "")
            return True, ""
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
            elif ch == "discord":
                r = await cli.post(url, json={"content": f"**{tag}**\n{text}"})
            elif ch == "slack":
                r = await cli.post(url, json={"text": f"*{tag}*\n{text}"})
            elif ch == "ntfy":     # url = 完整主题 URL（如 https://ntfy.sh/my-topic）
                # httpx 头仅 latin-1 可编码：Title 中文时省略（正文 UTF-8 不受影响）
                headers = {"Priority": "high"}
                if tag.isascii():
                    headers["Title"] = tag
                r = await cli.post(url, content=text.encode("utf-8"), headers=headers)
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


def parse_smtp_url(url: str) -> dict:
    """smtp://user:pass@host:port?to=a@b.c&from=x@y.z → 配置 dict（纯函数，可测）。"""
    from urllib.parse import urlsplit, parse_qs
    sp = urlsplit(url.strip())
    if sp.scheme != "smtp" or not sp.hostname:
        raise ValueError("expected smtp://user:pass@host:port?to=...")
    q = parse_qs(sp.query)
    to = (q.get("to") or [""])[0]
    if not to:
        raise ValueError("missing ?to= recipient")
    return {
        "host": sp.hostname,
        "port": sp.port or 587,
        "user": sp.username or "",
        "password": sp.password or "",
        "to": to,
        "from": (q.get("from") or [sp.username or "hermes-watch@localhost"])[0],
    }


async def _smtp_send(url: str, tag: str, text: str) -> str:
    """SMTP 发送（线程池里跑同步 smtplib）；返回错误说明，空串=成功。"""
    import asyncio as _asyncio
    return await _asyncio.to_thread(_smtp_send_sync, url, tag, text)


def _smtp_send_sync(url: str, tag: str, text: str) -> str:
    import smtplib
    from email.message import EmailMessage
    try:
        cfg = parse_smtp_url(url)
    except ValueError as e:
        return str(e)[:160]
    msg = EmailMessage()
    msg["Subject"] = tag
    msg["From"] = cfg["from"]
    msg["To"] = cfg["to"]
    msg.set_content(text)
    try:
        if cfg["port"] == 465:
            with smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=10) as s:
                _smtp_login(s, cfg)
                s.send_message(msg)
        else:
            with smtplib.SMTP(cfg["host"], cfg["port"], timeout=10) as s:
                s.ehlo()
                try:
                    s.starttls()
                    s.ehlo()
                except smtplib.SMTPNotSupportedError:
                    pass  # 内网中继可能明文
                _smtp_login(s, cfg)
                s.send_message(msg)
        return ""
    except Exception as e:
        return f"{type(e).__name__}: {e}"[:200]


def _smtp_login(s, cfg: dict) -> None:
    if cfg["user"] and cfg["password"]:
        s.login(cfg["user"], cfg["password"])


def _log(kind: str, text: str, channel: str, ok: bool, error: str) -> None:
    """发送留痕（含静默拦截与失败），排障与审计用；上限由保留策略清理。"""
    try:
        db.execute("INSERT INTO notify_log(ts,kind,text,channel,ok,error) VALUES(?,?,?,?,?,?)",
                   (db.now(), kind, text[:300], channel, 1 if ok else 0, error))
    except Exception:
        pass  # 留痕失败绝不影响通知主流程
