# 公开演示站部署指南

把 Hermes Watch 以只读演示站的形态挂到公网，供 README / 社区帖的访客
30 秒摸到真实界面。一台 2GB 内存的 VPS 即可。

## 一键起

```bash
git clone https://github.com/StarrySea1412/hermes-watch.git
cd hermes-watch
docker compose -f compose.demo.yml up -d --build
```

浏览器访问 `http://<服务器IP>:8800`。演示数据由内置 mock 模拟器实时生成
（4 台主机：web-1 / db-1 / app-1 / cache-1，各带一种病症），巡检循环每 60s
产生新的指标点，告警 → 诊断 → 提案全链路可视化。

## HW_DEMO_MODE=on 做了什么

| 面 | 行为 |
|---|---|
| GET（读） | 全放行——巡检曲线、根因卡、证据链、拓扑、拨测、报告、公开状态页全可看 |
| 写（POST/PUT/DELETE） | 一律 `423 Locked`，登录后也改不动（加主机 / 审批 / 执行 / 改设置 / 备份恢复） |
| 密钥外泄面 | `GET /api/settings`、`/api/notify/channels`、`/api/status/token` 上的密钥类字段全部脱敏为空（LLM api_key / webhook URL / chat_id / 面板口令哈希 / 状态页 token） |
| 前端 | `/api/auth/status` 回带 `demo_mode: true`，整站按 observer 只读视图渲染（设置/接入/终端入口不出现），顶边常驻只读横幅 |
| 登录例外 | `login` / `logout` / `auth/self/password` 放行（演示站可叠加自己的访问口令层） |

演示站与访问控制正交：想再叠一层口令，正常走「设置 → 访问控制」即可，
两层互不干扰。

## 上线前两件事

1. **HTTPS 反代**（必须）：Caddy 两行即可——

   ```
   demo.example.com {
       reverse_proxy 127.0.0.1:8800
   }
   ```

   WebSocket（终端）/ SSE（事件流/对话流）Caddy 默认透传，无需额外配置。

2. **AI 对话的省钱姿势**：容器内**不**内置真实 LLM key。想演示 AI 叙事时，
   另起一个 `mock_llm` 容器（`python backend/mock_llm.py`，本地试运行端点），
   在演示站设置页把 Base URL 指向它——但注意演示站设置页是只读的，LLM 配置
   需要在**本地**先用一份同构库配好再整体替换 `/data/hermes-watch.db`，
   或者直接保持 AI 外发关闭：对话页自动降级为本地规则引擎摘要，效果同样完整。

   最简推荐：**保持 AI 关闭**。访客看到的「本地规则引擎回答」本身就展示了
   零依赖的降级能力，真实 LLM 留给自部署用户体验。

## 日常维护

- **重置剧本**：`docker compose -f compose.demo.yml down -v && up -d --build`
  （清卷即回到初始 seed，适合长期运行后数据太杂时一键还原）
- **升级**：`git pull && docker compose -f compose.demo.yml up -d --build`
- **看日志**：`docker logs -f hermes-watch-demo`

## 为什么敢公开 GET

读通道里没有秘密：主机凭据在库中加密存储且所有读出口抹掉 secret 字段；
端口基线 / 巡检指标 / 根因证据本来就是「给人看」的数据。演示站的 mock
主机使用 `10.0.0.x` 段编造地址，没有任何真实资产可摸。
`guard.py` 的 per-IP 限流（60 req/min）照常兜底公开端点。
