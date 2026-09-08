"""Background loop: collect -> rules -> persist findings/events. Runs every POLL seconds.

告警生命周期闭环（对标 Uptime Kuma 心跳模型 / Gatus success-threshold / Netdata CLEAR）：
- 触发：阈值规则命中即落 findings（滞后双阈值，恢复须回落到更低 clear 线之下）
- 防抖：条件解除后连续 RECOVER_ROUNDS 轮不再触发 → 自动 resolved（单次抖动不打翻告警）
- 恢复：resolved 时发恢复事件 + 恢复通知，文案带持续时长
- 重发：crit 持续未恢复时按 notify_resend_min 周期提醒（防看一眼就忘）
- 静默：免打扰时段（quiet_hours）内只记事件不发外呼（notify.send 内部处理）
采集并发执行（信号量限流），单主机失败不影响其他主机。
"""
import asyncio
import traceback

from . import analysis, collector, db, notify, rules

POLL = 60
_notify_broadcast = None  # set by main.py at startup to avoid a circular import


def set_broadcaster(fn):
    global _notify_broadcast
    _notify_broadcast = fn


def _setting_int(key: str, default: int) -> int:
    r = db.query_one("SELECT value FROM settings WHERE key=?", (key,))
    try:
        return max(5, int(float(r["value"])))
    except (TypeError, ValueError):
        return default


# 同类采集失败事件聚合窗口：10 分钟内同主机同错误只更新一条（×N 计数），不刷屏
_ERR_WINDOW = 600

# 发现连续 N 轮未再触发 → 自动恢复（60s 周期下约 3 分钟观察期，Gatus 默认 success-threshold=2）
RECOVER_ROUNDS = 3

# 采集并发上限：真实主机 SSH 探波单台最多 ~10s，限流防打爆对端
_COLLECT_SEMA = asyncio.Semaphore(6)


def _log_collect_error(host_id: int, message: str) -> None:
    dup = db.query_one(
        "SELECT id, data FROM events WHERE host_id=? AND kind='error' AND message=? AND ts>=? "
        "ORDER BY id DESC LIMIT 1", (host_id, message, db.now() - _ERR_WINDOW))
    if dup:
        data = db.uj(dup["data"], {})
        data["count"] = int(data.get("count", 1)) + 1
        db.execute("UPDATE events SET ts=?, data=? WHERE id=?", (db.now(), db.j(data), dup["id"]))
    else:
        db.execute("INSERT INTO events(ts,host_id,kind,message,data) VALUES(?,?,?,?,?)",
                   (db.now(), host_id, "error", message, db.j({"count": 1})))


def _mark_ok(host_id: int) -> None:
    db.execute("UPDATE hosts SET last_ok_ts=?, last_error='' WHERE id=?", (db.now(), host_id))
    last = db.query_one(
        "SELECT kind, ts FROM events WHERE host_id=? ORDER BY id DESC LIMIT 1", (host_id,))
    if last and last["kind"] == "error" and db.now() - last["ts"] < _ERR_WINDOW * 6:
        db.execute("INSERT INTO events(ts,host_id,kind,message,data) VALUES(?,?,'ok',?,?)",
                   (db.now(), host_id, "采集恢复，巡检数据恢复更新", db.j({"count": 1})))


def _mark_err(host_id: int, err: Exception) -> None:
    # 主机指纹变更 = 安全事件，与普通采集失败区分（醒目事件 + 事件流置顶）
    from .ssh import HostKeyChanged
    if isinstance(err, HostKeyChanged):
        db.execute("UPDATE hosts SET last_error=? WHERE id=?", ("HostKeyChanged", host_id))
        msg = f"安全告警: {err}——疑似中间人或系统重装，采集已拒绝连接；确认后可在设置页重置该主机指纹"
        db.execute("INSERT INTO events(ts,host_id,kind,message,data) VALUES(?,?,?,?,?)",
                   (db.now(), host_id, "finding", msg, db.j({"count": 1})))
        if _notify_broadcast:
            _spawn(_notify_broadcast("finding", msg, {}))
        _spawn(notify.send("SSH 指纹变更", msg.split(": ", 1)[-1]))
        return
    db.execute("UPDATE hosts SET last_error=? WHERE id=?", (type(err).__name__, host_id))
    _log_collect_error(host_id, f"采集失败: {type(err).__name__}")


def _spawn(coro):
    """在事件循环内调度协程；无循环的同步上下文（如回归测试）直接跑完。"""
    try:
        asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        asyncio.run(coro)


def _fmt_dur(sec: float) -> str:
    m = int(sec // 60)
    if m < 60:
        return f"{m} 分钟"
    h = m // 60
    if h < 24:
        return f"{h} 小时 {m % 60} 分" if m % 60 else f"{h} 小时"
    d = h // 24
    return f"{d} 天 {h % 24} 小时" if h % 24 else f"{d} 天"


def _check_recovery(host_id: int, active_types: set[str]) -> int:
    """本轮仍触发的类型清零观察计数；连续 RECOVER_ROUNDS 轮未触发的发现自动恢复。

    返回本轮回滚为恢复的发现数。主机采集失败轮不计入观察（见调用处）。"""
    n = 0
    rows = db.query(
        "SELECT id, type, ts, ok_streak, title FROM findings "
        "WHERE host_id=? AND status IN ('open','analyzed')", (host_id,))
    for r in rows:
        if r["type"] in active_types:
            if r["ok_streak"]:
                db.execute("UPDATE findings SET ok_streak=0 WHERE id=?", (r["id"],))
            continue
        streak = (r["ok_streak"] or 0) + 1
        if streak >= RECOVER_ROUNDS:
            db.execute("UPDATE findings SET status='resolved', resolved_at=?, ok_streak=0 WHERE id=?",
                       (db.now(), r["id"]))
            n += 1
            dur = _fmt_dur(db.now() - (r["ts"] or db.now()))
            msg = f"发现已自动恢复: {r['title']}（持续 {dur}，连续 {RECOVER_ROUNDS} 轮未再触发）"
            db.execute("INSERT INTO events(ts,host_id,kind,message,data) VALUES(?,?,'ok',?,?)",
                       (db.now(), host_id, msg, db.j({"count": 1})))
            if _notify_broadcast:
                _spawn(_notify_broadcast("ok", msg, {}))
            _spawn(notify.send("告警恢复", msg.split(": ", 1)[-1]))
        else:
            db.execute("UPDATE findings SET ok_streak=? WHERE id=?", (streak, r["id"]))
    return n


def _resend_crit(host_name: str, open_rows: list[dict], active_types: set[str]) -> None:
    """crit 发现持续未恢复 → 按 notify_resend_min 周期重发提醒（0 = 关闭）。
    已确认（acked_at 非空）或主机处于静默窗口的发现不重发。"""
    minutes = _setting_int("notify_resend_min", 0)
    if minutes <= 0 or not notify.configured():
        return
    silences = {r["id"]: (r["silenced_until"] or 0)
                for r in db.query("SELECT id, silenced_until FROM hosts")}
    for r in open_rows:
        if r["severity"] != "crit" or r["type"] not in active_types or r["acked_at"]:
            continue
        if silences.get(r["host_id"], 0) > db.now():
            continue
        last = r["last_notified"] or r["ts"] or 0
        if db.now() - last >= minutes * 60:
            db.execute("UPDATE findings SET last_notified=? WHERE id=?", (db.now(), r["id"]))
            _spawn(notify.send(
                "持续告警", f"{host_name}: {r['title']}（已持续超 {minutes} 分钟未恢复）"))


async def process_findings(h: dict, latest: dict, extras: dict) -> list[dict]:
    """规则评估 → 落新发现 → 事件/通知 → crit 重发 → 恢复观察。
    scheduler 轮询与 agent push 共用同一套告警生命周期。返回新落库的发现。"""
    triggered = rules.evaluate(h, latest, extras)
    active_types = {f["type"] for f in triggered}
    open_rows = db.query(
        "SELECT id, host_id, type, severity, ts, last_notified, acked_at, title FROM findings "
        "WHERE host_id=? AND status IN ('open','analyzed')", (h["id"],))
    prev_open = {r["type"] for r in open_rows}
    silenced = (h.get("silenced_until") or 0) > db.now()
    new = []
    for f in triggered:
        if f["type"] in prev_open:  # one open finding per type per host
            continue
        fid = db.execute(
            "INSERT INTO findings(host_id,ts,type,severity,title,detail,evidence) "
            "VALUES(?,?,?,?,?,?,?)",
            (f["host_id"], f["ts"], f["type"], f["severity"], f["title"],
             f["detail"], db.j(f["evidence"])))
        new.append(f)
        db.execute("INSERT INTO events(ts,host_id,kind,message,data) VALUES(?,?,?,?,?)",
                   (db.now(), h["id"], "finding",
                    f"[{f['severity'].upper()}] {f['title']}",
                    db.j({"finding_id": fid})))
        if _notify_broadcast:
            await _notify_broadcast("finding", f"[{f['severity'].upper()}] {f['title']}",
                                    {"finding_id": fid})
        if f["severity"] == "crit" and not silenced:
            ok, _ = await notify.send("发现告警", f"{h['name']}: {f['title']}")
            if ok:
                db.execute("UPDATE findings SET last_notified=? WHERE id=?", (db.now(), fid))
        if f["severity"] == "crit" or f["type"] in ("login",):
            # auto deep-dive critical findings, mirroring the demo story
            try:
                fresh = db.query_one("SELECT * FROM findings WHERE id=?", (fid,))
                await analysis.analyze_finding(h, fresh)
            except Exception:
                pass
    _resend_crit(h["name"], open_rows, active_types)
    _check_recovery(h["id"], active_types)
    return new


def _agent_fresh(h: dict) -> bool:
    """出站 agent 最近有上报 → 本轮跳过 SSH 探测（agent 主机不走 SSH）。"""
    if not h.get("agent_token"):
        return False
    ts = h.get("last_ok_ts") or 0
    return bool(ts) and db.now() - ts < _setting_int("poll_seconds", POLL) * 1.5


async def _collect_one(h: dict, summary: dict) -> None:
    async with _COLLECT_SEMA:
        if _agent_fresh(h):
            summary["collected"] += 1
            return
        try:
            latest, extras = await collector.collect(h)
        except Exception as e:
            summary["errors"].append(f"{h['name']}: {type(e).__name__}: {e}")
            _mark_err(h["id"], e)
            return
        _mark_ok(h["id"])
        summary["collected"] += 1
        try:
            created = await process_findings(h, latest, extras)
            summary["new_findings"] += len(created)
        except Exception:
            traceback.print_exc()


async def collect_all() -> dict:
    summary = {"collected": 0, "new_findings": 0, "errors": []}
    hosts = db.query("SELECT * FROM hosts")
    if not hosts:
        return summary
    await asyncio.gather(*(_collect_one(h, summary) for h in hosts), return_exceptions=True)
    return summary


async def tick():
    """Lightweight SSE heartbeat so the UI stays visibly live between cycles."""
    if _notify_broadcast:
        await _notify_broadcast("tick", "巡检心跳", {})


async def _maybe_autoreport():
    """Generate a fleet report every N minutes when auto_report_min > 0."""
    minutes = _setting_int("auto_report_min", 0)
    if minutes <= 0:
        return
    last = db.query_one("SELECT ts FROM reports ORDER BY ts DESC LIMIT 1")
    if last and db.now() - last["ts"] < minutes * 60:
        return
    from . import reports
    r = reports.generate("auto")
    db.execute("INSERT INTO events(ts,host_id,kind,message,data) VALUES(?,NULL,'report',?,?)",
               (db.now(), f"定时报告已生成（每 {minutes} 分钟），整体 {r['overall']} 分",
                db.j({"report_id": r["id"]})))
    print(f"[report] auto report #{r['id']} overall={r['overall']}")
    if _notify_broadcast:
        _spawn(_notify_broadcast(
            "report", f"定时健康报告已生成（整体 {r['overall']} 分）", {"report_id": r["id"]}))
    try:
        await reports.attach_ai_summary(r["id"])
    except Exception:
        traceback.print_exc()


def _maybe_retention():
    """保留策略：metrics 默认 7 天（0=永久），events 默认 30 天（审计表不动）。"""
    days = _setting_int("metrics_retention_days", 7)
    if days > 0:
        db.execute("DELETE FROM metrics WHERE ts < ?", (db.now() - days * 86400,))
    ev_days = _setting_int("events_retention_days", 30)
    if ev_days > 0:
        db.execute("DELETE FROM events WHERE ts < ?", (db.now() - ev_days * 86400,))


async def loop():
    while True:
        for _ in range(max(1, _setting_int("poll_seconds", POLL) // 15)):
            try:
                await tick()
            except Exception:
                pass
            await asyncio.sleep(15)
        try:
            await collect_all()
            await _maybe_autoreport()
            _maybe_retention()
        except Exception:
            traceback.print_exc()  # 调度循环的 bug 绝不静默
