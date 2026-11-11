"""Serve static frontend files and forward same-origin /api requests to Django."""
from http.client import HTTPConnection
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import os
from urllib.parse import urlsplit

DIRECTORY = Path(__file__).resolve().parent
ASSETS = {"/", "/index.html", "/styles.css", "/app.js", "/tailwind.css"}
HOP_HEADERS = {"connection", "keep-alive", "transfer-encoding", "proxy-authenticate",
               "proxy-authorization", "te", "trailer", "upgrade"}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIRECTORY), **kwargs)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path.startswith("/api/"):
            self.proxy()
        elif path in ASSETS:
            super().do_GET()
        else:
            self.send_error(404)

    def do_HEAD(self):
        if urlsplit(self.path).path in ASSETS:
            super().do_HEAD()
        else:
            self.send_error(404)

    def proxy(self):
        if not urlsplit(self.path).path.startswith("/api/"):
            self.send_error(404)
            return
        backend = HTTPConnection("127.0.0.1", int(os.getenv("LEDGER_API_PORT", "51801")), timeout=60)
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        headers = {key: value for key, value in self.headers.items() if key.lower() not in HOP_HEADERS}
        try:
            backend.request(self.command, self.path, body=body, headers=headers)
            response = backend.getresponse()
            data = response.read()
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in HOP_HEADERS | {"content-length", "server", "date"}:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (OSError, ValueError):
            data = b'{"error":"The API is unavailable. Start Django and try again."}'
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        finally:
            backend.close()

    do_POST = do_PATCH = do_DELETE = proxy


if __name__ == "__main__":
    port = int(os.getenv("LEDGER_WEB_PORT", "51800"))
    print(f"Household Ledger: http://127.0.0.1:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()