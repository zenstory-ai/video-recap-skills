"""Path gate, capped JSON reads and marker discovery for the read-only dashboard.

Every request path goes through ``resolve_under``; every artifact read goes through
``read_json`` (size-capped, never raises). ``discover`` walks the dashboard root with a
bounded depth and directory count. Nothing here writes to disk.
"""
from __future__ import annotations

import json
import os
import stat
import re
from collections import deque
from pathlib import Path, PurePosixPath, PureWindowsPath

LIBRARY_FILE = "library.json"
PROJECT_FILE = "recap_project.json"
RUN_FILE = "recap_run_manifest.json"
MARKERS = (LIBRARY_FILE, PROJECT_FILE, RUN_FILE)

MAX_DEPTH = 8
MAX_DIRS = 5000
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_RAW_CHARS = 20000
MAX_MEDIA_BYTES = 4 * 1024 * 1024 * 1024
SKIP_DIR_NAMES = {"node_modules", "__pycache__"}
MEDIA_TYPES = {
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
    ".m4a": "audio/mp4", ".wav": "audio/wav", ".mp3": "audio/mpeg",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
}
VIDEO_EXTS = {".mp4", ".mov", ".webm"}
AUDIO_EXTS = {".m4a", ".wav", ".mp3"}


class Refused(Exception):
    """A request that must not be served; ``status`` is the HTTP code to answer with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def rel_of(root: Path, path) -> str | None:
    """Root-relative POSIX path of ``path`` after resolving symlinks, or None when outside."""
    try:
        rel = Path(path).resolve().relative_to(root)
    except (ValueError, OSError):
        return None
    return rel.as_posix() or "."


def dir_of(root: Path, rel: str) -> Path:
    return root if rel == "." else root / rel


def resolve_under(root: Path, rel) -> Path:
    """Resolve a root-relative request path; refuse absolute paths, ``..`` and escapes."""
    if not isinstance(rel, str) or not rel or "\0" in rel:
        raise Refused(400, "缺少 path 参数")
    if (PurePosixPath(rel).is_absolute() or PureWindowsPath(rel).is_absolute()
            or PureWindowsPath(rel).drive or rel.startswith(("/", "\\"))):
        raise Refused(403, "只接受 --root 内的相对路径")
    if ".." in re.split(r"[\\/]", rel):
        raise Refused(403, "路径不能包含 ..")
    target = (root / rel).resolve()
    if rel_of(root, target) is None:
        raise Refused(403, "路径解析后不在 --root 内")
    return target


def media_path(root: Path, rel) -> Path:
    """A servable media file: inside root, whitelisted extension, within the size cap."""
    target = resolve_under(root, rel)
    if target.suffix.lower() not in MEDIA_TYPES:
        raise Refused(403, "不支持的媒体类型")
    if not target.is_file():
        raise Refused(404, "文件不存在")
    if target.stat().st_size > MAX_MEDIA_BYTES:
        raise Refused(403, "文件超过大小上限")
    return target


def media_entry(root: Path, path) -> dict | None:
    """``{path, kind, name, size}`` for a previewable file inside root, else None."""
    path = Path(path)
    rel = rel_of(root, path)
    ext = path.suffix.lower()
    if rel is None or ext not in MEDIA_TYPES or not path.is_file():
        return None
    kind = "video" if ext in VIDEO_EXTS else "audio" if ext in AUDIO_EXTS else "image"
    return {"path": rel, "kind": kind, "name": path.name, "size": path.stat().st_size}


def unreadable_reason(path: Path) -> str | None:
    """Why a data file must not be opened: symlinks (they could point outside root) and
    anything that is not a regular file (a FIFO or device would block or never end)."""
    try:
        st = os.lstat(path)
    except OSError as exc:
        return f"无法读取: {exc.strerror or exc}"
    if stat.S_ISLNK(st.st_mode):
        return "是符号链接，未读取"
    if not stat.S_ISREG(st.st_mode):
        return "不是普通文件，未读取"
    if st.st_size > MAX_JSON_BYTES:
        return "文件超过 2 MB，未读取"
    return None


def read_json(path: Path):
    """``(data, None)`` or ``(None, 中文原因)``; never raises, never reads past the cap."""
    reason = unreadable_reason(path)
    if reason:
        return None, reason
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, ValueError) as exc:
        return None, f"无法解析: {exc}"


def read_object(path: Path):
    data, error = read_json(path)
    if error is None and not isinstance(data, dict):
        return None, "无法解析: 顶层必须是 JSON object"
    return data, error


def raw_text(path: Path) -> str:
    """The start of a file for the "按原文显示" fallback; empty when unreadable."""
    if unreadable_reason(path):
        return ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            text = handle.read(MAX_RAW_CHARS + 1)
    except OSError:
        return ""
    return text if len(text) <= MAX_RAW_CHARS else text[:MAX_RAW_CHARS] + "\n…（已截断）"


def discover(root: Path) -> dict:
    """Breadth-first marker scan: bounded depth and directory count; skips hidden, symlinked
    and dependency directories. Library roots are not descended (``scan_library`` owns them)."""
    found = {marker: [] for marker in MARKERS}
    warnings: list[str] = []
    queue = deque([(root, 0)])
    seen = 0
    depth_cut = False
    while queue:
        directory, depth = queue.popleft()
        seen += 1
        if seen > MAX_DIRS:
            warnings.append(f"目录超过 {MAX_DIRS} 个，扫描已截断；请把 --root 指向更小的目录")
            break
        try:
            with os.scandir(directory) as it:
                entries = list(it)
        except OSError as exc:
            warnings.append(f"无法读取目录 {rel_of(root, directory) or directory}: {exc.strerror or exc}")
            continue
        names = {e.name for e in entries if e.is_file(follow_symlinks=False)}
        rel = directory.relative_to(root).as_posix() or "."
        for marker in MARKERS:
            if marker in names:
                found[marker].append(rel)
        if LIBRARY_FILE in names:
            continue
        for entry in sorted(entries, key=lambda e: e.name):
            if (entry.name.startswith(".") or entry.name in SKIP_DIR_NAMES
                    or entry.is_symlink() or not entry.is_dir(follow_symlinks=False)):
                continue
            if depth >= MAX_DEPTH:
                depth_cut = True
                continue
            queue.append((Path(entry.path), depth + 1))
    if depth_cut:
        warnings.append(f"目录层级超过 {MAX_DEPTH} 层的部分未扫描")
    return {"libraries": found[LIBRARY_FILE], "projects": found[PROJECT_FILE],
            "runs": found[RUN_FILE], "warnings": warnings}


def nearest(rel: str, candidates, include_self=False) -> str | None:
    """The closest ancestor of ``rel`` (optionally ``rel`` itself) that is in ``candidates``."""
    if rel == ".":
        chain = ["."] if include_self else []
    else:
        chain = ([rel] if include_self else []) + [str(p) for p in PurePosixPath(rel).parents]
    return next((c for c in chain if c in candidates), None)
