#!/usr/bin/env python3
"""
dashboard_api.py — Hyperliquid CopyBot Web Panel API
Built-in http.server (no Flask required) | Port 5050
"""

import json
import os
import time
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# ------------------------------------------------------------------
# Config paths (absolute)
# ------------------------------------------------------------------
WORK_DIR    = "/app/data/所有对话/主对话"
STATE_FILE  = f"{WORK_DIR}/bot_state.json"
CONFIG_FILE = f"{WORK_DIR}/config.json"
MODE_FILE   = f"{WORK_DIR}/mode_switch.json"

# Global API start time
_api_start = time.time()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def _read_json(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return default


def _safe_config():
    """Return config with SECRET_KEY stripped."""
    cfg = _read_json(CONFIG_FILE)
    if cfg is None:
        return {}
    safe = dict(cfg)
    safe.pop("SECRET_KEY", None)
    safe.pop("secret_key", None)
    return safe


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ------------------------------------------------------------------
# CORS pre-flight & simple router inside the handler
# ------------------------------------------------------------------
class CopyBotHandler(BaseHTTPRequestHandler):

    def _send_json(self, status, data):
        body = json.dumps(data, ensure_ascii=False)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def _send_html(self, status, content):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(content.encode("utf-8"))

    def _send_text(self, status, content,ctype="text/plain"):
        self.send_response(status)
        self.send_header("Content-Type", ctype+"; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(content.encode("utf-8"))

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path

        # ---- /api/health ----
        if path == "/api/health":
            uptime_s = round(time.time() - _api_start, 1)
            return self._send_json(200, {
                "status": "ok",
                "uptime": f"{uptime_s}s",
                "api_start": datetime.fromtimestamp(_api_start).strftime("%Y-%m-%dT%H:%M:%S"),
            })

        # ---- /api/state ----
        if path == "/api/state":
            if not os.path.exists(STATE_FILE):
                return self._send_json(404, {"error": "bot not running"})
            state = _read_json(STATE_FILE)
            if state is None:
                return self._send_json(500, {"error": "bot state file unreadable"})
            return self._send_json(200, state)

        # ---- /api/config ----
        if path == "/api/config":
            cfg = _safe_config()
            if not cfg:
                return self._send_json(404, {"error": "config not found"})
            return self._send_json(200, cfg)

        # ---- /dashboard ----
        if path == "/dashboard" or path == "/":
            dash_path = f"{WORK_DIR}/dashboard.html"
            if not os.path.exists(dash_path):
                return self._send_text(404, "dashboard.html not found")
            with open(dash_path, "r", encoding="utf-8") as f:
                content = f.read()
            return self._send_html(200, content)

        # ---- /mobile ----
        if path == "/mobile":
            mobile_path = f"{WORK_DIR}/dashboard_mobile.html"
            if not os.path.exists(mobile_path):
                return self._send_text(404, "dashboard_mobile.html not found")
            with open(mobile_path, "r", encoding="utf-8") as f:
                content = f.read()
            return self._send_html(200, content)

        # ---- 404 ----
        self._send_text(404, "Not found: " + path)

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))

        # ---- /api/mode ----
        if path == "/api/mode":
            try:
                body = self.rfile.read(length)
                data = json.loads(body.decode("utf-8"))
            except (ValueError, IOError):
                return self._send_json(400, {"error": "invalid JSON body"})

            mode = str(data.get("mode", "")).strip().lower()
            if mode not in ("live", "monitor"):
                return self._send_json(400, {'error': 'mode must be "live" or "monitor"'})

            payload = {
                "mode": mode,
                "live": mode == "live",
                "requested_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            }
            try:
                _write_json(MODE_FILE, payload)
            except Exception as e:
                return self._send_json(500, {"error": f"failed to write mode_switch.json: {e}"})

            return self._send_json(200, {"ok": True, "mode": mode, "file": MODE_FILE})

        self._send_text(404, "Not found: " + path)

    def log_message(self, fmt, *args):
        # Suppress default stderr noise; print to stdout instead
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {args[0]}", flush=True)


# ------------------------------------------------------------------
# Threaded HTTPServer so it stays responsive
# ------------------------------------------------------------------
class ThreadedHTTPServer(HTTPServer):
    """HTTP server that handles each request in its own thread."""
    def process_request(self, request, client_address):
        t = threading.Thread(target=HTTPServer.process_request, args=(self, request, client_address), daemon=True)
        t.start()


def main():
    host = "0.0.0.0"
    port = 5050
    server = ThreadedHTTPServer((host, port), CopyBotHandler)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] Starting Dashboard API on http://{host}:{port}")
    print(f"  State file : {STATE_FILE}")
    print(f"  Dashboard  : http://localhost:{port}/dashboard")
    print(f"  Health     : http://localhost:{port}/api/health")
    print(f"  Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
