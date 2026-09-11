"""统一 SSH 连接入口：TOFU 主机指纹校验（trust-on-first-use）。

三个调用方（采集 / 执行 / 终端）共用 connect()，行为一致：
- 首次连接：记录服务器公钥指纹到 hosts.host_key_fp，正常放行
- 后续连接：指纹一致放行；不一致 = 可能中间人/重装系统 → 抛 HostKeyChanged，
  由调用方决定降级（记事件、断终端等），绝不静默连上
- 库里没这台主机（token 会话等边缘态）跳过校验
"""
import asyncio

import asyncssh

from . import db, i18n


class HostKeyChanged(RuntimeError):
    def __init__(self, hostname: str, old: str, new: str):
        super().__init__(f"{hostname} 主机指纹已变更（原 {old[:20]}… → 新 {new[:20]}…）")
        self.old, self.new = old, new


class SSHUntrusted(RuntimeError):
    """tofu_confirm 开启时的拦截：指纹已记录但未经人工确认（或变更待采纳）。"""


def _tofu_manual() -> bool:
    return (db.query_one("SELECT value FROM settings WHERE key='tofu_confirm'") or {}).get("value") == "on"


def _fp(key) -> str:
    return key.get_fingerprint("sha256")


class _TofuClient(asyncssh.SSHClient):
    """TOFU 校验：在连接完成时比对服务器公钥指纹。

    指纹写入列由 _fp_col 决定：目标机写 host_key_fp，堡垒机连接写 bastion_key_fp
    （两台设备两把钥匙，各自独立信任）。
    tofu_confirm=on（人工确认模式，仅作用于目标机；跳板保持自动 TOFU）：
    - 首次连接：记录指纹、trusted=0、拒绝连接，直到面板点「确认信任」
    - 已记录未确认（trusted=0）→ 持续拒绝
    - 指纹变更：新指纹存 host_key_pending，旧指纹继续拦截，面板采纳后才换锁"""

    def __init__(self, host_row: dict):
        self._host = host_row or {}
        self._fp_col = self._host.get("_fp_col") or "host_key_fp"

    def _manual(self) -> bool:
        """人工确认模式仅作用于目标机指纹；跳板连接（_auto）保持自动 TOFU。"""
        return self._fp_col == "host_key_fp" and not self._host.get("_auto") and _tofu_manual()

    def validate_host_public_key(self, host: str, addr: str, port: int, key) -> bool:
        fp = _fp(key)
        expected = self._host.get("host_key_fp") or ""
        hid = self._host.get("id")
        manual = self._manual()
        if not expected:  # 首次见到该主机 → 记录指纹（TOFU 信任）
            if hid:
                if manual:
                    db.execute("UPDATE hosts SET host_key_fp=?, trusted=0 WHERE id=?", (fp, hid))
                    raise SSHUntrusted(i18n.t(
                        "SSH 首次指纹已记录，等待面板人工确认后放行",
                        "First-use SSH fingerprint recorded — confirm in the panel to allow"))
                db.execute(f"UPDATE hosts SET {self._fp_col}=? WHERE id=?", (fp, hid))
            return True
        if expected != fp:
            if hid and manual:
                db.execute("UPDATE hosts SET host_key_pending=? WHERE id=?", (fp, hid))
            raise HostKeyChanged(self._host.get("hostname", host), expected, fp)
        if manual and not (self._host.get("trusted") or 0):
            raise SSHUntrusted(i18n.t(
                "SSH 指纹已记录，等待面板人工确认后放行",
                "SSH fingerprint recorded — confirm in the panel to allow"))
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
    """统一异步入口：配置了堡垒机 → 链式隧道；否则直连。"""
    if (host.get("bastion_host") or "").strip():
        return await _connect_via_bastion(host, timeout, **kw)
    return await asyncio.wait_for(connect(host, **kw), timeout)


# ---------------------------------------------------------------- 堡垒机隧道

# 跳板连接缓存：同跳板的 N 台主机共享一次握手（每轮 2N 次 SSH 握手 → N+1）
_bastions: dict[tuple, asyncssh.SSHClientConnection] = {}


def _bastion_key(host: dict) -> tuple:
    return (host["bastion_host"], host.get("bastion_port") or 22,
            host.get("bastion_username") or "root", host.get("bastion_secret") or "")


def _conn_alive(conn) -> bool:
    """transport 未关闭即视为可用（asyncssh 未暴露公开判定，读私有 transport）。"""
    tr = getattr(conn, "_transport", None)
    return tr is not None and not tr.is_closing()


async def _bastion_get(host: dict, timeout: float, key: tuple):
    """取（或建立）跳板连接；缓存里的死连接自动重建。"""
    conn = _bastions.get(key)
    if conn is not None and _conn_alive(conn):
        return conn
    brow = {"id": host.get("id"),
            "hostname": host["bastion_host"],
            "port": host.get("bastion_port") or 22,
            "username": host.get("bastion_username") or "root",
            "secret": host.get("bastion_secret"),
            "host_key_fp": host.get("bastion_key_fp") or "",
            "_fp_col": "bastion_key_fp",
            "_auto": True}  # 跳板保持自动 TOFU；人工确认仅管目标机
    conn = await asyncio.wait_for(connect(brow), timeout)
    _bastions[key] = conn
    asyncio.create_task(_bastion_reaper(key, conn))
    return conn


async def _bastion_reaper(key: tuple, conn) -> None:
    """跳板连接关闭（任何原因）→ 从缓存摘除，下次访问自动重建。"""
    try:
        await conn.wait()
    except Exception:
        pass
    if _bastions.get(key) is conn:
        _bastions.pop(key, None)


async def _connect_via_bastion(host: dict, timeout: float, **kw) -> asyncssh.SSHClientConnection:
    """堡垒机链式连接：目标连接经跳板 direct-tcpip 隧道建立。

    TOFU 各自独立（跳板 bastion_key_fp / 目标 host_key_fp）；隧道打开失败且
    跳板已死时重建一次再试（目标侧原因如口令错误则直接抛，不白握跳板手）。"""
    key = _bastion_key(host)
    for attempt in (1, 2):
        bconn = await _bastion_get(host, timeout, key=key)
        try:
            return await asyncio.wait_for(connect(host, tunnel=bconn, **kw), timeout)
        except Exception as e:
            alive = _conn_alive(bconn)
            if not alive:
                _bastions.pop(key, None)
            if attempt == 2 or alive:
                raise e
