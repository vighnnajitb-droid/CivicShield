import sys, os, json, http.server, threading
HERE = os.path.dirname(__file__); sys.path.insert(0, HERE); sys.path.insert(1, os.path.join(HERE, "..", ".."))
from app import main
from fastapi.responses import FileResponse
for f in main.app.startup: f()
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _do(self, method):
        n = int(self.headers.get("Content-Length") or 0); raw = self.rfile.read(n) if n else b""
        try: body = json.loads(raw) if raw else {}
        except Exception: body = {}
        hdr = {k.lower(): v for k, v in self.headers.items()}
        try: code, res = main.app.dispatch(method, self.path.split("?")[0], hdr, body)
        except Exception as e:
            import traceback; traceback.print_exc(); code, res = 500, {"detail": repr(e)}
        if isinstance(res, FileResponse): data, ct = open(res.path, "rb").read(), res.media_type
        else: data, ct = json.dumps(res, default=str).encode(), "application/json"
        self.send_response(code); self.send_header("Content-Type", ct); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self): self._do("GET")
    def do_POST(self): self._do("POST")
    def do_PUT(self): self._do("PUT")
    def do_DELETE(self): self._do("DELETE")
http.server.ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("PORT", "8000"))), H).serve_forever()
