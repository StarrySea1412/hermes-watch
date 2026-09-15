"""端口暴露面侦查：LISTEN 端口清点 → 业务猜测 → 基线比对 → 新端口告警。

- 数据来源：agent（/proc/net/tcp{,6}）、SSH 探针（ss -tlnp）或网络观测扫描
  （probe 主机：面板机 TCP connect 常用端口），结构统一 [{port, addr, proc, biz?}]
- 业务猜测：PORT_BIZ 知识表 + 进程名提示（guess_biz），merge_ports 去重合并附标签
- 基线：每主机存 settings 键 `ports_baseline:<host_id>`（JSON：{"ports": "22,80,443", "seen_once": true}）
  首次见到该主机 → 只建基线不发告警（避免新装机第一轮满屏噪音）
- 告警：当前 LISTEN 中出现基线外的端口 → port_new 发现（warn，proc 可知时附进程名）
- 白名单：设置页可把误报端口加入基线（从发现卡一键「加入基线」）
- 端口消失不告警（下线是正常运维动作），但基线同步收缩
"""
import json

from . import db, i18n

BASELINE_PREFIX = "ports_baseline:"

# 常见端口 → 业务猜测（zh, en）。覆盖不到的端口 guess_biz 返回空串（前端不显示标签）。
PORT_BIZ: dict[int, tuple[str, str]] = {
    22: ("SSH 运维", "SSH"), 3389: ("远程桌面", "RDP"), 5900: ("VNC 桌面", "VNC"),
    80: ("Web 服务", "Web"), 443: ("Web 服务 (HTTPS)", "Web (HTTPS)"),
    8080: ("Web 应用", "Web app"), 8443: ("Web 应用 (HTTPS)", "Web app (HTTPS)"),
    8000: ("应用服务", "App server"), 8888: ("应用服务", "App server"),
    53: ("DNS 域名解析", "DNS"), 161: ("SNMP 监控", "SNMP"),
    3306: ("MySQL 数据库", "MySQL"), 5432: ("PostgreSQL 数据库", "PostgreSQL"),
    1433: ("SQL Server 数据库", "SQL Server"), 1521: ("Oracle 数据库", "Oracle"),
    6379: ("Redis 缓存", "Redis"), 11211: ("Memcached 缓存", "Memcached"),
    27017: ("MongoDB 数据库", "MongoDB"), 9200: ("Elasticsearch 搜索", "Elasticsearch"),
    8123: ("ClickHouse 分析库", "ClickHouse"),
    5672: ("RabbitMQ 消息队列", "RabbitMQ"), 9092: ("Kafka 消息队列", "Kafka"),
    15672: ("RabbitMQ 管理台", "RabbitMQ mgmt"),
    2375: ("Docker API", "Docker API"), 2376: ("Docker API (TLS)", "Docker API (TLS)"),
    9090: ("Prometheus 监控", "Prometheus"), 3000: ("Grafana/Node 应用", "Grafana/Node app"),
    25: ("邮件服务", "Mail"), 465: ("邮件服务 (SMTPS)", "Mail (SMTPS)"),
    587: ("邮件服务 (提交)", "Mail (submission)"), 1194: ("VPN 隧道", "VPN"),
}

_PROC_HINTS = {"mysql": ("MySQL 数据库", "MySQL"), "postgres": ("PostgreSQL 数据库", "PostgreSQL"),
               "redis": ("Redis 缓存", "Redis"), "nginx": ("Web 反向代理", "Web proxy"),
               "httpd": ("Web 服务", "Web"), "node": ("Node 应用", "Node app"),
               "python": ("Python 应用", "Python app"), "java": ("Java 应用", "Java app"),
               "docker": ("容器服务", "Containers"), "oracle": ("Oracle 数据库", "Oracle")}


def guess_biz(port: int, proc: str = "") -> str:
    """端口 → 业务猜测（双语跟随面板语言）；进程名辅助兜底（如 mysqld → MySQL）。"""
    biz = PORT_BIZ.get(int(port))
    if biz:
        return i18n.t(*biz)
    p = (proc or "").lower()
    for k, v in _PROC_HINTS.items():
        if k in p:
            return i18n.t(*v)
    return ""


def merge_ports(ports: list) -> list[dict]:
    """端口清单合并：同端口去重（proc 取信息最全，addr 去重拼接），每条附 biz 猜测。
    输入顺序无关，输出按端口号升序；非法行静默丢弃。"""
    by_port: dict[int, dict] = {}
    for p in ports or []:
        if not isinstance(p, dict):
            continue
        try:
            port = int(p.get("port") or 0)
        except (TypeError, ValueError):
            continue
        if port <= 0 or port > 65535:
            continue
        prev = by_port.get(port)
        if prev is None:
            by_port[port] = {"port": port, "addr": str(p.get("addr") or "").strip(),
                             "proc": str(p.get("proc") or "").strip()}
        else:
            if not prev["proc"] and p.get("proc"):
                prev["proc"] = str(p["proc"]).strip()
            addr = str(p.get("addr") or "").strip()
            if addr and addr not in (prev["addr"] or ""):
                prev["addr"] = ", ".join(x for x in (prev["addr"], addr) if x)
    out = []
    for port in sorted(by_port):
        row = by_port[port]
        row["biz"] = guess_biz(port, row["proc"])
        out.append(row)
    return out


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
