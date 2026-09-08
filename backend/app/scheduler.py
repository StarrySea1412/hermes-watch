"""Background loop: collect -> rules -> persist findings/events. Runs every POLL seconds."""
import asyncio

from . import analysis, collector, db, rules

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


def _log_collect_ok(host_id: int) -> None:
    """采集成功；若该主机此前处于失败状态，记一条恢复事件。"""
    last = db.query_one(
        "SELECT kind, ts FROM events WHERE host_id=? ORDER BY id DESC LIMIT 1", (host_id,))
    if last and last["kind"] == "error" and db.now() - last["ts"] < _ERR_WINDOW * 6:
        db.execute("INSERT INTO events(ts,host_id,kind,message,data) VALUES(?,?,'ok',?,?)",
                   (db.now(), host_id, "采集恢复，巡检数据恢复更新", db.j({"count": 1})))


async def collect_all() -> dict:
    summary = {"collected": 0, "new_findings": 0, "errors": []}
    hosts = db.query("SELECT * FROM hosts")
    for h in hosts:
        try:
            latest, extras = await collector.collect(h)
        except Exception as e:
            summary["errors"].append(f"{h['name']}: {type(e).__name__}: {e}")
            _log_collect_error(h["id"], f"采集失败: {type(e).__name__}")
            continue
        _log_collect_ok(h["id"])
        summary["collected"] += 1
        prev_open = {f["type"] for f in db.query(
            "SELECT type FROM findings WHERE host_id=? AND status IN ('open','analyzed')", (h["id"],))}
        findings = rules.evaluate(h, latest, extras)
        for f in findings:
            if f["type"] in prev_open:  # one open finding per type per host
                continue
            fid = db.execute(
                "INSERT INTO findings(host_id,ts,type,severity,title,detail,evidence) "
                "VALUES(?,?,?,?,?,?,?)",
                (f["host_id"], f["ts"], f["type"], f["severity"], f["title"],
                 f["detail"], db.j(f["evidence"])))
            summary["new_findings"] += 1
            db.execute("INSERT INTO events(ts,host_id,kind,message,data) VALUES(?,?,?,?,?)",
                       (db.now(), h["id"], "finding",
                        f"[{f['severity'].upper()}] {f['title']}",
                        db.j({"finding_id": fid})))
            if f["severity"] == "crit" or f["type"] in ("login",):
                # auto deep-dive critical findings, mirroring the demo story
                try:
                    fresh = db.query_one("SELECT * FROM findings WHERE id=?", (fid,))
                    await analysis.analyze_finding(h, fresh)
                except Exception:
                    pass
    return summary


async def tick():
    """Lightweight SSE heartbeat so the UI stays visibly live between cycles."""
    if _notify_broadcast:
        await _notify_broadcast("tick", "巡检心跳", {})


def _maybe_autoreport():
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
        await_notify = _notify_broadcast("report", f"定时健康报告已生成（整体 {r['overall']} 分）", {"report_id": r["id"]})
        asyncio.ensure_future(await_notify)


def _maybe_retention():
    """指标数据保留策略：默认保留 7 天原始点（metrics_retention_days 可调，0 = 永久）。"""
    days = _setting_int("metrics_retention_days", 7)
    if days <= 0:
        return
    db.execute("DELETE FROM metrics WHERE ts < ?", (db.now() - days * 86400,))


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
            _maybe_autoreport()
            _maybe_retention()
        except Exception:
            pass
