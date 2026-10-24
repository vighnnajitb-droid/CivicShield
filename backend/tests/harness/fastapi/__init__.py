"""MINIMAL STAND-IN for FastAPI used ONLY by the offline test harness (the sandbox has no network to install FastAPI).
It runs the real handlers in app/main.py: path params, Depends, Header, Body, File, HTTPException, startup events."""
import re, inspect, asyncio, base64
class HTTPException(Exception):
    def __init__(self, status_code, detail=None): self.status_code, self.detail = status_code, detail
class _Dep:
    def __init__(self, fn): self.fn = fn
class _Hdr:
    def __init__(self, d=None): self.d = d
class _Body:
    def __init__(self, d=...): self.d = d
class _File: pass
def Depends(fn=None): return _Dep(fn)
def Header(default=None): return _Hdr(default)
def Body(default=...): return _Body(default)
def File(*a, **k): return _File()
class UploadFile:
    def __init__(self, filename, data): self.filename, self._d = filename, data
    async def read(self, n=-1): return self._d if n < 0 else self._d[:n]
class FastAPI:
    def __init__(self, **k): self.routes, self.startup, self.mounts = [], [], []
    def on_event(self, name):
        def d(fn): self.startup.append(fn); return fn
        return d
    def mount(self, *a, **k): self.mounts.append(a)
    def _r(self, method):
        def deco(path, dependencies=None, **k):
            rx = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", path) + "$")
            def reg(fn): self.routes.append((method, rx, fn, dependencies or [])); return fn
            return reg
        return deco
    def __getattr__(self, n):
        if n in ("get", "post", "put", "delete"): return self._r(n.upper())
        raise AttributeError(n)
    def dispatch(self, method, path, headers, body, query=None):
        from .responses import FileResponse
        for m, rx, fn, deps in self.routes:
            mt = rx.match(path)
            if m == method and mt:
                try:
                    for d in deps: self._resolve(d.fn, headers, body, mt.groupdict(), {})
                    r = self._resolve(fn, headers, body, mt.groupdict(), {})
                    if inspect.iscoroutine(r): r = asyncio.run(r)
                    return 200, r
                except HTTPException as e: return e.status_code, {"detail": e.detail}
        return 404, {"detail": "no route"}
    def _resolve(self, fn, headers, body, pp, cache):
        kw = {}
        for name, p in inspect.signature(fn).parameters.items():
            d = p.default
            if isinstance(d, _Dep): v = self._resolve(d.fn, headers, body, pp, cache)
            elif isinstance(d, _Hdr): v = headers.get(name.replace("_", "-").lower(), d.d)
            elif isinstance(d, _Body): v = body if body else (d.d if d.d is not ... else None)
            elif isinstance(d, _File): v = UploadFile(body["filename"], base64.b64decode(body["content_base64"]))
            elif name in pp: v = int(pp[name]) if p.annotation is int else pp[name]
            else: v = d if d is not inspect._empty else None
            kw[name] = v
        return fn(**kw)
