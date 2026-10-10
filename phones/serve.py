"""Optional local web server for the catalog view.

Not required: web/index.html works when opened directly from disk (it loads
data/catalog.js), and the project can be put on any static host. This server
exists as a convenience and exposes only the app and the catalog data files,
never the raw crawl data.
"""

from __future__ import annotations

import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .store import CATALOG_JS, INDEX, TRENDS_JS

WEB = Path(__file__).resolve().parent.parent / "web"
ROUTES = {
    "/": WEB / "index.html",
    "/index.html": WEB / "index.html",
    "/app.js": WEB / "app.js",
    "/style.css": WEB / "style.css",
    "/data/index.json": INDEX,
    "/data/catalog.js": CATALOG_JS,
    "/data/trends.js": TRENDS_JS,
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = ROUTES.get(self.path.split("?", 1)[0])
        if path is None or not path.exists():
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", (mimetypes.guess_type(path.name)[0] or "application/octet-stream") + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")  # always show the latest crawl
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args) -> None:  # keep the terminal quiet
        pass


def serve(host: str = "127.0.0.1", port: int = 8787) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"catalog: http://{host}:{port}/   (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
