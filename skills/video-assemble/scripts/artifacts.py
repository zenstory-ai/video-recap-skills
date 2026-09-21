"""File identity and work-directory JSON helpers for video-assemble."""

import json
import os
from pathlib import Path

from lib import CONFIG


def file_identity(path):
    """``{size, mtime_ns}`` of one input file: enough to notice a rewrite, cheap to take."""
    st = os.stat(os.fspath(path))
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


def _artifact_identity(path):
    path = Path(path)
    return file_identity(path) if path.exists() else None


def _explicit_source_video():
    """Return the cut-mode source video only when the caller opted in explicitly."""
    if not CONFIG["source_video_explicit"]:
        return ""
    return CONFIG["source_video"]


def _source_video_identity():
    """``{path, size, mtime_ns}`` of the explicit cut-mode source video, else None."""
    source_video = _explicit_source_video()
    if not source_video:
        return None
    path = Path(source_video)
    return {"path": str(path.resolve()), **file_identity(path)}


def _timeline_provenance_status(work_dir):
    data = _load_work_json(work_dir, "timeline.json")
    return None if data is None else data["provenance"]


def _load_work_json(work_dir, name):
    """Parse a JSON artifact in work_dir; None only when the file does not exist."""
    path = Path(work_dir) / name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
