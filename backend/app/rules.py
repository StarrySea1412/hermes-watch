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
    "io_warn": 80_000,   # 磁盘持续写入 KiB/s（~80MB/s，接近 SATA/常见 SSD 顺序写上限）
    "temp_warn": 80,     # CPU/NVMe 温度 °C
    "swap_warn": 70,     # swap 使用率 %（持续高 swap = 内存压力信号）
}

# 滞后恢复阈值（Netdata CLEAR 模式）：触发用 warn/crit 阈值，恢复要求指标回落到
# 更低的 clear 阈值之下——防止指标在告警线附近抖动导致告警反复打翻（flapping）。
CLEAR_THRESHOLDS = {
    "disk": 80, "memory": 80, "cpu": 75, "load": 6.0,
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


def clear_thresholds() -> dict:
    """恢复阈值 = min(用户同主阈值 - 5, 内置 clear 值)，用户调高告警线时恢复线跟随。"""
    t = dict(CLEAR_THRESHOLDS)
    T = thresholds()
    for k_main, k_clear in (("disk_warn", "disk"), ("mem_warn", "memory"),
                            ("cpu_warn", "cpu"), ("load_warn", "load")):
        t[k_clear] = min(t[k_clear], T[k_main] - 5)
    return t


def evaluate(host: dict, latest: dict | None, extras: dict) -> list[dict]:
    """Return list of finding dicts (not persisted). extras: top_proc/failed_services/logins/cert."""
    f: list[dict] = []
    if not latest:
        return f
    T = thresholds()
    C = clear_thresholds()
    hid, now = host["id"], db.now()

    def add(type_, sev, title, detail, evidence=None):
        f.append({"host_id": hid, "ts": now, "type": type_, "severity": sev,
                  "title": title, "detail": detail, "evidence": evidence or {}})

    disk, mem, cpu, load = latest["disk"], latest["mem"], latest["cpu"], latest["load1"]
    # 滞后：告警已处于打开状态（open/analyzed）时，指标须回落到 clear 阈值之下才算解除
    active = {r["type"] for r in db.query(
        "SELECT DISTINCT type FROM findings WHERE host_id=? AND status IN ('open','analyzed')", (hid,))}
    if disk >= T["disk_crit"] or ("disk" in active and disk >= C["disk"]):
        add("disk", "crit" if disk >= T["disk_crit"] else "warn",
            f"磁盘使用率 {disk:.0f}%",
            "磁盘即将写满，有写入失败风险" if disk >= T["disk_crit"] else f"超过 {T['disk_warn']:.0f}% 警戒线",
            {"disk": disk})
    elif disk >= T["disk_warn"]:
        add("disk", "warn", f"磁盘使用率 {disk:.0f}%", f"超过 {T['disk_warn']:.0f}% 警戒线", {"disk": disk})
    if mem >= T["mem_crit"] or ("memory" in active and mem >= C["memory"]):
        add("memory", "crit" if mem >= T["mem_crit"] else "warn",
            f"内存使用率 {mem:.0f}%",
            "内存逼近 OOM 边界" if mem >= T["mem_crit"] else f"超过 {T['mem_warn']:.0f}% 警戒线",
            {"mem": mem})
    elif mem >= T["mem_warn"]:
        add("memory", "warn", f"内存使用率 {mem:.0f}%", f"超过 {T['mem_warn']:.0f}% 警戒线", {"mem": mem})
    if cpu >= T["cpu_warn"] or ("cpu" in active and cpu >= C["cpu"]):
        add("cpu", "warn", f"CPU 使用率 {cpu:.0f}%", "持续高负载", {"cpu": cpu})
    if load >= T["load_warn"] or ("load" in active and load >= C["load"]):
        add("load", "warn", f"系统负载 {load:.1f}", f"load1 超过 {T['load_warn']:.0f}", {"load1": load})

    io_write = float(latest.get("io_write") or 0)
    if io_write >= T["io_warn"]:
        add("io", "warn", f"磁盘持续高写入 {io_write / 1024:.0f} MB/s",
            f"写入速率超过 {T['io_warn'] / 1024:.0f} MB/s 警戒线（agent 上报）", {"io_write": io_write})
    temp = float(latest.get("temp_c") or 0)
    if temp >= T["temp_warn"]:
        sev = "warn" if temp < T["temp_warn"] + 15 else "crit"
        add("temp", sev, f"温度过高 {temp:.0f}°C",
            f"传感器温度超过 {T['temp_warn']:.0f}°C", {"temp": temp})
    swap = float(latest.get("swap") or 0)
    if swap >= T["swap_warn"]:
        add("swap", "warn", f"Swap 使用率 {swap:.0f}%",
            f"swap 持续高位（>{T['swap_warn']:.0f}%）通常意味着物理内存不足", {"swap": swap})

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
