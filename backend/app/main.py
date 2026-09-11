"""FastAPI application: REST + SSE stream + WebSocket terminal + MCP. Run: python run.py"""
import asyncio
import json
import math
import os
import pathlib
import re
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from . import agent, analysis, auth, backups, badge, ccswitch, db, executor, guard, i18n, mcp_server, notify, ports, probes, reports, scheduler, seed, secrets, terminal

# 前端经 vite 代理（生产同源部署）访问 /api，浏览器永远同源 —— 不开 CORS 面


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 恢复标记消费必须先于 init_db：用户点了「恢复此备份」后，重启即还原库文件
    restored = backups.consume_restore_if_pending()
    if restored:
        print(f"[backup] database restored from {restored}", flush=True)
    db.init_db()
    scheduler.set_broadcaster(broadcast)
    probes.set_broadcaster(broadcast)
    if seed.seed_if_empty():
        print("[seed] demo fleet created: web-1 / db-1 / app-1 / cache-1")
    asyncio.create_task(scheduler.collect_all())  # 首轮巡检后台跑：端口先监听，SSH 慢主机不阻塞启动
    asyncio.create_task(scheduler.loop())
    asyncio.create_task(probes.loop())  # 拨测独立循环（15s 步进，与 60s 巡检解耦）
    yield


app = FastAPI(title="Hermes Watch", version="0.2.0", lifespan=lifespan)

# 无需面板会话的路径：登录流程自身、出站 Agent（token 自鉴权）、本地 MCP（只读、仅本机）
AUTH_OPEN = ("/api/auth/status", "/api/auth/login",
             "/api/agent/push", "/api/agent/push/body", "/api/agent/script",
             "/api/mcp", "/api/mcp/tools")


@app.middleware("http")
async def panel_auth_middleware(request: Request, call_next):
    path = request.url.path
    # 公开端点兜底限流（token 门禁之外的防滥用层）+ 访问审计（内存环形缓冲）
    if any(path.startswith(p) for p in ("/badge/", "/status/", "/api/push/", "/api/agent/push")):
        ip = request.client.host if request.client else "-"
        if not guard.allow(ip):
            return JSONResponse({"detail": i18n.t("请求过于频繁", "Too many requests")}, status_code=429)
    if auth.enabled() and path.startswith("/api") and path not in AUTH_OPEN \
            and not path.startswith("/api/push/"):
        role = auth.session_role(request.cookies.get(auth.COOKIE, ""))
        if not role:
            return JSONResponse({"detail": i18n.t("面板未登录", "Panel not signed in")}, status_code=401)
        # observer 只读：拦写方法；登录后的自身口令修改走专用端点不受影响
        if role == "observer" and request.method not in ("GET", "HEAD", "OPTIONS") \
                and not path.startswith("/api/auth/self"):
            return JSONResponse({"detail": i18n.t("观察者角色为只读，写操作需要管理员权限",
                                                  "Observer role is read-only — write operations require admin")},
                                 status_code=403)
    resp = await call_next(request)
    if any(path.startswith(p) for p in ("/badge/", "/status/", "/api/push/", "/api/agent/push")):
        guard.audit(request.client.host if request.client else "-", path, resp.status_code)
    return resp

_subs: set[asyncio.Queue] = set()


async def broadcast(kind: str, message: str, data: dict | None = None):
    evt = {"ts": db.now(), "kind": kind, "message": message, "data": data or {}}
    for q in list(_subs):
        await q.put(json.dumps(evt, ensure_ascii=False))


@app.get("/api/stream")
async def stream():
    q: asyncio.Queue = asyncio.Queue()

    async def gen():
        _subs.add(q)
        try:
            while True:
                data = await q.get()
                yield f"data: {data}\n\n"
        finally:
            _subs.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------- fleet / hosts ----------

@app.get("/api/fleet")
def fleet():
    return analysis.fleet_snapshot()


# ---------- 演示引导模式 ----------

@app.delete("/api/demo")
async def remove_demo_hosts():
    """移除全部演示主机及其数据，进入真实接入模式。真实主机不受影响。"""
    ids = [m["id"] for m in db.query("SELECT id FROM hosts WHERE mock=1")]
    for hid in ids:
        for table in ("metrics", "findings", "proposals", "events"):
            db.execute(f"DELETE FROM {table} WHERE host_id=?", (hid,))
        db.execute("DELETE FROM hosts WHERE id=?", (hid,))
    await broadcast("host", i18n.t(f"已移除 {len(ids)} 台演示主机，切换到真实接入模式",
                                   f"Removed {len(ids)} demo hosts — switched to real-host mode"))
    return {"removed": len(ids)}


@app.post("/api/demo/seed")
async def seed_demo_hosts_ep():
    """恢复演示引导（真实主机保留，演示机并列加入）。"""
    if db.query_one("SELECT id FROM hosts WHERE mock=1"):
        raise HTTPException(400, i18n.t("演示主机已存在", "Demo hosts already exist"))
    n = seed.seed_demo_hosts()
    await broadcast("host", i18n.t(f"已恢复 {n} 台演示引导主机",
                                   f"Restored {n} demo bootstrap hosts"))
    return {"seeded": n}


class HostIn(BaseModel):
    name: str
    hostname: str
    port: int = 22
    username: str = "root"
    secret: str = ""
    group_name: str = "default"
    bastion_host: str = ""      # 堡垒机/跳板（空=直连）
    bastion_port: int = 22
    bastion_username: str = "root"
    bastion_secret: str = ""


@app.post("/api/hosts")
async def add_host(h: HostIn):
    if db.query_one("SELECT id FROM hosts WHERE name=?", (h.name,)):
        raise HTTPException(400, i18n.t("同名主机已存在", "A host with this name already exists"))
    # TOFU 人工确认模式：新主机初始 trusted=0（首次指纹记录后仍需面板确认）
    tofu_manual = (db.query_one("SELECT value FROM settings WHERE key='tofu_confirm'") or {}).get("value") == "on"
    hid = db.execute(
        "INSERT INTO hosts(name,hostname,port,username,secret,group_name,mock,created_at,"
        "bastion_host,bastion_port,bastion_username,bastion_secret,trusted) VALUES(?,?,?,?,?,?,0,?,?,?,?,?,?)",
        (h.name, h.hostname, h.port, h.username, secrets.encrypt(h.secret), h.group_name, db.now(),
         (h.bastion_host or "").strip(), h.bastion_port or 22, (h.bastion_username or "").strip() or "root",
         secrets.encrypt(h.bastion_secret) if (h.bastion_secret or "").strip() else "",
         0 if tofu_manual else 1))
    await broadcast("host", i18n.t(f"新增主机 {h.name}", f"Host added: {h.name}"))
    return {"id": hid}


@app.delete("/api/hosts/{hid}")
async def del_host(hid: int):
    if not db.query_one("SELECT id FROM hosts WHERE id=?", (hid,)):
        raise HTTPException(404, i18n.t("主机不存在", "Host not found"))
    for table in ("metrics", "findings", "proposals", "events", "proposal_runs"):
        db.execute(f"DELETE FROM {table} WHERE host_id=?", (hid,))
    db.execute("DELETE FROM hosts WHERE id=?", (hid,))
    await broadcast("host", i18n.t(f"已删除主机 #{hid} 及其全部巡检数据",
                                   f"Host #{hid} deleted along with all its inspection data"))
    return {"ok": True}


@app.get("/api/hosts/{hid}")
def host_detail(hid: int, range_min: int = 240):
    h = db.query_one("SELECT * FROM hosts WHERE id=?", (hid,))
    if not h:
        raise HTTPException(404, i18n.t("主机不存在", "Host not found"))
    metrics = db.query(
        "SELECT ts,cpu,mem,disk,net_in,net_out,load1 FROM metrics WHERE host_id=? AND ts>? ORDER BY ts",
        (hid, db.now() - range_min * 60))
    findings = db.query(
        "SELECT * FROM findings WHERE host_id=? AND status IN ('open','analyzed') ORDER BY ts DESC", (hid,))
    for f in findings:
        f["evidence"] = db.uj(f["evidence"], {})
        # 读出口兜底：历史中文行跟随面板语言（同 /api/findings，card 走 analysis.tr_card 反查）
        f["title"] = i18n.tr_finding_title(f["title"])
        f["detail"] = i18n.tr_finding_detail(f["detail"])
        f["card"] = analysis.tr_card(db.uj(f["card"], None))
    extras = {}
    if h["mock"]:
        from .collector import mock_extras
        extras = mock_extras(dict(h))
    elif h.get("last_extras"):
        extras = db.uj(h["last_extras"], {})  # Go agent 推送的最新 extras
    return {"host": {**h, "secret": ""}, "metrics": metrics, "findings": findings, "extras": extras}


# ---------- findings / analysis / proposals ----------

@app.get("/api/findings")
def findings(status: str = "open,analyzed,resolved"):
    # resolved 也返回：诊断列表要能看到"已解决 ✓"的发现及其执行审计
    rows = db.query(
        "SELECT f.*, h.name AS host_name FROM findings f JOIN hosts h ON h.id=f.host_id "
        f"WHERE f.status IN ({','.join('?' * len(status.split(',')))}) "
        "ORDER BY CASE f.severity WHEN 'crit' THEN 0 WHEN 'warn' THEN 1 ELSE 2 END, f.ts DESC",
        tuple(status.split(",")))
    for r in rows:
        r["evidence"] = db.uj(r["evidence"], {})
        # 读出口兜底：历史中文存库行按面板语言翻译（正则模板匹配，未命中保留原文）
        r["card"] = analysis.tr_card(db.uj(r["card"], None))
        r["title"] = i18n.tr_finding_title(r["title"])
        r["detail"] = i18n.tr_finding_detail(r["detail"])
    return rows


@app.post("/api/findings/{fid}/analyze")
async def analyze(fid: int):
    f = db.query_one("SELECT * FROM findings WHERE id=?", (fid,))
    if not f:
        raise HTTPException(404, i18n.t("finding 不存在", "finding not found"))
    h = db.query_one("SELECT * FROM hosts WHERE id=?", (f["host_id"],))
    card = await analysis.analyze_finding(h, f)
    await broadcast("analysis", i18n.t(f"agent 完成诊断: {f['title']}",
                                       f"Agent diagnosis completed: {f['title']}"),
                    {"finding_id": fid})
    if f["severity"] == "crit":
        asyncio.ensure_future(notify.send(i18n.t("诊断完成", "Diagnosis completed"),
                                          i18n.t(f"{h['name']}: {f['title']} → {card.get('root_cause', '')[:80]}",
                                                 f"{h['name']}: {f['title']} → {card.get('root_cause', '')[:80]}")))
        # Aurora Actions 式「诊断后留档」：crit 诊断完成且开关开启 → 自动生成一份诊断时点报告
        _ron = (db.query_one("SELECT value FROM settings WHERE key='report_on_diag'") or {}).get("value")
        if _ron == "on":
            # HTML 渲染是 CPU 密集：丢线程池跑，别在事件循环上卡住整个面板
            r = await asyncio.to_thread(reports.generate, "diag")
            await broadcast("report", i18n.t(f"诊断触发自动报告 #{r['id']}（整体 {r['overall']} 分）",
                                             f"Diagnosis-triggered report #{r['id']} generated (overall {r['overall']})"),
                            {"report_id": r["id"]})
    return card


class Decision(BaseModel):
    action: str  # approve | reject


@app.post("/api/proposals/{pid}/decide")
async def decide(pid: int, d: Decision):
    p = db.query_one("SELECT * FROM proposals WHERE id=?", (pid,))
    if not p:
        raise HTTPException(404, i18n.t("proposal 不存在", "proposal not found"))
    if p["status"] != "pending":
        raise HTTPException(400, i18n.t("该提案已处理", "This proposal was already decided"))
    if d.action not in ("approve", "reject"):
        raise HTTPException(400, i18n.t("action 必须是 approve/reject",
                                        "action must be approve or reject"))
    status = "approved" if d.action == "approve" else "rejected"
    db.execute("UPDATE proposals SET status=?, decided_at=? WHERE id=?", (status, db.now(), pid))
    # 注意：批准 ≠ 解决。发现保持 analyzed（列表可见），执行成功后才转 resolved
    h = db.query_one("SELECT hostname FROM hosts WHERE id=?", (p["host_id"],))
    verb = i18n.t("已批准", "approved") if status == "approved" else i18n.t("已拒绝", "rejected")
    tail = (i18n.t(f"（演示环境，命令未实际执行于 {h['hostname']}）",
                   f" (demo environment, command not executed on {h['hostname']})") if h else "")
    await broadcast("proposal",
                    i18n.t(f"提案{verb}: {p['title']}", f"Proposal {verb}: {p['title']}") + tail,
                    {"proposal_id": pid})
    return {"ok": True, "status": status}


@app.get("/api/proposals")
def proposals():
    rows = db.query(
        "SELECT p.*, h.name AS host_name, h.mock AS host_mock FROM proposals p JOIN hosts h ON h.id=p.host_id "
        "ORDER BY p.ts DESC LIMIT 50")
    runs = executor.runs_for([r["id"] for r in rows])
    for r in rows:
        r["runs"] = runs.get(r["id"], [])
        r["exec_enabled"] = executor.exec_enabled()
        # 读出口兜底：历史中文提案行跟随面板语言（command 是命令，不翻译）
        r["title"] = i18n.tr_proposal_title(r["title"])
        r["rationale"] = i18n.tr_proposal_rationale(r["rationale"])
    return rows


@app.post("/api/proposals/{pid}/execute")
async def execute_proposal(pid: int):
    """批准之后的显式执行动作（与 decide 分离）：白名单校验 + 超时 + 全审计。"""
    p = db.query_one("SELECT * FROM proposals WHERE id=?", (pid,))
    if not p:
        raise HTTPException(404, i18n.t("proposal 不存在", "proposal not found"))
    if p["status"] != "approved":
        raise HTTPException(400, i18n.t("只能执行已批准的提案", "Only approved proposals can be executed"))
    h = db.query_one("SELECT * FROM hosts WHERE id=?", (p["host_id"],))
    if not h:
        raise HTTPException(404, i18n.t("主机不存在", "Host not found"))
    run = await executor.execute(p, dict(h))
    ok_exec = run["status"] == "ok"
    await broadcast("exec",
                    i18n.t(f"提案 #{pid} 执行{'' if ok_exec else '失败'}（{run['status']}）: {p['title']}",
                           f"Proposal #{pid} executed{' failed' if not ok_exec else ''} "
                           f"({run['status']}): {p['title']}"),
                    {"proposal_id": pid, "run_id": run["id"]})
    if run["status"] == "ok" and p["finding_id"]:
        # 执行成功 → 发现真正解决
        db.execute("UPDATE findings SET status='resolved', resolved_at=? WHERE id=?",
                   (db.now(), p["finding_id"]))
    elif run["status"] != "ok" and p["finding_id"]:
        # 执行失败/超时 → 发现保持 analyzed，回到待处理
        db.execute("UPDATE findings SET status='analyzed' WHERE id=? AND status='resolved'",
                   (p["finding_id"],))
    return run


# ---------- events / reports / settings ----------

@app.get("/api/events")
def events(limit: int = 80):
    rows = db.query(
        "SELECT e.*, h.name AS host_name FROM events e LEFT JOIN hosts h ON h.id=e.host_id "
        "ORDER BY e.ts DESC LIMIT ?", (limit,))
    for r in rows:
        r["data"] = db.uj(r["data"], {})
        # 读出口兜底：历史中文事件行按面板语言翻译（嵌套标题一并处理）
        r["message"] = probes.tr_probe_message(i18n.tr_event_message(r["message"]))
    return rows


@app.post("/api/reports/generate")
async def gen_report(kind: str = "manual"):
    r = await asyncio.to_thread(reports.generate, kind)  # CPU 密集渲染离线程池
    await broadcast("report", i18n.t(f"健康报告已生成（整体 {r['overall']} 分）",
                                     f"Health report generated (overall score {r['overall']})"), r)
    # AI 摘要异步追加（AI 外发开启时）：报告立即可看，摘要稍后出现在第 06 节
    async def _ai():
        res = await reports.attach_ai_summary(r["id"])
        if res.get("ai") or res.get("error"):
            await broadcast("report",
                            i18n.t(f"报告 #{r['id']} AI 摘要已{'生成' if res.get('ai') else '失败（规则结论不受影响）'}",
                                   f"Report #{r['id']} AI summary "
                                   f"{'generated' if res.get('ai') else 'failed (rule verdicts unaffected)'}"),
                            {"report_id": r["id"]})
    asyncio.ensure_future(_ai())
    return r


@app.get("/api/reports")
def list_reports():
    return reports.list_reports()


@app.get("/api/reports/{rid}/html", response_class=HTMLResponse)
def report_html(rid: int):
    r = db.query_one("SELECT content_html FROM reports WHERE id=?", (rid,))
    if not r:
        raise HTTPException(404)
    return r["content_html"]


@app.delete("/api/reports/{rid}")
async def report_delete(rid: int):
    db.execute("DELETE FROM reports WHERE id=?", (rid,))
    await broadcast("report", i18n.t(f"报告 #{rid} 已删除", f"Report #{rid} deleted"))
    return {"ok": True}


@app.delete("/api/reports")
async def reports_clear():
    n = db.query_one("SELECT COUNT(*) AS n FROM reports")["n"]
    db.execute("DELETE FROM reports")
    await broadcast("report", i18n.t(f"已一键清空全部报告（{n} 份）",
                                     f"Cleared all reports ({n} in total)"))
    return {"ok": True, "removed": n}


@app.get("/api/settings")
def get_settings():
    rows = dict((r["key"], r["value"]) for r in db.query("SELECT key,value FROM settings"))
    rows.setdefault("ai_outbound", "off")
    return rows


@app.post("/api/settings")
async def set_settings(payload: dict):
    for k, v in payload.items():
        if k.startswith("panel_"):
            continue  # 口令/开关走 /api/auth/*，不允许经通用设置端点篡改
        db.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))
        if k == "ai_outbound":
            await broadcast("settings", i18n.t(f"AI 外发已{'开启' if str(v) == 'on' else '关闭'}",
                                               f"AI outbound {'enabled' if str(v) == 'on' else 'disabled'}"))
    return {"ok": True}


# ---------- notification channels ----------
@app.get("/api/notify/channels")
def notify_channels():
    return {"labels": notify.LABELS, "configured": notify.configured(),
            **notify.conf()}


@app.post("/api/notify/test")
async def notify_test():
    ok, err = await notify.send(i18n.t("测试", "Test"),
                                i18n.t("这是一条 Hermes Watch 测试通知，收到即代表渠道配置生效",
                                       "This is a Hermes Watch test notification — receiving it means the channel works"))
    if not ok:
        raise HTTPException(400, err or i18n.t("发送失败", "Send failed"))
    await broadcast("settings", i18n.t("通知渠道测试通过", "Notification channel test passed"))
    return {"ok": True}


@app.get("/api/notify/log")
def notify_log(limit: int = 30):
    rows = db.query("SELECT * FROM notify_log ORDER BY ts DESC LIMIT ?", (limit,))
    # 读出口兜底：历史中文 kind 与 text 中嵌套的发现标题跟随面板语言（同 /api/events）
    for r in rows:
        r["kind"] = i18n.tr_notify_kind(r["kind"])
        r["text"] = i18n.tr_notify_text(r["text"])
        if r.get("error"):
            r["error"] = i18n.tr_notify_error(r["error"])
    return rows


# ---------- 拨测（URL / TCP 服务监控，独立于主机巡检） ----------

class ProbeIn(BaseModel):
    name: str
    kind: str = "url"          # url | tcp | dns | push
    target: str                # url=完整地址 / tcp=host:port / dns=域名 / push=占位（自动生成）
    fail_threshold: int = probes.DEFAULT_FAIL_THRESHOLD
    success_threshold: int = probes.DEFAULT_SUCCESS_THRESHOLD
    timeout_s: int = 10
    keyword: str = ""          # URL 条件:响应体须包含
    max_latency_ms: int = 0    # URL 条件:响应时间上限 ms（0=不查）
    cert_days_min: int = 0     # URL 条件:HTTPS 证书最低剩余天数（0=不查）
    interval_s: int = 0        # 每目标独立周期（0=用全局 probe_interval）
    dns_resolver: str = ""     # DNS 条件:解析器 host[:port]（DNS 拨测必填）
    dns_type: str = "A"        # DNS 条件:记录类型 A/AAAA/CNAME/TXT/MX/NS
    dns_expected: str = ""     # DNS 条件:答案须包含（空=有答案即可）
    push_grace_s: int = 600    # Push 条件:容忍窗口（超时未上报 → down）


@app.get("/api/probes")
def probes_list():
    return db.query("SELECT * FROM probes ORDER BY id")


# Push 拨测上报（Kuma push monitor 式）：外部服务 GET/POST ?status=up|down&msg=…；
# token 自鉴权，中间件已对 /api/push/ 前缀豁免会话门
@app.get("/api/push/{token}")
@app.post("/api/push/{token}")
async def probe_push(token: str, status: str = "up", msg: str = ""):
    pid = probes.push_report(token)
    if pid is None:
        raise HTTPException(404, i18n.t("push token 无效", "Invalid push token"))
    p = db.query_one("SELECT * FROM probes WHERE id=?", (pid,))
    ok = status != "down"
    await probes.handle_result(p, ok, None, "" if ok else (msg or "reported down"))
    return {"ok": True}


@app.post("/api/probes")
async def probe_add(p: ProbeIn):
    name = p.name.strip()[:60]
    target = p.target.strip()
    if not name:
        raise HTTPException(400, i18n.t("名称不能为空", "Name is required"))
    kind = p.kind if p.kind in ("url", "tcp", "dns", "push") else "url"
    if kind != "push" and not target:
        raise HTTPException(400, i18n.t("目标不能为空", "Target is required"))
    if kind == "url" and not target.startswith(("http://", "https://")):
        raise HTTPException(400, i18n.t("URL 拨测目标须以 http:// 或 https:// 开头",
                                        "URL probe target must start with http:// or https://"))
    if kind == "tcp" and (":" not in target or not target.rpartition(":")[2].isdigit()):
        raise HTTPException(400, i18n.t("TCP 拨测目标格式为 主机:端口", "TCP probe target must be host:port"))
    if kind == "dns":
        if "/" in target or " " in target:
            raise HTTPException(400, i18n.t("DNS 拨测目标应为待解析域名", "DNS probe target must be a hostname"))
        if not (p.dns_resolver or "").strip():
            raise HTTPException(400, i18n.t("DNS 拨测须指定解析器（如 223.5.5.5）",
                                            "DNS probe requires a resolver (e.g. 223.5.5.5)"))
    if db.query_one("SELECT id FROM probes WHERE name=?", (name,)):
        raise HTTPException(400, i18n.t(f"同名拨测已存在: {name}", f"A probe named {name} already exists"))
    if kind == "tcp" and (p.keyword or p.max_latency_ms or p.cert_days_min):
        raise HTTPException(400, i18n.t("条件引擎仅适用于 URL 拨测",
                                        "Conditions apply to URL probes only"))
    if kind == "dns" and ((p.dns_type or "A").upper() not in probes.QTYPES):
        raise HTTPException(400, i18n.t("DNS 记录类型不支持", "DNS record type not supported"))
    if p.interval_s and p.interval_s < 15:
        raise HTTPException(400, i18n.t("独立周期不能低于 15 秒", "Per-probe interval must be ≥ 15s"))
    if p.push_grace_s and p.push_grace_s < 30:
        raise HTTPException(400, i18n.t("Push 容忍窗口不能低于 30 秒", "Push grace window must be ≥ 30s"))
    # push 型 target 仅作占位展示；上报凭据为独立 push_token（Kuma push monitor 式）
    target = "push" if kind == "push" else target
    push_token = agent.new_token() if kind == "push" else ""
    pid = db.execute(
        "INSERT INTO probes(name,kind,target,fail_threshold,success_threshold,timeout_s,"
        "keyword,max_latency_ms,cert_days_min,interval_s,dns_resolver,dns_type,dns_expected,"
        "push_token,push_grace_s,up,fail_streak,succ_streak,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (name, kind, target, max(1, p.fail_threshold), max(1, p.success_threshold),
         max(1, p.timeout_s), (p.keyword or "").strip()[:200], max(0, p.max_latency_ms),
         max(0, p.cert_days_min), max(0, p.interval_s), (p.dns_resolver or "").strip()[:120],
         (p.dns_type or "A").upper(), (p.dns_expected or "").strip()[:200],
         push_token, max(30, p.push_grace_s or 600), 1, 0, 0, db.now()))
    await broadcast("probe", i18n.t(f"新增拨测: {name} → {target}", f"Probe added: {name} → {target}"),
                    {"probe_id": pid})
    return {"id": pid, "push_token": push_token}


@app.delete("/api/probes/{pid}")
async def probe_delete(pid: int):
    row = db.query_one("SELECT * FROM probes WHERE id=?", (pid,))
    if not row:
        raise HTTPException(404, i18n.t("拨测不存在", "Probe not found"))
    db.execute("DELETE FROM probes WHERE id=?", (pid,))
    db.execute("DELETE FROM probe_log WHERE probe_id=?", (pid,))
    await broadcast("probe", i18n.t(f"已删除拨测: {row['name']}", f"Probe removed: {row['name']}"),
                    {"probe_id": pid})
    return {"deleted": pid}


@app.post("/api/probes/{pid}/run")
async def probe_run_now(pid: int):
    """立即拨一次（不看周期），心跳与状态机照常走。"""
    p = db.query_one("SELECT * FROM probes WHERE id=?", (pid,))
    if not p:
        raise HTTPException(404, i18n.t("拨测不存在", "Probe not found"))
    ok, latency, error = await probes.run_probe(p)
    await probes.handle_result(p, ok, latency, error)
    return {"ok": ok, "latency": latency and round(latency), "error": error}


@app.get("/api/probes/{pid}/log")
def probe_log(pid: int, limit: int = 120):
    return db.query("SELECT * FROM probe_log WHERE probe_id=? ORDER BY ts DESC LIMIT ?", (pid, limit))


# ---------- 备份与恢复（SQLite 单文件是部署优点，这里把它做成运维能力） ----------

@app.get("/api/backups")
def backups_list(request: Request):
    _require_admin(request)
    return backups.list_backups()


@app.post("/api/backups")
async def backups_create(request: Request):
    _require_admin(request)
    info = backups.create_backup()
    backups.prune()
    await broadcast("settings", i18n.t(f"已创建数据库备份: {info['name']}",
                                       f"Database backup created: {info['name']}"))
    return info


@app.delete("/api/backups/{name}")
async def backups_delete(name: str, request: Request):
    _require_admin(request)
    p = backups.resolve_name(name)
    if not p:
        raise HTTPException(404, i18n.t("备份不存在", "Backup not found"))
    p.unlink()
    return {"deleted": name}


@app.post("/api/backups/{name}/restore")
async def backups_restore(name: str, request: Request):
    """暂存恢复标记；重启后面板自动还原到该备份（运行中换库文件不安全）。"""
    _require_admin(request)
    if not backups.stage_restore(name):
        raise HTTPException(404, i18n.t("备份不存在", "Backup not found"))
    msg = i18n.t(f"已选择恢复到 {name}，重启面板后生效",
                 f"Restore to {name} staged; takes effect on next panel restart")
    await broadcast("settings", msg)
    return {"staged": True, "message": msg}


# ---------- AI chat ----------

class ChatIn(BaseModel):
    question: str
    history: list[dict] = []   # 多轮对话上下文：[{role:'user'|'assistant', content}...]


@app.post("/api/chat")
async def chat(c: ChatIn):
    return await analysis.chat_answer(c.question.strip()[:500], c.history)


@app.post("/api/chat/stream")
async def chat_stream(c: ChatIn):
    """SSE 流式对话：逐 token 推送，LLM 关闭时推本地规则引擎摘要。"""
    async def gen():
        async for chunk in analysis.chat_stream(c.question.strip()[:500], c.history):
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------- cc-switch 一键导入 ----------

@app.get("/api/llm/ccswitch")
def ccswitch_list():
    """列出本机 cc-switch 里可导入的 provider（只读其 SQLite，不改不删）。"""
    return ccswitch.list_providers()


@app.post("/api/llm/ccswitch/apply")
async def ccswitch_apply(p: dict):
    if not p.get("base_url"):
        raise HTTPException(400, i18n.t("base_url 不能为空", "base_url is required"))
    ccswitch.apply(p)
    name = p.get("name") or p["base_url"]
    await broadcast("settings", i18n.t(f"已从 cc-switch 导入 LLM 配置: {name}",
                                       f"LLM config imported from cc-switch: {name}"))
    return {"ok": True}


# ---------- LLM 端点工具：获取模型列表 + 测活（ccswitch 式） ----------

# 已知「Anthropic 协议兼容子路径」后缀：base URL 命中时额外追加剥离后缀的候选
# （DeepSeek/Kimi/GLM 等把 OpenAI 兼容端点挂在别的路径下的场景）
_COMPAT_SUFFIXES = ("/api/claudecode", "/api/anthropic", "/apps/anthropic",
                    "/api/coding", "/claudecode", "/anthropic", "/coding", "/claude")


def _models_url_candidates(base_url: str) -> list[str]:
    b = base_url.strip().rstrip("/")
    if not b:
        return []
    m = re.search(r"/v\d+$", b)
    # 已以版本段结尾（/v1、智谱 /v4 等）→ OpenAI 惯例是 {base}/models，不能再补 /v1
    cands = [f"{b}/models"] if m else [f"{b}/v1/models"]
    if m and not b.endswith("/v1"):
        cands.append(f"{b}/v1/models")
    for suf in _COMPAT_SUFFIXES:
        if b.endswith(suf):
            root = b[:-len(suf)].rstrip("/")
            cands += [f"{root}/v1/models", f"{root}/models"]
            break
    return list(dict.fromkeys(cands))


class LlmProbeIn(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""


def _llm_headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}"} if api_key.strip() else {}


@app.post("/api/llm/models")
async def llm_models(p: LlmProbeIn):
    """拉取 OpenAI 兼容模型列表：GET {base}/models，候选 URL 按序尝试，404/405 换下一个。"""
    tried, models, last = [], [], i18n.t("Base URL 未填写", "Base URL is required")
    for url in _models_url_candidates(p.base_url)[:3]:
        tried.append(url)
        try:
            async with httpx.AsyncClient(timeout=15) as cli:
                r = await cli.get(url, headers=_llm_headers(p.api_key))
            if r.status_code in (404, 405):
                last = f"HTTP {r.status_code}"
                continue
            r.raise_for_status()
            data = r.json().get("data") or []
            models = sorted({str(m["id"]) for m in data if isinstance(m, dict) and m.get("id")})
            last = None
            break
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
    return {"ok": bool(models), "models": models, "tried": tried, "error": last}


@app.post("/api/llm/test")
async def llm_test(p: LlmProbeIn):
    """测活：对 {base}/chat/completions 发一次真实最小生成请求并计时。
    与诊断叙事走完全相同的路径，通了就代表 AI 叙事可用。
    base 未带版本段时先做与对话链路一致的归一探测（/v1 vs 裸域）。"""
    base_in = p.base_url.strip().rstrip("/")
    if not base_in:
        raise HTTPException(400, i18n.t("Base URL 未填写", "Base URL is required"))
    conf = {"base_url": base_in, "api_key": p.api_key}
    base = await analysis.resolve_base(conf)  # 复用归一：探测 /models JSON 胜出即回写
    headers = _llm_headers(p.api_key)
    url = f"{base}/chat/completions"
    payload = {"model": p.model.strip() or "gpt-4o-mini",
               "messages": [{"role": "user", "content": "ping"}],
               "max_tokens": 1, "stream": False}
    async with httpx.AsyncClient(timeout=30) as cli:
        try:
            await cli.post(url, headers=headers, json=payload)  # 热身：复用连接，去掉首包惩罚
        except Exception:
            pass
        t0 = time.perf_counter()
        try:
            r = await cli.post(url, headers=headers, json=payload)
            ms = int((time.perf_counter() - t0) * 1000)
            if r.status_code >= 400:
                return {"ok": False, "latency_ms": ms, "status": r.status_code,
                        "error": (r.text or "").strip()[:300]}
            reply = ""
            try:
                reply = (r.json()["choices"][0]["message"]["content"] or "").strip()[:200]
            except Exception:
                pass
            return {"ok": True, "latency_ms": ms, "status": r.status_code,
                    "model": p.model.strip(), "reply": reply}
        except httpx.TimeoutException:
            return {"ok": False, "latency_ms": int((time.perf_counter() - t0) * 1000),
                    "error": i18n.t("请求超时（30s）", "Request timed out (30s)")}
        except Exception as e:
            return {"ok": False, "latency_ms": int((time.perf_counter() - t0) * 1000),
                    "error": f"{type(e).__name__}: {e}"}


# ---------- panel auth ----------

class PasswordIn(BaseModel):
    password: str


class ChangePwIn(BaseModel):
    old: str
    new: str


class LoginIn(BaseModel):
    username: str = ""   # 多用户模式必填；legacy 单口令模式忽略
    password: str


@app.get("/api/auth/status")
async def auth_status(request: Request):
    role = auth.session_role(request.cookies.get(auth.COOKIE, ""))
    return {"enabled": auth.enabled(), "authenticated": bool(role), "role": role,
            "multi_user": auth.has_users()}


@app.post("/api/auth/login")
async def auth_login(p: LoginIn, request: Request):
    ip = request.client.host if request.client else "?"
    if auth.too_many_fails(ip):
        raise HTTPException(429, i18n.t("尝试过于频繁，请 1 分钟后再试",
                                        "Too many attempts — try again in 1 minute"))
    ok, role = auth.verify_login(p.username, p.password)
    if not ok:
        auth.record_fail(ip)
        raise HTTPException(401, i18n.t("口令错误", "Incorrect password"))
    resp = JSONResponse({"ok": True, "role": role})
    resp.set_cookie(auth.COOKIE, auth.make_session(role), max_age=auth.TTL,
                    httponly=True, samesite="lax", path="/")
    return resp


# ---------- 用户管理（admin）----------

class UserIn(BaseModel):
    username: str
    password: str
    role: str = "observer"


class RoleIn(BaseModel):
    role: str


def _require_admin(request: Request):
    if not auth.enabled():
        return  # 访问控制未开 = 单人本机模式，无需角色
    if auth.session_role(request.cookies.get(auth.COOKIE, "")) != "admin":
        raise HTTPException(403, i18n.t("需要管理员权限", "Admin privileges required"))


@app.get("/api/auth/users")
async def users_list(request: Request):
    _require_admin(request)
    return {"users": auth.list_users(), "legacy_password": not auth.has_users()}


@app.post("/api/auth/users")
async def users_add(u: UserIn, request: Request):
    _require_admin(request)
    if len(u.username.strip()) < 2:
        raise HTTPException(400, i18n.t("用户名至少 2 位", "Username must be at least 2 characters"))
    if len(u.password) < 4:
        raise HTTPException(400, i18n.t("口令至少 4 位", "Password must be at least 4 characters"))
    try:
        uid = auth.add_user(u.username.strip(), u.password, u.role)
    except ValueError as e:
        raise HTTPException(400, str(e))
    auth.bump_session_epoch()
    await broadcast("settings", i18n.t(f"已添加用户 {u.username}（{u.role}）",
                                       f"User added: {u.username} ({u.role})"))
    return {"ok": True, "id": uid}


@app.post("/api/auth/users/{uid}/role")
async def users_set_role(uid: int, r: RoleIn, request: Request):
    _require_admin(request)
    try:
        auth.set_role(uid, r.role)
    except ValueError as e:
        raise HTTPException(400, str(e))
    await broadcast("settings", i18n.t(f"用户 #{uid} 角色已改为 {r.role}",
                                       f"Role of user #{uid} changed to {r.role}"))
    return {"ok": True}


@app.delete("/api/auth/users/{uid}")
async def users_del(uid: int, request: Request):
    _require_admin(request)
    n_admin = len([u for u in auth.list_users() if u["role"] == "admin"])
    row = db.query_one("SELECT username, role FROM users WHERE id=?", (uid,))
    if not row:
        raise HTTPException(404, i18n.t("用户不存在", "User not found"))
    if row["role"] == "admin" and n_admin <= 1:
        raise HTTPException(400, i18n.t("至少保留一名管理员", "At least one admin must remain"))
    auth.del_user(uid)
    await broadcast("settings", i18n.t(f"已删除用户 {row['username']}",
                                       f"User deleted: {row['username']}"))
    return {"ok": True}


@app.post("/api/auth/self/password")
async def self_change_password(p: ChangePwIn, request: Request):
    """已登录用户改自己的口令（observer 也可用——中间件放行 /api/auth/self）。"""
    if not auth.enabled():
        raise HTTPException(400, i18n.t("访问控制未开启", "Access control is not enabled"))
    username = request.headers.get("x-hw-user", "")
    row = db.query_one("SELECT id FROM users WHERE username=?", (username,))
    if not row or not auth.verify_login(username, p.old)[0]:
        raise HTTPException(401, i18n.t("旧口令错误", "Old password is incorrect"))
    if len(p.new) < 4:
        raise HTTPException(400, i18n.t("新口令至少 4 位", "New password must be at least 4 characters"))
    auth.set_user_password(row["id"], p.new)
    await broadcast("settings", i18n.t(f"用户 {username} 口令已修改",
                                       f"Password changed for user {username}"))
    return {"ok": True}


@app.post("/api/auth/logout")
async def auth_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


@app.post("/api/auth/enable")
async def auth_enable(p: PasswordIn):
    if auth.enabled():
        raise HTTPException(400, i18n.t("访问控制已开启", "Access control is already enabled"))
    if len(p.password) < 4:
        raise HTTPException(400, i18n.t("口令至少 4 位", "Password must be at least 4 characters"))
    auth.set_password(p.password)
    auth.bump_session_epoch()  # 重新启用 = 全部旧会话作废，需用新口令登录
    db.execute("INSERT INTO settings(key,value) VALUES('panel_auth','on') "
               "ON CONFLICT(key) DO UPDATE SET value='on'")
    await broadcast("settings", i18n.t("面板访问控制已开启", "Panel access control enabled"))
    return {"ok": True}


@app.post("/api/auth/disable")
async def auth_disable(p: PasswordIn, request: Request):
    if not auth.enabled():
        raise HTTPException(400, i18n.t("访问控制未开启", "Access control is not enabled"))
    if not auth.verify_password(p.password):
        raise HTTPException(401, i18n.t("口令错误", "Incorrect password"))
    db.execute("UPDATE settings SET value='off' WHERE key='panel_auth'")
    auth.bump_session_epoch()
    await broadcast("settings", i18n.t("面板访问控制已关闭", "Panel access control disabled"))
    return {"ok": True}


@app.post("/api/auth/change")
async def auth_change(c: ChangePwIn):
    if not auth.enabled():
        raise HTTPException(400, i18n.t("访问控制未开启", "Access control is not enabled"))
    if not auth.verify_password(c.old):
        raise HTTPException(401, i18n.t("旧口令错误", "Old password is incorrect"))
    if len(c.new) < 4:
        raise HTTPException(400, i18n.t("新口令至少 4 位", "New password must be at least 4 characters"))
    auth.set_password(c.new)
    auth.bump_session_epoch()  # 换口令后所有旧会话（含当前浏览器）失效，需重新登录
    await broadcast("settings", i18n.t("面板口令已更换", "Panel password changed"))
    return {"ok": True}


# ---------- webhook notifications（多渠道实现见 notify.py）----------


# ---------- web terminal (WebSocket) ----------

@app.websocket("/ws/terminal/{hid}")
async def ws_terminal(ws: WebSocket, hid: int):
    await ws.accept()
    if auth.enabled() and not auth.verify_session(ws.cookies.get(auth.COOKIE, "")):
        await ws.send_text(i18n.t("面板未登录，终端连接被拒绝\r\n",
                                  "Panel not signed in — terminal connection refused\r\n"))
        await ws.close()
        return
    h = db.query_one("SELECT * FROM hosts WHERE id=?", (hid,))
    if not h:
        await ws.send_text(i18n.t("主机不存在\r\n", "Host not found\r\n"))
        await ws.close()
        return
    host = dict(h)
    if host.get("mock"):
        shell = terminal.MockShell(host)
        await ws.send_text(shell.banner + shell.prompt())
        buf = ""
        try:
            while True:
                buf += await ws.receive_text()
                while "\r" in buf or "\n" in buf:
                    line, _, buf = (buf.replace("\n", "\r").partition("\r")
                                    if "\r" in buf else buf.partition("\n"))
                    out = shell.feed(line)
                    await ws.send_text(out + ("\r\n" if out else "") + "\r\n" + shell.prompt())
        except WebSocketDisconnect:
            return
    else:
        if terminal.asyncssh is None:
            await ws.send_text(i18n.t("asyncssh 未安装，无法连接真实主机\r\n",
                                      "asyncssh not installed — cannot connect to real hosts\r\n"))
            await ws.close()
            return
        try:
            await terminal.ssh_session(ws, host)
        except Exception as e:
            try:
                await ws.send_text(f"\r\n{i18n.t(f'SSH 连接失败: {type(e).__name__}: {e}', f'SSH connection failed: {type(e).__name__}: {e}')}\r\n")
                await ws.close()
            except Exception:
                pass


@app.post("/api/findings/{fid}/ack")
async def ack_finding(fid: int):
    """人工确认告警：已知悉，停止 crit 周期重发（恢复后状态照常流转）。"""
    f = db.query_one("SELECT * FROM findings WHERE id=?", (fid,))
    if not f:
        raise HTTPException(404, i18n.t("finding 不存在", "finding not found"))
    if f["acked_at"]:
        raise HTTPException(400, i18n.t("该发现已确认过", "This finding was already acknowledged"))
    db.execute("UPDATE findings SET acked_at=? WHERE id=?", (db.now(), fid))
    await broadcast("proposal", i18n.t(f"发现已确认: {f['title']}",
                                       f"Finding acknowledged: {f['title']}"), {"finding_id": fid})
    return {"ok": True}


class SilenceIn(BaseModel):
    minutes: int  # >0 = 静默 N 分钟，0 = 取消静默


@app.post("/api/hosts/{hid}/silence")
async def silence_host(hid: int, s: SilenceIn):
    h = db.query_one("SELECT * FROM hosts WHERE id=?", (hid,))
    if not h:
        raise HTTPException(404, i18n.t("主机不存在", "Host not found"))
    until = db.now() + s.minutes * 60 if s.minutes > 0 else 0
    db.execute("UPDATE hosts SET silenced_until=? WHERE id=?", (until, hid))
    await broadcast("host",
                    i18n.t(f"{h['name']} 已静默 {s.minutes} 分钟", f"{h['name']} silenced for {s.minutes} min")
                    if s.minutes > 0
                    else i18n.t(f"{h['name']} 已取消静默", f"{h['name']} unsilenced"),
                    {"host_id": hid})
    return {"ok": True, "silenced_until": until}


@app.post("/api/hosts/{hid}/trust-key")
async def trust_host_key(hid: int):
    """人工确认后重置主机指纹（下次连接重新 TOFU 记录）。"""
    h = db.query_one("SELECT * FROM hosts WHERE id=?", (hid,))
    if not h:
        raise HTTPException(404, i18n.t("主机不存在", "Host not found"))
    db.execute("UPDATE hosts SET host_key_fp='', last_error='' WHERE id=?", (hid,))
    await broadcast("host", i18n.t(f"{h['name']} 主机指纹已重置，下次连接将重新记录",
                                   f"Host fingerprint of {h['name']} reset — will be re-recorded on next connect"),
                    {"host_id": hid})
    return {"ok": True}


# ---------- 端口暴露面侦查 ----------

class PortBaselineIn(BaseModel):
    ports: list[int]  # 加入基线的端口列表（发现卡「加入基线」）


@app.get("/api/hosts/{hid}/ports")
async def host_ports(hid: int):
    """当前 LISTEN 清单（last_extras.ports）+ 基线 + 基线外端口，供详情页「端口与暴露」tab。"""
    h = db.query_one("SELECT * FROM hosts WHERE id=?", (hid,))
    if not h:
        raise HTTPException(404, i18n.t("主机不存在", "Host not found"))
    extras = db.uj(h["last_extras"], {}) or {}
    current = extras.get("ports") or []
    baseline = sorted(ports.get_baseline(hid))
    current_ports = {int(p.get("port") or 0) for p in current if isinstance(p, dict)}
    return {"ports": current, "baseline": baseline,
            "new_ports": sorted(current_ports - set(baseline))}


@app.post("/api/hosts/{hid}/ports/baseline")
async def ports_baseline_add(hid: int, b: PortBaselineIn, request: Request):
    _require_admin(request)
    cur = ports.get_baseline(hid)
    ports.save_baseline(hid, cur | {int(x) for x in b.ports if 0 < int(x) <= 65535})
    # 端口进基线后，对应 port_new 发现自动解决
    for port in b.ports:
        db.execute("UPDATE findings SET status='resolved', resolved_at=? WHERE host_id=? AND type='port_new' "
                   "AND status IN ('open','analyzed') AND evidence LIKE ?",
                   (db.now(), hid, f'%{int(port)}%'))
    await broadcast("host", i18n.t(f"主机 #{hid} 端口基线已更新（+{len(b.ports)}）",
                                   f"Port baseline of host #{hid} updated (+{len(b.ports)})"),
                    {"host_id": hid})
    return {"ok": True}


@app.delete("/api/hosts/{hid}/ports/baseline")
async def ports_baseline_reset(hid: int, request: Request):
    """重置基线：下一轮采集重新学习（主机业务大改后用）。"""
    _require_admin(request)
    db.execute("DELETE FROM settings WHERE key IN (?, ?)",
               (f"{ports.BASELINE_PREFIX}{hid}", f"{ports.BASELINE_PREFIX}{hid}:seen"))
    await broadcast("host", i18n.t(f"主机 #{hid} 端口基线已重置，下轮巡检重新学习",
                                   f"Port baseline of host #{hid} reset — relearned in the next inspection round"),
                    {"host_id": hid})
    return {"ok": True}


# ---------- outbound agent endpoints (beszel-style) ----------

@app.post("/api/hosts/{hid}/agent-token")
async def gen_agent_token(hid: int):
    if not db.query_one("SELECT id FROM hosts WHERE id=?", (hid,)):
        raise HTTPException(404, i18n.t("主机不存在", "Host not found"))
    token = agent.new_token()
    db.execute("UPDATE hosts SET agent_token=? WHERE id=?", (token, hid))
    await broadcast("host", i18n.t(f"已为 #{hid} 生成出站 Agent Token",
                                   f"Outbound agent token generated for host #{hid}"), {"host_id": hid})
    return {"token": token}


@app.get("/api/agent/script", response_class=PlainTextResponse)
def agent_script():
    return agent.AGENT_SCRIPT


@app.post("/api/agent/push")
async def agent_push(authorization: str = ""):
    token = agent.parse_secret_token(authorization)
    h = agent.host_by_token(token)
    if not h:
        raise HTTPException(401, i18n.t("无效 agent token", "Invalid agent token"))
    # body parsed lazily to give a clean 401 before touching it
    return {"ok": True, "host": h["name"]}


@app.post("/api/agent/push/body")
async def agent_push_body(request: Request):
    """agent 指标推送。两种鉴权：
    - Go 二进制：X-HW-Token + X-HW-Signature(HMAC-SHA256) + X-HW-Timestamp 防重放
    - sh 脚本：token 在 body 中（最简 curl 演示）
    extras（进程/失败服务/证书）随包上报，入库并驱动规则引擎产生发现。"""
    raw = await request.body()
    try:
        data = json.loads(raw or b"{}") or {}
    except json.JSONDecodeError:
        raise HTTPException(400, "bad json")
    token = request.headers.get("x-hw-token") or data.get("token", "")
    h = agent.host_by_token(token)
    if not h:
        raise HTTPException(401, i18n.t("无效 agent token", "Invalid agent token"))
    sig = request.headers.get("x-hw-signature")
    if sig:  # 签名通道：验签 + 时间戳防重放
        if not agent.verify_signature(token, raw, request.headers.get("x-hw-timestamp", ""), sig):
            raise HTTPException(401, i18n.t("签名无效或时间戳过期",
                                            "Invalid signature or expired timestamp"))
    # NaN/inf 防御：一个坏点就能让阈值判断全部失效并污染图表
    def _num(k: str) -> float:
        v = float(data.get(k, 0) or 0)
        return v if math.isfinite(v) else 0.0
    m = {k: _num(k) for k in ("cpu", "mem", "disk", "load1", "net_in", "net_out", "swap")}
    extras = data.get("extra") or {}
    # 磁盘 IO 速率与温度随 extras 上报，写入 metrics（图表/规则引擎用）；NaN/负值防御同上
    def _xnum(k: str) -> float:
        try:
            v = float(extras.get(k) or 0)
        except (TypeError, ValueError):
            v = 0.0
        return v if math.isfinite(v) and v > 0 else 0.0
    io_read, io_write, temp = _xnum("disk_io_read"), _xnum("disk_io_write"), _xnum("temp_c")
    m.update(io_read=io_read, io_write=io_write, temp_c=temp)  # 规则引擎要看到 io/temp 字段
    db.execute(
        "INSERT INTO metrics(host_id,ts,cpu,mem,disk,net_in,net_out,load1,io_read,io_write,temp_c,swap) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (h["id"], db.now(), m["cpu"], m["mem"], m["disk"], m["net_in"], m["net_out"], m["load1"],
         io_read, io_write, temp, m["swap"]))
    db.execute("UPDATE hosts SET last_ok_ts=?, last_error='' WHERE id=?", (db.now(), h["id"]))
    if extras:
        db.execute("UPDATE hosts SET last_extras=? WHERE id=?", (db.j(extras), h["id"]))
    await scheduler.process_findings(h, m, extras)
    await broadcast("host", i18n.t(f"{h['name']} agent 指标已上报",
                                   f"Agent metrics reported for {h['name']}"), {"host_id": h["id"]})
    return {"ok": True}


# ---------- public status page (token-gated, read-only) ----------

@app.get("/api/status/token")
def status_token_info():
    tok = (db.query_one("SELECT value FROM settings WHERE key='status_token'") or {}).get("value") or ""
    return {"enabled": bool(tok), "token": tok}


@app.post("/api/status/token")
async def status_token_gen():
    tok = agent.new_token()  # hw_ 前缀随机 token，复用出站 agent 的生成器
    db.execute("INSERT INTO settings(key,value) VALUES('status_token',?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (tok,))
    await broadcast("settings", i18n.t("公开状态页已开启（新分享链接）",
                                       "Public status page enabled (new share link)"))
    return {"enabled": True, "token": tok}


@app.delete("/api/status/token")
async def status_token_revoke():
    db.execute("DELETE FROM settings WHERE key='status_token'")
    await broadcast("settings", i18n.t("公开状态页已关闭（链接立即失效）",
                                       "Public status page disabled (link revoked immediately)"))
    return {"enabled": False}


@app.get("/status/{token}", response_class=HTMLResponse)
def public_status(token: str):
    """带 token 的只读状态页：任何拿到链接的人可看健康概览，看不到凭据/终端/证据链。
    不在 /api 下，天然绕过面板会话门；token 撤销即失效。"""
    expected = (db.query_one("SELECT value FROM settings WHERE key='status_token'") or {}).get("value") or ""
    if not expected or token != expected:
        raise HTTPException(404, i18n.t("状态页不存在或已关闭", "Status page not found or disabled"))
    return reports.render_status_page(analysis.fleet_snapshot())


# ---------- 状态徽章 SVG（外嵌 README/看板；门禁复用 status_token，撤销即失效） ----------

def _badge_response(svg: str) -> Response:
    # 短缓存：外部 CDN 可缓存 30s，状态翻转不至长期陈旧
    return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=30"})


@app.get("/badge/{token}.svg")
def badge_fleet(token: str):
    if not badge.token_ok(token):
        raise HTTPException(404, i18n.t("徽章不存在或已关闭", "Badge not found or disabled"))
    return _badge_response(badge.fleet_badge())


@app.get("/badge/{token}/{probe_id}.svg")
def badge_probe(token: str, probe_id: int):
    if not badge.token_ok(token):
        raise HTTPException(404, i18n.t("徽章不存在或已关闭", "Badge not found or disabled"))
    p = db.query_one("SELECT id, name, up FROM probes WHERE id=?", (probe_id,))
    if not p:
        raise HTTPException(404, i18n.t("拨测目标不存在", "Probe not found"))
    return _badge_response(badge.probe_badge(p))


# ---------- SSH 指纹人工确认（tofu_confirm=on 时用） ----------

@app.post("/api/hosts/{hid}/trust")
async def host_trust(hid: int, body: dict):
    """action=confirm 信任当前已记录指纹；adopt 采纳 pending 新指纹；reject 丢弃候选。"""
    row = db.query_one("SELECT id, host_key_fp, host_key_pending FROM hosts WHERE id=?", (hid,))
    if not row:
        raise HTTPException(404, i18n.t("主机不存在", "Host not found"))
    action = (body or {}).get("action", "confirm")
    if action == "adopt":
        if not row["host_key_pending"]:
            raise HTTPException(400, i18n.t("没有待采纳的候选指纹", "No candidate fingerprint pending"))
        db.execute("UPDATE hosts SET host_key_fp=?, host_key_pending='', trusted=1 WHERE id=?",
                   (row["host_key_pending"], hid))
        msg = i18n.t("已采纳主机新指纹", "Adopted new host fingerprint")
    elif action == "reject":
        db.execute("UPDATE hosts SET host_key_pending='' WHERE id=?", (hid,))
        msg = i18n.t("已拒绝候选指纹", "Rejected candidate fingerprint")
    else:
        if not row["host_key_fp"]:
            raise HTTPException(400, i18n.t("尚未记录指纹（主机未连过）", "No fingerprint recorded yet"))
        db.execute("UPDATE hosts SET trusted=1 WHERE id=?", (hid,))
        msg = i18n.t("已信任主机指纹", "Host fingerprint trusted")
    await broadcast("host", f"{msg}: {hid}")
    return {"ok": True}


# ---------- 公开访问审计（admin only：会话门自动套用 /api 前缀） ----------

@app.get("/api/guard/audit")
def guard_audit():
    """最近 500 条公开端点访问（徽章/状态页/push），重启即清。"""
    return guard.recent()


# ---------- local MCP server (streamable HTTP, read-only) ----------

@app.post("/api/mcp")
async def mcp_endpoint(body: dict):
    return mcp_server.handle(body)


@app.get("/api/mcp/tools")
def mcp_tools():
    return mcp_server.TOOLS


def run():
    import uvicorn
    # 生产容器内监听 0.0.0.0（HW_LISTEN 覆盖时生效）；本机默认仍只听 127.0.0.1
    host = os.environ.get("HW_LISTEN", "127.0.0.1")
    port = int(os.environ.get("HW_PORT") or 8800)
    uvicorn.run(app, host=host, port=port, log_level="warning")


# ---------- 生产模式：单进程托管前端构建产物（frontend dist 同仓部署）----------
# 约定目录：backend/static（构建后拷入）或仓库根 frontend/dist。存在即启用；
# /api、/ws、/status 路由优先，其余 GET 落 SPA 的 index.html（前端路由接管）。
_DIST_DIRS = [
    pathlib.Path(__file__).resolve().parent.parent / "static",
    pathlib.Path(__file__).resolve().parent.parent.parent / "frontend" / "dist",
]
_dist = next((d for d in _DIST_DIRS if (d / "index.html").exists()), None)
if _dist is not None:
    from fastapi.staticfiles import StaticFiles

    assets = _dist / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{spa_path:path}", include_in_schema=False)
    async def spa_fallback(spa_path: str):
        # /api 与 /status 前缀永不落入 SPA（交给 REST/状态页路由处理）
        if spa_path.startswith(("api/", "status/")) or spa_path in ("api", "status"):
            raise HTTPException(404, "Not Found")
        candidate = (_dist / spa_path).resolve()
        if spa_path and candidate.is_file() and candidate.is_relative_to(_dist.resolve()):
            ct = {".svg": "image/svg+xml", ".webmanifest": "application/manifest+json",
                  ".js": "application/javascript", ".png": "image/png", ".ico": "image/x-icon",
                  ".html": "text/html; charset=utf-8"}.get(candidate.suffix, "application/octet-stream")
            return HTMLResponse(candidate.read_bytes(), media_type=ct)
        return HTMLResponse((_dist / "index.html").read_bytes(), media_type="text/html; charset=utf-8")
