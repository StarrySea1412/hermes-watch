"""Tiny OpenAI-compatible mock endpoint for demoing AI narration (127.0.0.1:18777).

Local-only stand-in so the AI narration path can be demoed without a real LLM:
  py backend/mock_llm.py   →  设置页 Base URL http://127.0.0.1:18777/v1（API Key 留空）
"""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json

REPLIES = {
    "disk": "journal 与 /var/log/app 下的历史日志持续写入且未轮转，38G 占用直接吃满磁盘；优先执行 journal vacuum 并为应用日志配置 logrotate。",
    "memory": "ps 输出里 /opt/app/worker.py 的 RSS 占 91% 且随运行时长线性增长，符合典型内存泄漏特征；建议低峰期重启并排查其缓存逻辑。",
    "login": "root 从 185.220.101.34（Tor 出口特征段）成功登录且会话仍在，属于高危入侵迹象；建议立即封禁该 IP 并轮换 root 凭据。",
    "cpu": "CPU 持续越过告警阈值，证据输出可见具体进程占用；建议结合 top 定位热点进程。",
}


def _pick(prompt: str) -> str:
    if "登录" in prompt or "lastb" in prompt or "still logged in" in prompt:
        return "login"
    if "内存" in prompt or "RSS" in prompt or "ps aux" in prompt:
        return "memory"
    if "磁盘" in prompt or "du -" in prompt or "journalctl" in prompt:
        return "disk"
    return "cpu"


class MockLLM(BaseHTTPRequestHandler):
    def do_GET(self):
        # OpenAI 兼容 /models：配合设置页「获取模型列表」按钮
        if self.path.rstrip("/").endswith("models"):
            out = json.dumps({"data": [{"id": m, "owned_by": "hermes-mock"}
                                       for m in ("mock-7b", "mock-14b", "mock-72b-instruct")]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(out)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n)) if n else {}
        prompt = body.get("messages", [{}])[0].get("content", "")
        content = REPLIES[_pick(prompt)]
        out = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


print("mock LLM on 127.0.0.1:18777")
HTTPServer(("127.0.0.1", 18777), MockLLM).serve_forever()
