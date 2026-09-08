#!/usr/bin/env bash
# WSL test harness: verifies the Linux agent collects real /proc metrics,
# reports extras, and signs correctly (HMAC checked by the receiver).
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
TOKEN="hw_wsltest_deadbeef"
python3 "$DIR/wsl_receiver.py" "$TOKEN" > /tmp/hw_recv.out 2>&1 &
RECV=$!
sleep 1
HW_URL=http://127.0.0.1:18899 HW_TOKEN=$TOKEN HW_INTERVAL=5 "$DIR/hermes-watch-agent-linux-amd64" > /tmp/hw_agent.out 2>&1 &
AGENT=$!
sleep 9
kill $AGENT 2>/dev/null || true
kill $RECV 2>/dev/null || true
echo "=== agent stdout ==="; cat /tmp/hw_agent.out
echo "=== receiver ==="; cat /tmp/hw_recv.out
