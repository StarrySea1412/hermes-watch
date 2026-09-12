# Hermes Watch 🐚

> AI 服务器巡检与可视化平台 · 把 AI 巡检还给你自己的机房

[English](README_EN.md) · 中文

**一句话**：Grafana/Netdata 把 AI 锁在云里按条收费，Hermes Watch 让一个懂运维的 agent 住进你自己的机器——自动巡检本地和云服务器、深挖根因、出可视化健康报告，全程数据不出本机。

## 架构

```
浏览器 (React + ECharts + xterm.js, :5273)
   │ /api 代理 · /ws WebSocket
后端 (FastAPI + SQLite, :8800)
   ├─ 采集层   SSH 只读探测（asyncssh，并发限流）/ 内置演示模拟器（实时行走曲线）
   │           出站 Agent（beszel 式：目标机 sh+curl 主动 push，免入站端口）
   │           在线判定：3 个巡检周期无成功采集 → 主机标记离线（Fleet/拓扑可视化）
   ├─ 拨测     URL / TCP / DNS / Push 四类服务拨测（独立周期，Gatus 式双阈值防抖：
   │           连续 N 次失败才下线 / 连续 N 次成功才恢复）→ 心跳条带图 + 翻转才告警
   ├─ 规则引擎  确定性阈值规则（阈值 + 滞后恢复线，可在设置页调整）→ 发现 + 健康分
   ├─ 告警生命周期  自动恢复（连续 3 轮未再触发 → resolved + 恢复通知带持续时长）
   │           crit 持续告警周期重发 · 免打扰时段（跨午夜）· 多渠道通知（模板可自定义）
   ├─ 分析引擎  按发现类型深挖（du/ps/last/systemctl）→ 根因卡 + 证据链
   │           （可选：OpenAI 兼容 LLM 叙事，默认关，BYO；AI 只补充视角，不覆盖规则结论，backend/mock_llm.py 提供本地试运行端点）
   ├─ AI 对话  聊天式问答：LLM 自动调用只读工具查实时数据（fleet/发现/指标/拨测/事件）再回答，
   │           思考链与工具调用折叠展示；LLM 关闭降级本地规则引擎摘要
   ├─ 提案审批  修复动作 = 卡片，批准才执行，全审计（只读提议制）
   ├─ 远程终端  WebSocket → asyncssh PTY（演示主机为模拟 shell）
   ├─ 本地 MCP  只读 streamable-HTTP 端点（8 工具），Claude/Cursor 直查巡检数据
   └─ 报告引擎  结构化数据 → 本地 HTML 健康报告（支持定时自动生成）
```

## 快速开始

**Linux / macOS 一键安装**（装依赖 + 构建前端 + 可选 systemd 开机自启）：

```bash
git clone https://github.com/StarrySea1412/hermes-watch.git && cd hermes-watch
./install.sh                 # 或 sudo ./install.sh --systemd
./install.sh --update        # 以后升级：拉代码 + 重构 + 重启服务
```

**Windows 手动安装**：

```powershell
# 后端（Windows，Python 3.11+）
cd backend
py -3.11 -m venv venv
./venv/Scripts/python -m pip install fastapi "uvicorn[standard]" asyncssh httpx
./venv/Scripts/python run.py        # http://127.0.0.1:8800

# 前端
cd frontend
npm install
npm run dev                          # http://localhost:5273
```

**生产模式（单进程）**：`cd frontend && npm run build`，后端自动托管 `frontend/dist`——重启后端后直接访问 `http://127.0.0.1:8800` 即是完整面板（SPA 路由/静态资源/PWA 全部就绪），无需第二个进程。**Docker**：`docker build -t hermes-watch . && docker run -p 8800:8800 -v hermes-data:/data hermes-watch`（数据与密钥持久化在 `/data` 卷，`HW_DATA_DIR` 可重定向；每日自动备份到 `backups/`，设置页可手动备份/恢复）。CI：GitHub Actions 每次 push 自动跑 200+ 项回归（后端）+ 25 项组件测试（前端）+ agent 三平台编译检查；打 `v*` tag 自动发布 agent 二进制到 Release。

首次启动自动播种 4 台演示主机（web-1 健康 / db-1 磁盘填满 / app-1 内存泄漏 / cache-1 可疑登录），并自动触发诊断。

## 演示故事线（3 分钟）

1. 打开 `Fleet 总览`：db-1/app-1/cache-1 卡片告警，事件流实时滚动
2. 打开 `诊断中心`：每条发现的根因卡 → 展开 Agent 执行轨迹（命令+输出）→ 修复提案
3. 点 `✓ 批准执行`（仅记录决策）→ 提案变已批准 → 点 `▶ 现在执行`：逐段白名单校验，mock 主机在模拟环境真实生效（指标曲线回落、chaos 解除），每次尝试留执行审计
4. `报告中心` 生成/查看 Fleet 健康报告
5. `设置` 里可添加真实 SSH 主机（填 IP + 凭据，下一轮巡检自动采集）；`提案执行` 开关默认关闭

## 界面主题：日 / 夜双模式

- 右上角 🌙 按钮循环切换 **夜间 → 日间 → 跟随系统**（next-themes），偏好存本地，刷新无闪烁
- 全站两套设计 token：夜间「深海观测站」/ 日间「白昼灯塔」，组件与图表全部走 CSS 变量
- ECharts 图表随主题实时取色重绘（拓扑视图、指标曲线等）
- 终端画面刻意保持深色（更像真实终端），页面框架随主题变化
- 定时生成的 HTML 健康报告同样适配系统明暗偏好（prefers-color-scheme）

## 接入真实服务器

- **出站 Agent（推荐）**：接入中心页生成 Token → 目标机运行 [Go 单二进制](agent-go/README.md)（HMAC 签名防重放，上报进程/失败服务/证书），或下载 `hermes-watch-agent.sh`（sh + curl，纯只读）。机器在内网/防火墙后无需开入站端口，指标每 60s 主动推送
- **SSH 拉取**：设置页填 `名称/IP/用户/密码`，巡检走 SSH 只读探测（top/free/df/ps/systemctl/last/openssl）
- **服务拨测**：拨测页添加 URL（`https://…/health`）或 TCP（`host:port`）目标，周期自动拨测（全局 30s，可每目标独立）；Gatus 式条件引擎：状态码 + **关键词包含** + **响应时间上限** + **HTTPS 证书剩余天数**，双阈值防抖（默认连续 3 次失败下线 / 2 次成功恢复），下线/恢复事件进时间线并外发通知，48 桶心跳条带图看历史
- **Docker 演示 fleet**：`cp .env.example .env` 填入公钥 → `docker compose up -d`（2221-2223 端口）
- **故障注入**：`./chaos.sh disk demo-db-1` 观察告警→诊断→提案全链路
- **WSL 当服务器（已实战验证）**：`sudo apt install openssh-server && systemctl enable --now ssh`，把 WSL IP（`hostname -I`）添加进面板。密码留空 = 自动使用本机 `~/.ssh/id_ed25519` 密钥免密登录（公钥需在目标机 `authorized_keys` 中）。注意：WSL2 空闲约 60s 会回收整个 VM，sshd 随之消失——保持 WSL 终端开着，或在 `%UserProfile%\.wslconfig` 配 `[wsl2] vmIdleTimeout=-1`
- **接入任意 LLM**：MCP 客户端（Claude Desktop / Cursor）配置 `http://127.0.0.1:8800/api/mcp`，8 个只读工具直查巡检数据；面板 AI 对话页同样由 LLM 自动调用这套只读工具（思考链/工具调用折叠展示）；LLM 调用链路详解见 [`docs/LLM.md`](docs/LLM.md)
- **告警通知**：设置 → 通知渠道，10 渠道：企业微信 / 钉钉 / 飞书 / Telegram / Server酱 / 通用 Webhook / Discord / Slack / ntfy / SMTP 邮件（Shoutrrr 式单字段配置）；crit 告警推送、恢复通知（带持续时长）、持续告警周期重发、免打扰时段，发送前可一键测试
- **本地试 AI 叙事（无需真实 LLM）**：`py backend/mock_llm.py`（内置本地 mock 端点 :18777）→ 设置页 Base URL 填 `http://127.0.0.1:18777/v1`，开启 AI 外发后重新诊断即可看到「AI 叙事」区块

## 设计铁律（来自竞品调研，见 `docs/`）

1. 规则引擎先出事实，AI 只串叙事 + 深挖（不拿 LLM 做实时检测）
2. 每条 AI 结论必须带证据链，可回跳命令输出
3. 提议制不变：agent 永不直接改机器。批准仅记录决策；执行是批准后的独立显式动作——逐段白名单校验（正则全匹配，`rm -rf`、管道注入直接拦截）、20s 超时、每次尝试（含被拦截/超时）写入 proposal_runs 审计表，开关默认关闭
4. AI 外发默认关闭（Termix 式 gating），开启前数据不出本机
5. 部署极轻：单文件 SQLite，无消息队列/全家桶

## 路线图

- [x] 拓扑视图 + WebSocket 远程终端 + 出站 Agent + 本地 MCP 端点
- [x] 阈值可配置 + 定时自动报告 + 实时行走曲线（演示主机不再静止）
- [x] 日/夜双主题切换（跟随系统偏好，图表/报告同步适配）
- [x] LLM 叙事接线（诊断卡新增「AI 叙事」区：规则结论为准、AI 只补充、失败留痕不阻塞；API Key 可空适配 Ollama 等本地端点）
- [x] SSH 密码加密存储（Fernet + 本地主密钥文件 `backend/.secret_key`，DB 泄露不等于凭据泄露，历史明文自动迁移）
- [x] 提案可执行（批准与执行分离：白名单正则校验 + 超时拦截 + proposal_runs 全审计 + mock 主机真实恢复；执行开关默认关）
- [x] 面板访问控制（可选口令门：PBKDF2 口令存储 + HMAC 签名 Cookie 会话 + 登录限速；出站 Agent 与本地 MCP 各走通道不受影响；设置页可开关/改口令，默认关闭）
- [x] 出站 Agent 单二进制（Go，纯 stdlib）：HMAC-SHA256 签名 + 时间戳防重放，新增进程/失败服务/证书上报，extras 入库并驱动规则引擎；保留 sh+curl 轻量版
- [x] 工程化：路由懒加载 + echarts/xterm 独立分包（主包 1.4MB → 242KB）、指标默认保留 7 天（可配）、`py backend/run_tests.py` 69 项回归全绿
- [x] 告警生命周期层（对标 Uptime Kuma / Gatus / Netdata，见 `docs/竞品调研.md`）：告警自动恢复 + 恢复通知带持续时长、阈值滞后双阈值防抖、crit 持续告警周期重发、免打扰时段（跨午夜）、主机离线检测与可视化、10 渠道通知（企业微信/钉钉/飞书/Telegram/Server酱/通用 Webhook/Discord/Slack/ntfy/SMTP，设置页可测活 + 发送留痕）
- [x] AI 对话流式输出（SSE 逐 token，LLM 关闭自动降级本地规则引擎摘要）+ LLM 出站脱敏（k8sgpt 式 anonymize：主机名/IP/用户名出站前替换占位符，映射不落盘，回答映射回真实名）
- [x] 巡检心跳条带图（主机详情 48 桶上下状态带）+ PWA 可安装（manifest + service worker，仅生产注册）+ 公开状态页（设置页一键生成带 token 只读分享链接，60s 自动刷新，不含地址/凭据/证据，可随时撤销）+ i18n 全量双语（自建零依赖翻译层，PageHead 一键切 EN/中文；前端全部 UI 文案 + 后端发现/事件/诊断卡/通知留痕/公开状态页均跟随面板语言，历史中文行读出口正则兜底翻译）
- [x] 多用户与 RBAC 三级角色（admin 全权 / operator 值班：终端/审批/告警确认/拨测/AI 对话 / observer 只读；users 表 + 角色签名会话 + 按角色过滤导航与 WS 终端；存量单口令兼容自动 admin）
- [x] 运维两件套：一键安装/升级脚本（install.sh：venv + 前端构建 + 可选 systemd 自启，--update 一键升级，HW_PORT 端口可配）、SQLite 每日自动备份（在线 backup API，保留 10 份；设置页手动备份/恢复——恢复走暂存标记、重启生效，避免运行中换库）
- [x] 服务拨测（对标 Uptime Kuma 拨测 / Gatus 状态机）：URL / TCP 目标拨测（全局/每目标独立周期），Gatus 式条件引擎（状态码 + 关键词包含 + 响应时间上限 + HTTPS 证书剩余天数）与双阈值防抖（连续失败下线 / 连续成功恢复，阈值可配），48 桶心跳条带图 + 延迟显示，翻转才落事件并外发通知（复用免打扰/留痕），手动「立即拨测」，心跳随指标保留期清理
- [x] 拨测矩阵补宽：DNS 拨测（纯 stdlib UDP 客户端，六类 RDATA 应答校验）+ ICMP 拨测（Windows IcmpSendEcho / POSIX SOCK_DGRAM 双路，零特权零依赖）+ Push 心跳（`hw_` token 主动上报 + 容忍窗口防抖）+ 状态徽章 SVG（shields 风 `/badge/{token}.svg`，门禁独立 token 可撤销）；拨测并发化（单连接多探针 gather）
- [x] 堡垒机链式 SSH（跳板 → 目标机两跳隧道 + 同跳板连接池共享，握手 2N→N+1）+ SSH TOFU 人工确认（首连指纹拦截、变更需采纳制，跳板自动 TOFU）+ 端口基线（LISTEN 端口漂移对比告警）
- [x] 健康分 v2（类型化权重：安全类重、性能类轻，同型发现 0.5^n 指数衰减）+ 告警聚合（同轮同主机 crit 合并通知）+ 公开端点限流与访问审计 + 通知模板自定义（`{var}` 渲染，四调用点）
- [x] metrics 小时降采样（Grafana 式长期层）：原始行保留期照删，小时均值桶固定留 90 天；主机详情新增 7/30/90 天趋势视图（长范围自动切小时桶，行数恒定）
- [x] AI 对话 agentic 工具循环（对话页 LLM 自动调用与 MCP 同源的只读工具查实时数据，最多 3 轮；思考链与工具调用 SSE 透传、前端默认折叠展示；不支持 function calling 的端点自动退回纯对话）
