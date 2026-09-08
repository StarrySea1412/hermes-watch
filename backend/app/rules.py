"""Deterministic rule engine: runs before any LLM. Emits findings + health score.

Principle from the research: AI narrates and deep-dives, rules decide facts.
Thresholds are user-configurable via the settings page (keys: disk_warn … cert_days);
falls back to built-in defaults when unset.
"""
from . import db

DEFAULT_THRESHOLDS = {
    "disk_warn": 85, "disk_crit": 95,
    "mem_warn": 85, "mem_crit": 92,
    "cpu_warn": 85,
    "load_warn": 8.0,
    "cert_days": 14,
}

_INT_KEYS = set(DEFAULT_THRESHOLDS)


def thresholds() -> dict:
    """Merge user overrides from the settings table over the defaults."""
    t = dict(DEFAULT_THRESHOLDS)
    for r in db.query("SELECT key,value FROM settings"):
        if r["key"] in _INT_KEYS:
            try:
                t[r["key"]] = float(r["value"])
            except (TypeError, ValueError):
                pass
    return t


SEV_ORDER = {"info": 0, "warn": 1, "crit": 2}

DEDUCTIONS = {"warn": 8, "crit": 25}


def evaluate(host: dict, latest: dict | None, extras: dict) -> list[dict]:
    """Return list of finding dicts (not persisted). extras: top_proc/failed_services/logins/cert."""
    f: list[dict] = []
    if not latest:
        return f
    T = thresholds()
    hid, now = host["id"], db.now()

    def add(type_, sev, title, detail, evidence=None):
        f.append({"host_id": hid, "ts": now, "type": type_, "severity": sev,
                  "title": title, "detail": detail, "evidence": evidence or {}})

    disk, mem, cpu, load = latest["disk"], latest["mem"], latest["cpu"], latest["load1"]
    if disk >= T["disk_crit"]:
        add("disk", "crit", f"磁盘使用率 {disk:.0f}%", "磁盘即将写满，有写入失败风险", {"disk": disk})
    elif disk >= T["disk_warn"]:
        add("disk", "warn", f"磁盘使用率 {disk:.0f}%", f"超过 {T['disk_warn']:.0f}% 警戒线", {"disk": disk})
    if mem >= T["mem_crit"]:
        add("memory", "crit", f"内存使用率 {mem:.0f}%", "内存逼近 OOM 边界", {"mem": mem})
    elif mem >= T["mem_warn"]:
        add("memory", "warn", f"内存使用率 {mem:.0f}%", f"超过 {T['mem_warn']:.0f}% 警戒线", {"mem": mem})
    if cpu >= T["cpu_warn"]:
        add("cpu", "warn", f"CPU 使用率 {cpu:.0f}%", "持续高负载", {"cpu": cpu})
    if load >= T["load_warn"]:
        add("load", "warn", f"系统负载 {load:.1f}", f"load1 超过 {T['load_warn']:.0f}", {"load1": load})

    for svc in extras.get("failed_services", []):
        add("service", "crit", f"服务失败: {svc}", "systemd 检测到 failed unit", {"service": svc})
    for ln in extras.get("suspicious_logins", []):
        add("login", "crit", f"可疑登录: {ln['user']}@{ln['ip']}",
            "非常见来源的 root/sudo 登录", ln)
    cert = extras.get("cert_days_left")
    if cert is not None and cert <= T["cert_days"]:
        sev = "warn" if cert > 3 else "crit"
        add("cert", sev, f"证书 {cert} 天后到期", "TLS 证书即将过期", {"days": cert})
    return f


def health_score(findings: list[dict]) -> int:
    score = 100
    for x in findings:
        score -= DEDUCTIONS.get(x["severity"], 0)
    return max(0, min(100, score))


def status_of(score: int) -> str:
    return "ok" if score >= 90 else ("warn" if score >= 70 else "crit")


def severity_rank(sev: str) -> int:
    return SEV_ORDER.get(sev, 0)
