# Hermes Watch 出站 Agent（Go 单二进制）

纯 Go 标准库实现，静态编译、零依赖、只读采集，每 60s 向面板推送一次。

## 编译

```bash
cd agent-go
GOOS=linux   GOARCH=amd64 go build -ldflags "-s -w" -o hermes-watch-agent-linux-amd64
GOOS=windows GOARCH=amd64 go build -ldflags "-s -w" -o hermes-watch-agent.exe
```

## 部署

1. 面板 → 接入中心 → 为目标主机生成 Token
2. 目标机上：
   ```bash
   HW_URL=http://<面板地址>:8800 HW_TOKEN=hw_xxx ./hermes-watch-agent-linux-amd64
   ```
   （HW_INTERVAL 可调采集间隔，最小 5s）

## 上报内容

- 指标：cpu / mem / disk / load1 / net_in / net_out（直读 /proc，df 一次）
- extras：Top5 进程（/proc/*/stat 解析）、systemd failed 单元、letsencrypt 证书剩余天数
- 鉴权：`HMAC-SHA256(token, body + timestamp)` 请求头签名，±300s 防重放；
  面板验证通过后 extras 入库并在主机详情页展示，同时驱动规则引擎产生发现

## 本地测试

`wsl-test.sh` + `wsl_receiver.py`：在 WSL 里起一个一次性 HMAC 校验接收器，
验证 Linux 二进制的采集与签名（ receiver 打印 VALID + 采集内容）。
