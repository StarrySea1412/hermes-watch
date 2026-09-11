"""端口暴露面侦查：LISTEN 端口清点 → 基线比对 → 新端口告警。

- 数据来源：agent（/proc/net/tcp{,6}）或 SSH 探针（ss -tlnp），结构统一 [{port, addr, proc}]
- 基线：每主机存 settings 键 `ports_baseline:<host_id>`（JSON：{"ports": "22,80,443", "seen_once": true}）
  首次见到该主机 → 只建基线不发告警（避免新装机第一轮满屏噪音）
- 告警：当前 LISTEN 中出现基线外的端口 → port_new 发现（warn，proc 可知时附进程名）
- 白名单：设置页可把误报端口加入基线（从发现卡一键「加入基线」）
- 端口消失不告警（下线是正常运维动作），但基线同步收缩
"""
import json

from . import db, i18n

BASELINE_PREFIX = "ports_baseline:"


def _baseline_key(host_id: int) -> str:
    return f"{BASELINE_PREFIX}{host_id}"


def get_baseline(host_id: int) -> set[int]:
    row = db.query_one("SELECT value FROM settings WHERE key=?", (_baseline_key(host_id),))
    if not row:
        return set()
    try:
        return {int(x) for x in json.loads(row["value"]).get("ports", [])}
    except (ValueError, TypeError, json.JSONDecodeError):
        return set()


def save_baseline(host_id: int, ports: set[int]) -> None:
    db.execute("INSERT INTO settings(key,value) VALUES(?,?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (_baseline_key(host_id), json.dumps({"ports": sorted(ports)})))


def evaluate_ports(host: dict, extras: dict) -> list[dict]:
    """清单到达（agent extras 或 SSH 探针 extras）→ 比对基线，产出 port_new 发现。
    基线外端口在连续 SEEN_ROUNDS 轮后自动并入基线（用户不处理则视为认可，避免永久噪音）。"""
    ports_raw = extras.get("ports")
    if not isinstance(ports_raw, list) or not ports_raw:
        return set(), [], []
    current = {int(p["port"]) for p in ports_raw if isinstance(p, dict) and p.get("port")}
    baseline = get_baseline(host["id"])

    if not db.query_one("SELECT 1 FROM settings WHERE key=?", (_baseline_key(host["id"]),)):
        # 首轮：建基线，静默
        save_baseline(host["id"], current)
        return current, [], []

    new_ports = current - baseline
    findings = []
    if new_ports:
        proc_by_port = {int(p["port"]): (p.get("proc") or "") for p in ports_raw if isinstance(p, dict)}
        for port in sorted(new_ports):
            proc_name = proc_by_port.get(port, "")
            findings.append({
                "host_id": host["id"], "ts": db.now(), "type": "port_new", "severity": "warn",
                "title": i18n.t(f"新增监听端口 {port}", f"New listening port {port}"),
                "detail": i18n.t(
                    f"端口 {port} 不在既有基线中" + (f"，监听进程 {proc_name}" if proc_name else ""),
                    f"Port {port} is not in the existing baseline"
                    + (f", listening process: {proc_name}" if proc_name else "")),
                "evidence": {"port": port, "proc": proc_name},
            })

    # 基线维护：新端口连续 SEEN_ROUNDS 轮仍在监听才并入基线（用户不处理则视为认可，
    # 防止永久噪音）；消失的端口移出基线（下线是正常运维动作）
    seen_key = f"{_baseline_key(host['id'])}:seen"
    seen_row = db.query_one("SELECT value FROM settings WHERE key=?", (seen_key,))
    seen = db.uj(seen_row["value"], {}) if seen_row else {}
    seen = {int(k): int(v) for k, v in seen.items()} if isinstance(seen, dict) else {}

    for port in new_ports:
        seen[port] = seen.get(port, 0) + 1
    for port, count in list(seen.items()):
        if port not in current:
            seen.pop(port, None)
            continue
        seen[port] = count + 1
        if count + 1 >= 5:  # SEEN_ROUNDS
            baseline.add(port)
            seen.pop(port, None)
    for port in list(baseline - current):
        baseline.discard(port)
        seen.pop(port, None)
    save_baseline(host["id"], baseline)
    db.execute("INSERT INTO settings(key,value) VALUES(?,?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (seen_key, db.j(seen)))
    return current, new_ports, findings
