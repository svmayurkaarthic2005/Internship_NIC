"""
Frontend dev server with no-cache headers.
Run with: python serve_frontend.py
"""
import http.server
import socketserver
import os

PORT = 3000
DIRECTORY = os.path.join(os.path.dirname(__file__), "frontend")


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


with socketserver.TCPServer(("", PORT), NoCacheHandler) as httpd:
    print(f"✅ Frontend dev server running at http://127.0.0.1:{PORT}/chatbot.html")
    print(f"   Serving: {DIRECTORY}")
    print(f"   Cache-Control: no-store (JS changes load immediately)")
    print(f"   Press Ctrl+C to stop.")
    httpd.serve_forever()
