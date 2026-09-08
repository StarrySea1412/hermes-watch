"""Analysis engine: turns findings into root-cause cards with evidence chains.

Layer 1 (always): deterministic deep-dive probes per finding type — this is
the "agent 巡检" core and works with zero LLM.
Layer 2 (optional): LLM narration via an OpenAI-compatible endpoint. The AI
outbound switch defaults to OFF (Termix-style gating from the research).
可选脱敏（k8sgpt 式 anonymize）：主机名/IP 在出站前替换为占位符。
"""
import json
import re

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

# ---- 演示主机专属叙事（编造文案只允许出现在 mock 主机上）----
MOCK_CHAIN = {
    "disk": ["磁盘使用率越过阈值", "写入压力主要来自日志目录", "日志由 app 服务持续输出未轮转"],
    "memory": ["内存使用率越过阈值", "单一进程 RSS 占比异常", "该进程随时间线性增长，符合泄漏特征"],
    "login": ["出现非常见来源登录", "来源 IP 无历史记录", "登录后尚无破坏性命令，处于侦察阶段"],
    "service": ["systemd 报告 unit 失败", "进程异常退出", "退出码指向配置/依赖问题"],
    "cert": ["证书剩余有效期低于阈值", "需要续签并重新部署"],
    "cpu": ["CPU 持续高于阈值", "由进程级占用驱动"],
    "load": ["系统负载过高", "通常与 CPU/IO 排队相关"],
}
MOCK_ROOT_CAUSE = {
    "disk": "/var/log 下应用日志未轮转，38G 日志持续增长吃满磁盘",
    "memory": "/opt/app/worker.py 存在内存泄漏，RSS 随运行时长线性上涨至 91%",
    "login": "来源 IP 185.220.101.34（非内网段）以 root 登录成功，疑似暴力破解得手或口令泄露",
    "service": "服务异常退出，退出码指向配置错误",
}

# ---- 真实主机：只陈述规则事实，根因以深挖证据为准，绝不编造 ----
REAL_CHAIN = {
    "disk": ["磁盘使用率越过阈值（规则判定）", "深挖命令已取回真实输出（见执行轨迹）", "结合输出确认主要占用后处置"],
    "memory": ["内存使用率越过阈值（规则判定）", "ps 已定位高内存进程（见执行轨迹）", "确认泄漏特征后安排低峰重启"],
    "login": ["出现非常见来源登录（规则判定）", "last 审计已取回登录记录（见执行轨迹）", "核实授权状态后封禁来源/改密"],
    "service": ["systemd 报告 unit 失败（规则判定）", "status 详情见执行轨迹", "按退出原因修复后重启"],
    "cert": ["证书剩余有效期低于阈值（规则判定）", "续签并重新部署"],
    "cpu": ["CPU 持续高于阈值（规则判定）", "ps 定位热点进程（见执行轨迹）"],
    "load": ["系统负载过高（规则判定）", "结合 CPU/IO 排队定位"],
}


def _rule_root_cause(finding: dict) -> str:
    """真实主机的根因陈述 = 规则引擎的事实，不猜测未验证的成因。"""
    ev = db.uj(finding["evidence"], {}) or {}
    t = finding["type"]
    if t == "disk":
        return f"磁盘使用率 {ev.get('disk', 0):.0f}% 越过阈值，主要占用来源以深挖输出为准"
    if t == "memory":
        return f"内存使用率 {ev.get('mem', 0):.0f}% 越过阈值，占用大头以深挖输出为准"
    if t == "cpu":
        return f"CPU 使用率 {ev.get('cpu', 0):.0f}% 越过阈值，热点进程以深挖输出为准"
    if t == "load":
        return f"load1 {ev.get('load1', 0):.1f} 越过阈值，通常与 CPU/IO 排队相关"
    if t == "login":
        return f"来源 IP {ev.get('ip', '?')} 以用户 {ev.get('user', '?')} 登录成功，不在常见内网段"
    if t == "service":
        return f"systemd 报告 unit {ev.get('service', '?')} 处于 failed 状态"
    if t == "cert":
        return f"证书剩余 {ev.get('days', '?')} 天，低于告警阈值"
    return "规则引擎确认指标越限，证据见执行轨迹"


def _proposal_for(host: dict, finding: dict) -> tuple[str, str, str] | None:
    """按主机类型与真实证据生成修复提案。真实主机只提有安全依据的命令：
    提案是要被执行的，命令里的 IP/服务名必须来自这台机器的证据，而不是演示剧本。"""
    t = finding["type"]
    if host.get("mock"):
        return PROPOSALS.get(t)
    ev = db.uj(finding["evidence"], {}) or {}
    if t == "disk":
        return PROPOSALS["disk"]  # journal 清理对所有 Linux 机器安全且通用
    if t == "service":
        svc = ev.get("service")
        return (f"重启失败服务 {svc}", f"systemctl restart {svc}", "恢复 failed unit") if svc else None
    if t == "login":
        ip = ev.get("ip")
        return (f"封禁来源 IP {ip} 并改密", f"ufw insert 1 deny {ip} && passwd root",
                "先封禁可疑来源，再轮换 root 凭据") if ip else None
    # memory/cpu/load/cert：真实主机上没有可安全白名单执行的一键命令，宁可不给提案
    return None

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


def _anonymize_enabled() -> bool:
    s = db.query_one("SELECT value FROM settings WHERE key='ai_anonymize'")
    return bool(s and s["value"] == "on")


# RFC 5737 文档专用段：脱敏后的公网 IP 落在这里，不会指向真实主机
_DOC_NET = "203.0.113"


def anonymize(text: str, mapping: dict[str, str]) -> str:
    """把主机名 / 主机地址 / 任意 IPv4 替换为稳定占位符（同一次调用内映射一致，
    模型可跨句引用同一实体）。映射表只存在于本次调用的内存里，绝不落库。
    单趟正则替换：占位符本身形似 IP，但 re.sub 不回扫已替换文本，杜绝二次脱敏。"""
    for h in db.query("SELECT id, name, hostname FROM hosts"):
        if h["name"]:
            mapping.setdefault(h["name"], f"主机-{h['id']}")
        addr = h["hostname"] or ""
        if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", addr):
            mapping.setdefault(addr, f"10.9.0.{h['id'] % 250 + 1}")
        elif addr:
            mapping.setdefault(addr, f"srv-{h['id']}.local")
    mapping.setdefault("root", "用户-A")
    mapping.setdefault("admin", "用户-B")

    def _sub(m: re.Match) -> str:
        s = m.group(0)
        if s in mapping:
            return mapping[s]
        if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", s):
            placeholder = (f"10.9.1.{len(mapping) % 250 + 1}"
                           if s.startswith(("10.", "192.168.", "172.", "127."))
                           else f"{_DOC_NET}.{len(mapping) % 250 + 1}")
            mapping[s] = placeholder
            return placeholder
        return s

    alternation = "|".join(re.escape(k) for k in mapping)
    return re.sub(alternation + r"|\b\d{1,3}(?:\.\d{1,3}){3}\b", _sub, text)


def _maybe_anonymize(text: str, mapping: dict[str, str]) -> str:
    return anonymize(text, mapping) if _anonymize_enabled() else text


def build_fleet_context(mapping: dict[str, str] | None = None) -> str:
    """Compact text snapshot of the fleet for grounding LLM answers.
    mapping 传入时执行脱敏（k8sgpt 式 anonymize）。"""
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
    text = "\n".join(lines)
    return _maybe_anonymize(text, mapping) if mapping is not None else text


async def chat_answer(question: str, history: list[dict] | None = None) -> dict:
    """LLM chat grounded in fleet data. Falls back to a deterministic answer
    when AI is disabled/unconfigured — the UI must never be a dead end.
    history = 前端带来的多轮对话（[{role:'user'|'assistant', content}...]），最多取最近 10 条。
    脱敏开关开启时，快照与提问出站前都做 anonymize，回答再映射回真实名。"""
    mapping: dict[str, str] = {}
    conf = _ai_conf()
    if _llm_enabled() and conf.get("base_url"):
        msgs = [{"role": "system", "content": (
            "你是服务器巡检平台 Hermes Watch 的助手。以下是当前 fleet 快照，"
            "回答必须只基于这些数据，不要编造。用简洁中文，必要时给出具体建议。\n\n"
            + build_fleet_context(mapping))}]
        for h in (history or [])[-10:]:
            role, content = h.get("role"), str(h.get("content", ""))[:2000]
            if role in ("user", "assistant") and content.strip():
                msgs.append({"role": role, "content": _maybe_anonymize(content, mapping)})
        msgs.append({"role": "user", "content": _maybe_anonymize(question, mapping)})
        try:
            async with httpx.AsyncClient(timeout=45) as cli:
                r = await cli.post(
                    f"{conf['base_url'].rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {conf.get('api_key', '')}"},
                    json={"model": conf.get("model", "gpt-4o-mini"),
                          "messages": msgs,
                          "max_tokens": 500})
                answer = r.json()["choices"][0]["message"]["content"]
                # 回答里的占位符映射回真实主机名，用户看到的仍是自己的机房
                for k, v in mapping.items():
                    answer = answer.replace(v, k)
                return {"answer": answer, "grounded": True, "source": "llm"}
        except Exception as e:
            return {"answer": f"LLM 调用失败（{type(e).__name__}），以下是本地规则引擎的确定性回答：\n\n"
                              + _fallback_answer(question),
                    "grounded": True, "source": "fallback"}
    return {"answer": _fallback_answer(question), "grounded": True, "source": "rules"}


async def chat_stream(question: str, history: list[dict] | None = None):
    """流式对话：LLM 开启且配置了 base_url 时逐 token 产出（脱敏在出站前完成）；
    否则一次性产出本地规则引擎摘要。yield 事件 dict：
    {"type":"meta","source":...} → {"type":"delta","text":...}* → {"type":"done"}"""
    conf = _ai_conf()
    mapping: dict[str, str] = {}
    if _llm_enabled() and conf.get("base_url"):
        yield {"type": "meta", "source": "llm"}
        msgs = [{"role": "system", "content": (
            "你是服务器巡检平台 Hermes Watch 的助手。以下是当前 fleet 快照，"
            "回答必须只基于这些数据，不要编造。用简洁中文，必要时给出具体建议。\n\n"
            + build_fleet_context(mapping))}]
        for h in (history or [])[-10:]:
            role, content = h.get("role"), str(h.get("content", ""))[:2000]
            if role in ("user", "assistant") and content.strip():
                msgs.append({"role": role, "content": _maybe_anonymize(content, mapping)})
        msgs.append({"role": "user", "content": _maybe_anonymize(question, mapping)})
        try:
            # API Key 可空（Ollama 等本地端点）：空 key 绝不发 Bearer 头（httpx 拒绝空凭证）
            headers = {"Authorization": f"Bearer {conf['api_key']}"} if conf.get("api_key") else {}
            async with httpx.AsyncClient(timeout=60) as cli:
                async with cli.stream(
                    "POST", f"{conf['base_url'].rstrip('/')}/chat/completions",
                    headers=headers,
                    json={"model": conf.get("model", "gpt-4o-mini"),
                          "messages": msgs, "max_tokens": 500, "stream": True}) as r:
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        chunk = line[5:].strip()
                        if chunk == "[DONE]":
                            break
                        try:
                            delta = json.loads(chunk)["choices"][0]["delta"].get("content")
                        except Exception:
                            continue
                        if delta:
                            yield {"type": "delta", "text": delta}
            yield {"type": "done"}
        except Exception as e:
            text = f"\n\n（LLM 流式调用失败：{type(e).__name__}，以下为本地规则引擎回答）\n\n" \
                   + _fallback_answer(question)
            yield {"type": "delta", "text": text}
            yield {"type": "done"}
        return
    yield {"type": "meta", "source": "rules"}
    yield {"type": "delta", "text": _fallback_answer(question)}
    yield {"type": "done"}


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
    脱敏开启时，提示词中的主机名/IP 出站前替换为占位符，回答再映射回真实名。
    返回 (叙事文本, 模型名)；失败抛异常由调用方留痕，绝不影响规则引擎结论。"""
    conf = _ai_conf()
    if not conf.get("base_url"):
        raise RuntimeError("未配置 Base URL")
    model = conf.get("model", "gpt-4o-mini")
    mapping: dict[str, str] = {}
    prompt = _maybe_anonymize(
        f"服务器 {host['name']} 出现问题: {finding['title']}。{finding['detail']}\n"
        f"排查输出:\n{evidence_text}\n"
        "用不超过 3 句中文说明根因，只基于以上输出，不要编造。", mapping)
    async with httpx.AsyncClient(timeout=30) as cli:
        headers = {"Authorization": f"Bearer {conf['api_key']}"} if conf.get("api_key") else {}
        r = await cli.post(
            f"{conf['base_url'].rstrip('/')}/chat/completions",
            headers=headers,
            json={"model": model, "messages": [{"role": "user", "content": prompt}]})
        text = r.json()["choices"][0]["message"]["content"]
        for k, v in mapping.items():
            text = text.replace(v, k)
        return text, model


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

    is_mock = host.get("mock")
    chain = (MOCK_CHAIN if is_mock else REAL_CHAIN).get(finding["type"], ["指标越过阈值"])
    confidence = "高" if steps else "中"

    proposal_id = None
    prop = _proposal_for(host, finding)
    if prop:
        title, command, rationale = prop
        proposal_id = db.execute(
            "INSERT INTO proposals(ts,host_id,finding_id,title,command,rationale) "
            "VALUES(?,?,?,?,?,?)",
            (db.now(), host["id"], finding["id"], title, command, rationale))

    # 规则引擎结论永远为准；AI 叙事仅作为附加视角单列，绝不覆盖 root_cause
    if is_mock:
        root_cause = MOCK_ROOT_CAUSE.get(finding["type"], "规则引擎确认指标越限，证据见左侧执行轨迹")
    else:
        root_cause = _rule_root_cause(finding)

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
    db.execute("UPDATE findings SET card=?, ok_streak=0, status='analyzed' WHERE id=?",
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
    poll = 60
    try:
        r = db.query_one("SELECT value FROM settings WHERE key='poll_seconds'")
        poll = max(5, int(float(r["value"]))) if r else 60
    except (TypeError, ValueError):
        pass
    # 超过 3 个巡检周期无成功采集 → 离线（agent push 主机同样适用）
    offline_after = poll * 3
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
        last_ok = h["last_ok_ts"] or 0
        if last_ok:
            online = (db.now() - last_ok) < offline_after
        else:  # 从未成功采集：有采集错误记为离线，全新主机（如刚播种）不算离线
            online = not (h["last_error"] or "")
        # 卡片状态跟随最严重发现：crit 发现即使分数未跌破也标红，避免"99% 磁盘显示警告"的矛盾
        status = "crit" if worst == "crit" else rules.status_of(score)
        if not online:
            status = "offline"
        out.append({
            "id": h["id"], "name": h["name"], "hostname": h["hostname"],
            "group": h["group_name"], "mock": bool(h["mock"]),
            "score": score, "status": status,
            "online": online, "last_ok_ts": last_ok,
            "last_error": h["last_error"] or "",
            "latest": dict(latest) if latest else None,
            "spark": list(reversed([dict(x) for x in spark])),
            "open_findings": len(findings),
            "worst": worst,
        })
    return {"hosts": out,
            "generated_at": db.now()}
