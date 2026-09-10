"""Metrics collection: real hosts via SSH (asyncssh), demo hosts via simulator.

Real probe runs ONE shell script per host emitting key=value lines, then a few
read-only detail probes. Demo hosts generate a plausible 24h series with
injected "chaos" (disk filling / mem leak / suspicious login) so the whole
demo story works with zero external machines.
"""
import asyncio
import random
import re

from . import db

try:
    import asyncssh
except ImportError:  # pragma: no cover
    asyncssh = None

PROBE_SCRIPT = """
echo "cpu=$(top -bn1 | grep 'Cpu(s)' | awk '{print $2+$4}' | cut -d. -f1)"
echo "mem=$(free | awk '/Mem:/{printf("%.0f", $3/$2*100)}')"
free | awk '/Swap:/{if($2>0) printf("swap=%.0f\\n", $3/$2*100)}'
echo "load1=$(awk '{print $1}' /proc/loadavg)"
df -P -x tmpfs -x devtmpfs | awk 'NR>1{u=$5; gsub(/%/,"",u); if(u>m)m=u} END{printf("disk=%.0f\\n", m)}'
cat /proc/net/dev | awk -F'[: ]+' '/eth0|ens|enp/{rx+=$3; tx+=$11} END{printf("net_in=%.0f net_out=%.0f\\n", rx, tx)}'
"""

DETAIL_PROBES = {
    "top_proc": "ps aux --sort=-%mem | head -6",
    "failed_services": "systemctl list-units --state=failed --no-legend --plain 2>/dev/null | awk '{print $1}'",
    "logins": "last -n 12 -w 2>/dev/null || last -n 12",
    "cert": "for d in /etc/letsencrypt/live/*/cert.pem; do openssl x509 -enddate -noout -in $d 2>/dev/null; done",
    "ports": "ss -tlnpH 2>/dev/null || ss -tlnp 2>/dev/null",
    # 容器清单(docker 缺失时输出为空,解析得空列表):state 只认官方取值 running/exited/dead/created/paused/restarting
    "docker": "docker ps -a --format '{{.Names}}|{{.State}}|{{.Status}}|{{.Image}}' 2>/dev/null",
}

KNOWN_IPS = {"10.", "192.168.", "172.", "127."}


def parse_ports(out: str) -> list[dict]:
    """ss -tlnp 输出 → [{port, addr, proc}]（与 agent 上报同结构）。
    -H 无表头；旧 ss 无 -H 时跳过表头行。进程列可能缺（无 -p 权限）。"""
    ports = []
    seen = set()
    for line in out.splitlines():
        if not line.strip() or line.startswith(("State", "Netid")):
            continue
        fields = line.split()
        if len(fields) < 4 or fields[0].lower() not in ("listen",):
            continue
        local = fields[3] if not line.startswith("LISTEN") else fields[3]
        # 形态：0.0.0.0:22 / [::]:22 / 127.0.0.1:631
        if ":" not in local:
            continue
        addr, _, port_s = local.rpartition(":")
        try:
            port = int(port_s)
        except ValueError:
            continue
        if port <= 0 or port > 65535 or (addr, port) in seen:
            continue
        seen.add((addr, port))
        proc = ""
        if len(fields) >= 6:
            users = " ".join(fields[5:])
            m = re.search(r'"([^"]+)"', users)
            if m:
                proc = m.group(1)
        addr_disp = "::" if addr == "[::]" else addr
        ports.append({"port": port, "addr": addr_disp, "proc": proc})
    ports.sort(key=lambda x: x["port"])
    return ports[:64]


def parse_probe(out: str) -> dict:
    m = {}
    for line in out.splitlines():
        for tok in line.split():  # 一行可能带多个 k=v（如 net_in=x net_out=y）
            if "=" in tok:
                k, _, v = tok.partition("=")
                try:
                    m[k.strip()] = float(v.strip())
                except ValueError:
                    pass
    return m


def parse_containers(out: str) -> list[dict]:
    """docker ps -a --format 'Names|State|Status|Image' 输出 → [{name,state,status,image}]。
    名字含 | 的容器极罕见,按 3 段切忽略多余;字段不足的行跳过。"""
    out2 = []
    for line in out.splitlines():
        parts = line.split('|', 3)
        if len(parts) != 4 or not parts[0].strip():
            continue
        out2.append({"name": parts[0].strip(), "state": parts[1].strip(),
                     "status": parts[2].strip(), "image": parts[3].strip()})
    return out2

def parse_cert_days(out: str):
    # notAfter=Sep 20 12:00:00 2026 GMT
    import datetime
    for line in out.splitlines():
        if "notAfter=" in line:
            raw = line.split("=", 1)[1].strip()
            try:
                exp = datetime.datetime.strptime(raw, "%b %d %H:%M:%S %Y %Z")
                return max(0, (exp - datetime.datetime.utcnow()).days)
            except ValueError:
                continue
    return None


def parse_logins(out: str) -> tuple[list[dict], list[dict]]:
    """Split into (recent logins, suspicious ones from non-private IPs)."""
    recent, suspicious = [], []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 10 or parts[0] in ("wtmp", "btmp", ""):
            continue
        user, tty, ip, when = parts[0], parts[1], parts[2], " ".join(parts[3:7])
        rec = {"user": user, "tty": tty, "ip": ip, "when": when}
        recent.append(rec)
        if user in ("root", "admin") and not ip.startswith(tuple(KNOWN_IPS)) and "(" not in ip:
            suspicious.append(rec)
    return recent[:8], suspicious


async def run_cmd(host: dict, cmd: str, timeout: int = 12) -> str:
    """在目标机上执行一条只读命令并返回 stdout（诊断深挖探针用）。"""
    if asyncssh is None:
        raise RuntimeError("asyncssh 未安装")
    from . import ssh
    conn = await ssh.connect_async(host)
    try:
        r = await asyncio.wait_for(conn.run(cmd, check=True), timeout=timeout)
        return r.stdout or ""
    finally:
        conn.close()


async def probe_real(host: dict) -> tuple[dict | None, dict]:
    if asyncssh is None:
        raise RuntimeError("asyncssh 未安装")
    from . import ssh
    conn = await ssh.connect_async(host)
    try:
        r = await conn.run(PROBE_SCRIPT, check=True)
        base = parse_probe(r.stdout)
        # 键恒存在：单个探针失败（精简发行版缺 last/openssl 等）时前端拿到的 extras 结构仍完整
        extras: dict = {"failed_services": [], "logins": [], "suspicious_logins": [],
                        "docker_containers": [],
                        "cert_days_left": None, "ports": []}
        for key in ("top_proc", "failed_services", "logins", "cert", "ports", "docker"):
            try:  # 精简发行版可能缺 last/openssl 等，单探针缺失不拖垮采集
                r = await conn.run(DETAIL_PROBES[key], check=True)
            except Exception:
                continue
            if key == "top_proc":
                extras["top_proc"] = r.stdout.strip()
            elif key == "failed_services":
                extras["failed_services"] = [x for x in r.stdout.split() if x.strip()]
            elif key == "logins":
                recent, susp = parse_logins(r.stdout)
                extras["logins"], extras["suspicious_logins"] = recent, susp
            elif key == "cert":
                extras["cert_days_left"] = parse_cert_days(r.stdout)
            elif key == "ports":
                extras["ports"] = parse_ports(r.stdout)
            elif key == "docker":
                extras["docker_containers"] = parse_containers(r.stdout)
        return base, extras
    finally:
        conn.close()


def _wav(base: float, amp: float) -> float:
    return max(0.0, base + random.uniform(-amp, amp))


def next_mock_point(host: dict, prev: dict | None) -> dict:
    """One fresh 1-min point, continuing the host's chaos ramp from the previous value.

    Keeps the demo fleet *alive* during a session — spark lines and charts
    advance in real time instead of freezing on the seeded 24h series.
    """
    chaos = host.get("chaos") or ""
    disk = _wav(62.0, 2)
    mem = _wav(55.0, 3)
    cpu = _wav(25.0, 8)
    net_in, net_out, load = _wav(2_400_000, 4e5), _wav(1_100_000, 3e5), _wav(0.6, 0.2)
    if prev:
        # 均值回归游走：长期运行不漂移到 100%；chaos 在此之上加向上漂移
        disk = max(0, min(99.5, prev["disk"] + (62 - prev["disk"]) * 0.03 + random.uniform(-0.5, 0.5)))
        mem = max(0, min(98.5, prev["mem"] + (55 - prev["mem"]) * 0.03 + random.uniform(-0.6, 0.6)))
        cpu = max(0, min(100, prev["cpu"] + (25 - prev["cpu"]) * 0.05 + random.uniform(-4, 4)))
        net_in = max(0, prev["net_in"] * random.uniform(0.9, 1.1))
        net_out = max(0, prev["net_out"] * random.uniform(0.9, 1.1))
        load = max(0, prev["load1"] + random.uniform(-0.08, 0.08))
    if "disk_filling" in chaos:
        disk = min(99.5, disk + 0.28)          # ~1.7%/h upward drift
    if "mem_leak" in chaos:
        mem = min(98.5, mem + 0.3)
        cpu = min(100, cpu + 0.15)
    if "busy" in chaos:
        cpu = _wav(45, 12)
    return {"host_id": host["id"], "ts": db.now(), "cpu": round(cpu, 1),
            "mem": round(mem, 1), "disk": round(disk, 1),
            "net_in": round(net_in), "net_out": round(net_out),
            "load1": round(load, 2)}


def generate_mock(host: dict, points: int = 1440, step: int = 60) -> list[dict]:
    """24h of 1-min metrics with a slow ramp driven by the host's chaos mode."""
    chaos = host.get("chaos") or ""
    rows = []
    now = db.now()
    disk0, mem0, cpu0 = 62.0, 55.0, 25.0
    for i in range(points, 0, -1):
        ts = now - i * step
        frac = 1 - i / points  # 0 → 1 over the window
        disk, mem, cpu = _wav(disk0, 3), _wav(mem0, 4), _wav(cpu0, 10)
        net_in, net_out, load = _wav(2_400_000, 4e5), _wav(1_100_000, 3e5), _wav(0.6, 0.3)
        if "disk_filling" in chaos:
            disk = min(99.0, 84.0 + frac * 16 + random.uniform(-1, 1))
        if "mem_leak" in chaos:
            mem = min(98.0, 58.0 + frac * 40 + random.uniform(-1, 1))
            cpu = min(100.0, cpu + frac * 25)
        if "busy" in chaos:
            cpu = _wav(45, 15)
        rows.append({"host_id": host["id"], "ts": ts, "cpu": round(cpu, 1),
                     "mem": round(mem, 1), "disk": round(disk, 1),
                     "net_in": round(net_in), "net_out": round(net_out),
                     "load1": round(load, 2)})
    return rows


def mock_extras(host: dict) -> dict:
    """Fabricated read-only probe results matching each chaos story."""
    chaos = host.get("chaos") or ""
    extras = {
        "top_proc": ("USER  PID  %MEM %CPU COMMAND\n"
                     "root  812  12.4  1.1  /usr/bin/python3 /opt/app/server.py\n"
                     "mysql 943  9.8   3.2  /usr/sbin/mysqld\n"
                     "root  511  2.1   0.3  /usr/bin/node /opt/dashboard/server.js"),
        "failed_services": [], "logins": [], "suspicious_logins": [],
        "cert_days_left": 45,
    }
    if "disk_filling" in chaos:
        extras["top_proc"] += "\nroot 771  1.2  24.0 /usr/bin/java -jar logshipper.jar"
    if "mem_leak" in chaos:
        extras["top_proc"] = ("USER  PID  %MEM %CPU COMMAND\n"
                              "root  812  91.3  88.2 /usr/bin/python3 /opt/app/worker.py\n"
                              "mysql 943  9.8   3.2  /usr/sbin/mysqld") + extras["top_proc"].split("\n", 1)[1]
    if "suspicious_login" in chaos:
        extra_login = {"user": "root", "tty": "pts/1", "ip": "185.220.101.34",
                       "when": "Sat Sep  5 03:12"}
        extras["logins"] = [extra_login,
                            {"user": "deploy", "tty": "pts/0", "ip": "192.168.1.24",
                             "when": "Fri Sep  4 18:40"}]
        extras["suspicious_logins"] = [extra_login]
    if "cert_soon" in chaos:
        extras["cert_days_left"] = 9
    return extras


async def collect(host: dict) -> tuple[dict, dict]:
    """Return (latest_metrics, extras). For mock hosts also backfills the series once."""
    if host.get("mock"):
        existing = db.query_one(
            "SELECT * FROM metrics WHERE host_id=? ORDER BY ts DESC LIMIT 1", (host["id"],))
        if not existing:
            rows = generate_mock(host)
            db.executemany(
                "INSERT INTO metrics(host_id,ts,cpu,mem,disk,net_in,net_out,load1,swap) "
                "VALUES(:host_id,:ts,:cpu,:mem,:disk,:net_in,:net_out,:load1,0)", rows)
        else:
            # keep the series alive: one new 1-min point per collection cycle
            prev = dict(existing)
            if db.now() - prev["ts"] >= 55:
                pt = next_mock_point(host, prev)
                db.execute(
                    "INSERT INTO metrics(host_id,ts,cpu,mem,disk,net_in,net_out,load1,swap) "
                    "VALUES(?,?,?,?,?,?,?,?,0)",
                    (pt["host_id"], pt["ts"], pt["cpu"], pt["mem"], pt["disk"],
                     pt["net_in"], pt["net_out"], pt["load1"]))
        latest = db.query_one(
            "SELECT * FROM metrics WHERE host_id=? ORDER BY ts DESC LIMIT 1", (host["id"],))
        return dict(latest) if latest else {}, mock_extras(host)
    base, extras = await probe_real(host)
    db.execute(
        "INSERT INTO metrics(host_id,ts,cpu,mem,disk,net_in,net_out,load1,swap) VALUES(?,?,?,?,?,?,?,?,?)",
        (host["id"], db.now(), base.get("cpu", 0), base.get("mem", 0), base.get("disk", 0),
         base.get("net_in", 0), base.get("net_out", 0), base.get("load1", 0), base.get("swap", 0)))
    if extras:
        db.execute("UPDATE hosts SET last_extras=? WHERE id=?", (db.j(extras), host["id"]))
    return base, extras
