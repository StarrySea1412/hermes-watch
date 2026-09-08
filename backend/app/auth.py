"""面板访问控制：PBKDF2 口令哈希 + HMAC 签名会话 Cookie。

- 口令存 settings.panel_password（pbkdf2$迭代$salt$hash），从不明文落库
- 会话 = 过期时间戳 + HMAC(key, exp)，key 从 .secret_key 派生——与 SSH 凭据加密同一把主密钥
- 登录失败限速：单 IP 每分钟 10 次后 429
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
_FAILS: dict[str, list[float]] = {}


def _hmac_key() -> bytes:
    raw = KEY_PATH.read_bytes().strip() if KEY_PATH.exists() else b"hermes-watch-dev"
    return hashlib.sha256(raw + b"panel-session").digest()


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


def make_session() -> str:
    exp = int(time.time()) + TTL
    sig = hmac.new(_hmac_key(), str(exp).encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def verify_session(value: str) -> bool:
    if not value or "." not in value:
        return False
    exp, _, sig = value.rpartition(".")
    if not exp.isdigit():
        return False
    want = hmac.new(_hmac_key(), exp.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, want):
        return False
    return int(exp) > time.time()


def too_many_fails(ip: str) -> bool:
    now = time.time()
    wins = [t for t in _FAILS.get(ip, []) if now - t < 60]
    _FAILS[ip] = wins
    return len(wins) >= 10


def record_fail(ip: str):
    _FAILS.setdefault(ip, []).append(time.time())
