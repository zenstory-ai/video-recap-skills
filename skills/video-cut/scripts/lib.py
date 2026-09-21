"""Self-contained utilities for the video-cut skill (no cross-skill imports)."""
import math
import os
import subprocess


def log(msg):
    print(f"[video-cut] {msg}", flush=True)


def env_bool(name, default):
    """Read an env var as a boolean (1/true/yes → True; 0/false/no → False)."""
    val = os.environ.get(name)
    if val is None:
        return default
    text = val.strip().lower()
    if text in ("1", "true", "yes"):
        return True
    if text in ("0", "false", "no"):
        return False
    raise ValueError(f"{name} must be 1/true/yes or 0/false/no, got {val!r}")


def env_float(name, default, min_val=None):
    """Read an env var as a float, rejecting malformed or below-minimum values."""
    val = os.environ.get(name)
    if val is None:
        return default
    try:
        result = float(val)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {val!r}") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite, got {val!r}")
    if min_val is not None and result < min_val:
        raise ValueError(f"{name} must be >= {min_val}, got {val!r}")
    return result


CONFIG = {
    "snap_clip_line_end": env_bool("SNAP_CLIP_LINE_END", True),
    "clip_snap_max_extend": env_float("CLIP_SNAP_MAX_EXTEND", 2.0, min_val=0.0),
    "clip_start_snap_max_prepend": env_float("CLIP_START_SNAP_MAX_PREPEND", 1.8, min_val=0.0),
    "clip_start_snap_max_trim": env_float("CLIP_START_SNAP_MAX_TRIM", 0.35, min_val=0.0),
    "clip_join_audio_fade_ms": env_float("CLIP_JOIN_AUDIO_FADE_MS", 30.0, min_val=0.0),
    # video-cut is the only skill that implements clip padding, so it is the only skill that
    # may declare the knob. cut_cli reads this as the default for --clip-padding.
    "clip_padding": env_float("CLIP_PADDING", 0.0, min_val=0.0),
    # Keep clip boundaries off the ORIGINAL footage's hard cuts: a clip that opens/closes a few
    # tenths of a second from a source shot-change shows a brief sliver of the adjacent shot that
    # then hard-cuts again — a visible 闪烁/flicker at the edit point. Snap source_start forward
    # past (and source_end back before) any shot-change within the margin.
    "scene_cut_snap": env_bool("SCENE_CUT_SNAP", True),
    "scene_cut_snap_margin": env_float("SCENE_CUT_SNAP_MARGIN", 0.5, min_val=0.0),    # 边界±此秒内有切镜头才避让
    "scene_cut_detect_threshold": env_float("SCENE_CUT_DETECT_THRESHOLD", 0.4, min_val=0.0),  # ffmpeg scene 分数阈值(硬切)
}


def file_identity(path):
    """{size, mtime_ns} — the cache identity of an input file (no content read)."""
    st = os.stat(os.fspath(path))
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


def run_cmd(cmd, **kwargs):
    """Run a command list and return the CompletedProcess (stdout/stderr captured)."""
    display = " ".join(
        str(part) if len(str(part)) <= 240 else str(part)[:237] + "..." for part in cmd
    )
    log(f"运行: {display}")
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def get_video_duration(video_path):
    """Return media duration in seconds via ffprobe."""
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "csv=p=0", str(video_path)]
    result = run_cmd(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe 无法读取时长: {video_path}: {result.stderr.strip()}")
    return float(result.stdout.strip())
