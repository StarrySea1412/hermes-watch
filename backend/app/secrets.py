"""SSH 凭据静态加密：Fernet（cryptography 随 asyncssh 安装，零新增依赖）。

主密钥存 .secret_key（不入库、不进前端）；DB 单独泄露拿不到密码。
入库格式 enc:v1:<token>，历史明文由 init_db 的一次性迁移就地加密。
密钥位置跟随 HW_DATA_DIR（Docker 挂卷持久化），与 SQLite 同目录。
"""
import base64
import os
import pathlib

from .db import _DATA_DIR

KEY_PATH = _DATA_DIR / ".secret_key"
PREFIX = "enc:v1:"

_fernet = None


def _key() -> bytes:
    if KEY_PATH.exists():
        return KEY_PATH.read_bytes().strip()
    key = base64.urlsafe_b64encode(os.urandom(32))
    KEY_PATH.write_bytes(key)
    try:
        KEY_PATH.chmod(0o600)
    except OSError:
        pass  # Windows 文件权限模型不同，忽略
    return key


def _get_fernet():
    global _fernet
    if _fernet is None:
        from cryptography.fernet import Fernet
        _fernet = Fernet(_key())
    return _fernet


def encrypt(plain: str) -> str:
    if not plain or plain.startswith(PREFIX):
        return plain
    return PREFIX + _get_fernet().encrypt(plain.encode()).decode()


def decrypt(stored: str | None) -> str:
    if not stored:
        return ""
    if not stored.startswith(PREFIX):
        return stored  # 兼容迁移前的明文
    return _get_fernet().decrypt(stored[len(PREFIX):].encode()).decode()


def migrate_plaintext(con) -> int:
    """把 hosts 表里存量明文密码就地加密。幂等：已加密的值原样返回。"""
    rows = con.execute("SELECT id, secret FROM hosts WHERE secret != ''").fetchall()
    n = 0
    for r in rows:
        enc = encrypt(r["secret"])
        if enc != r["secret"]:
            con.execute("UPDATE hosts SET secret=? WHERE id=?", (enc, r["id"]))
            n += 1
    return n


def default_client_keys() -> list[str]:
    """本机默认 SSH 私钥路径。asyncssh 在 Windows 上不会自动发现 ~/.ssh 下的密钥，
    密码留空的主机（密钥登录）必须显式传入。"""
    import os
    return [p for p in (os.path.expanduser("~/.ssh/id_ed25519"),
                        os.path.expanduser("~/.ssh/id_rsa")) if os.path.exists(p)]
