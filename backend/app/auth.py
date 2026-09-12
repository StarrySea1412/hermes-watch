"""面板访问控制：PBKDF2 口令哈希 + HMAC 签名会话 Cookie + 多用户角色。

- users 表存多用户（username 唯一 + pbkdf2 口令 + role：admin / operator / observer）；
  兼容存量单口令模式：无用户行时 panel_password 继续生效，角色按 admin
- 会话 = 过期时间戳 + HMAC(key, epoch + exp + role)，key 从 .secret_key 派生；
  epoch 存 settings（session_epoch），改口令/开关访问控制/用户变更时 +1，
  已签发的所有旧 Cookie 立即整体失效（会话可撤销）
- 登录失败限速：单 IP 每分钟 10 次后 429（内存表有上限，防无界增长）
- 角色语义：admin 全权（改配置/主机/用户管理）；operator 值班运维（终端/
  审批/告警确认/拨测/AI 对话），不可管用户、主机、设置；observer 只读
  （GET /api 走自己的读通道；登录/登出/自己的口令修改除外）
"""
import hashlib
import hmac
import os
import time

from . import db
from .secrets import KEY_PATH

COOKIE = "hw_session"
TTL = 7 * 86400
ITER = 200_000
ROLES = ("admin", "operator", "observer")
_FAILS: dict[str, list[float]] = {}
_FAILS_CAP = 1000


def _hmac_key() -> bytes:
    raw = KEY_PATH.read_bytes().strip() if KEY_PATH.exists() else b"hermes-watch-dev"
    return hashlib.sha256(raw + b"panel-session").digest()


def _epoch() -> int:
    s = db.query_one("SELECT value FROM settings WHERE key='session_epoch'")
    try:
        return int((s or {}).get("value") or 0)
    except (TypeError, ValueError):
        return 0


def bump_session_epoch():
    """作废所有已签发会话：改口令、开关访问控制、用户增删改时调用。"""
    db.execute("INSERT INTO settings(key,value) VALUES('session_epoch',?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (str(_epoch() + 1),))


# ---------- 多用户 ----------

def _hash(pw: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, ITER)


def add_user(username: str, password: str, role: str = "observer") -> int:
    if role not in ROLES:
        raise ValueError(f"role 必须是 {ROLES}")
    if db.query_one("SELECT id FROM users WHERE username=?", (username,)):
        raise ValueError("同名用户已存在")
    salt = os.urandom(16)
    return db.execute(
        "INSERT INTO users(username,pw_hash,salt,role,created_at) VALUES(?,?,?,?,?)",
        (username, _hash(password, salt).hex(), salt.hex(), role, time.time()))


def list_users() -> list[dict]:
    return db.query("SELECT id, username, role, created_at FROM users ORDER BY id")


def set_role(uid: int, role: str) -> None:
    if role not in ROLES:
        raise ValueError(f"role 必须是 {ROLES}")
    db.execute("UPDATE users SET role=? WHERE id=?", (role, uid))
    bump_session_epoch()  # 角色变更立刻体现在会话上（会话签名携带角色）


def del_user(uid: int) -> None:
    db.execute("DELETE FROM users WHERE id=?", (uid,))
    bump_session_epoch()


def set_user_password(uid: int, password: str) -> None:
    salt = os.urandom(16)
    db.execute("UPDATE users SET pw_hash=?, salt=? WHERE id=?",
               (_hash(password, salt).hex(), salt.hex(), uid))


# ---------- 登录校验（多用户优先，回落存量单口令）----------

def verify_login(username: str, password: str) -> tuple[bool, str]:
    """返回 (ok, role)。用户表命中按行校验；否则回落 legacy 单口令（admin），
    兼容升级前部署——首个登录者仍可用原口令进入并获得 admin。"""
    row = db.query_one("SELECT * FROM users WHERE username=?", (username or "",))
    if row:
        ok = hmac.compare_digest(
            _hash(password, bytes.fromhex(row["salt"])),
            bytes.fromhex(row["pw_hash"]))
        return ok, row["role"]
    if verify_password(password):  # legacy panel_password
        return True, "admin"
    return False, ""


def has_users() -> bool:
    return bool(db.query_one("SELECT id FROM users LIMIT 1"))


def enabled() -> bool:
    s = db.query_one("SELECT value FROM settings WHERE key='panel_auth'")
    return bool(s and s["value"] == "on")


def _stored() -> str:
    s = db.query_one("SELECT value FROM settings WHERE key='panel_password'")
    return (s or {}).get("value") or ""


def set_password(pw: str):
    salt = os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, ITER)
    db.execute("INSERT INTO settings(key,value) VALUES('panel_password',?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (f"pbkdf2${ITER}${salt.hex()}${h.hex()}",))


def verify_password(pw: str) -> bool:
    parts = _stored().split("$")
    if len(parts) != 4:
        return False
    salt = bytes.fromhex(parts[2])
    h = bytes.fromhex(parts[3])
    return hmac.compare_digest(hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, int(parts[1])), h)


def make_session(role: str = "admin") -> str:
    exp = int(time.time()) + TTL
    payload = f"{_epoch()}.{exp}.{role}"
    sig = hmac.new(_hmac_key(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{role}.{sig}"


def verify_session(value: str) -> bool:
    return session_role(value) != ""


def session_role(value: str) -> str:
    """校验会话并返回角色；无效返回空串。角色写进签名防篡改。"""
    if not value or "." not in value:
        return ""
    head, _, sig = value.rpartition(".")
    exp, _, role = head.partition(".")
    if not exp.isdigit() or role not in ROLES:
        return ""
    payload = f"{_epoch()}.{exp}.{role}"
    want = hmac.new(_hmac_key(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, want):
        return ""
    if int(exp) <= time.time():
        return ""
    return role


def too_many_fails(ip: str) -> bool:
    if len(_FAILS) > _FAILS_CAP:  # 攻击者伪造大量源 IP 时丢弃最旧记录，防无界增长
        for k in list(_FAILS)[:len(_FAILS) - _FAILS_CAP // 2]:
            _FAILS.pop(k, None)
    now = time.time()
    wins = [t for t in _FAILS.get(ip, []) if now - t < 60]
    _FAILS[ip] = wins
    return len(wins) >= 10


def record_fail(ip: str):
    _FAILS.setdefault(ip, []).append(time.time())
