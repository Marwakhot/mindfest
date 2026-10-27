"""Serve the Inventra frontend on http://localhost:3000.

The backend's default CORS setting allows exactly this origin, so no backend change is needed.
Open http://localhost:3000 (not 127.0.0.1:3000, which is a different origin).
Point at another API with ?api=http://host:port in the URL.
"""
import http.server
import socketserver
import sys
from pathlib import Path

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
ROOT = Path(__file__).resolve().parent


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(ROOT), **k)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()


socketserver.TCPServer.allow_reuse_address = True
with socketserver.TCPServer(("localhost", PORT), Handler) as httpd:
    print(f"Inventra frontend on http://localhost:{PORT}  (API: http://127.0.0.1:8000)")
    httpd.serve_forever()
