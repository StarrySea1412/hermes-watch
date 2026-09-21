# v0.5.0 Release Notes（稿）

> 打 tag 前把三段叙事按「用户可感知」整理；发布时对照本稿 + git log 校对版本号与细节。
> 注意：README 的 CI 数字（200+/269 项回归、25 项前端）同步更新。

---

## Hermes Watch v0.5.0 — 双向告警第一步、Kuma 没有的 DoH 拨测、30 秒在线演示

### 1. Telegram 双向告警（IM 对话的第一级）

告警不再只能看，还能回。设置页开启 `tg_ack` 后，Telegram 告警消息直接回复
`ack <ID>` 即确认告警——停止 crit 周期重发，面板同步状态。长轮询实现，
无需公网入站端口。这是「IM 双向对话」路线的第一步：管道已铺好，
下一步是回复即查询/审批。

### 2. DoH 拨测（RFC 8484）— Uptime Kuma 都没有的差异化项

拨测家族第六类：URL / TCP / DNS / **DoH** / ICMP / Push。DNS 线格式编解码
直发 DoH 端点（默认 `https://1.1.1.1/dns-query`，可自定义任意 RFC 8484 端点），
条件引擎（记录类型 + 期望答案）、双阈值防抖、心跳条带图、下线/恢复通知——
全套既有机制照走。加密 DNS 时代，监控「你的递归解析器」而不再只是明文 53 端口。

### 3. 在线演示站（HW_DEMO_MODE）— 30 秒摸到真实界面

```bash
docker compose -f compose.demo.yml up -d --build
```

一条命令起一台只读演示站：GET 全放行看个够（巡检曲线/根因卡/证据链/拓扑/
拨测/报告），写操作一律 423，密钥类字段出口脱敏，前端自动渲染只读视图 +
常驻横幅。挂到公网 + HTTPS 反代，就是 README / 社区帖的 demo 链接。
（部署指南：`docs/demo-site.md`）

### 同时包含

- 流式回答脱敏映射收口（done 事件占位符还原 + 前缀误伤修复）
- 拓扑图布局修复（环半径自适应 / 起始角错开 / 图表初始化竞态）
- rollup 显式桶（面板与测试进程的聚合竞态结构性消除）
- backups 还原 Windows 文件锁重试
- 回归测试 269 项全绿（较 v0.4.0 +46），前端组件测试 25 项

### 自部署

```bash
./install.sh          # Linux/macOS 一键（可 --systemd）
# 或 docker build -t hermes-watch . && docker run -p 8800:8800 -v hermes-data:/data hermes-watch
```

单进程 + SQLite，2GB 内存盒子可跑。数据不出你的机器——AI 外发默认关闭，
LLM BYO（OpenAI 兼容协议全支持）。

### 发布检查单

- [ ] demo 站真机部署 + HTTPS 反代 + README 顶部链接
- [ ] `git tag v0.5.0 && git push --tags`（CI 自动发布 agent 三平台二进制）
- [ ] GitHub Release 页贴本稿（英文版另行翻译，或双语混排）
- [ ] r/selfhosted / Show HN / V2EX / HelloGitHub 帖子：demo 链接第一位，GitHub 第二位
- [ ] Gitee 凭据配置后同步 push（国内分发线）
