# Hermes Watch · LLM 调用说明

> 一句话：**LLM 是可选的"叙事层"，不是必需件**。所有 LLM 调用都是对 OpenAI 兼容端点的
> `POST /chat/completions`（外加一个 `GET /models` 拉模型列表），默认关闭、Key 自备、
> 失败永远不阻塞规则引擎。

---

## 1. 配置入口（设置页 → AI 外发总开关）

| 配置项 | 存储键 | 说明 |
|---|---|---|
| 总开关 | `ai_outbound` | `on` / `off`（**默认 off**，关闭时数据不出本机） |
| Base URL | `ai_provider.base_url` | OpenAI 兼容端点，如 `http://127.0.0.1:11434/v1`（Ollama） |
| 模型 | `ai_provider.model` | 如 `qwen2.5:7b`、`deepseek-chat` |
| API Key | `ai_provider.api_key` | 可空（Ollama / vLLM 等本地端点无需鉴权），非空时走 `Authorization: Bearer <key>` |

开关关闭时：**代码里所有 LLM 调用点都会短路**，界面上 AI 相关功能自动降级为本地规则引擎输出。

## 2. 全部调用点（共 2 条业务链路 + 2 个辅助工具）

### 2.1 AI 对话 —— `POST /api/chat`（多轮，带 fleet 快照）

```
前端 Chat.tsx ──{question, history[≤10]}──▶ /api/chat ──▶ analysis.chat_answer()
```

- **触发条件**：`ai_outbound == 'on'` 且配置了 `base_url`；否则直接走规则引擎摘要（无状态、永远基于最新快照）。
- **messages 结构**（发给 LLM 的数组，见 `analysis.chat_answer`）：

| 顺序 | role | 内容 |
|---|---|---|
| 1 | system | 角色设定 + `build_fleet_context()` 生成的 fleet 快照（主机/健康分/发现清单） |
| 2…n | user / assistant | 前端带来的**多轮历史**，最多最近 10 条，每条截断 2000 字符 |
| n+1 | user | 当前问题（≤500 字符） |

- **参数**：`max_tokens: 500`，超时 45s。
- **失败语义**：LLM 调用抛错 → 返回 `source: "fallback"` 的规则引擎摘要（回答以「LLM 调用失败…」开头），**UI 永远不是死胡同**。

### 2.2 诊断 AI 叙事 —— `analysis._llm_narrate()`

```
诊断中心「重新诊断」▶ 规则引擎深挖(真实 SSH 只读探针) ▶ 证据链齐备
    ▶（且仅当 ai_outbound=on 且有证据文本）▶ POST {base}/chat/completions
```

- **Prompt 结构**：主机名 + 发现标题/详情 + 排查输出（证据链文本）+ 指令
  「用不超过 3 句中文说明根因，只基于以上输出，不要编造」。
- **参数**：`timeout 30s`，无 max_tokens 限制（跟随端点默认）。
- **失败语义**：异常被捕获，卡片 `ai_error` 字段留痕（诊断卡显示
  「AI 生成失败，规则引擎结论不受影响」），**绝不覆盖 root_cause、绝不阻塞诊断流程**。
- 结果落在 finding.card 的 `ai_narration` / `ai_model` / `ai_error` 三个字段。

### 2.3 获取模型列表 —— `POST /api/llm/models`（设置页「⌄ 获取模型列表」）

- 请求 `GET {base}/models`，带 Bearer（Key 非空时）。
- **候选 URL 按序兜底**（cc-switch 式，见 `main._models_url_candidates`）：
  1. base 以 `/v1` 等 `/v{N}` 结尾 → `{base}/models`（再兜底 `{base}/v1/models`）
  2. 否则 → `{base}/v1/models`
  3. base 命中 `/anthropic`、`/claude` 等兼容子路径 → 追加剥离后缀的 `{root}/v1/models`
- 404/405 自动换下一个候选；解析 `{data:[{id}]}`，排序去重返回。超时 15s。

### 2.4 连通测活 —— `POST /api/llm/test`（设置页「⚡ 连通测活」）

- 先发一次热身请求（复用连接去掉首包惩罚），再正式计时。
- 载荷：`{"messages":[{"role":"user","content":"ping"}], "max_tokens": 1}`。
- 返回 `{ok, latency_ms, status, reply?, error?}` —— 与 2.2 走**同一条
  `/chat/completions` 路径**，测活通过即代表 AI 叙事/对话可用。

## 3. 本地试运行（不需要真实 LLM）

```powershell
py backend/mock_llm.py      # 127.0.0.1:18777
```

- `POST /v1/chat/completions`：按提问关键词（磁盘/内存/登录/CPU）返回预置中文叙事
- `GET  /v1/models`：返回 `mock-7b / mock-14b / mock-72b-instruct`
- 设置页 Base URL 填 `http://127.0.0.1:18777/v1`（Key 留空）→ 开启 AI 外发 →
  获取模型 / 测活 / 对话 / 诊断叙事全链路均可演示。

## 4. 与「本地 MCP 端点」的区别

`/api/mcp` 是给 Claude Desktop / Cursor 等 MCP 客户端**读取巡检数据**的只读通道
（5 个工具直查 SQLite），方向相反：不是本平台调用 LLM，而是外部 LLM 客户端来查数据。
它不受 `ai_outbound` 开关影响。

## 5. 调用安全铁律（代码里的硬约束）

1. **默认关闭**：`ai_outbound` 缺省 `off`；关闭时零外呼（辅助工具 2.3/2.4 仅在用户显式点按钮时发起）。
2. **只读提议制**：LLM 的任何输出只进 `ai_narration` 附加视角或对话回答，**永不覆盖规则引擎结论**，更不会直接操作主机；修复动作必须人工批准后经白名单校验执行。
3. **失败不阻塞**：两条业务链路的 LLM 异常都被捕获留痕（`source: "fallback"` / `ai_error`），规则引擎照常工作。
4. **BYO Key**：Key 存本机 SQLite，仅用于对用户自填端点的 Bearer 头；遥测、上报均为零。

## 6. 代码索引

| 文件 | 职责 |
|---|---|
| `backend/app/analysis.py` | `chat_answer()`（对话）、`_llm_narrate()`（诊断叙事）、`_llm_enabled()` / `_ai_conf()`（开关与配置读取） |
| `backend/app/main.py` | `/api/chat`、`/api/llm/models`、`/api/llm/test` 端点与候选 URL 算法 |
| `backend/app/mock_llm.py` | 本地 mock 端点（演示用） |
| `frontend/src/pages/Chat.tsx` | 多轮对话 UI，组装 `history` 随请求提交，会话存 localStorage |
| `frontend/src/pages/Settings.tsx` | AI 配置卡（获取模型 / 测活按钮） |
