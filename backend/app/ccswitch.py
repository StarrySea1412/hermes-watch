"""一键导入 cc-switch 供应商配置（本机 ~/.cc-switch/cc-switch.db，只读）。

cc-switch 用 SQLite 存多渠道 provider，三种配置形态都能映射成 Hermes Watch
的 ai_provider（base_url / model / api_key）：
- codex 类：config TOML 的 base_url + model，auth.OPENAI_API_KEY 作 key
- claude 类：env.ANTHROPIC_BASE_URL + ANTHROPIC_MODEL，ANTHROPIC_AUTH_TOKEN 作 key
- 其他（gemini/grokbuild）：尽力解析，解析不出端点的跳过

本模块只读 cc-switch 的库，不写不删；导入是否生效由用户在设置页确认。
"""
import json
import pathlib
import re

from . import db

CC_SWITCH_DB = pathlib.Path.home() / ".cc-switch" / "cc-switch.db"


def _toml_field(text: str, key: str) -> str:
    """从 codex 的 config TOML 里取顶层标量字段（够用即可，不引 toml 依赖）。"""
    m = re.search(rf'^\s*{re.escape(key)}\s*=\s*"([^"]*)"', text or "", re.M)
    return m.group(1) if m else ""


def _parse_provider(app_type: str, name: str, cfg: dict, website: str) -> dict | None:
    """把一份 cc-switch provider 配置解析成 {name, base_url, model, api_key, source}。"""
    base_url = model = api_key = ""
    if app_type == "codex":
        text = cfg.get("config") or ""
        base_url = _toml_field(text, "base_url") or website or ""
        model = _toml_field(text, "model") or ""
        api_key = (cfg.get("auth") or {}).get("OPENAI_API_KEY", "")
    elif app_type == "claude":
        env = cfg.get("env") or {}
        base_url = env.get("ANTHROPIC_BASE_URL") or website or ""
        model = (env.get("ANTHROPIC_MODEL")
                 or env.get("ANTHROPIC_DEFAULT_SONNET_MODEL")
                 or env.get("ANTHROPIC_DEFAULT_OPUS_MODEL") or "")
        api_key = env.get("ANTHROPIC_AUTH_TOKEN") or ""
    elif app_type == "gemini":
        env = cfg.get("env") or {}
        base_url = env.get("GOOGLE_GEMINI_BASE_URL") or env.get("GEMINI_BASE_URL") or website or ""
        model = env.get("GEMINI_MODEL") or ""
        api_key = env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY") or ""
    else:
        return None
    base_url = (base_url or "").strip().rstrip("/")
    if not base_url or not re.match(r"^https?://", base_url):
        return None  # 没有可用端点的条目（官方登录类）不导入
    # 官方登录类条目的 website 是产品页而非 API 端点，且通常无 key → 排除
    if not api_key and not re.search(r"/v\d+|api\.|/api/", base_url):
        return None
    # 有人把完整 completions 路径填进 base_url：剥到 /v1（与面板的 {base}/chat/completions 拼接约定一致）
    base_url = re.sub(r"/(chat/)?completions/?$", "", base_url)
    return {"name": name, "base_url": base_url, "model": model, "api_key": api_key,
            "app_type": app_type}


def list_providers() -> dict:
    """读 cc-switch 库，返回可导入的 provider 列表（含 API Key，仅本机面板传输）。
    库不存在 / 无可导入条目时返回空列表，UI 显示原因。"""
    if not CC_SWITCH_DB.exists():
        return {"found": False, "reason": f"未找到 cc-switch 配置库（{CC_SWITCH_DB}）", "providers": []}
    out = []
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{CC_SWITCH_DB.as_posix()}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT app_type, name, settings_config, website_url FROM providers "
            "ORDER BY is_current DESC, sort_index IS NULL, sort_index").fetchall()
        con.close()
    except Exception as e:
        return {"found": True, "reason": f"读取 cc-switch 数据库失败: {type(e).__name__}: {e}", "providers": []}
    seen: set[tuple] = set()
    for r in rows:
        try:
            cfg = json.loads(r["settings_config"] or "{}")
        except json.JSONDecodeError:
            continue
        p = _parse_provider(r["app_type"], r["name"], cfg, r["website_url"] or "")
        if not p:
            continue
        sig = (p["base_url"], p["api_key"][:24])
        if sig in seen:
            continue  # 同端点同 key 的重名条目只留一份
        seen.add(sig)
        out.append(p)
    return {"found": True, "reason": "" if out else "cc-switch 里没有可导入的 OpenAI 兼容端点配置",
            "providers": out}


def apply(provider: dict) -> dict:
    """把选中的 provider 写入 ai_provider 设置（总开关不动，由用户自己控制）。"""
    db.execute("INSERT INTO settings(key,value) VALUES('ai_provider',?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (db.j({"base_url": provider["base_url"], "model": provider.get("model", ""),
                      "api_key": provider.get("api_key", "")}),))
    return {"ok": True}
