"""状态徽章 SVG（对标 Uptime Kuma / Gatus 的外嵌 badge）：
- 门禁复用公开状态页 status_token，不新增凭据面；/badge 不在 /api 前缀下，天然绕过会话门
- /badge/{token}/{probe_id}.svg = 单目标（当前状态 + 24h 可用率）；/badge/{token}.svg = 全 fleet 概览
- 纯函数生成 shields 风格扁平 SVG：外部嵌入（README/工单/看板）无面板主题依赖，文本 XML 转义防注入
"""
from . import db

UPTIME_WINDOW = 86400  # 24h 可用率窗口
_COLORS = {"up": "#16a34a", "down": "#dc2626", "partial": "#d97706", "none": "#94a3b8"}
_LABEL_BG = "#475569"  # 左段石板灰（shields 风格）


def token_ok(token: str) -> bool:
    expected = (db.query_one("SELECT value FROM settings WHERE key='status_token'") or {}).get("value") or ""
    return bool(expected) and token == expected


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _tw(s: str) -> float:
    """字符宽估算：CJK 全宽 ≈11px，其余 ≈6.2px（11px 无衬线近似），决定两段各自宽度"""
    return sum(11.0 if ord(ch) > 0x2E7F else 6.2 for ch in s)


def _svg(left: str, right: str, color: str) -> str:
    lw, rw = round(_tw(left) + 16), round(_tw(right) + 16)
    total = lw + rw
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="20" role="img" '
        f'aria-label="{_esc(left)}: {_esc(right)}">'
        '<linearGradient id="g" x2="0" y2="100%">'
        '<stop offset="0" stop-color="#bbb" stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/>'
        '</linearGradient>'
        f'<clipPath id="r"><rect width="{total}" height="20" rx="3" fill="#fff"/></clipPath>'
        f'<g clip-path="url(#r)">'
        f'<rect width="{lw}" height="20" fill="{_LABEL_BG}"/>'
        f'<rect x="{lw}" width="{rw}" height="20" fill="{color}"/>'
        f'<rect width="{total}" height="20" fill="url(#g)"/>'
        '</g>'
        f'<g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">'
        f'<text x="{lw // 2}" y="14">{_esc(left)}</text>'
        f'<text x="{lw + rw // 2}" y="14">{_esc(right)}</text>'
        '</g></svg>'
    )


def uptime24(probe_id: int) -> float | None:
    """24h 可用率（%）：probe_log 窗口内 up 占比；无样本返回 None（徽章只显示状态）"""
    row = db.query_one("SELECT COUNT(*) c, COALESCE(SUM(up),0) s FROM probe_log "
                       "WHERE probe_id=? AND ts>=?", (probe_id, db.now() - UPTIME_WINDOW))
    if not row or not row["c"]:
        return None
    return row["s"] / row["c"] * 100


def _fmt_pct(p: float | None) -> str:
    if p is None:
        return ""
    return "100%" if p >= 99.995 else f"{p:.1f}%"


def probe_badge(probe: dict) -> str:
    up = probe.get("up") == 1
    right = ("UP " if up else "DOWN ") + _fmt_pct(uptime24(probe["id"]))
    return _svg(probe.get("name") or "probe", right.strip(), _COLORS["up" if up else "down"])


def fleet_badge() -> str:
    rows = db.query("SELECT up FROM probes")
    total = len(rows)
    if total == 0:
        return _svg("hermes-watch", "no probes", _COLORS["none"])
    ups = sum(1 for r in rows if r["up"] == 1)
    color = _COLORS["up"] if ups == total else (_COLORS["down"] if ups == 0 else _COLORS["partial"])
    return _svg("hermes-watch", f"{ups}/{total} UP", color)
