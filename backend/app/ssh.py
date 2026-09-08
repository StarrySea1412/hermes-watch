"""统一 SSH 连接入口：TOFU 主机指纹校验（trust-on-first-use）。

三个调用方（采集 / 执行 / 终端）共用 connect()，行为一致：
- 首次连接：记录服务器公钥指纹到 hosts.host_key_fp，正常放行
- 后续连接：指纹一致放行；不一致 = 可能中间人/重装系统 → 抛 HostKeyChanged，
  由调用方决定降级（记事件、断终端等），绝不静默连上
- 库里没这台主机（token 会话等边缘态）跳过校验
"""
import asyncio

import asyncssh

from . import db


class HostKeyChanged(RuntimeError):
    def __init__(self, hostname: str, old: str, new: str):
        super().__init__(f"{hostname} 主机指纹已变更（原 {old[:20]}… → 新 {new[:20]}…）")
        self.old, self.new = old, new


def _fp(key) -> str:
    return key.get_fingerprint("sha256")


class _TofuClient(asyncssh.SSHClient):
    """TOFU 校验：在连接完成时比对服务器公钥指纹。"""

    def __init__(self, host_row: dict):
        self._host = host_row

    def validate_host_public_key(self, host: str, addr: str, port: int, key) -> bool:
        fp = _fp(key)
        expected = (self._host or {}).get("host_key_fp") or ""
        if not expected:  # 首次见到该主机 → 记录指纹（TOFU 信任）
            hid = (self._host or {}).get("id")
            if hid:
                db.execute("UPDATE hosts SET host_key_fp=? WHERE id=?", (fp, hid))
            return True
        if expected != fp:
            raise HostKeyChanged((self._host or {}).get("hostname", host), expected, fp)
        return True


def connect(host: dict, **kw) -> asyncssh.SSHClientConnection:
    """按主机行建立 SSH 连接（密码留空 = 本机默认密钥），统一 TOFU。"""
    from . import secrets as sec
    secret = sec.decrypt(host.get("secret"))
    return asyncssh.connect(
        host["hostname"], port=host["port"] or 22,
        username=host["username"] or "root",
        client_keys=sec.default_client_keys() or None,  # 密码留空 = 密钥登录
        password=secret or None,
        known_hosts=None,  # 指纹校验由 _TofuClient 接管
        client_factory=lambda: _TofuClient(host),
        **kw)


async def connect_async(host: dict, timeout: float = 10, **kw) -> asyncssh.SSHClientConnection:
    return await asyncio.wait_for(connect(host, **kw), timeout)
