"""IM 双向 v2 端到端实测：隔离数据目录 + 伪 Telegram API（真 HTTP）驱动生产轮询协程。
用法：HW_DATA_DIR=<临时目录> venv/Scripts/python e2e_tg_v2.py
不碰用户面板的真实库；结束后打印伪 API 收到的 sendMessage 回执与落库状态。"""
import asyncio
import json
import os
import shutil
import sys

sys.path.insert(0, '.')
import httpx
from http.server import BaseHTTPRequestHandler, HTTPServer

from app import db, notify

CHAT = "-100999"
CHATS = [
    {"id": int(CHAT), "text": "status"},
    {"id": int(CHAT), "text": "/help"},
    {"id": int(CHAT), "text": "ask 现在集群怎么样？"},
    {"id": int(CHAT) + 1, "text": "status"},   # 陌生 chat → 静默
]
REPLIES: list[dict] = []
FINDING_ID = None

DATA_DIR = os.environ.get("HW_DATA_DIR", r"C:\Users\45111\AppData\Local\Temp\hw-e2e-tg-v2")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "hermes-watch.db")
db.DB_PATH = type(db.DB_PATH)(DB_PATH) if hasattr(db.DB_PATH, '__class__') else DB_PATH


class FakeTG(BaseHTTPRequestHandler):
    def _json(self, data):
        b = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if "/getUpdates" in self.path:
            off = body.get("offset") or 0
            results = []
            for u in CHATS:
                results.append({"update_id": u["id"], "message": {"chat": {"id": u["id"]}, "text": u["text"]}})
            self._json({"ok": True, "result": [u for u in results if u["update_id"] >= off]})
        elif "/sendMessage" in self.path:
            REPLIES.append(body)
            self._json({"ok": True})
        else:
            self._json({"ok": False})

    def log_message(self, *a):
        pass


async def run_loop():
    while True:
        try:
            c = notify.conf()
            if not notify._setting_on("tg_command") or c["channel"] != "telegram" or not c["url"]:
                await asyncio.sleep(15)
                continue
            offset = int((db.query_one("SELECT value FROM settings WHERE key='tg_ack_offset'")
                          or {}).get("value") or 0)
            data = notify._tg_call(c["url"], "getUpdates", offset=offset, timeout=30,
                                   allowed_updates=["message"])
            consumed = 0
            for upd in (data.get("result") or []):
                msg = upd.get("message") or {}
                if str(msg.get("chat", {}).get("id")) == str(c["chat_id"]) and msg.get("text"):
                    await notify._handle_tg_command(msg["text"], msg["chat"]["id"])
                    consumed += 1
            db.execute("INSERT INTO settings(key,value) VALUES('tg_ack_offset',?) "
                       "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(offset + consumed),))
        except Exception:
            pass
        await asyncio.sleep(3)


async def main():
    global FINDING_ID
    db.init_db()
    # 种子：mock 主机 + crit 发现 + 通知/命令配置（隔离库，随便造）
    for k, v in [("notify_channel", "telegram"), ("webhook_url", "E2EFAKETOKEN"),
                 ("telegram_chat_id", CHAT), ("tg_command", "on"), ("hw_lang", "zh"),
                 ("ai_provider", '{"base_url":"http://127.0.0.1:9/v1"}'), ("ai_outbound", "off")]:
        db.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
    hid = db.execute("INSERT INTO hosts(name,hostname,group_name,mock,created_at) "
                     "VALUES('e2e-node','10.0.0.9','t',1,?)", (db.now(),))
    fid = db.execute("INSERT INTO findings(host_id,ts,type,severity,title,detail,evidence) "
                     "VALUES(?,?,?,?,?,?,?)", (hid, db.now(), "memory", "crit", "内存超限", "e2e", "{}"))
    FINDING_ID = fid
    print(f"Test finding created: id={fid}")

    srv = HTTPServer(("127.0.0.1", 18791), FakeTG)
    notify._tg_call = lambda url, method, **payload: httpx.post(
        f"http://127.0.0.1:{srv.server_port}{url}/{method}", json=payload, timeout=10).json()

    task = asyncio.create_task(run_loop())
    try:
        await asyncio.sleep(15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    finally:
        srv.shutdown()
    print("== 伪 Telegram API 收到的回执 ==")
    for r in REPLIES:
        print(json.dumps(r, ensure_ascii=False)[:400])
    print("== 落库状态 ==")
    print("tg_ack_offset:", (db.query_one("SELECT value FROM settings WHERE key='tg_ack_offset'") or {}).get("value"))
    row = db.query_one("SELECT status FROM proposals WHERE host_id=?", (hid,))
    print("proposal status:", row["status"] if row else None)
    ok = (
        len(REPLIES) == 3  # 4 条消息，陌生 chat 静默 → 3 条回执
        and any("概览" in r["text"] for r in REPLIES)
        and any("可用命令" in r["text"] for r in REPLIES)
        and any("AI 外发未开启" in r["text"] for r in REPLIES)
        and any("诊断完成" in r["text"] and "approve" in r["text"] for r in REPLIES)
        and (db.query_one("SELECT value FROM settings WHERE key='tg_ack_offset'") or {}).get("value") == "4"
        and row and row["status"] == "pending"  # 只诊断未审批 → pending
    )
    db.execute("DELETE FROM proposals WHERE host_id=?", (hid,))
    db.execute("DELETE FROM findings WHERE host_id=?", (hid,))
    db.execute("DELETE FROM hosts WHERE id=?", (hid,))
    print("E2E_RESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


asyncio.run(main())
