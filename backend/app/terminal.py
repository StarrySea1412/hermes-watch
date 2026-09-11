"""Web terminal: WebSocket bridge to an interactive shell on a host.

- Real hosts: asyncssh PTY session (read-only user recommended; the terminal is
  the one place operators type live commands, so it is a separate surface from
  the proposal system).
- Demo hosts: a small simulated shell with realistic outputs so the demo story
  needs no external machines.
"""
import asyncio


try:
    import asyncssh
except ImportError:  # pragma: no cover
    asyncssh = None


# ---------- simulated shell for demo hosts ----------

class MockShell:
    """A tiny fake shell with just enough commands to demo the terminal UI."""

    FS = {
        "": ["bin/", "etc/", "home/", "opt/", "root/", "usr/", "var/"],
        "var": ["log/", "lib/", "cache/"],
        "var/log": ["app.log (38G)", "journal/ (2.3G)", "syslog", "nginx/"],
        "opt": ["app/"],
        "opt/app": ["server.py", "worker.py", "requirements.txt"],
        "etc": ["nginx/", "systemd/", "passwd", "hostname"],
    }
    PROCS = (
        "USER   PID  %CPU %MEM COMMAND\n"
        "root   812  88.2 91.3 /usr/bin/python3 /opt/app/worker.py\n"
        "mysql  943   3.2  9.8 /usr/sbin/mysqld\n"
        "root   511   0.3  2.1 /usr/bin/node /opt/dashboard/server.js\n"
        "root   311   0.1  0.4 /sbin/init"
    )

    def __init__(self, host: dict):
        self.host = host
        self.cwd = ""
        self.banner = (
            f"Linux {host['name']} 6.8.0-generic #1 SMP x86_64\n"
            "Last login: Sat Sep  5 03:12 from 192.168.1.24\n"
            "（演示终端 — 模拟 shell，支持 help/ls/ps/df/free/uptime/top 等只读命令）\n"
        )

    def _resolve(self, path: str) -> str:
        if path.startswith("/"):
            return path.strip("/")
        parts = [p for p in self.cwd.split("/") if p]
        for seg in path.split("/"):
            if seg == "..":
                parts = parts[:-1]
            elif seg and seg != ".":
                parts.append(seg)
        return "/".join(parts)

    def feed(self, line: str) -> str:
        cmd, *args = line.strip().split()
        base = cmd if cmd else ""
        H, R = self.host["hostname"], "\n"
        if base in ("", "help"):
            return ("可用（只读）：ls cd pwd ps df free uptime whoami hostname cat head uname "
                    "systemctl status last\n其余命令回显演示输出。")
        if base == "pwd":
            return "/" + self.cwd if self.cwd else "/"
        if base == "whoami":
            return self.host["username"] or "root"
        if base == "hostname":
            return H
        if base == "uname":
            return "Linux " + H + " 6.8.0-generic #1 SMP x86_64 GNU/Linux"
        if base == "ls":
            tgt = self._resolve(args[0] if args and not args[0].startswith("-") else "")
            return R.join(self.FS.get(tgt, [f"{tgt or '/'}: 演示文件系统仅预置少量目录"]))
        if base == "cd":
            tgt = self._resolve(args[0] if args else "")
            if tgt in self.FS:
                self.cwd = tgt
                return ""
            return f"cd: {args[0] if args else ''}: 演示文件系统中不存在该目录"
        if base == "cat":
            tgt = args[0] if args else ""
            if tgt.endswith("hostname"):
                return H
            if "worker.py" in tgt:
                return ("def leak():\n    cache = {}\n    while True:\n"
                        "        cache[len(cache)] = b'x' * 1024 * 512  # TODO: bounded\n")
            if "app.log" in tgt:
                return "… [ERROR] out of disk space (38G of this log, rotation not configured)"
            return f"cat: {tgt}: No such file or directory"
        if base == "head":
            return self.feed("cat " + (args[-1] if args else "")).split(R)[:10] and R.join(
                self.feed("cat " + (args[-1] if args else "")).split(R)[:10])
        if base == "ps":
            return self.PROCS
        if base == "top":
            return "top - 09:12:33 up 42 days, load average: 0.42, 0.35, 0.31\n" + self.PROCS
        if base == "df":
            return ("Filesystem      Size  Used Avail Use% Mounted on\n"
                    "/dev/sda1        98G   96G  2.1G  98% /\n"
                    "tmpfs           7.8G     0  7.8G   0% /dev/shm")
        if base == "free":
            return ("              total        used        free\n"
                    "Mem:          15873       14102        1771\n"
                    "Swap:          2047        1918         129")
        if base == "uptime":
            return " 09:12:33 up 42 days,  1 user,  load average: 0.42, 0.35, 0.31"
        if base == "systemctl":
            if "failed" in args:
                return "0 loaded units listed."
            if "status" in args:
                unit = args[args.index("status") + 1] if len(args) > args.index("status") + 1 else "?"
                return (f"● {unit}.service - {unit}\n   Loaded: loaded (/lib/systemd/system/{unit}.service)\n"
                        f"   Active: active (running) since Sat 2026-09-05 03:14:11 UTC")
            return "systemctl: 演示终端支持 systemctl status <unit> / --failed"
        if base == "last":
            return ("root     pts/1        185.220.101.34   Sat Sep  5 03:12   still logged in\n"
                    "deploy   pts/0        192.168.1.24      Fri Sep  4 18:40 - 20:15  (01:35)")
        return f"bash: {base}: command found (demo shell) — 输出为模拟数据"

    def prompt(self) -> str:
        return f"{self.host['username'] or 'root'}@{self.host['name']}:{'/' + self.cwd if self.cwd else '~'}$ "


# ---------- real SSH bridge ----------

async def ssh_session(ws, host: dict):
    """Bridge the websocket to an interactive PTY over asyncssh."""
    from . import ssh
    try:
        conn = await ssh.connect_async(host)
    except Exception as e:
        # TOFU 指纹变更或其他连接失败：显式告知，不静默
        await ws.send_text(f"\r\nSSH 连接失败: {type(e).__name__}: {e}\r\n")
        await ws.close()
        return

    class WsSink(asyncssh.SSHClientSession):
        def data_received(self, data, datatype):
            asyncio.ensure_future(ws.send_text(data))

        def connection_lost(self, exc):
            asyncio.ensure_future(ws.close())

    chan, _ = await conn.create_session(WsSink, term_type="xterm-256color",
                                        term_size=(120, 32))
    try:
        while True:
            msg = await ws.receive_text()
            chan.write(msg)
    except Exception:
        pass
    finally:
        chan.close()
        conn.close()
