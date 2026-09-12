# 安全政策 · Security Policy

## 报告漏洞

**请勿为安全问题开公开 issue。** 使用 GitHub 的私密漏洞报告（仓库页 → Security → Report a vulnerability），或通过仓库主页联系方式私下触达。收到后 72 小时内确认，修复节奏视严重程度协商；修复后在 Release 说明中致谢（可选）。

## 项目的安全设计（报告前值得一读）

Hermes Watch 是自托管巡检平台，安全模型围绕「AI/agent 不越权」构建：

1. **只读提议制**：诊断 agent 只有只读探测通道；修复动作走提案审批，执行前逐段白名单正则校验（全匹配，`rm -rf`/管道注入直接拦截），每次尝试入 `proposal_runs` 审计表，执行开关默认关闭
2. **凭据存储**：SSH 密码 Fernet 加密（主密钥在 `backend/.secret_key`，不入库）；DB 泄露不等于凭据泄露
3. **面板访问控制**：PBKDF2 口令 + HMAC 签名会话（角色签名防篡改）+ 登录限速 + session_epoch 全量撤销
4. **公开端点**：`/api/push/*`（agent 上报）走 HMAC-SHA256 签名 + 时间戳防重放；状态页/徽章走可撤销独立 token；均有限流与访问审计
5. **出站 AI**：默认关闭；开启后可选 k8sgpt 式脱敏（主机名/IP/用户名出站前替换占位符，映射不落盘）
6. **MCP 端点**：只读工具面，默认仅绑定本机

## 适用范围

面板（`backend/`、`frontend/`）、出站 Agent（`agent-go/`、sh 版）、install.sh、Dockerfile。演示数据与 mock 端点（`backend/mock_llm.py`、演示主机模拟器）不在安全边界内。

## 已知取舍

单用户/小团队自托管场景设计：未做多租户隔离、未做审计防篡改（追加式日志够用）。威胁模型里有内网堡垒，但没有把「面板管理员即系统 root」当威胁。
