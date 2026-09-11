"""公开端点防护：per-IP 滑动窗口限流 + 公开访问审计环形缓冲。

- 只作用于 token 门禁的公开端点（/badge /status /api/push /api/agent/push）：
  token 本身高熵，这里是兜底——防脚本滥用、扫描噪音与异常流量放大
- 限流与审计均为进程内实现：多 worker 部署不共享计数（本项目单进程，够用）；
  审计重启即清、不落库——公开端点流量不进事件流，避免刷屏
"""
import time
from collections import defaultdict, deque

WINDOW_S = 60
MAX_REQ = 60  # 每 IP 每窗口

_hits: dict[str, deque] = defaultdict(deque)
_audit: deque = deque(maxlen=500)


def allow(ip: str) -> bool:
    """滑动窗口限流；超限返回 False（端点回 429）。"""
    now = time.monotonic()
    q = _hits[ip]
    while q and now - q[0] > WINDOW_S:
        q.popleft()
    if len(q) >= MAX_REQ:
        return False
    q.append(now)
    return True


def audit(ip: str, path: str, code: int) -> None:
    _audit.append({"ts": time.time(), "ip": ip, "path": path, "code": code})


def recent() -> list:
    return list(_audit)
