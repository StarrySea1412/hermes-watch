"""One-shot HMAC-verifying receiver for testing the Linux Go agent in WSL."""
import hashlib
import hmac
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

TOKEN = sys.argv[1].encode()


class Receiver(BaseHTTPRequestHandler):
    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        ts = self.headers.get("X-HW-Timestamp", "")
        sig = self.headers.get("X-HW-Signature", "")
        mac = hmac.new(TOKEN, raw + ts.encode(), hashlib.sha256)
        ok = hmac.compare_digest(mac.hexdigest(), sig)
        data = json.loads(raw)
        print("HMAC:", "VALID" if ok else "INVALID")
        print("metrics:", {k: data.get(k) for k in ("cpu", "mem", "disk", "load1", "net_in", "net_out")})
        ex = data.get("extra") or {}
        print("top_proc present:", bool(ex.get("top_proc")))
        print("top_proc head:", (ex.get("top_proc") or "").splitlines()[0:2])
        print("failed_services:", ex.get("failed_services"))
        print("cert_days_left:", ex.get("cert_days_left"))

        def w(text):
            self.wfile.write(text.encode())

        w("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 13\r\n\r\n")
        w('{"ok": true}\n')
        self.server.done = True

    def log_message(self, *a):
        pass


srv = HTTPServer(("127.0.0.1", 18899), Receiver)
srv.done = False
while not srv.done:
    srv.handle_request()
