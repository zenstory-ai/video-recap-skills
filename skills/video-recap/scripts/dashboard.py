#!/usr/bin/env python3
"""Read-only review dashboard: zero-server HTML builder + local loopback server.

Subcommands:
    dashboard.py build <work_dir> [--video <mp4>] [--out <work_dir>/review.html]
    dashboard.py serve --root <dir> [--port 0] [--open]

Reads only canonical `timeline.json`, optional `check.json`, the workspace Markdown
(`BRIEF.md`/`MEDIA_NOTES.md`/`EDIT_PLAN.md`/`PROMO.md`/`REFERENCE_NOTES.md`), and `catalog.json`/`materials/`. Never modifies project data in `serve` mode.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import parse_qs, urlsplit

__all__ = ["build_review_html", "discover_workspace", "make_server", "main"]

_ASSET_HTML = Path(__file__).resolve().parent.parent / "assets" / "dashboard.html"
_MAX_DEPTH = 8
_MAX_DIRS = 5000
_MAX_JSON_BYTES = 2 * 1024 * 1024
_MAX_MEDIA_BYTES = 4 * 1024 * 1024 * 1024
_SKIP_DIRS = {"node_modules", "__pycache__", ".git"}
_MEDIA_TYPES = {
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
    ".m4a": "audio/mp4", ".wav": "audio/wav", ".mp3": "audio/mpeg", ".ogg": "audio/ogg",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
}
_AUDIO_EXTS = {".m4a", ".wav", ".mp3", ".ogg"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "media-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; "
        "frame-ancestors 'none'; form-action 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Cross-Origin-Resource-Policy": "same-origin",
}
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
_CHUNK = 64 * 1024


class _Refused(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _rel_of(root: Path, path: Path) -> str | None:
    try:
        rel = path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return None
    return rel.as_posix() or "."


def _resolve_under(root: Path, rel: str | None) -> Path:
    if not isinstance(rel, str) or not rel or "\0" in rel:
        raise _Refused(400, "missing path parameter")
    if (PurePosixPath(rel).is_absolute() or PureWindowsPath(rel).is_absolute()
            or PureWindowsPath(rel).drive or rel.startswith(("/", "\\"))):
        raise _Refused(403, "absolute path refused")
    if ".." in re.split(r"[\\/]", rel):
        raise _Refused(403, "parent traversal refused")
    target = (root / rel).resolve()
    if _rel_of(root, target) is None:
        raise _Refused(403, "path escapes --root")
    return target


def _unreadable_reason(path: Path) -> str | None:
    try:
        st = os.lstat(path)
    except OSError as exc:
        return str(exc.strerror or exc)
    if stat.S_ISLNK(st.st_mode):
        return "symlink refused"
    if not stat.S_ISREG(st.st_mode):
        return "not a regular file"
    if st.st_size > _MAX_JSON_BYTES:
        return "exceeds 2 MB cap"
    return None


def _read_json(path: Path) -> dict | None:
    if not path.exists() or _unreadable_reason(path):
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _pick_video(work_dir: Path, explicit: Path | None, check: dict | None) -> Path | None:
    if explicit is not None:
        return explicit.expanduser().resolve()
    if check and isinstance(check.get("media"), str):
        cand = Path(check["media"])
        if not cand.is_absolute():
            cand = work_dir / cand
        if cand.is_file():
            return cand.resolve()
    for rel_cand in ("out/final.mp4", "out/preview.mp4"):
        cand = work_dir / rel_cand
        if cand.is_file():
            return cand.resolve()
    mp4s = sorted(work_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime_ns, reverse=True)
    return mp4s[0].resolve() if mp4s else None


def _run_payload(work_dir: Path, *, root: Path | None = None, explicit_video: Path | None = None) -> dict:
    tl = _read_json(work_dir / "timeline.json") or {}
    chk = _read_json(work_dir / "check.json")
    video = _pick_video(work_dir, explicit_video, chk)

    clips = []
    cursor = 0.0
    for idx, c in enumerate(tl.get("video") or []):
        s_in, s_out = float(c.get("in", 0.0)), float(c.get("out", 0.0))
        dur = max(0.0, s_out - s_in)
        clips.append({
            "id": c.get("id") or f"v{idx + 1}", "source": str(c.get("source") or ""),
            "src_in": round(s_in, 3), "src_out": round(s_out, 3),
            "start": round(cursor, 3), "end": round(cursor + dur, 3),
        })
        cursor += dur

    notes: dict[str, str] = {}
    for fname in ("EDIT_PLAN.md", "BRIEF.md", "MEDIA_NOTES.md", "PROMO.md", "REFERENCE_NOTES.md"):
        p = work_dir / fname
        if p.is_file() and not _unreadable_reason(p):
            notes[fname] = p.read_text(encoding="utf-8", errors="replace")[:20000]

    video_url = None
    if video is not None and video.is_file():
        if root is not None:
            video_url = _rel_of(root, video)
        else:
            try:
                video_url = video.relative_to(work_dir.resolve()).as_posix()
            except ValueError:
                video_url = video.name

    title = ((tl.get("meta") or {}).get("title") or "").strip() or work_dir.name
    return {
        "title": title,
        "path": _rel_of(root, work_dir) if root is not None else ".",
        "duration_s": round(cursor, 3),
        "video_url": video_url,
        "timeline": tl,
        "clips": clips,
        "narration": (tl.get("audio") or {}).get("narration") or [],
        "subtitles": tl.get("subtitles") or [],
        "check": chk,
        "notes": notes,
    }


def build_review_html(
    work_dir: str | Path, *, video_path: str | Path | None = None, out_path: str | Path | None = None
) -> Path:
    """Write a self-contained, zero-server `review.html` for one workspace directory."""
    wdir = Path(work_dir).expanduser().resolve()
    if not wdir.is_dir():
        raise FileNotFoundError(f"work_dir not found: {wdir}")
    payload = _run_payload(wdir, explicit_video=Path(video_path) if video_path else None)
    safe_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/").replace("<!--", "<\\!--")
    html = _ASSET_HTML.read_text(encoding="utf-8").replace("__EMBEDDED_DATA__", safe_json, 1)
    target = Path(out_path).expanduser().resolve() if out_path else (wdir / "review.html")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")
    return target


def discover_workspace(root: str | Path) -> dict:
    """Bounded BFS discovery of runs (`timeline.json`) and libraries (`catalog.json` / `materials/`)."""
    root_p = Path(root).expanduser().resolve()
    runs, libraries, warnings = [], [], []
    queue = deque([(root_p, 0)])
    seen = 0
    while queue:
        cur, depth = queue.popleft()
        seen += 1
        if seen > _MAX_DIRS:
            warnings.append(f"directory scan capped at {_MAX_DIRS}")
            break
        try:
            with os.scandir(cur) as it:
                entries = list(it)
        except OSError:
            continue
        fnames = {e.name for e in entries if e.is_file(follow_symlinks=False)}
        dnames = {e.name for e in entries if e.is_dir(follow_symlinks=False)}
        rel = _rel_of(root_p, cur) or "."
        if "timeline.json" in fnames:
            tl = _read_json(cur / "timeline.json") or {}
            title = ((tl.get("meta") or {}).get("title") or "").strip() or cur.name
            runs.append({"path": rel, "title": title})
        is_lib = "catalog.json" in fnames or ("materials" in dnames and "timeline.json" not in fnames)
        if is_lib:
            cat = _read_json(cur / "catalog.json") or {}
            libraries.append({"path": rel, "name": str(cat.get("name") or cur.name)})
            continue
        if depth >= _MAX_DEPTH:
            continue
        for e in sorted(entries, key=lambda x: x.name):
            if e.name.startswith(".") or e.name in _SKIP_DIRS or e.is_symlink() or not e.is_dir(follow_symlinks=False):
                continue
            queue.append((Path(e.path), depth + 1))
    return {"runs": runs, "libraries": libraries, "warnings": warnings}


def _library_payload(root: Path, rel: str) -> dict:
    lib_dir = _resolve_under(root, rel)
    cat = _read_json(lib_dir / "catalog.json") or {}
    assets = []
    for item in cat.get("assets") or []:
        if not isinstance(item, dict):
            continue
        entry = dict(item)
        raw_path = item.get("path")
        if isinstance(raw_path, str) and raw_path:
            p = Path(raw_path)
            if not p.is_absolute():
                p = (lib_dir / p).resolve()
            rel_media = _rel_of(root, p)
            ext = p.suffix.lower()
            if rel_media and p.is_file() and ext in _MEDIA_TYPES:
                entry["media_url"] = rel_media
                entry["media_kind"] = "audio" if ext in _AUDIO_EXTS else ("image" if ext in _IMAGE_EXTS else "video")
        assets.append(entry)
    materials = []
    mat_root = lib_dir / "materials"
    if mat_root.is_dir() and not mat_root.is_symlink():
        for child in sorted(mat_root.iterdir()):
            if child.is_dir() and not child.is_symlink():
                files = sorted(f.name for f in child.iterdir() if f.is_file() and not f.is_symlink())
                materials.append({"id": child.name, "files": files})
    return {
        "path": _rel_of(root, lib_dir) or ".",
        "name": str(cat.get("name") or lib_dir.name),
        "assets": assets,
        "materials": materials,
    }


def _parse_range(header: str, size: int):
    if not header:
        return None
    m = _RANGE_RE.match(header.strip())
    if not m or (not m[1] and not m[2]):
        return "invalid"
    if not m[1]:
        length = int(m[2])
        if length == 0 or size == 0:
            return "invalid"
        return max(size - length, 0), size - 1
    start = int(m[1])
    end = int(m[2]) if m[2] else size - 1
    if start >= size or end < start:
        return "invalid"
    return start, min(end, size - 1)


def _media_path(root: Path, rel: str | None) -> Path:
    target = _resolve_under(root, rel)
    if target.suffix.lower() not in _MEDIA_TYPES:
        raise _Refused(403, "unsupported media type")
    if not target.is_file():
        raise _Refused(404, "file not found")
    if target.stat().st_size > _MAX_MEDIA_BYTES:
        raise _Refused(403, "file exceeds media size cap")
    return target


class _Handler(BaseHTTPRequestHandler):
    server_version = "video-recap-dashboard"
    sys_version = ""
    _head = False

    def __getattr__(self, name: str):
        if name.startswith("do_"):
            return self._method_not_allowed
        raise AttributeError(name)

    def _method_not_allowed(self) -> None:
        length = self.headers.get("Content-Length", "0")
        if length.isdigit() and 0 < int(length) <= 1024 * 1024:
            self.rfile.read(int(length))
        self._send_json(405, {"error": "read-only dashboard: GET/HEAD only"}, extra={"Allow": "GET, HEAD"})

    def do_GET(self) -> None:
        self._dispatch(head=False)

    def do_HEAD(self) -> None:
        self._dispatch(head=True)

    def log_message(self, fmt: str, *args) -> None:  # noqa: A002
        sys.stderr.write(f"[dashboard] {self.address_string()} {fmt % args}\n")

    def _host_allowed(self) -> bool:
        allowed = self.server.allowed_hosts  # type: ignore[attr-defined]
        host = (self.headers.get("Host") or "").strip().lower()
        if host not in allowed:
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin.strip().lower() not in {f"http://{h}" for h in allowed}:
            return False
        site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        return site not in {"cross-site", "same-site"}

    def _start(self, status: int, ctype: str, length: int, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        for k, v in {**_SECURITY_HEADERS, **(extra or {})}.items():
            self.send_header(k, v)
        self.end_headers()

    def _send_bytes(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self._start(status, ctype, len(body), extra)
        if not self._head:
            self.wfile.write(body)

    def _send_json(self, status: int, payload, extra: dict | None = None) -> None:
        self._send_bytes(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                         "application/json; charset=utf-8", extra)

    def _send_media(self, path: Path) -> None:
        size = path.stat().st_size
        ctype = _MEDIA_TYPES[path.suffix.lower()]
        span = _parse_range(self.headers.get("Range", ""), size)
        if span == "invalid":
            self._send_bytes(416, b"", ctype, {"Content-Range": f"bytes */{size}"})
            return
        start, end = span or (0, max(0, size - 1))
        length = 0 if size == 0 else (end - start + 1)
        extra = {"Accept-Ranges": "bytes"}
        if span:
            extra["Content-Range"] = f"bytes {start}-{end}/{size}"
        self._start(206 if span else 200, ctype, length, extra)
        if self._head or not length:
            return
        with path.open("rb") as fh:
            fh.seek(start)
            rem = length
            while rem:
                chunk = fh.read(min(_CHUNK, rem))
                if not chunk:
                    break
                self.wfile.write(chunk)
                rem -= len(chunk)

    def _dispatch(self, head: bool) -> None:
        self._head = head
        if not self._host_allowed():
            self._send_json(403, {"error": "Host / Origin must be loopback"})
            return
        parts = urlsplit(self.path)
        query = parse_qs(parts.query, keep_blank_values=True)
        root: Path = self.server.root  # type: ignore[attr-defined]
        qpath = (query.get("path") or [None])[0]
        try:
            if parts.path in {"/", "/index.html"}:
                self._send_bytes(200, _ASSET_HTML.read_bytes(), "text/html; charset=utf-8")
            elif parts.path == "/api/overview":
                self._send_json(200, discover_workspace(root))
            elif parts.path == "/api/run":
                rdir = _resolve_under(root, qpath)
                if not (rdir / "timeline.json").is_file():
                    raise _Refused(404, "timeline.json not found")
                self._send_json(200, _run_payload(rdir, root=root))
            elif parts.path == "/api/library":
                self._send_json(200, _library_payload(root, qpath or "."))
            elif parts.path == "/api/media":
                self._send_media(_media_path(root, qpath))
            else:
                self._send_json(404, {"error": "not found"})
        except _Refused as exc:
            self._send_json(exc.status, {"error": exc.message})
        except (BrokenPipeError, ConnectionResetError):
            pass


class _DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, root: Path, port: int):
        super().__init__(("127.0.0.1", port), _Handler)
        self.root = root
        bound = self.server_address[1]
        self.allowed_hosts = {f"127.0.0.1:{bound}", f"localhost:{bound}"}


def make_server(root: str | Path, port: int = 0) -> _DashboardServer:
    root_p = Path(root).expanduser().resolve()
    if not root_p.is_dir():
        raise SystemExit(f"--root is not a directory: {root_p}")
    return _DashboardServer(root_p, port)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only review dashboard (zero-server HTML builder + local server).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="Generate self-contained review.html for one work_dir.")
    p_build.add_argument("work_dir", help="directory containing timeline.json")
    p_build.add_argument("--video", default=None, help="explicit MP4 path (default: check.json media, out/final.mp4, out/preview.mp4, or newest *.mp4)")
    p_build.add_argument("--out", default=None, help="output HTML path (default: <work_dir>/review.html)")

    p_serve = sub.add_parser("serve", help="Run strictly read-only loopback server over --root.")
    p_serve.add_argument("--root", required=True, help="root directory containing runs and libraries")
    p_serve.add_argument("--port", type=int, default=0, help="port (0 = random free port)")
    p_serve.add_argument("--open", action="store_true", help="open default browser")

    args = ap.parse_args(argv)
    if args.cmd == "build":
        out = build_review_html(args.work_dir, video_path=args.video, out_path=args.out)
        print(json.dumps({"status": "built", "html": str(out)}, ensure_ascii=False))
        return 0
    server = make_server(args.root, args.port)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"dashboard: {url} (root: {server.root}; Ctrl+C to stop)", flush=True)
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
