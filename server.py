"""Local web dashboard at http://localhost:8765 - streams everything Jarvis does via Server-Sent Events."""

import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil

from events import bus

UI_FILE = Path(__file__).with_name("ui.html")
_net = {"t": time.time(), "io": psutil.net_io_counters()}


def net_speed():
    """Download/upload in Mbps since the last call."""
    now, io = time.time(), psutil.net_io_counters()
    dt = max(now - _net["t"], 0.1)
    down = (io.bytes_recv - _net["io"].bytes_recv) * 8 / dt / 1e6
    up = (io.bytes_sent - _net["io"].bytes_sent) * 8 / dt / 1e6
    _net.update(t=now, io=io)
    return round(down, 2), round(up, 2)


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep the terminal quiet
            pass

        def _json(self, data, code=200):
            body = json.dumps(data).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            length = int(self.headers.get("Content-Length") or 0)
            try:
                return json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return {}

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                body = UI_FILE.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/events":
                self._stream()
            elif self.path == "/api/stats":
                batt = psutil.sensors_battery()
                down, up = net_speed()
                self._json({
                    "down": down, "up": up,
                    "cpu": psutil.cpu_percent(interval=None),
                    "ram": psutil.virtual_memory().percent,
                    "disk": psutil.disk_usage("C:/").percent,
                    "battery": round(batt.percent) if batt else None,
                    "charging": bool(batt and batt.power_plugged),
                })
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            data = self._body()
            if self.path == "/api/command" and str(data.get("text", "")).strip():
                app.submit(data["text"].strip(), "typed")
                self._json({"ok": True})
            elif self.path == "/api/control":
                app.control(data.get("action", ""))
                self._json({"ok": True})
            elif self.path == "/api/confirm":
                app.answer_confirm(bool(data.get("yes")))
                self._json({"ok": True})
            else:
                self._json({"error": "bad request"}, 400)

        def _stream(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            q, snapshot = bus.subscribe()
            try:
                self.wfile.write(b"event: reset\ndata: {}\n\n")
                for event in snapshot + [{"kind": "state", **bus.state}]:
                    self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                self.wfile.flush()
                while True:
                    try:
                        event = q.get(timeout=15)
                        self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
            except OSError:
                pass  # browser tab closed
            finally:
                bus.unsubscribe(q)

    return Handler


def start(app, port=8765):
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}"
