"""Local MCP (Model Context Protocol) server over streamable HTTP.

Lets Claude Desktop / Cursor / any MCP client query the fleet's live inspection
data with local tool calls — "AI not locked in the cloud", Netdata-style. The
toolset is strictly read-only.
"""
from . import analysis, badge, db

MCP_PROTOCOL_VERSION = "2025-06-18"

TOOLS = [
    {
        "name": "fleet_status",
        "description": "获取整个 fleet 的实时状态：每台主机的健康分、CPU/内存/磁盘使用率、待处理发现数",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_findings",
        "description": "列出当前待处理的发现（含严重级别、主机、详情）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "severity": {"type": "string", "enum": ["crit", "warn", "info"],
                             "description": "按严重级别过滤（可选）"},
            },
        },
    },
    {
        "name": "get_finding",
        "description": "获取单条发现的完整信息：证据链、Agent 诊断根因卡、关联修复提案",
        "inputSchema": {
            "type": "object",
            "properties": {"finding_id": {"type": "integer", "description": "发现 ID"}},
            "required": ["finding_id"],
        },
    },
    {
        "name": "host_history",
        "description": "获取某台主机最近的指标序列（cpu/mem/disk/net/load，1 分钟粒度）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "host_id": {"type": "integer", "description": "主机 ID"},
                "range_min": {"type": "integer", "description": "回看分钟数，默认 240", "default": 240},
            },
            "required": ["host_id"],
        },
    },
    {
        "name": "host_extras",
        "description": "获取某台主机的扩展采集面：容器清单（docker ps）、LISTEN 端口、失败服务、最近登录、证书剩余天数",
        "inputSchema": {
            "type": "object",
            "properties": {"host_id": {"type": "integer", "description": "主机 ID"}},
            "required": ["host_id"],
        },
    },
    {
        "name": "list_probes",
        "description": "列出服务拨测目标：URL/TCP/DNS/Push 四类，含当前状态、最近延迟与 24h 可用率",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "probe_history",
        "description": "获取某条拨测目标最近的心跳记录（时间/状态/延迟/错误）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "probe_id": {"type": "integer", "description": "拨测 ID"},
                "limit": {"type": "integer", "description": "条数上限，默认 30", "default": 30},
            },
            "required": ["probe_id"],
        },
    },
    {
        "name": "recent_events",
        "description": "巡检事件流（发现/诊断/提案/报告/拨测/错误）",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 20}},
        },
    },
]


def _text(content: str) -> dict:
    return {"content": [{"type": "text", "text": content}]}


def _hosts() -> list[dict]:
    return db.query("SELECT id,name,hostname,group_name FROM hosts ORDER BY id")


def call_tool(name: str, args: dict) -> dict:
    if name == "fleet_status":
        snap = analysis.fleet_snapshot()
        lines = [f"Fleet 状态（{snap['generated_at']:.0f}）:"]
        for h in snap["hosts"]:
            l = h.get("latest") or {}
            lines.append(
                f"- [{h['id']}] {h['name']} ({h['hostname']}, 组 {h['group']}): "
                f"健康 {h['score']}/{h['status']}, CPU {l.get('cpu', 0):.0f}%, "
                f"内存 {l.get('mem', 0):.0f}%, 磁盘 {l.get('disk', 0):.0f}%, "
                f"发现 {h['open_findings']} 条")
        return _text("\n".join(lines))

    if name == "list_findings":
        sev = args.get("severity")
        rows = analysis.top_findings(50)
        if sev:
            rows = [r for r in rows if r["severity"] == sev]
        if not rows:
            return _text("当前没有待处理发现。")
        return _text("\n".join(
            f"- [#{r['id']}] [{r['severity']}] {r['host_name']}: {r['title']} — {r['detail']}"
            for r in rows))

    if name == "get_finding":
        fid = int(args.get("finding_id", 0))
        row = db.query_one(
            "SELECT f.*, h.name AS host_name FROM findings f JOIN hosts h ON h.id=f.host_id WHERE f.id=?",
            (fid,))
        if not row:
            return _text(f"发现 #{fid} 不存在。")
        row["evidence"] = db.uj(row["evidence"], {})
        row["card"] = db.uj(row["card"], None)
        prop = db.query_one("SELECT * FROM proposals WHERE finding_id=? ORDER BY ts DESC LIMIT 1", (fid,))
        parts = [f"[#{row['id']}] [{row['severity']}] {row['host_name']}: {row['title']}",
                 f"状态: {row['status']}", f"详情: {row['detail']}",
                 f"证据: {db.j(row['evidence'])}"]
        if row["card"]:
            c = row["card"]
            parts += [f"根因: {c.get('root_cause')}",
                      f"诊断链: {' → '.join(c.get('chain', []))}",
                      f"置信度: {c.get('confidence')}"]
            for s in c.get("steps", []):
                parts.append(f"  步骤[{s['label']}] $ {s['command']}\n{s['output']}")
        if prop:
            parts.append(f"修复提案 #{prop['id']} ({prop['status']}): {prop['title']}"
                         f" — {prop['command']}")
        return _text("\n".join(parts))

    if name == "host_history":
        hid = int(args.get("host_id", 0))
        minutes = int(args.get("range_min", 240))
        rows = db.query(
            "SELECT ts,cpu,mem,disk,net_in,net_out,load1 FROM metrics "
            "WHERE host_id=? AND ts>? ORDER BY ts",
            (hid, db.now() - minutes * 60))
        if not rows:
            return _text(f"主机 #{hid} 无指标数据（或 ID 不存在）。")
        step = max(1, len(rows) // 60)  # cap output size
        sampled = rows[::step]
        return _text(f"主机 #{hid} 最近 {minutes} 分钟 {len(rows)} 点（采样 {len(sampled)}）:\n" + "\n".join(
            f"{r['ts']:.0f} cpu={r['cpu']} mem={r['mem']} disk={r['disk']} "
            f"net_in={r['net_in']:.0f} net_out={r['net_out']:.0f} load={r['load1']}"
            for r in sampled))

    if name == "host_extras":
        hid = int(args.get("host_id", 0))
        h = db.query_one("SELECT id,name FROM hosts WHERE id=?", (hid,))
        if not h:
            return _text(f"主机 #{hid} 不存在。")
        ex = db.uj(db.query_one("SELECT last_extras FROM hosts WHERE id=?", (hid,))["last_extras"], {}) or {}
        parts = [f"主机 #{hid} {h['name']} 扩展采集面:"]
        cons = ex.get("docker_containers") or []
        if cons:
            parts.append(f"容器（{len(cons)}）:")
            for c in cons[:20]:
                parts.append(f"  - {c.get('name')} [{c.get('state')}] {c.get('status')} ({c.get('image')})")
        else:
            parts.append("容器: 无（未装 docker 或未上报）")
        ports = ex.get("ports") or []
        parts.append(f"LISTEN 端口（{len(ports)}）: " + ", ".join(
            f"{x.get('port')}{('/' + x['proc']) if x.get('proc') else ''}" for x in ports[:20]) if ports else "LISTEN 端口: 无")
        failed = ex.get("failed_services") or []
        parts.append(("失败服务: " + ", ".join(failed)) if failed else "失败服务: 无")
        logins = ex.get("logins") or []
        if logins:
            parts.append("最近登录: " + "; ".join(
                f"{x.get('user')}@{x.get('ip')} {x.get('when')}" for x in logins[:6]))
        if ex.get("cert_days_left") is not None:
            parts.append(f"证书剩余: {ex['cert_days_left']} 天")
        if ex.get("top_proc"):
            parts.append(f"最耗内存进程: {ex['top_proc']}")
        return _text("\n".join(parts))

    if name == "list_probes":
        rows = db.query("SELECT * FROM probes ORDER BY id")
        if not rows:
            return _text("没有拨测目标。")
        lines = [f"拨测目标（{len(rows)}）:"]
        for p in rows:
            up = "UP" if p["up"] == 1 else "DOWN"
            pct = badge.uptime24(p["id"])
            uptime = f", 24h {pct:.1f}%" if pct is not None else ""
            lat = f"{round(p['last_latency'])}ms" if p["last_latency"] is not None else "—"
            lines.append(f"- [#{p['id']}] {p['name']} ({p['kind'].upper()} {p['target']}): "
                         f"{up}, 最近 {lat}{uptime}")
        return _text("\n".join(lines))

    if name == "probe_history":
        pid = int(args.get("probe_id", 0))
        limit = min(100, int(args.get("limit", 30)))
        p = db.query_one("SELECT id,name,kind,target FROM probes WHERE id=?", (pid,))
        if not p:
            return _text(f"拨测 #{pid} 不存在。")
        rows = db.query("SELECT ts,up,latency,error FROM probe_log WHERE probe_id=? ORDER BY ts DESC LIMIT ?",
                        (pid, limit))
        if not rows:
            return _text(f"拨测 #{pid}（{p['name']}）暂无心跳记录。")
        return _text(f"拨测 #{pid}（{p['name']}, {p['kind'].upper()} {p['target']}）最近 {len(rows)} 次心跳:\n" + "\n".join(
            f"{r['ts']:.0f} {'UP' if r['up'] else 'DOWN'}" +
            (f" {round(r['latency'])}ms" if r["latency"] is not None else "") +
            (f" — {r['error']}" if r["error"] else "")
            for r in rows))

    if name == "recent_events":
        limit = min(100, int(args.get("limit", 20)))
        rows = db.query(
            "SELECT e.*, h.name AS host_name FROM events e LEFT JOIN hosts h ON h.id=e.host_id "
            "ORDER BY e.ts DESC LIMIT ?", (limit,))
        return _text("\n".join(
            f"{r['ts']:.0f} [{r['kind']}] {r['host_name'] or '-'}: {r['message']}" for r in rows))

    return _text(f"未知工具: {name}")


def handle(body: dict) -> dict:
    """Minimal JSON-RPC dispatch for the streamable-HTTP MCP transport."""
    method = body.get("method", "")
    mid = body.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "hermes-watch", "version": "0.4.0"}}}
    if method == "notifications/initialized":
        return {"jsonrpc": "2.0", "result": {}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        result = call_tool(body["params"]["name"], body["params"].get("arguments") or {})
        return {"jsonrpc": "2.0", "id": mid, "result": result}
    if method.startswith("resources/") or method.startswith("prompts/"):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "not implemented"}}
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": f"unknown method {method}"}}
