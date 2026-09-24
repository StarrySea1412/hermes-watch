"""多渠道告警通知：企业微信 / 钉钉 / 飞书 / Telegram / Server酱 / 通用 Webhook /
Discord / Slack / ntfy / SMTP 邮件。

配置全部存 settings 表（webhook_url / notify_channel / telegram_chat_id /
quiet_hours）。免打扰时段（quiet_hours，如 "23:00-08:00"）内只记事件不发外呼。
发送失败静默返回错误串，由调用方决定是否留痕，绝不阻塞巡检主流程。

SMTP 用 Shoutrrr 式单字段打包进 webhook_url（与拨测 URL 同一输入框，UI 不加字段）：
    smtp://user:pass@smtp.gmail.com:587?to=me@example.com&from=alert@example.com
    端口 465 走 SMTP_SSL，其余端口 STARTTLS。

IM 双向（仅 Telegram，零公网回调）：长轮询循环 getUpdates。
v1（tg_ack=on）：回复 `ack <finding_id>` = 确认告警（停止 crit 周期重发）；`list`
= 当前未确认 crit 清单。通知附 ID 才可 ack——crit 聚合通知尾部带「ack: 3,7」。
v2（tg_command=on，默认关——命令面能审批/诊断，须显式授权）：
    status － 集群概览（在线/均分/crit·warn 计数/最需关注主机）
    ask <问题> － 与面板 /api/chat 同一 chat_answer 链路（脱敏/工具循环/AI
      关闭自动降级规则摘要全继承），回答直接回进聊天
    diag <发现ID> － 立即跑诊断流水线并回传根因（规则引擎结论，AI 仅附加视角）
    approve|reject [提案ID] － 审批提案（裸命令=待批清单）；批准 ≠ 执行，执行仍
      须在面板显式触发——IM 侧永不直接改机器，铁律不破
    help － 命令菜单
只听配置的 telegram_chat_id（防陌生人指挥面板）；回执超 4096 截断。
"""
import asyncio
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


# ---------------------------------------------------------------- IM 双向（Telegram ack）

def _setting_on(key: str) -> bool:
    r = db.query_one("SELECT value FROM settings WHERE key=?", (key,))
    return bool(r and r["value"] == "on")


def parse_ack(text: str) -> list[int]:
    """聊天命令 → finding ID 列表（纯函数）。`ack`/`ack 3`/`ack 3,7`/`确认 3`；
    裸 `ack` 返回空列表（由调用方解释为「确认全部未确认 crit」）。非命令返回 []。"""
    t = (text or "").strip().lower()
    if t in ("ack", "acknowledge", "确认"):
        return []
    for prefix in ("ack", "acknowledge", "确认"):
        if t.startswith(prefix + " ") or t.startswith(prefix + "：") or t.startswith(prefix + ":"):
            rest = t[len(prefix):].lstrip(" ：:").replace("，", ",")
            ids = []
            for part in rest.split(","):
                part = part.strip().lstrip("#")
                if part.isdigit():
                    ids.append(int(part))
            return ids
    return []


def parse_int_arg(text: str, prefixes: tuple[str, ...]) -> tuple[bool, int | None]:
    """`<前缀> <ID>` 单 ID 命令解析（纯函数，可测）。
    命中前缀 → (True, ID)；裸命令（无 ID）→ (True, None)；非命令 → (False, None)。
    兼容 `诊断：12` / `approve #5` / `diag  12`（多空白）。"""
    t = (text or "").strip().lower()
    for p in prefixes:
        if t == p:
            return True, None
        for sep in (" ", "：", ":"):
            if t.startswith(p + sep):
                rest = t[len(p) + len(sep):].strip().lstrip("#").lstrip("：:")
                return True, (int(rest) if rest.isdigit() else None)
    return False, None


def _tg_call(url: str, method: str, **payload):
    """Telegram Bot API 调用（返回 json 或抛异常；超时由调用方的轮询节奏兜底）。"""
    r = httpx.post(f"https://api.telegram.org/bot{url}/{method}",
                   json=payload, timeout=40)
    return r.json()


def _unacked_crits() -> list[dict]:
    return db.query(
        "SELECT id, host_id, title FROM findings WHERE severity='crit' "
        "AND acked_at IS NULL AND status IN ('open','analyzed') ORDER BY id DESC LIMIT 20")


def _tg_reply(url: str, chat_id: str, text: str) -> None:
    if len(text) > 3900:  # Telegram 单条上限 4096，留余量
        text = text[:3900] + "\n…（已截断）"
    try:
        _tg_call(url, "sendMessage", chat_id=chat_id, text=text)
    except Exception:
        pass  # 回执失败不阻塞循环


def apply_acks(ids: list[int]) -> tuple[int, list[int]]:
    """确认一批 finding（跳过不存在/已确认的）。返回 (新确认数, 实际处理的目标列表)。
    纯 db 函数（不外呼），供 TG 命令与测试共用。"""
    done, targets = 0, []
    for fid in ids:
        row = db.query_one("SELECT id, acked_at FROM findings WHERE id=?", (fid,))
        if row and not row["acked_at"]:
            db.execute("UPDATE findings SET acked_at=? WHERE id=?", (db.now(), fid))
            done += 1
        targets.append(fid)
    return done, targets


def _help_text() -> str:
    return i18n.t(
        "可用命令：\n"
        "status － 集群概览\n"
        "list － 未确认 crit 告警\n"
        "ack [ID] － 确认告警（裸 ack=全部）\n"
        "ask <问题> － 向巡检助手提问（AI 关闭时走规则引擎）\n"
        "diag <发现ID> － 立即诊断并回传根因\n"
        "approve [提案ID] / reject [提案ID] － 审批提案（裸命令=看待批清单）\n"
        "批准 ≠ 执行，执行请在面板完成。",
        "Commands:\n"
        "status — fleet overview\n"
        "list — unacked crit alerts\n"
        "ack [ID] — acknowledge (bare ack = all)\n"
        "ask <question> — ask the patrol assistant (rules-engine fallback)\n"
        "diag <finding_id> — diagnose now and return the root cause\n"
        "approve [id] / reject [id] — decide proposals (bare = list pending)\n"
        "Approval is not execution — run it from the panel.")


def _status_text() -> str:
    """集群概览文本：复用 fleet_snapshot（与面板/状态页同一数据源）。"""
    from . import analysis
    hosts = analysis.fleet_snapshot()["hosts"]
    if not hosts:
        return i18n.t("暂无主机。", "No hosts yet.")
    online = sum(1 for h in hosts if h["online"])
    scores = [h["score"] for h in hosts if h["score"] is not None]
    avg = round(sum(scores) / len(scores)) if scores else None
    crit = sum(1 for h in hosts if h["worst"] == "crit")
    warn = sum(1 for h in hosts if h["worst"] == "warn")
    head = i18n.t(f"概览：在线 {online}/{len(hosts)}", f"Overview: online {online}/{len(hosts)}")
    if avg is not None:
        head += i18n.t(f" ｜ 均分 {avg}", f" | avg score {avg}")
    head += i18n.t(f" ｜ crit {crit} · warn {warn}", f" | crit {crit} · warn {warn}")
    # 最需关注：健康分最低的 3 台，附其最严重发现标题
    worst_rows = db.query(
        "SELECT host_id, severity, title FROM findings WHERE status IN ('open','analyzed') "
        "ORDER BY CASE severity WHEN 'crit' THEN 0 WHEN 'warn' THEN 1 ELSE 2 END, id DESC")
    title_by: dict[int, str] = {}
    for r in worst_rows:
        title_by.setdefault(r["host_id"], r["title"])
    lines = []
    for h in sorted(hosts, key=lambda x: (x["score"] if x["score"] is not None else 999))[:3]:
        if h["status"] == "offline":
            lines.append(i18n.t(f"{h['name']}（离线）", f"{h['name']} (offline)"))
        else:
            t = title_by.get(h["id"], "")
            lines.append(i18n.t(f"{h['name']}（{h['score']} 分，{h['status']}）{t}",
                                f"{h['name']} ({h['score']}, {h['status']}) {t}"))
    return head + "\n" + "\n".join(lines)


def _pending_proposals_text() -> str:
    rows = db.query(
        "SELECT p.id, p.title, h.name AS host_name FROM proposals p JOIN hosts h ON h.id=p.host_id "
        "WHERE p.status='pending' ORDER BY p.id DESC LIMIT 10")
    if not rows:
        return i18n.t("当前无待批提案。", "No pending proposals.")
    lines = "\n".join(f"#{r['id']} [{r['host_name']}] {i18n.tr_proposal_title(r['title'])}" for r in rows)
    return i18n.t(f"待批提案：\n{lines}\n回复 approve <ID> 批准，reject <ID> 拒绝。",
                  f"Pending proposals:\n{lines}\nReply 'approve <ID>' or 'reject <ID>'.")


async def _v2_command(text: str, low: str, url: str, cid: str) -> bool:
    """v2 命令面（tg_command=on）：status / ask / diag / approve / reject / help。
    返回是否消费了这条消息（消费则外层直接返回；未命中前缀保持 v1 的静默语义）。"""
    from . import analysis
    if low in ("help", "帮助", "/help", "/start"):
        _tg_reply(url, cid, _help_text())
        return True
    if low in ("status", "概览", "overview"):
        _tg_reply(url, cid, _status_text())
        return True
    t = (text or "").strip()
    # ask <问题>：与面板 /api/chat 同一 chat_answer 链路（脱敏/工具循环/降级全继承）
    tl = t.lower()
    for p in ("ask", "问"):
        if tl == p or tl.startswith(p + " ") or t.startswith(p + "：") or t.startswith(p + ":"):
            q = t[len(p):].lstrip(" ：:").strip()
            if not q:
                _tg_reply(url, cid, i18n.t("用法：ask <问题>", "Usage: ask <question>"))
                return True
            out = await analysis.chat_answer(q)
            _tg_reply(url, cid, (out.get("answer") or "").strip() or
                      i18n.t("（助手没有给出回答）", "(no answer from the assistant)"))
            return True
    hit, fid = parse_int_arg(text, ("diag", "诊断"))
    if hit:
        if fid is None:
            _tg_reply(url, cid, i18n.t("用法：diag <发现ID>（回复 list 查看ID）",
                                       "Usage: diag <finding_id> (reply 'list' for IDs)"))
            return True
        f = db.query_one("SELECT * FROM findings WHERE id=?", (fid,))
        h = db.query_one("SELECT * FROM hosts WHERE id=?", (f["host_id"],)) if f else None
        if not f or not h:
            _tg_reply(url, cid, i18n.t(f"发现 #{fid} 不存在。", f"Finding #{fid} not found."))
            return True
        card = await analysis.analyze_finding(h, f)
        try:
            from . import scheduler
            await scheduler.broadcast("analysis",
                                      i18n.t(f"agent 完成诊断: {f['title']}",
                                             f"Agent diagnosis completed: {f['title']}"),
                                      {"finding_id": fid})
        except Exception:
            pass  # 广播失败不影响诊断回执
        prop = ""
        if card.get("proposal_id"):
            prop = i18n.t(f"\n已生成提案 #{card['proposal_id']}，回复 approve {card['proposal_id']} 批准。",
                          f"\nProposal #{card['proposal_id']} created — reply 'approve {card['proposal_id']}' to approve.")
        _tg_reply(url, cid, i18n.t(
            f"诊断完成：{h['name']} — {f['title']}\n根因：{card.get('root_cause', '')}",
            f"Diagnosis done: {h['name']} — {f['title']}\nRoot cause: {card.get('root_cause', '')}") + prop)
        return True
    for prefixes, action in ((("approve", "批准", "同意"), "approve"),
                             (("reject", "拒绝", "驳回"), "reject")):
        hit, pid = parse_int_arg(text, prefixes)
        if not hit:
            continue
        if pid is None:
            _tg_reply(url, cid, _pending_proposals_text())
            return True
        p = db.query_one("SELECT * FROM proposals WHERE id=?", (pid,))
        if not p:
            _tg_reply(url, cid, i18n.t(f"提案 #{pid} 不存在。", f"Proposal #{pid} not found."))
            return True
        if p["status"] != "pending":
            _tg_reply(url, cid, i18n.t(f"提案 #{pid} 已处理（{p['status']}）。",
                                       f"Proposal #{pid} already decided ({p['status']})."))
            return True
        status = "approved" if action == "approve" else "rejected"
        db.execute("UPDATE proposals SET status=?, decided_at=? WHERE id=?", (status, db.now(), pid))
        h = db.query_one("SELECT name FROM hosts WHERE id=?", (p["host_id"],))
        verb = i18n.t("已批准", "approved") if action == "approve" else i18n.t("已拒绝", "rejected")
        try:
            from . import scheduler
            await scheduler.broadcast("proposal",
                                      i18n.t(f"提案{verb}: {p['title']}", f"Proposal {verb}: {p['title']}") +
                                      (i18n.t(f"（Telegram 审批，主机 {h['name']}）",
                                              f" (via Telegram, host {h['name']})") if h else ""),
                                      {"proposal_id": pid})
        except Exception:
            pass
        _tg_reply(url, cid, i18n.t(
            f"提案 #{pid} {verb}：{p['title']}\n批准 ≠ 执行，执行请在面板完成。",
            f"Proposal #{pid} {verb}: {p['title']}\nApproval is not execution — run it from the panel."))
        return True
    if low.startswith("/"):
        _tg_reply(url, cid, _help_text())  # 未知斜杠命令给菜单，非命令文本保持静默
        return True
    return False


async def _handle_tg_command(text: str, chat_id: str) -> None:
    """解析并执行一条聊天命令。
    v1（tg_ack）：ack <ids>（裸 ack=全部未确认 crit）/ list
    v2（tg_command）：status / ask <问题> / diag <发现ID> / approve|reject [提案ID] / help"""
    c = conf()
    url, cid = c["url"], c["chat_id"]
    if str(chat_id) != str(cid):
        return  # 只听配置的 chat（防陌生人指挥面板）
    low = (text or "").strip().lower()
    if low in ("list", "列表", "状态"):
        rows = _unacked_crits()
        if not rows:
            _tg_reply(url, cid, i18n.t("当前无未确认 crit 告警。", "No unacknowledged crit alerts."))
        else:
            lines = "\n".join(f"#{r['id']} {r['title']}" for r in rows)
            _tg_reply(url, cid, i18n.t(
                f"未确认 crit：\n{lines}\n回复 ack <ID> 确认，或 ack 全部。",
                f"Unacked crit:\n{lines}\nReply 'ack <ID>' or 'ack' for all."))
        return
    ids = parse_ack(text)
    if ids is not None and (low in ("ack", "acknowledge", "确认") or ids):
        targets = ids or [r["id"] for r in _unacked_crits()]
        done, targets = apply_acks(targets)
        _tg_reply(url, cid, i18n.t(
            f"已确认 {done} 条告警（{', '.join('#' + str(i) for i in targets[:10])}）。",
            f"Acknowledged {done} alert(s) ({', '.join('#' + str(i) for i in targets[:10])})."))
        if done:
            try:
                from . import scheduler
                asyncio.get_running_loop().create_task(scheduler.broadcast(
                    "finding", i18n.t(f"Telegram 已确认 {done} 条告警",
                                      f"{done} alert(s) acknowledged via Telegram"), {}))
            except Exception:
                pass  # 广播失败不影响确认
        return
    if _setting_on("tg_command"):
        if await _v2_command(text, low, url, cid):
            return


async def tg_ack_loop():
    """Telegram 长轮询循环：tg_ack 或 tg_command 任一开启且渠道为 telegram 时由 main.py 启动。
    getUpdates 无需公网回调 URL（自托管本地面板的零配置双向通道）。"""
    await asyncio.sleep(3)  # 等启动期采集落库
    while True:
        try:
            c = conf()
            if not (_setting_on("tg_ack") or _setting_on("tg_command")) \
                    or c["channel"] != "telegram" or not c["url"]:
                await asyncio.sleep(15)
                continue
            offset = int((db.query_one("SELECT value FROM settings WHERE key='tg_ack_offset'")
                          or {}).get("value") or 0)
            data = _tg_call(c["url"], "getUpdates", offset=offset, timeout=30,
                            allowed_updates=["message"])
            for upd in (data.get("result") or []):
                offset = max(offset, upd["update_id"] + 1)
                msg = upd.get("message") or {}
                if str(msg.get("chat", {}).get("id")) == str(c["chat_id"]) and msg.get("text"):
                    await _handle_tg_command(msg["text"], msg["chat"]["id"])
            db.execute("INSERT INTO settings(key,value) VALUES('tg_ack_offset',?) "
                       "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(offset),))
        except Exception:
            pass  # 网络抖动/未配置：睡一觉重来，绝不外抛
        await asyncio.sleep(3)
