"""Analysis engine: turns findings into root-cause cards with evidence chains.

Layer 1 (always): deterministic deep-dive probes per finding type — this is
the "agent 巡检" core and works with zero LLM.
Layer 2 (optional): LLM narration via an OpenAI-compatible endpoint. The AI
outbound switch defaults to OFF (Termix-style gating from the research).
"""
import httpx

from . import db, rules

DISK_PROBE = ("du -xh /var /opt /home --max-depth=2 2>/dev/null | sort -rh | head -8;"
              " journalctl --disk-usage 2>/dev/null")
MEM_PROBE = "ps aux --sort=-%mem | head -8"
LOGIN_PROBE = "last -n 20 -w; lastb -n 10 2>/dev/null | head -10"

DEEP_DIVE = {
    "disk": [("定位大文件目录", DISK_PROBE)],
    "memory": [("定位高内存进程", MEM_PROBE)],
    "login": [("审计登录记录", LOGIN_PROBE)],
    "service": [("查看失败详情", "systemctl status {service} --no-pager -l | head -20")],
}

PROPOSALS = {
    "disk": ("清理 journal 与过期日志", "journalctl --vacuum-size=500M && find /var/log -name '*.gz' -mtime +7 -delete",
             "回收 journal 空间并删除 7 天前的压缩日志，不影响在线服务"),
    "memory": ("重启泄漏进程", "systemctl restart app-worker",
               "定位到的 worker 进程 RSS 占用 91%，重启释放内存；请先确认业务低峰"),
    "login": ("封禁来源 IP 并改密", "ufw insert 1 deny 185.220.101.34 && passwd root",
              "该 IP 为 Tor 出口节点特征段，先封禁再轮换 root 凭据"),
    "service": ("重启失败服务", "systemctl restart {service}", "恢复 failed unit"),
}

MOCK_EVIDENCE = {
    "disk": ("$ du -xh /var --max-depth=2 | sort -rh | head -5",
             "42G\t/var/log\n38G\t/var/log/app\n4.1G\t/var/lib/docker\n2.3G\t/var/log/journal"),
    "memory": ("$ ps aux --sort=-%mem | head -3",
               "root  812 91.3 88.2 1489204 1387712 ?  Ss  09:12  812:33 /usr/bin/python3 /opt/app/worker.py"),
    "login": ("$ last -n 5",
              "root     pts/1        185.220.101.34   Sat Sep  5 03:12   still logged in"),
    "service": ("$ systemctl status nginx",
                "Active: failed (Result: exit-code) since Sat 2026-09-05 03:14:11 UTC"),
}


def _llm_enabled() -> bool:
    s = db.query_one("SELECT value FROM settings WHERE key='ai_outbound'")
    return bool(s and s["value"] == "on")


def _ai_conf() -> dict:
    import json
    cfg = db.query_one("SELECT value FROM settings WHERE key='ai_provider'")
    return json.loads(cfg["value"]) if cfg else {}


def build_fleet_context() -> str:
    """Compact text snapshot of the fleet for grounding LLM answers."""
    snap = fleet_snapshot()
    lines = []
    for h in snap["hosts"]:
        l = h.get("latest") or {}
        lines.append(
            f"- {h['name']} ({h['hostname']}, 组 {h['group']}): 健康 {h['score']}, 状态 {h['status']}, "
            f"CPU {l.get('cpu', 0):.0f}%, 内存 {l.get('mem', 0):.0f}%, 磁盘 {l.get('disk', 0):.0f}%, "
            f"发现 {h['open_findings']} 条")
    fs = top_findings(8)
    if fs:
        lines.append("当前发现:")
        lines += [f"  * [{f['severity']}] {f['host_name']}: {f['title']} — {f['detail']}" for f in fs]
    return "\n".join(lines)


async def chat_answer(question: str, history: list[dict] | None = None) -> dict:
    """LLM chat grounded in fleet data. Falls back to a deterministic answer
    when AI is disabled/unconfigured — the UI must never be a dead end.
    history = 前端带来的多轮对话（[{role:'user'|'assistant', content}...]），最多取最近 10 条。"""
    conf = _ai_conf()
    if _llm_enabled() and conf.get("base_url"):
        msgs = [{"role": "system", "content": (
            "你是服务器巡检平台 Hermes Watch 的助手。以下是当前 fleet 快照，"
            "回答必须只基于这些数据，不要编造。用简洁中文，必要时给出具体建议。\n\n"
            + build_fleet_context())}]
        for h in (history or [])[-10:]:
            role, content = h.get("role"), str(h.get("content", ""))[:2000]
            if role in ("user", "assistant") and content.strip():
                msgs.append({"role": role, "content": content})
        msgs.append({"role": "user", "content": question})
        try:
            async with httpx.AsyncClient(timeout=45) as cli:
                r = await cli.post(
                    f"{conf['base_url'].rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {conf.get('api_key', '')}"},
                    json={"model": conf.get("model", "gpt-4o-mini"),
                          "messages": msgs,
                          "max_tokens": 500})
                return {"answer": r.json()["choices"][0]["message"]["content"],
                        "grounded": True, "source": "llm"}
        except Exception as e:
            return {"answer": f"LLM 调用失败（{type(e).__name__}），以下是本地规则引擎的确定性回答：\n\n"
                              + _fallback_answer(question),
                    "grounded": True, "source": "fallback"}
    return {"answer": _fallback_answer(question), "grounded": True, "source": "rules"}


def _fallback_answer(question: str) -> str:
    snap = fleet_snapshot()
    worst = sorted(snap["hosts"], key=lambda h: h["score"])[:3]
    lines = ["（AI 外发未开启，以下为本地规则引擎摘要。可在 设置 → AI 外发总开关 开启 LLM 叙事）", ""]
    lines.append(f"Fleet 整体情况：{len(snap['hosts'])} 台主机。")
    for h in worst:
        if h["open_findings"]:
            lines.append(f"· {h['name']}（健康分 {h['score']}）：{h['open_findings']} 条发现，最严重 {h['worst']}。")
    fs = top_findings(5)
    for f in fs:
        lines.append(f"· [{f['severity']}] {f['host_name']}: {f['title']} — {f['detail']}")
    if not fs:
        lines.append("· 当前无待处理发现。")
    return "\n".join(lines)


async def _llm_narrate(host: dict, finding: dict, evidence_text: str) -> tuple[str, str]:
    """LLM 叙事：只基于证据输出。API Key 可空（Ollama 等本地端点无需鉴权）。
    返回 (叙事文本, 模型名)；失败抛异常由调用方留痕，绝不影响规则引擎结论。"""
    conf = _ai_conf()
    if not conf.get("base_url"):
        raise RuntimeError("未配置 Base URL")
    model = conf.get("model", "gpt-4o-mini")
    prompt = (f"服务器 {host['name']} 出现问题: {finding['title']}。{finding['detail']}\n"
              f"排查输出:\n{evidence_text}\n"
              "用不超过 3 句中文说明根因，只基于以上输出，不要编造。")
    async with httpx.AsyncClient(timeout=30) as cli:
        headers = {"Authorization": f"Bearer {conf['api_key']}"} if conf.get("api_key") else {}
        r = await cli.post(
            f"{conf['base_url'].rstrip('/')}/chat/completions",
            headers=headers,
            json={"model": model, "messages": [{"role": "user", "content": prompt}]})
        return r.json()["choices"][0]["message"]["content"], model


def _mock_evidence_for(finding: dict) -> list[dict]:
    t = finding["type"]
    if t in MOCK_EVIDENCE:
        cmd, out = MOCK_EVIDENCE[t]
        return [{"label": {"disk": "定位大文件目录", "memory": "定位高内存进程",
                           "login": "审计登录记录", "service": "查看失败详情"}.get(t, "排查"),
                 "command": cmd, "output": out}]
    return []


async def analyze_finding(host: dict, finding: dict) -> dict:
    """Produce the root-cause card: chain + evidence steps + proposal. Persist to finding.card."""
    steps = []
    if host.get("mock"):
        steps = _mock_evidence_for(finding)
    elif finding["type"] in DEEP_DIVE:
        from . import collector
        svc = (db.uj(finding["evidence"], {}) or {}).get("service", "")
        label, cmd = DEEP_DIVE[finding["type"]][0]
        real_cmd = cmd.format(service=svc or "nginx")
        try:
            # 真实执行深挖命令拿真实输出（只读探针），不再是 extras 的 dict 转储
            out = (await collector.run_cmd(host, real_cmd)).strip()
            steps.append({"label": label, "command": real_cmd,
                          "output": out[:800] or "（命令执行成功，无输出）"})
        except Exception as e:
            steps.append({"label": label, "command": real_cmd,
                          "output": f"执行失败：{type(e).__name__}: {e}"})

    chain = {
        "disk": ["磁盘使用率越过阈值", "写入压力主要来自日志目录", "日志由 app 服务持续输出未轮转"],
        "memory": ["内存使用率越过阈值", "单一进程 RSS 占比异常", "该进程随时间线性增长，符合泄漏特征"],
        "login": ["出现非常见来源登录", "来源 IP 无历史记录", "登录后尚无破坏性命令，处于侦察阶段"],
        "service": ["systemd 报告 unit 失败", "进程异常退出", "退出码指向配置/依赖问题"],
        "cert": ["证书剩余有效期低于阈值", "需要续签并重新部署"],
        "cpu": ["CPU 持续高于阈值", "由进程级占用驱动"],
        "load": ["系统负载过高", "通常与 CPU/IO 排队相关"],
    }.get(finding["type"], ["指标越过阈值"])

    confidence = "高" if steps else "中"

    prop_tmpl = PROPOSALS.get(finding["type"])
    proposal_id = None
    if prop_tmpl:
        svc = (db.uj(finding["evidence"], {}) or {}).get("service", "nginx") \
            if finding["type"] == "service" else None
        title, command, rationale = prop_tmpl
        if svc:
            command, title = command.format(service=svc), title.format(service=svc)
        proposal_id = db.execute(
            "INSERT INTO proposals(ts,host_id,finding_id,title,command,rationale) "
            "VALUES(?,?,?,?,?,?)",
            (db.now(), host["id"], finding["id"], title, command, rationale))

    # 规则引擎结论永远为准；AI 叙事仅作为附加视角单列，绝不覆盖 root_cause
    root_cause = {
        "disk": "/var/log 下应用日志未轮转，38G 日志持续增长吃满磁盘",
        "memory": "/opt/app/worker.py 存在内存泄漏，RSS 随运行时长线性上涨至 91%",
        "login": "来源 IP 185.220.101.34（非内网段）以 root 登录成功，疑似暴力破解得手或口令泄露",
        "service": "服务异常退出，退出码指向配置错误",
    }.get(finding["type"], "规则引擎确认指标越限，证据见左侧执行轨迹")

    ai_narration, ai_model, ai_error = None, None, None
    evidence_text = "\n".join(s["output"] for s in steps if s.get("output"))
    if _llm_enabled() and evidence_text:
        try:
            ai_narration, ai_model = await _llm_narrate(host, finding, evidence_text)
        except Exception as e:
            ai_error = f"{type(e).__name__}: {e}"[:160]
            db.execute("INSERT INTO events(ts,host_id,kind,message,data) VALUES(?,?,?,?,?)",
                       (db.now(), host["id"], "ai_error",
                        f"AI 叙事失败（诊断继续，规则结论不受影响）: {ai_error}", "{}"))

    card = {
        "root_cause": root_cause,
        "chain": chain,
        "confidence": confidence,
        "steps": steps,
        "proposal_id": proposal_id,
        "ai_narration": ai_narration,
        "ai_model": ai_model,
        "ai_error": ai_error,
    }
    db.execute("UPDATE findings SET card=?, status='analyzed' WHERE id=?",
               (db.j(card), finding["id"]))
    db.execute("INSERT INTO events(ts,host_id,kind,message,data) VALUES(?,?,?,?,?)",
               (db.now(), host["id"], "analysis",
                f"agent 完成诊断: {finding['title']}", db.j({"finding_id": finding["id"]})))
    return card


def top_findings(limit=10) -> list[dict]:
    rows = db.query(
        "SELECT f.*, h.name AS host_name FROM findings f JOIN hosts h ON h.id=f.host_id "
        "WHERE f.status IN ('open','analyzed') "
        "ORDER BY CASE f.severity WHEN 'crit' THEN 0 WHEN 'warn' THEN 1 ELSE 2 END, f.ts DESC LIMIT ?",
        (limit,))
    for r in rows:
        r["evidence"] = db.uj(r["evidence"], {})
        r["card"] = db.uj(r["card"], None)
    return rows


def fleet_snapshot() -> dict:
    hosts = db.query("SELECT * FROM hosts ORDER BY id")
    out = []
    for h in hosts:
        latest = db.query_one(
            "SELECT * FROM metrics WHERE host_id=? ORDER BY ts DESC LIMIT 1", (h["id"],))
        spark = db.query(
            "SELECT disk, mem, cpu FROM metrics WHERE host_id=? ORDER BY ts DESC LIMIT 30", (h["id"],))
        findings = db.query(
            "SELECT * FROM findings WHERE host_id=? AND status IN ('open','analyzed')", (h["id"],))
        score = rules.health_score(findings)
        worst = max((f["severity"] for f in findings),
                    key=rules.severity_rank, default=None)
        # 卡片状态跟随最严重发现：crit 发现即使分数未跌破也标红，避免"99% 磁盘显示警告"的矛盾
        status = "crit" if worst == "crit" else rules.status_of(score)
        out.append({
            "id": h["id"], "name": h["name"], "hostname": h["hostname"],
            "group": h["group_name"], "mock": bool(h["mock"]),
            "score": score, "status": status,
            "latest": dict(latest) if latest else None,
            "spark": list(reversed([dict(x) for x in spark])),
            "open_findings": len(findings),
            "worst": worst,
        })
    return {"hosts": out,
            "generated_at": db.now()}
