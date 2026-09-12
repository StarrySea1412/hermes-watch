# 贡献指南 · Contributing

> 中文为主的项目（面板 i18n 全双语）。中英 PR 均欢迎，回复语言跟随你来文。

## 开发环境

```bash
# 后端（Python 3.11+）
cd backend
python -m venv venv
venv/Scripts/python -m pip install fastapi "uvicorn[standard]" asyncssh httpx   # Windows
# Linux/macOS: venv/bin/python -m pip install ...

# 前端
cd frontend && npm install

# 跑起来：后端 run.py（:8800）+ 前端 npm run dev（:5273，/api 代理）
# 生产单进程：npm run build 后由后端托管 dist
```

## 提 PR 前的自查清单

- **回归全绿**：`py backend/run_tests.py`（200+ 项，无框架直接跑）+ `npx vitest run`（前端组件测试）
- **ruff 干净**：`venv/Scripts/python -m ruff check .`（版本钉在 CI 里，本地装最新即可，规则集见 `backend/ruff.toml`）
- **测试现场免疫**：测试库与运行面板共用 `backend/hermes-watch.db`——涉及 settings 的测试必须备份/还原现场值（见 run_tests.py 既有先例），跑完 `tail` 确认数量
- **i18n 双语**：新增 UI 文案同时补 `frontend/src/i18n.ts` 的 zh 与 en 块；后端用户可见文案走 `app/i18n.py` 的 `t(zh, en)`
- **安全铁律不破**：
  1. 规则引擎先出事实，AI 只叙事/深挖，不做实时检测
  2. agent 永不直接改机器——修复走提案审批制，执行 = 白名单正则校验 + 超时 + 全审计
  3. AI 外发默认关闭；新增 LLM 链路必须接 `resolve_base()` 归一（见 `docs/LLM.md`）
  4. 数据面保持只读：新增工具/端点若出数据，复用 MCP 只读工具面（`mcp_server.call_tool`），别另开实现
- **部署保持轻**：单文件 SQLite；不引入消息队列/常驻全家桶；前端主包不背 echarts/xterm（独立分包已有先例）

## 分支与提交

- 直接在 `main` 上小步提交是当前常态（个人项目阶段）；多人协作期会切分支保护
- 提交信息用中文一句话说清「做了什么」，风格参照 `git log`
- CI（GitHub Actions）每次 push 全量跑：后端回归 + 前端 vitest/tsc/build + agent 三平台编译；绿了才算数

## 讨论与安全

- 功能想法 / 体验问题 → [Feature Request](https://github.com/StarrySea1412/hermes-watch/issues/new?template=feature_request.yml)
- 缺陷 → [Bug Report](https://github.com/StarrySea1412/hermes-watch/issues/new?template=bug_report.yml)
- 安全漏洞 → 走私密渠道，见 [SECURITY.md](SECURITY.md)，勿开公开 issue
