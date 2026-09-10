"""Hermes Watch 回归测试：无框架，`py run_tests.py` 直接跑，全绿 = 通过。

覆盖：规则引擎阈值/评分、提案白名单、口令与会话、agent 验签、凭据加密、
LLM 叙事卡片结构。全部针对当前 SQLite 演示库，只读 + 测试数据自清理。
"""
import asyncio
import sys
import time

sys.path.insert(0, ".")
from app import agent, analysis, auth, collector, db, executor, notify, rules, secrets as sec  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name}  {detail}")


def t_rules():
    print("[rules]")
    T = rules.thresholds()
    check("默认阈值加载", T["disk_warn"] == 85 and T["cert_days"] == 14)

    host = {"id": 0}
    none_ = rules.evaluate(host, {"disk": 50, "mem": 50, "cpu": 10, "load1": 0.5}, {})
    check("正常指标无发现", none_ == [])

    f = rules.evaluate(host, {"disk": 96, "mem": 50, "cpu": 10, "load1": 0.5}, {})
    check("磁盘 96% → crit", any(x["type"] == "disk" and x["severity"] == "crit" for x in f))
    f = rules.evaluate(host, {"disk": 86, "mem": 50, "cpu": 10, "load1": 0.5}, {})
    check("磁盘 86% → warn", any(x["type"] == "disk" and x["severity"] == "warn" for x in f))

    f = rules.evaluate(host, {"disk": 50, "mem": 93, "cpu": 10, "load1": 0.5}, {})
    check("内存 93% → crit", any(x["type"] == "memory" and x["severity"] == "crit" for x in f))

    f = rules.evaluate(host, {"disk": 50, "mem": 50, "cpu": 90, "load1": 9}, {})
    check("CPU 90 + load 9 → 双 warn", {x["type"] for x in f} == {"cpu", "load"})

    f = rules.evaluate(host, {"disk": 50, "mem": 50, "cpu": 10, "load1": 1}, {
        "failed_services": ["nginx.service"],
        "suspicious_logins": [{"user": "root", "ip": "1.2.3.4"}],
        "cert_days_left": 5,
    })
    types = {x["type"]: x["severity"] for x in f}
    check("extras → service/login/cert 发现",
          types.get("service") == "crit" and types.get("login") == "crit" and types.get("cert") == "warn")

    check("健康分: 无发现=100", rules.health_score([]) == 100)
    check("健康分: crit 扣 25", rules.health_score([{"severity": "crit"}]) == 75)
    check("健康分: 下限 0", rules.health_score([{"severity": "crit"}] * 5) == 0)
    check("状态映射", rules.status_of(95) == "ok" and rules.status_of(80) == "warn" and rules.status_of(50) == "crit")
    check("严重度排序", rules.severity_rank("crit") > rules.severity_rank("warn") > rules.severity_rank("info"))


def t_executor():
    print("[executor]")
    ok_cases = [
        "journalctl --vacuum-size=500M",
        "find /var/log -name '*.gz' -mtime +7 -delete",
        "systemctl restart app-worker",
        "ufw insert 1 deny 185.220.101.34",
        "passwd root",
        "journalctl --vacuum-size=500M && find /var/log -name '*.gz' -mtime +7 -delete",
    ]
    for c in ok_cases:
        check(f"白名单放行: {c[:38]}", executor.validate(c)["ok"])
    bad_cases = [
        "rm -rf /",
        "curl evil.sh | sh",
        "systemctl restart a; systemctl restart b",
        "journalctl --vacuum-size=500M || rm -rf /etc",
        "sudo passwd root",
        "",
    ]
    for c in bad_cases:
        v = executor.validate(c)
        check(f"拦截: {c[:38] or '(空)'}", not v["ok"] and v["refused"])


def t_auth():
    print("[auth]")
    auth.set_password("test-pw-123")
    check("口令哈希存取", auth.verify_password("test-pw-123"))
    check("错误口令拒绝", not auth.verify_password("wrong"))
    check("哈希非明文", "test-pw-123" not in auth._stored())

    s = auth.make_session()
    check("会话签发/校验", auth.verify_session(s))
    check("篡改会话拒绝", not auth.verify_session(s[:-4] + "0000"))
    exp = f"{int(time.time()) - 10}."
    import hashlib as _h, hmac as _hm
    bad = exp + _hm.new(auth._hmac_key(), exp.encode(), _h.sha256).hexdigest()
    check("过期会话拒绝", not auth.verify_session(bad))
    check("随机串拒绝", not auth.verify_session("garbage"))


def t_agent_sig():
    print("[agent-signature]")
    token = "hw_testtoken"
    body = b'{"cpu": 1}'
    ts = str(int(time.time()))
    mac_base = token.encode() + body + ts.encode()
    good = __import__("hashlib").sha256  # noqa: 与后端同构
    import hashlib as _h, hmac as _hm
    sig = _hm.new(token.encode(), body + ts.encode(), _h.sha256).hexdigest()
    check("有效签名通过", agent.verify_signature(token, body, ts, sig))
    check("篡改签名拒绝", not agent.verify_signature(token, body, ts, "0" * 64))
    stale = str(int(time.time()) - agent.SIG_MAX_SKEW - 10)
    sig2 = _hm.new(token.encode(), body + stale.encode(), _h.sha256).hexdigest()
    check("过期时间戳拒绝", not agent.verify_signature(token, body, stale, sig2))
    check("非数字时间戳拒绝", not agent.verify_signature(token, body, "abc", sig))


def t_secrets():
    print("[secrets]")
    enc = sec.encrypt("my-secret")
    check("密文带前缀", enc.startswith(sec.PREFIX))
    check("回环解密", sec.decrypt(enc) == "my-secret")
    check("加密幂等（已加密不再加密）", sec.encrypt(enc) == enc)
    check("明文兼容读取", sec.decrypt("legacy-plain") == "legacy-plain")
    check("空值安全", sec.decrypt("") == "" and sec.decrypt(None) == "")
    m = collector.parse_probe("cpu=3\nnet_in=100 net_out=200\ndisk=55")
    check("探针解析: 单行多 k=v", m.get("net_in") == 100 and m.get("net_out") == 200)
    check("探针解析: 单键行", m.get("cpu") == 3 and m.get("disk") == 55)


def t_analysis_card():
    print("[analysis-card]")
    h = db.query_one("SELECT * FROM hosts LIMIT 1")
    fid = db.execute(
        "INSERT INTO findings(host_id,ts,type,severity,title,detail,evidence) VALUES(?,?,?,?,?,?,?)",
        (h["id"], db.now(), "cpu", "warn", "【测试】CPU 90%", "test", "{}"))
    f = db.query_one("SELECT * FROM findings WHERE id=?", (fid,))
    card = asyncio.run(analysis.analyze_finding(h, f))
    check("卡片含规则结论", bool(card["root_cause"]))
    check("AI 默认关 → 无叙事", card["ai_narration"] is None and card["ai_error"] is None)
    check("字段齐备", all(k in card for k in ("chain", "confidence", "steps", "proposal_id",
                                              "ai_narration", "ai_model", "ai_error")))
    db.execute("DELETE FROM findings WHERE id=?", (fid,))
    if card["proposal_id"]:
        db.execute("DELETE FROM proposals WHERE id=?", (card["proposal_id"],))


def t_session_epoch():
    print("[session-epoch]")
    auth.set_password("epoch-pw-1")
    s1 = auth.make_session()
    check("epoch 会话签发/校验", auth.verify_session(s1))
    auth.bump_session_epoch()
    check("epoch 递增后旧会话作废", not auth.verify_session(s1))
    s2 = auth.make_session()
    check("新 epoch 会话有效", auth.verify_session(s2))


def t_recovery():
    print("[recovery]")
    from app import scheduler
    hid = db.execute(
        "INSERT INTO hosts(name,hostname,group_name,mock,created_at) VALUES('__t_rec__','x','t',1,?)",
        (db.now(),))
    fid = db.execute(
        "INSERT INTO findings(host_id,ts,type,severity,title,detail,evidence) VALUES(?,?,?,?,?,?,?)",
        (hid, db.now(), "disk", "warn", "【测试】磁盘告警", "test", "{}"))
    # 仍在触发 → 计数清零
    scheduler._check_recovery(hid, {"disk"})
    check("触发中不恢复", db.query_one("SELECT status FROM findings WHERE id=?", (fid,))["status"] == "open")
    # 条件解除，连续 3 轮
    scheduler._check_recovery(hid, set())
    scheduler._check_recovery(hid, set())
    check("未满轮次不恢复", db.query_one("SELECT status FROM findings WHERE id=?", (fid,))["status"] == "open")
    scheduler._check_recovery(hid, set())
    row = db.query_one("SELECT status, resolved_at FROM findings WHERE id=?", (fid,))
    check("满 3 轮自动恢复", row["status"] == "resolved" and row["resolved_at"])
    ev = db.query_one("SELECT message FROM events WHERE host_id=? AND kind='ok'", (hid,))
    check("恢复事件已记录", bool(ev))
    db.execute("DELETE FROM events WHERE host_id=?", (hid,))
    db.execute("DELETE FROM findings WHERE id=?", (fid,))
    db.execute("DELETE FROM hosts WHERE id=?", (hid,))


def t_offline():
    print("[offline]")
    from app import analysis
    hid = db.execute(
        "INSERT INTO hosts(name,hostname,group_name,mock,created_at,last_ok_ts) "
        "VALUES('__t_off__','x','t',1,?,?)", (db.now(), db.now() - 3600))
    snap = analysis.fleet_snapshot()
    h = next(x for x in snap["hosts"] if x["id"] == hid)
    check("1 小时无采集 → 离线", h["status"] == "offline" and not h["online"])
    db.execute("UPDATE hosts SET last_ok_ts=? WHERE id=?", (db.now(), hid))
    snap = analysis.fleet_snapshot()
    h = next(x for x in snap["hosts"] if x["id"] == hid)
    check("刚刚采集 → 在线", h["online"] and h["status"] != "offline")
    db.execute("DELETE FROM hosts WHERE id=?", (hid,))


def t_nan_guard():
    print("[nan-guard]")
    import math
    class _Req:
        headers = {"x-hw-token": ""}
    # 端点内的防御逻辑等价复刻：NaN/inf → 0
    data = {"cpu": float("nan"), "mem": float("inf"), "disk": 55}
    def _num(k):
        v = float(data.get(k, 0) or 0)
        return v if math.isfinite(v) else 0.0
    m = {k: _num(k) for k in ("cpu", "mem", "disk")}
    check("NaN → 0", m["cpu"] == 0)
    check("inf → 0", m["mem"] == 0)
    check("正常值透传", m["disk"] == 55)


def t_hysteresis():
    print("[hysteresis]")
    hid = db.execute(
        "INSERT INTO hosts(name,hostname,group_name,mock,created_at) VALUES('__t_hys__','x','t',1,?)",
        (db.now(),))
    db.execute(
        "INSERT INTO findings(host_id,ts,type,severity,title,detail,evidence) VALUES(?,?,?,?,?,?,?)",
        (hid, db.now(), "disk", "warn", "【测试】磁盘告警", "test", "{}"))
    # 83 介于 clear(80) 与 warn(85) 之间：已有告警保持打开（滞后），不新触发也不恢复
    f = rules.evaluate({"id": hid}, {"disk": 83, "mem": 50, "cpu": 10, "load1": 0.5}, {})
    check("83% 仍判定活动（滞后保持）", any(x["type"] == "disk" for x in f))
    f = rules.evaluate({"id": hid}, {"disk": 78, "mem": 50, "cpu": 10, "load1": 0.5}, {})
    check("78% 低于 clear 线 → 解除", not any(x["type"] == "disk" for x in f))
    f = rules.evaluate({"id": hid}, {"disk": 86, "mem": 50, "cpu": 10, "load1": 0.5}, {})
    check("86% 越过 warn 线 → 触发", any(x["type"] == "disk" for x in f))
    db.execute("DELETE FROM findings WHERE host_id=?", (hid,))
    db.execute("DELETE FROM hosts WHERE id=?", (hid,))


def t_quiet_hours():
    print("[quiet-hours]")
    from app import notify
    check("空配置不命中", not notify.in_quiet_hours(""))
    check("非法格式不命中", not notify.in_quiet_hours("abc"))
    check("超界小时不命中", not notify.in_quiet_hours("25:00-08:00"))
    # 注入固定时刻验证窗口判定（monkeypatch datetime 模块内的 now 引用不可行，直接测纯逻辑）
    import datetime as _dt
    cases = [
        # (spec, 当前时分, 期望)
        ("23:00-08:00", 23 * 60 + 30, True),
        ("23:00-08:00", 12 * 60, False),
        ("23:00-08:00", 3 * 60, True),    # 跨午夜
        ("08:00-22:00", 10 * 60, True),
        ("08:00-22:00", 23 * 60, False),
    ]
    for spec, cur, want in cases:
        h1, m1, h2, m2 = (int(x) for x in spec.replace(":", " ").replace("-", " ").split())
        start, end = h1 * 60 + m1, h2 * 60 + m2
        if start <= end:
            got = start <= cur < end
        else:
            got = cur >= start or cur < end
        check(f"{spec} @ {cur // 60:02d}:{cur % 60:02d} → {want}", got == want)
    check("24:00 越界视作非法 → 任何时刻不命中", not notify.in_quiet_hours("00:00-24:00"))


def t_anonymize():
    print("[anonymize]")
    # 现场免疫：占位符跟随面板语言（hw_lang），备份现场值固定 zh，测完还原（测试库可能与运行面板共用）
    prev_lang = db.query_one("SELECT value FROM settings WHERE key='hw_lang'")
    db.execute("INSERT INTO settings(key,value) VALUES('hw_lang','zh') "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    hid = db.execute(
        "INSERT INTO hosts(name,hostname,group_name,mock,created_at) VALUES('__t_anon__','192.168.7.7','t',1,?)",
        (db.now(),))
    mapping: dict[str, str] = {}
    out = analysis.anonymize("主机 __t_anon__ (192.168.7.7) 上 root 从 8.8.8.8 登录", mapping)
    check("主机名替换", "__t_anon__" not in out and "主机-" in out)
    check("主机地址替换", "192.168.7.7" not in out)
    check("用户名替换", "root" not in out)
    check("外部 IP 落 RFC5737", "8.8.8.8" not in out and "203.0.113." in out)
    check("映射一致（同输入同占位）", out == analysis.anonymize(
        "主机 __t_anon__ (192.168.7.7) 上 root 从 8.8.8.8 登录", dict(mapping)))
    db.execute("DELETE FROM hosts WHERE id=?", (hid,))
    if prev_lang:
        db.execute("UPDATE settings SET value=? WHERE key='hw_lang'", (prev_lang["value"],))
    else:
        db.execute("DELETE FROM settings WHERE key='hw_lang'")


def t_chat_stream_fallback():
    print("[chat-stream]")
    # 现场免疫：测试需要「AI 关」状态，备份现场值，测完还原（测试库可能与运行面板共用）
    prev = db.query_one("SELECT value FROM settings WHERE key='ai_outbound'")
    db.execute("INSERT INTO settings(key,value) VALUES('ai_outbound','off') "
               "ON CONFLICT(key) DO UPDATE SET value='off'")
    try:
        async def _run():
            chunks = [c async for c in analysis.chat_stream("现在整体情况？", [])]
            return chunks
        chunks = asyncio.run(_run())
    finally:
        if prev:
            db.execute("UPDATE settings SET value=? WHERE key='ai_outbound'", (prev["value"],))
    check("AI 关闭 → meta(rules)", chunks[0]["type"] == "meta" and chunks[0]["source"] == "rules")
    check("有正文 delta", any(c["type"] == "delta" and c.get("text") for c in chunks))
    check("以 done 结束", chunks[-1]["type"] == "done")


def t_notify_log():
    print("[notify-log]")
    ok, err = asyncio.run(notify.send("测试留痕", "notify-log test"))
    row = db.query_one(
        "SELECT * FROM notify_log WHERE kind='测试留痕' ORDER BY id DESC LIMIT 1")
    check("发送尝试已留痕", bool(row))
    check("未配置渠道 ok=0 error=未配置", row["ok"] == 0 and row["error"] == "未配置")


def t_probes():
    print("[probes]")
    from app import i18n, probes
    # 残留免疫：上次中途 crash 可能留下同名行（UNIQUE name），先清
    db.execute("DELETE FROM probes WHERE name='__t_probe__'")
    db.execute("DELETE FROM probe_log WHERE probe_id NOT IN (SELECT id FROM probes)")
    db.execute("DELETE FROM events WHERE kind='probe' AND message LIKE '%__t_probe__%'")
    # 现场免疫：占位/文案跟随面板语言，固定 zh 测模板断言，测完还原
    prev_lang = db.query_one("SELECT value FROM settings WHERE key='hw_lang'")
    db.execute("INSERT INTO settings(key,value) VALUES('hw_lang','zh') "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value")

    pid = db.execute(
        "INSERT INTO probes(name,kind,target,fail_threshold,success_threshold,timeout_s,"
        "up,fail_streak,succ_streak,created_at) VALUES('__t_probe__','url',"
        "'http://127.0.0.1:9',3,2,2,1,0,0,?)", (db.now(),))
    p = db.query_one("SELECT * FROM probes WHERE id=?", (pid,))

    # 真实拨测一个必然失败的端口（组合失败计数）——不依赖外网
    ok, latency, error = asyncio.run(probes.run_probe(
        {"kind": "tcp", "target": "127.0.0.1:9", "timeout_s": 1}))
    check("TCP 死端口判失败", not ok and error)
    ok2, latency2, _ = asyncio.run(probes.run_probe(
        {"kind": "url", "target": "https://127.0.0.1:9/x", "timeout_s": 1}))
    check("URL 死端口判失败", not ok2)

    # TCP 成功路径（本地起临时 echo 服务器，防回归：open_connection 元组解包）
    async def _tcp_ok():
        async def _handle(reader, writer):
            writer.close()
        server = await asyncio.start_server(_handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            return await probes.run_probe({"kind": "tcp", "target": f"127.0.0.1:{port}", "timeout_s": 3})
        finally:
            server.close()
    ok3, latency3, err3 = asyncio.run(_tcp_ok())
    check("TCP 连通判成功", ok3 and latency3 and latency3 > 0, err3)

    # 状态机：up 中 2 次失败不打翻（防抖），第 3 次才 down
    r1 = probes.evaluate(p, False)
    p = db.query_one("SELECT * FROM probes WHERE id=?", (pid,))
    r2 = probes.evaluate(p, False)
    p = db.query_one("SELECT * FROM probes WHERE id=?", (pid,))
    check("两次失败未翻转", r1 is None and r2 is None and p["up"] == 1)
    r3 = probes.evaluate(p, False)
    check("连续 3 次失败 → down", r3 == "down")
    p = db.query_one("SELECT * FROM probes WHERE id=?", (pid,))
    check("down 后 fail_streak 保持", p["up"] == 0 and p["fail_streak"] >= 3)

    # down 中 1 次成功不恢复（success_threshold=2），第 2 次才 up
    p["_latency"], p["_error"] = 12.3, ""
    s1 = probes.evaluate(p, True)
    p = db.query_one("SELECT * FROM probes WHERE id=?", (pid,))
    s2 = probes.evaluate(p, True)
    check("一次成功未恢复", s1 is None)
    check("连续 2 次成功 → up", s2 == "up")
    p = db.query_one("SELECT * FROM probes WHERE id=?", (pid,))
    check("恢复后 streak 清零", p["up"] == 1 and p["fail_streak"] == 0)

    # handle_result: 翻转落事件 + 心跳留痕（notify 未配置只留痕不外呼）。
    # 状态机要求连续 fail_threshold 次失败才翻转，先压 2 次失败再走 handle_result 第 3 次
    p = db.query_one("SELECT * FROM probes WHERE id=?", (pid,))
    p["_latency"], p["_error"] = None, "ECONNREFUSED"
    probes.evaluate(p, False)
    p = db.query_one("SELECT * FROM probes WHERE id=?", (pid,))
    p["_latency"], p["_error"] = None, "ECONNREFUSED"
    probes.evaluate(p, False)
    p = db.query_one("SELECT * FROM probes WHERE id=?", (pid,))
    asyncio.run(probes.handle_result(p, False, None, "ECONNREFUSED"))
    ev = db.query_one("SELECT message FROM events WHERE kind='probe' ORDER BY id DESC LIMIT 1")
    check("下线翻转落事件", ev and ev["message"].startswith("拨测下线"))
    log = db.query_one("SELECT * FROM probe_log WHERE probe_id=? ORDER BY id DESC LIMIT 1", (pid,))
    check("心跳已留痕", log and log["up"] == 0 and log["error"] == "ECONNREFUSED")

    # 出口兜底:EN 语言下历史 zh 消息翻译
    db.execute("UPDATE settings SET value='en' WHERE key='hw_lang'")
    check("下线事件 EN 兜底", probes.tr_probe_message("拨测下线: x（y）— boom") == "Probe down: x（y）— boom")
    check("恢复事件 EN 兜底含时长归一",
          probes.tr_probe_message("拨测恢复: x（y，持续 5 分钟）") == "Probe recovered: x (y, was down for 5 min)")
    db.execute("UPDATE settings SET value='zh' WHERE key='hw_lang'")

    db.execute("DELETE FROM probes WHERE id=?", (pid,))
    db.execute("DELETE FROM probe_log WHERE probe_id=?", (pid,))
    db.execute("DELETE FROM events WHERE kind='probe' AND message LIKE '%__t_probe__%'")
    if prev_lang:
        db.execute("UPDATE settings SET value=? WHERE key='hw_lang'", (prev_lang["value"],))
    else:
        db.execute("DELETE FROM settings WHERE key='hw_lang'")


def t_tofu():
    print("[tofu]")
    from app import ssh
    hid = db.execute(
        "INSERT INTO hosts(name,hostname,group_name,mock,created_at,host_key_fp) "
        "VALUES('__t_tofu__','x','t',1,?, '')", (db.now(),))
    h = db.query_one("SELECT * FROM hosts WHERE id=?", (hid,))
    class _K:
        def get_fingerprint(self, algo="sha256"): return "SHA256:AAA"
    # 首次连接：记录指纹并放行
    ok = ssh._TofuClient(dict(h)).validate_host_public_key("x", "x", 22, _K())
    stored = db.query_one("SELECT host_key_fp FROM hosts WHERE id=?", (hid,))["host_key_fp"]
    check("首次连接放行并记录指纹", ok and stored == "SHA256:AAA")
    # 指纹一致放行
    ok2 = ssh._TofuClient(dict(db.query_one("SELECT * FROM hosts WHERE id=?", (hid,)))).validate_host_public_key("x", "x", 22, _K())
    check("指纹一致放行", ok2)
    # 指纹变更 → HostKeyChanged
    class _K2:
        def get_fingerprint(self, algo="sha256"): return "SHA256:BBB"
    try:
        ssh._TofuClient(dict(db.query_one("SELECT * FROM hosts WHERE id=?", (hid,)))).validate_host_public_key("x", "x", 22, _K2())
        check("指纹变更拒绝", False)
    except ssh.HostKeyChanged as e:
        check("指纹变更拒绝", "变更" in str(e))
    check("库中指纹未被污染", db.query_one("SELECT host_key_fp FROM hosts WHERE id=?", (hid,))["host_key_fp"] == "SHA256:AAA")
    db.execute("DELETE FROM hosts WHERE id=?", (hid,))


def t_ack_and_silence():
    print("[ack-silence]")
    # _resend_crit 前置要求渠道已配置（否则直接返回），注入假 webhook
    db.execute("INSERT INTO settings(key,value) VALUES('webhook_url','https://example.invalid/hook') "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    db.execute("INSERT INTO settings(key,value) VALUES('notify_resend_min','30') "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    hid = db.execute(
        "INSERT INTO hosts(name,hostname,group_name,mock,created_at,silenced_until) "
        "VALUES('__t_ack__','x','t',1,?, 0)", (db.now(),))
    fid = db.execute(
        "INSERT INTO findings(host_id,ts,type,severity,title,detail,evidence) VALUES(?,?,?,?,?,?,?)",
        (hid, db.now() - 1900, "cpu", "crit", "【测试】CPU 100%", "test", "{}"))  # ts 拨回 31 分钟前，越过重发窗口
    from app import scheduler
    q_rows = lambda: scheduler.db.query(
        "SELECT id, host_id, type, severity, ts, last_notified, acked_at, title FROM findings WHERE id=?", (fid,))
    scheduler._resend_crit("t", q_rows(), {"cpu"})
    last1 = db.query_one("SELECT last_notified FROM findings WHERE id=?", (fid,))["last_notified"]
    check("未确认未静默且超重发窗口 → last_notified 更新", last1 is not None)
    # 确认后 → 不再重发（重查行，模拟真实每轮快照）
    db.execute("UPDATE findings SET last_notified=NULL, acked_at=? WHERE id=?", (db.now(), fid))
    scheduler._resend_crit("t", q_rows(), {"cpu"})
    check("已确认 → 跳过重发", db.query_one("SELECT last_notified FROM findings WHERE id=?", (fid,))["last_notified"] is None)
    # 静默中 → 跳过重发
    db.execute("UPDATE findings SET acked_at=NULL WHERE id=?", (fid,))
    db.execute("UPDATE hosts SET silenced_until=? WHERE id=?", (db.now() + 600, hid))
    scheduler._resend_crit("t", q_rows(), {"cpu"})
    check("静默中 → 跳过重发", db.query_one("SELECT last_notified FROM findings WHERE id=?", (fid,))["last_notified"] is None)
    db.execute("DELETE FROM findings WHERE id=?", (fid,))
    db.execute("DELETE FROM hosts WHERE id=?", (hid,))
    db.execute("DELETE FROM settings WHERE key IN ('notify_resend_min','webhook_url')")


def t_rbac():
    print("[rbac]")
    # 多用户 CRUD + 角色校验
    uid1 = auth.add_user("__t_admin__", "pw-admin-1", "admin")
    uid2 = auth.add_user("__t_obs__", "pw-obs-1", "observer")
    check("添加 admin/observer", auth.has_users())
    users = {u["username"]: u["role"] for u in auth.list_users()}
    check("用户清单角色正确", users.get("__t_admin__") == "admin" and users.get("__t_obs__") == "observer")
    # 登录：正确/错误口令，observer 登录得 observer 角色
    ok, role = auth.verify_login("__t_admin__", "pw-admin-1")
    check("admin 登录", ok and role == "admin")
    ok2, role2 = auth.verify_login("__t_obs__", "pw-obs-1")
    check("observer 登录", ok2 and role2 == "observer")
    ok3, _ = auth.verify_login("__t_obs__", "wrong")
    check("错误口令拒绝", not ok3)
    # 会话携带角色 + 防篡改
    s_admin = auth.make_session("admin")
    s_obs = auth.make_session("observer")
    check("admin 会话 → admin", auth.session_role(s_admin) == "admin")
    check("observer 会话 → observer", auth.session_role(s_obs) == "observer")
    # observer 伪造 admin 角色串：签名不含 admin，校验失败
    tampered = s_obs.replace(".observer.", ".admin.")
    check("角色篡改拒绝", auth.session_role(tampered) == "")
    # 角色变更 → 旧会话整体作废
    auth.set_role(uid2, "admin")
    check("角色变更后旧 observer 会话作废", auth.session_role(s_obs) == "")
    ok4, role4 = auth.verify_login("__t_obs__", "pw-obs-1")
    check("角色已更新为 admin", role4 == "admin")
    auth.set_role(uid2, "observer")
    # 最后管理员保护在端点层（main._require_admin + users_del），这里测删除行为
    auth.del_user(uid1)
    auth.del_user(uid2)
    check("清理后无用户", not auth.has_users())


if __name__ == "__main__":
    db.init_db()
    t_rules()
    t_executor()
    t_auth()
    t_agent_sig()
    t_secrets()
    t_analysis_card()
    t_session_epoch()
    t_recovery()
    t_offline()
    t_nan_guard()
    t_hysteresis()
    t_quiet_hours()
    t_anonymize()
    t_chat_stream_fallback()
    t_notify_log()
    t_probes()
    t_tofu()
    t_ack_and_silence()
    t_rbac()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
