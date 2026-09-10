#!/usr/bin/env bash
# Hermes Watch 一键安装 / 升级（Linux / macOS）
#
# 用法（在仓库根目录）:
#   ./install.sh              # 安装: venv + 依赖 + 前端构建 + 提示启动
#   ./install.sh --systemd    # 安装并注册 systemd 服务（需要 sudo）,开机自启
#   ./install.sh --update     # 升级: git pull + 依赖 + 重新构建 + 重启服务
#   ./install.sh --port 9000 [--systemd]   # 指定端口（写入 systemd 环境）
#
# 环境变量: HW_PORT(默认 8800) HW_LISTEN(默认 127.0.0.1;对外暴露改 0.0.0.0)
# 前端构建需要 Node.js ≥18;没有 Node 时只装后端(面板不可用,API/MCP 可用)。
set -euo pipefail
cd "$(dirname "$0")"

SYSTEMD=0 UPDATE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --systemd) SYSTEMD=1 ;;
    --update)  UPDATE=1 ;;
    --port)    HW_PORT="$2"; shift ;;
    *) echo "未知参数: $1"; exit 1 ;;
  esac
  shift
done
HW_PORT="${HW_PORT:-8800}"
HW_LISTEN="${HW_LISTEN:-127.0.0.1}"

log() { printf '\033[1;36m[hermes-watch]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[hermes-watch]\033[0m %s\n' "$*" >&2; exit 1; }

# ---- 前置检查 ----
command -v python3 >/dev/null || die "需要 python3 (3.11+)"
PYV=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || die "Python ${PYV} 过旧,需要 3.11+"
[[ -f backend/requirements.txt ]] || die "请在仓库根目录运行(找不到 backend/requirements.txt)"

# ---- 升级路径 ----
if [[ $UPDATE -eq 1 ]]; then
  log "拉取最新代码"
  git pull --ff-only || die "git pull 失败(本地有改动?请 stash 后重试)"
fi

# ---- 后端依赖 ----
log "准备 Python 虚拟环境 (backend/venv, Python ${PYV})"
python3 -m venv backend/venv
backend/venv/bin/pip install -q --upgrade pip
backend/venv/bin/pip install -q -r backend/requirements.txt

# ---- 前端构建 ----
if command -v npm >/dev/null; then
  log "构建前端 (npm ci + build,约 1 分钟)"
  ( cd frontend && npm ci --silent && npm run build --silent )
elif [[ -f frontend/dist/index.html ]]; then
  log "未装 Node.js,使用仓库里已有的 frontend/dist"
else
  log "⚠ 未装 Node.js 且无 frontend/dist —— 面板 UI 不可用,仅 API/MCP 可用。装 Node ≥18 后重跑本脚本"
fi

# ---- systemd ----
if [[ $SYSTEMD -eq 1 ]]; then
  [[ $(id -u) -eq 0 ]] || die "--systemd 需要 root(请用 sudo 运行)"
  ROOT="$(pwd)"
  cat > /etc/systemd/system/hermes-watch.service <<EOF
[Unit]
Description=Hermes Watch - AI server inspection panel
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=${ROOT}/backend
ExecStart=${ROOT}/backend/venv/bin/python run.py
Environment=HW_LISTEN=${HW_LISTEN}
Environment=HW_PORT=${HW_PORT}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now hermes-watch
  log "systemd 服务已启动: systemctl status hermes-watch"
  log "访问 http://${HW_LISTEN}:${HW_PORT}"
  exit 0
fi

# ---- 手动启动提示 ----
if [[ $UPDATE -eq 1 ]] && systemctl is-active --quiet hermes-watch 2>/dev/null; then
  systemctl restart hermes-watch
  log "已重启 hermes-watch 服务"
else
  log "安装完成。启动:"
  log "  cd backend && ./venv/bin/python run.py"
  log "访问 http://127.0.0.1:${HW_PORT} (对外暴露: HW_LISTEN=0.0.0.0 HW_PORT=${HW_PORT} 重新以 --systemd 安装,或自设反向代理)"
fi
