"""Outbound collection agent (beszel-style): hosts run a tiny agent that pushes
metrics to this endpoint — no inbound SSH needed. Two flavors:
- sh + curl 脚本（AGENT_SCRIPT）：仅 token 鉴权，最简依赖
- Go 单二进制（agent-go/）：HMAC-SHA256 签名 + 时间戳防重放，上报进程/失败服务/证书
"""
import hashlib
import hmac
import secrets
import time

from . import db


def new_token() -> str:
    return "hw_" + secrets.token_hex(16)


def host_by_token(token: str) -> dict | None:
    if not token:
        return None
    h = db.query_one("SELECT * FROM hosts WHERE agent_token=?", (token,))
    return dict(h) if h else None


SIG_MAX_SKEW = 300  # 秒；时间戳偏移超过此值视为重放


def verify_signature(token: str, body: bytes, ts: str, sig: str) -> bool:
    """Go agent 验签：HMAC-SHA256(token, body + timestamp)，时间戳 ±300s 内有效。"""
    try:
        ts_i = int(ts or "")
    except ValueError:
        return False
    if abs(time.time() - ts_i) > SIG_MAX_SKEW:
        return False
    mac = hmac.new(token.encode(), body + (ts or "").encode(), hashlib.sha256)
    return hmac.compare_digest(mac.hexdigest(), sig or "")


AGENT_SCRIPT = r"""#!/bin/sh
# Hermes Watch outbound agent — POSIX sh + curl, read-only, no daemon deps.
# Usage: HW_URL=http://host:8800 HW_TOKEN=hw_xxx ./hermes-watch-agent.sh
while true; do
  cpu=$(top -bn1 2>/dev/null | grep 'Cpu(s)' | awk '{print $2+$4}' | cut -d. -f1)
  mem=$(free 2>/dev/null | awk '/Mem:/{printf("%.1f", $3/$2*100)}')
  load1=$(awk '{print $1}' /proc/loadavg 2>/dev/null)
  df -P -x tmpfs -x devtmpfs 2>/dev/null | awk 'NR>1{u=$5; gsub(/%/,"",u); if(u>m)m=u} END{printf("disk=%.1f\n", m)}' > /tmp/hw.df
  disk=$(sed -n 's/^disk=//p' /tmp/hw.df)
  cat /proc/net/dev 2>/dev/null | awk -F'[: ]+' '/eth0|ens|enp/{rx+=$3; tx+=$11} END{printf("net_in=%.0f net_out=%.0f\n", rx, tx)}' > /tmp/hw.net
  net_in=$(sed -n 's/^net_in=//p' /tmp/hw.net)
  net_out=$(sed -n 's/^net_out=//p' /tmp/hw.net)
  # 容器清单（docker 缺失/无权限时为空 → 不带 extra 字段）
  # 容器名/镜像 tag 不含双引号，可安全内插 JSON
  cons=$(docker ps -a --format '{"name":"{{.Names}}","state":"{{.State}}","status":"{{.Status}}","image":"{{.Image}}"}' 2>/dev/null | paste -sd, -)
  body="{\"cpu\":${cpu:-0},\"mem\":${mem:-0},\"disk\":${disk:-0},\"load1\":${load1:-0},\"net_in\":${net_in:-0},\"net_out\":${net_out:-0}"
  [ -n "$cons" ] && body="$body,\"extra\":{\"docker_containers\":[$cons]}"
  body="$body}"
  curl -sf -m 8 -X POST "$HW_URL/api/agent/push" \
    -H "Authorization: Bearer $HW_TOKEN" \
    -H "Content-Type: application/json" \
    -d "$body" \
    || echo "[agent] push failed, retrying next cycle" >&2
  sleep "${HW_INTERVAL:-60}"
done
"""


def parse_secret_token(auth_header: str) -> str:
    if not auth_header:
        return ""
    for scheme in ("Bearer ", "bearer "):
        if auth_header.startswith(scheme):
            return auth_header[len(scheme):].strip()
    return auth_header.strip()


def rules_version() -> int:
    return 1
