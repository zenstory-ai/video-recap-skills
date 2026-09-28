#!/usr/bin/env python3
"""Strictly read-only local dashboard over libraries, projects and recap runs.

    python3 scripts/dashboard_server.py --root <dir> [--port 0] [--host 127.0.0.1] [--open]

Serves ``assets/dashboard/`` and a small GET-only JSON API built by ``dashboard_data``.
Binds to loopback only, checks Host / Origin against the bound port (DNS-rebinding guard),
answers every method other than GET / HEAD with 405, and serves only files that resolve
inside ``--root``. Nothing here writes to disk. Runs in the foreground until Ctrl+C.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import re
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import dashboard_data
import dashboard_io
from dashboard_io import Refused

ASSET_DIR = Path(__file__).resolve().parent.parent / "assets" / "dashboard"
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/tokens.css": ("tokens.css", "text/css; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/views.js": ("views.js", "text/javascript; charset=utf-8"),
}
API = {
    "/api/overview": lambda root, query: dashboard_data.overview(root),
    "/api/search": lambda root, query: dashboard_data.search(root, _param(query, "q")),
    "/api/library": lambda root, query: dashboard_data.library_detail(root, _path(query)),
    "/api/project": lambda root, query: dashboard_data.project_detail(root, _path(query)),
    "/api/run": lambda root, query: dashboard_data.run_detail(root, _path(query)),
}
SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; media-src 'self'; img-src 'self' data:; "
                                "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
                                "form-action 'none'"),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Cross-Origin-Resource-Policy": "same-origin",
}
RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
CHUNK = 64 * 1024


def _param(query: dict, name: str):
    return (query.get(name) or [None])[0]


def _path(query: dict) -> str:
    rel = _param(query, "path")
    if rel is None:
        raise Refused(400, "缺少 path 参数")
    return rel


def _parse_range(header: str, size: int):
    """``(start, end)`` inclusive for a single byte range, None for a full response,
    or ``"invalid"`` when the range cannot be satisfied."""
    if not header:
        return None
    match = RANGE_RE.match(header.strip())
    if not match or (not match[1] and not match[2]):
        return "invalid"
    if not match[1]:
        length = int(match[2])
        if length == 0 or size == 0:
            return "invalid"
        return max(size - length, 0), size - 1
    start = int(match[1])
    end = int(match[2]) if match[2] else size - 1
    if start >= size or end < start:
        return "invalid"
    return start, min(end, size - 1)


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "video-recap-dashboard"
    sys_version = ""
    _head = False

    # Every method other than GET / HEAD lands here (http.server dispatches ``do_<METHOD>``).
    def __getattr__(self, name):
        if name.startswith("do_"):
            return self._method_not_allowed
        raise AttributeError(name)

    def _method_not_allowed(self):
        # Drain a small request body so the client reads the 405 instead of a reset socket.
        length = self.headers.get("Content-Length", "0")
        if length.isdigit() and 0 < int(length) <= 1024 * 1024:
            self.rfile.read(int(length))
        self._send_json(405, {"error": "只读 dashboard：只允许 GET / HEAD"}, extra={"Allow": "GET, HEAD"})

    def do_GET(self):
        self._dispatch(head=False)

    def do_HEAD(self):
        self._dispatch(head=True)

    def log_message(self, format, *args):  # noqa: A002 - signature fixed by http.server
        sys.stderr.write(f"[dashboard] {self.address_string()} {format % args}\n")

    # --- guards --------------------------------------------------------------------------
    def _host_allowed(self) -> bool:
        allowed = self.server.allowed_hosts
        host = (self.headers.get("Host") or "").strip().lower()
        if host not in allowed:
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin.strip().lower() not in {f"http://{h}" for h in allowed}:
            return False
        # Browsers label cross-site subresource requests (<img>, <video>) even without Origin.
        site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        return site not in {"cross-site", "same-site"}

    # --- responses -----------------------------------------------------------------------
    def _start(self, status: int, content_type: str, length: int, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        for key, value in {**SECURITY_HEADERS, **(extra or {})}.items():
            self.send_header(key, value)
        self.end_headers()

    def _send_bytes(self, status: int, body: bytes, content_type: str, extra=None):
        self._start(status, content_type, len(body), extra)
        if not self._head:
            self.wfile.write(body)

    def _send_json(self, status: int, payload, extra=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8", extra)

    def _send_media(self, path: Path):
        size = path.stat().st_size
        content_type = dashboard_io.MEDIA_TYPES[path.suffix.lower()]
        span = _parse_range(self.headers.get("Range", ""), size)
        if span == "invalid":
            self._send_bytes(416, b"", content_type, {"Content-Range": f"bytes */{size}"})
            return
        start, end = span or (0, size - 1)
        length = max(end - start + 1, 0)
        extra = {"Accept-Ranges": "bytes"}
        if span:
            extra["Content-Range"] = f"bytes {start}-{end}/{size}"
        self._start(206 if span else 200, content_type, length, extra)
        if self._head or not length:
            return
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining:
                chunk = handle.read(min(CHUNK, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _dispatch(self, head: bool):
        self._head = head
        if not self._host_allowed():
            self._send_json(403, {"error": "Host / Origin 不是本机回环地址"})
            return
        parts = urlsplit(self.path)
        query = parse_qs(parts.query, keep_blank_values=True)
        root = self.server.root
        try:
            if parts.path in ASSETS:
                name, content_type = ASSETS[parts.path]
                self._send_bytes(200, (ASSET_DIR / name).read_bytes(), content_type)
            elif parts.path == "/api/media":
                self._send_media(dashboard_io.media_path(root, _path(query)))
            elif parts.path in API:
                self._send_json(200, API[parts.path](root, query))
            else:
                self._send_json(404, {"error": "未知地址"})
        except Refused as exc:
            self._send_json(exc.status, {"error": exc.message})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # noqa: BLE001 - one bad request must not stop the server
            self.log_message("error on %s: %r", self.path, exc)
            self._send_json(500, {"error": f"内部错误: {type(exc).__name__}"})


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, root: Path, host: str, port: int):
        super().__init__((host, port), DashboardHandler)
        self.root = root
        bound = self.server_address[1]
        self.allowed_hosts = {f"127.0.0.1:{bound}", f"localhost:{bound}", f"{host.lower()}:{bound}"}


def _loopback_host(host: str) -> str:
    if host.lower() == "localhost":
        return "127.0.0.1"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is None or address.version != 4 or not address.is_loopback:
        raise SystemExit(f"--host 只允许 IPv4 回环地址（127.0.0.1 或 localhost），收到: {host}")
    return host


def make_server(root, host: str = "127.0.0.1", port: int = 0) -> DashboardServer:
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"--root 不是目录: {root}")
    return DashboardServer(root, _loopback_host(host), port)


def main(argv=None):
    ap = argparse.ArgumentParser(description="video-recap 只读 dashboard：浏览资源库、项目与运行，不修改任何文件。")
    ap.add_argument("--root", required=True, help="要浏览的目录（资源库、项目、work_dir 都在它下面发现）")
    ap.add_argument("--port", type=int, default=0, help="端口，默认 0 = 随机空闲端口")
    ap.add_argument("--host", default="127.0.0.1", help="只接受回环地址，默认 127.0.0.1")
    ap.add_argument("--open", action="store_true", help="启动后用默认浏览器打开")
    args = ap.parse_args(argv)
    server = make_server(args.root, args.host, args.port)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"只读 dashboard: {url}  (root: {server.root}；Ctrl+C 退出)", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
