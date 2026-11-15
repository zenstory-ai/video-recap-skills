"""Self-contained utilities for the video-reference skill (no cross-skill imports)."""
import json
import os
import subprocess
import tempfile
from pathlib import Path


def log(msg):
    print(f"[video-reference] {msg}", flush=True)


def run_cmd(cmd, **kwargs):
    """Run a command list and return the CompletedProcess (stdout/stderr captured)."""
    return subprocess.run(
        [str(part) for part in cmd], capture_output=True, text=True,
        encoding="utf-8", errors="replace", **kwargs,
    )


def file_identity(path):
    """{size, mtime_ns} — the cache identity of an input file (no content read)."""
    st = os.stat(os.fspath(path))
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


def _fraction(text):
    try:
        num, _, den = str(text).partition("/")
        value = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return None
    return round(value, 3) if value > 0 else None


def _stream_rotation(video):
    tags = video.get("tags") or {}
    if "rotate" in tags:
        try:
            return int(round(float(tags["rotate"]))) % 360
        except (TypeError, ValueError):
            pass
    for side in video.get("side_data_list") or []:
        if "rotation" in side:
            try:
                return int(round(float(side["rotation"]))) % 360
            except (TypeError, ValueError):
                pass
    return 0


def ffprobe(video_path):
    """Duration, display-oriented canvas, fps and soft-subtitle stream count of a media file."""
    result = run_cmd([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=codec_type,width,height,sample_aspect_ratio,avg_frame_rate,r_frame_rate:stream_tags=rotate:stream_side_data=rotation",
        "-of", "json", video_path,
    ])
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe 失败: {result.stderr.strip()[-400:]}")
    payload = json.loads(result.stdout or "{}")
    streams = payload.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise RuntimeError(f"没有视频流: {video_path}")
    try:
        duration = float((payload.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0:
        raise RuntimeError(f"无法读取时长: {video_path}")
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    sar = str(video.get("sample_aspect_ratio") or "").replace("/", ":")
    if ":" in sar:
        num, _, den = sar.partition(":")
        try:
            s_num, s_den = int(num), int(den)
            if s_num > 0 and s_den > 0 and s_num != s_den:
                width = int(round(width * s_num / s_den))
        except ValueError:
            pass
    if _stream_rotation(video) in (90, 270):
        width, height = height, width
    return {
        "duration_s": round(duration, 3),
        "canvas": {"width": width, "height": height},
        "fps": _fraction(video.get("avg_frame_rate")) or _fraction(video.get("r_frame_rate")),
        "subtitle_streams": sum(1 for s in streams if s.get("codec_type") == "subtitle"),
        "audio_streams": sum(1 for s in streams if s.get("codec_type") == "audio"),
    }


def read_json(path, default=None):
    """Parsed JSON at `path`, or `default` when the file does not exist."""
    path = Path(path)
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, payload):
    """Atomically write `payload` as UTF-8 JSON (temp file + os.replace)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return path
