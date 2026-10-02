"""One ffmpeg pass over a finished video: hard-cut times (scdet) and loudness (ebur128).

The result is cached in `reference_measurements.json` by the video's {size, mtime_ns} plus the
settings, so re-running `measure` on an unchanged file never re-decodes it. Shot statistics are
computed from scdet cuts directly; an understanding run's scene list merges short shots and
would inflate the median shot length.
"""
import math
import re
from pathlib import Path

from lib import ffprobe, file_identity, log, read_json, run_cmd, write_json

MEASUREMENTS_FILE = "reference_measurements.json"
MEASUREMENTS_SCHEMA = "video-reference.measurements.v1"
DEFAULT_SCENE_THRESHOLD = 10.0
CURVE_WINDOW_S = 10.0
EDGE_S = 0.05                # cuts this close to either end are not cuts between two shots
SILENT_LUFS = -70.0          # ebur128 reports -120.7 for digital silence / warm-up blocks

_SCDET_TIME = re.compile(r"lavfi\.scd\.time\s*[:=]\s*(-?\d+(?:\.\d+)?)")
_EBU_FRAME = re.compile(r"\bt:\s*(\d+(?:\.\d+)?)\s.*?\bS:\s*(-?\d+(?:\.\d+)?|-?inf|nan)")
_SUMMARY_I = re.compile(r"\bI:\s*(-?\d+(?:\.\d+)?)\s*LUFS")
_SUMMARY_LRA = re.compile(r"\bLRA:\s*(-?\d+(?:\.\d+)?)\s*LU\b")
_SUMMARY_PEAK = re.compile(r"True peak:\s*\n\s*Peak:\s*(-?\d+(?:\.\d+)?|-inf)")


def parse_scdet(stderr, duration=None):
    """Sorted, de-duplicated cut times from scdet log lines (`time: 2.6` or `time=2.6`)."""
    cuts = set()
    for match in _SCDET_TIME.finditer(stderr or ""):
        t = round(float(match.group(1)), 3)
        if t <= EDGE_S or (duration is not None and t >= duration - EDGE_S):
            continue
        cuts.add(t)
    return sorted(cuts)


def _number(text):
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def parse_ebur128(stderr, duration):
    """Integrated loudness, LRA, true peak (Summary block) and a 1 s short-term series."""
    stderr = stderr or ""
    head, marker, summary = stderr.rpartition("Summary:")
    if not marker:
        return None
    seconds = max(1, math.ceil(duration))
    series = [None] * seconds
    for match in _EBU_FRAME.finditer(head):
        bucket = int(float(match.group(1)))
        value = _number(match.group(2))
        if 0 <= bucket < seconds:
            series[bucket] = None if value is None or value < SILENT_LUFS else round(value, 1)

    def pick(pattern):
        match = pattern.search(summary)
        return None if match is None else _number(match.group(1))

    return {
        "integrated_lufs": pick(_SUMMARY_I),
        "lra_lu": pick(_SUMMARY_LRA),
        "true_peak_dbtp": pick(_SUMMARY_PEAK),
        "short_term_1s": series,
    }


def _percentile(sorted_values, q):
    if not sorted_values:
        return None
    position = (len(sorted_values) - 1) * q
    low = math.floor(position)
    high = min(low + 1, len(sorted_values) - 1)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


def shot_stats(cuts, duration):
    """Shot-length distribution and cut density for cuts inside [0, duration]."""
    bounds = [0.0, *cuts, duration]
    lengths = sorted(b - a for a, b in zip(bounds, bounds[1:]) if b > a)
    count = len(lengths)
    minutes = duration / 60.0
    windows = max(1, math.ceil(duration / CURVE_WINDOW_S))
    curve = [0] * windows
    for cut in cuts:
        curve[min(windows - 1, int(cut // CURVE_WINDOW_S))] += 1
    per_min = 60.0 / CURVE_WINDOW_S
    return {
        "cuts": list(cuts),
        "count": count,
        "mean_s": round(sum(lengths) / count, 3) if count else None,
        "median_s": round(_percentile(lengths, 0.5), 3) if count else None,
        "p10_s": round(_percentile(lengths, 0.1), 3) if count else None,
        "p90_s": round(_percentile(lengths, 0.9), 3) if count else None,
        "share_under_1s": round(sum(1 for x in lengths if x < 1.0) / count, 3) if count else None,
        "share_over_8s": round(sum(1 for x in lengths if x > 8.0) / count, 3) if count else None,
        "cuts_per_min": round(len(cuts) / minutes, 2) if minutes > 0 else None,
        "curve": {"window_s": CURVE_WINDOW_S, "values": [round(c * per_min, 1) for c in curve]},
    }


def _ffmpeg_command(video, threshold, scaled, has_audio):
    video_filter = f"scdet=threshold={threshold:g}"
    if scaled:
        video_filter = "scale=320:-2," + video_filter
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", video, "-map", "0:v:0", "-vf", video_filter]
    if has_audio:
        cmd += ["-map", "0:a:0", "-af", "ebur128=peak=true:framelog=info"]
    return cmd + ["-f", "null", "-"]


def _cache_hit(cached, identity, settings):
    if not isinstance(cached, dict) or cached.get("schema") != MEASUREMENTS_SCHEMA:
        return False
    source = cached.get("source") or {}
    same_file = {k: source.get(k) for k in identity} == identity
    return same_file and cached.get("settings") == settings


def measure(video, work_dir, *, threshold=DEFAULT_SCENE_THRESHOLD, scaled=True):
    """Measure `video` into work_dir/reference_measurements.json; reuse it when nothing changed."""
    video = Path(video)
    out_path = Path(work_dir) / MEASUREMENTS_FILE
    identity = file_identity(video)
    settings = {"scene_threshold": float(threshold), "scaled": bool(scaled)}
    cached = read_json(out_path)
    if _cache_hit(cached, identity, settings):
        log(f"复用 {MEASUREMENTS_FILE}（成片与设置未变）")
        return cached

    probe = ffprobe(video)
    duration = probe["duration_s"]
    result = run_cmd(_ffmpeg_command(video, threshold, scaled, probe["audio_streams"] > 0))
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 测量失败: {result.stderr.strip()[-600:]}")
    cuts = parse_scdet(result.stderr, duration)
    payload = {
        "schema": MEASUREMENTS_SCHEMA,
        "source": {**identity, **probe},
        "settings": settings,
        "shots": shot_stats(cuts, duration),
        "loudness": parse_ebur128(result.stderr, duration) if probe["audio_streams"] else None,
    }
    write_json(out_path, payload)
    log(f"写入 {out_path}：{len(cuts)} 个切点，时长 {duration:.1f}s")
    return payload
