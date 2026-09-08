"""Local MCP (Model Context Protocol) server over streamable HTTP.

Lets Claude Desktop / Cursor / any MCP client query the fleet's live inspection
data with local tool calls — "AI not locked in the cloud", Netdata-style. The
toolset is strictly read-only.
"""
from . import analysis, db

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
        "name": "recent_events",
        "description": "巡检事件流（发现/诊断/提案/报告/错误）",
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
            "serverInfo": {"name": "hermes-watch", "version": "0.2.0"}}}
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
