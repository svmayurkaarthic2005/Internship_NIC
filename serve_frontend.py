"""
Frontend dev server with no-cache headers.
Run with: python serve_frontend.py
"""
import sys
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import http.server
import socketserver
import os
from dotenv import dotenv_values

PORT = 3000
DIRECTORY = os.path.join(os.path.dirname(__file__), "frontend")


def write_config_js():
    """frontend/js/config.js is generated, not committed -- it carries the
    backend URL from .env (API_BASE_URL) so it's never hardcoded in auth.js /
    chat.js. Regenerated on every server start, so editing .env and
    restarting is enough."""
    env = dotenv_values(os.path.join(os.path.dirname(__file__), ".env"))
    api_base_url = env.get("API_BASE_URL", "http://localhost:8000")
    config_path = os.path.join(DIRECTORY, "js", "config.js")
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(f"window.API_BASE_URL = {api_base_url!r};\n")
    print(f"   API_BASE_URL -> {api_base_url} (frontend/js/config.js)")


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP handler that disables all caching for development."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def log_message(self, format, *args):
        # Only log non-200 responses to reduce noise
        if args and not str(args[1]).startswith("2"):
            super().log_message(format, *args)


class ThreadingHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True


write_config_js()
with ThreadingHTTPServer(("", PORT), NoCacheHandler) as httpd:
    print(f"✅ Frontend dev server running at http://127.0.0.1:{PORT}/chatbot.html")
    print(f"   Serving: {DIRECTORY}")
    print(f"   Cache-Control: no-store (JS changes load immediately)")
    print(f"   Press Ctrl+C to stop.")
    httpd.serve_forever()
