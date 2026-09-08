"""提案执行引擎：白名单 + 校验 + 超时 + 全审计。

铁律不变：批准仅记录决策（decide 接口），执行是批准之后另一个显式动作，
且受设置 propose_exec 门控（默认 off）。每个命令逐段过白名单，
mock 主机在模拟环境中产生真实效果（清除 chaos），真实主机走 SSH，
每次尝试（含被拒/超时）都写 proposal_runs 审计表。
"""
import asyncio
import re
import time

from . import db, secrets

# 白名单：每段命令必须整体匹配其中一条，否则拒绝执行
WHITELIST = [
    (re.compile(r"^journalctl --vacuum-size=\d+[MG]$"), "低危", "清理 journal 日志"),
    (re.compile(r"^find /var/log -name '\*\.gz' -mtime \+\d+ -delete$"), "中危", "删除过期压缩日志"),
    (re.compile(r"^systemctl restart [a-z0-9@._-]+$"), "中危", "重启服务"),
    (re.compile(r"^ufw insert 1 deny \d{1,3}(\.\d{1,3}){3}$"), "中危", "防火墙封禁 IP"),
    (re.compile(r"^passwd [a-z_][a-z0-9_-]{0,30}$"), "高危", "轮换口令"),
]

TIMEOUT = 20  # 秒


def _split(command: str) -> list[str]:
    return [seg.strip() for seg in command.split("&&") if seg.strip()]


def validate(command: str) -> dict:
    """逐段白名单校验。返回 {ok, risk, effects, refused}。"""
    segs = _split(command)
    if not segs:
        return {"ok": False, "risk": "低危", "effects": [], "refused": "空命令"}
    risk, effects, bad = "低危", [], []
    for seg in segs:
        for pat, r, label in WHITELIST:
            if pat.match(seg):
                risk = max(risk, r, key=lambda x: ["低危", "中危", "高危"].index(x))
                effects.append(label)
                break
        else:
            bad.append(seg)
    if bad:
        return {"ok": False, "risk": risk, "effects": effects,
                "refused": f"白名单外命令: {bad[0]}"}
    return {"ok": True, "risk": risk, "effects": effects, "refused": None}


def exec_enabled() -> bool:
    s = db.query_one("SELECT value FROM settings WHERE key='propose_exec'")
    return bool(s and s["value"] == "on")


# ---------- mock 主机的模拟效果 ----------

MOCK_OUTPUT = {
    "journalctl": ("Vacuuming done, freed 2.1G of archived journals from /var/log/journal."
                   "\nDeleted archived journal /var/log/journal/…/system@…journal (1.1G)"),
    "find": ("removed '/var/log/app.log-20260820.gz'"
             "\nremoved '/var/log/app.log-20260813.gz'"),
    "systemctl": ("app-worker.service restarted."
                  "\n   Active: active (running) since (exec) — RSS 已归零"),
    "ufw": ("Rule inserted (skip 0)\nStatus: active"),
    "passwd": ("passwd: password updated successfully"),
}


def _mock_recover(host: dict, disk: float | None = None, mem: float | None = None):
    """修复生效 → 指标曲线真实回落（曲线图可见恢复过程）。"""
    prev = db.query_one(
        "SELECT * FROM metrics WHERE host_id=? ORDER BY ts DESC LIMIT 1", (host["id"],))
    if not prev:
        return
    pt = dict(prev)
    pt.update(ts=db.now())
    if disk is not None:
        pt["disk"] = disk
    if mem is not None:
        pt["mem"] = mem
        pt["cpu"] = min(pt["cpu"], 30.0)
    db.execute(
        "INSERT INTO metrics(host_id,ts,cpu,mem,disk,net_in,net_out,load1) VALUES(?,?,?,?,?,?,?,?)",
        (host["id"], pt["ts"], pt["cpu"], pt["mem"], pt["disk"],
         pt["net_in"], pt["net_out"], pt["load1"]))


def _mock_effect(host: dict, command: str) -> str:
    """模拟执行：返回输出；修复类命令总是让指标真实回落（曲线可见恢复），
    并在清除 chaos 后停止对应漂移。"""
    seg_prefix = command.split()[0]
    out = MOCK_OUTPUT.get(seg_prefix, f"{command}: done (demo exec)")
    chaos = host.get("chaos") or ""
    cleared = {"journalctl": "disk_filling", "find": "disk_filling",
               "systemctl": "mem_leak", "ufw": "suspicious_login",
               "passwd": "suspicious_login"}.get(seg_prefix)
    # 指标恢复按命令类型触发，不依赖 chaos 标志（chaos 可能已被清掉）
    if cleared == "disk_filling":
        _mock_recover(host, disk=62.0)
    elif cleared == "mem_leak":
        _mock_recover(host, mem=55.0)
    if cleared and cleared in chaos:
        chaos = " ".join(c for c in chaos.split() if c != cleared)
        db.execute("UPDATE hosts SET chaos=? WHERE id=?", (chaos.strip(), host["id"]))
        db.execute("INSERT INTO events(ts,host_id,kind,message,data) VALUES(?,?,?,?,?)",
                   (db.now(), host["id"], "exec",
                    f"模拟执行生效：{cleared} 已解除，主机开始恢复", "{}"))
    return out


async def _real_exec(host: dict, command: str) -> tuple[str, int]:
    from . import ssh
    conn = await ssh.connect_async(host)
    try:
        result = await asyncio.wait_for(conn.run(command), timeout=TIMEOUT)
        out = (result.stdout or "") + (result.stderr or "")
        return out.strip() or "(无输出)", result.exit_status or 0
    finally:
        conn.close()


async def execute(proposal: dict, host: dict) -> dict:
    """执行一个已批准提案。返回审计行。绝不静默：一切结果都落 proposal_runs。"""
    t0 = time.time()
    command = proposal["command"]
    v = validate(command)
    mode = "mock" if host.get("mock") else "ssh"
    if not v["ok"]:
        run = {"status": "refused", "exit_code": None, "output": v["refused"]}
    elif not exec_enabled():
        run = {"status": "refused", "exit_code": None,
               "output": "提案执行开关未开启（设置 → 提案执行）"}
    else:
        try:
            if host.get("mock"):
                await asyncio.sleep(0.4)  # 拟真延迟
                run = {"status": "ok", "exit_code": 0, "output": _mock_effect(host, command)}
            else:
                out, code = await _real_exec(host, command)
                run = {"status": "ok" if code == 0 else "failed", "exit_code": code, "output": out[:4000]}
        except asyncio.TimeoutError:
            run = {"status": "timeout", "exit_code": None,
                   "output": f"执行超过 {TIMEOUT}s 被终止（审计已留存；恢复需人工确认）"}
        except Exception as e:
            run = {"status": "failed", "exit_code": None,
                   "output": f"{type(e).__name__}: {e}"}

    duration = int((time.time() - t0) * 1000)
    rid = db.execute(
        "INSERT INTO proposal_runs(ts,proposal_id,host_id,mode,command,risk,status,exit_code,output,duration_ms) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (db.now(), proposal["id"], host["id"], mode, command, v["risk"],
         run["status"], run["exit_code"], run["output"], duration))

    ok = run["status"] == "ok"
    db.execute("INSERT INTO events(ts,host_id,kind,message,data) VALUES(?,?,?,?,?)",
               (db.now(), host["id"], "exec",
                f"提案 #{proposal['id']} 执行{'成功' if ok else '失败'}（{run['status']}，{duration}ms）: {proposal['title']}",
                db.j({"proposal_id": proposal["id"], "run_id": rid})))
    return {"id": rid, **run, "risk": v["risk"], "mode": mode,
            "duration_ms": duration, "ts": db.now(),
            "proposal_id": proposal["id"], "host_id": host["id"], "command": command}


def runs_for(proposal_ids: list[int]) -> dict[int, list[dict]]:
    if not proposal_ids:
        return {}
    marks = ",".join("?" * len(proposal_ids))
    rows = db.query(
        f"SELECT * FROM proposal_runs WHERE proposal_id IN ({marks}) ORDER BY ts DESC LIMIT 100",
        tuple(proposal_ids))
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r["proposal_id"], []).append(r)
    return out
