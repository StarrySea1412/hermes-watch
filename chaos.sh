#!/usr/bin/env bash
# 故障注入脚本：演示"深夜事故"故事线用
# 用法: ./chaos.sh disk|mem|login|heal <容器名，如 demo-db-1>
set -e
ACTION="${1:-help}"
TARGET="${2:-demo-db-1}"

case "$ACTION" in
  disk)   # 磁盘快速填满：写入 2GB 垃圾文件
    docker exec "$TARGET" sh -c 'dd if=/dev/zero of=/var/log/chaos.bin bs=1M count=2048' &
    echo "[$TARGET] 注入磁盘填充，观察 Fleet 卡片变红 → 自动诊断 → 提案"
    ;;
  mem)    # 内存泄漏
    docker exec -d "$TARGET" sh -c 'python3 -c "import time;b=bytearray()
while True:
    b+=bytearray(10*1024*1024)
    time.sleep(5)"'
    echo "[$TARGET] 注入内存泄漏"
    ;;
  login)  # 伪造可疑登录记录（写假 wtmp 需 root，演示机直接追加 utmp 风险大，改为提示手动）
    echo "提示：可疑登录建议用种子数据演示（backend 已内置 cache-1 剧情）"
    ;;
  heal)   # 清除注入
    docker exec "$TARGET" sh -c 'rm -f /var/log/chaos.bin /var/log/filler.bin'
    echo "[$TARGET] 已清理注入"
    ;;
  *) echo "用法: ./chaos.sh disk|mem|login|heal <容器名>" ;;
esac
