"""Artifact fingerprints and work-directory JSON helpers for video-assemble."""

import hashlib
import json
import os
from pathlib import Path

from lib import CONFIG

def _stable_json_dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _value_fingerprint(value):
    return hashlib.md5(_stable_json_dumps(value).encode("utf-8")).hexdigest()


_FILE_FINGERPRINT_MEMO = {}


def _file_identity(path):
    """(device, inode, size, mtime_ns) — changes whenever the bytes could have changed."""
    st = os.stat(os.fspath(path))
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def _file_fingerprint(path, chunk_size=1024 * 1024):
    """Return a full-content fingerprint for cache-correct identity checks.

    The digest covers CONTENT only — never the path or mtime — so a copied video or
    artifact is still recognised as the same asset, while any byte change invalidates
    the cache even if timestamps, size, head, or tail bytes are misleading.

    Identity metadata is used ONLY to memoize within a single process. One understanding
    run fingerprints the same source video 8-10 times and the whole extracted frame set
    2-3 times; on a 40-minute video at fps=1 that is gigabytes of redundant reads before
    any real work starts. A file rewritten in place gets a new (size, mtime_ns) and is
    re-hashed, so the memo can never serve a stale digest.
    """
    key = _file_identity(path)
    memoized = _FILE_FINGERPRINT_MEMO.get(key)
    if memoized is not None:
        return memoized
    h = hashlib.sha256()
    with open(os.fspath(path), "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    digest = h.hexdigest()
    _FILE_FINGERPRINT_MEMO[key] = digest
    return digest


def _artifact_fingerprint(path):
    path = Path(path)
    return _file_fingerprint(path) if path.exists() else None


def _explicit_source_video():
    """Return the cut-mode source video only when the caller opted in explicitly."""
    if not CONFIG["source_video_explicit"]:
        return ""
    return CONFIG["source_video"]


def _source_video_identity():
    source_video = _explicit_source_video()
    if not source_video:
        return None, None
    path = Path(source_video)
    return str(path.resolve()), _artifact_fingerprint(path)


def _timeline_provenance_status(work_dir):
    data = _load_work_json(work_dir, "timeline.json")
    return None if data is None else data["provenance"]


def _load_work_json(work_dir, name):
    """Parse a JSON artifact in work_dir; None only when the file does not exist."""
    path = Path(work_dir) / name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
