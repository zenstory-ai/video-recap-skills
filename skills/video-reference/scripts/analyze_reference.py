#!/usr/bin/env python3
"""Atomic reference-video analyzer: shot rhythm, loudness/audio density, and contact sheet.

Stdlib + ffmpeg/ffprobe only. Runs 100% locally with zero remote calls.
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import os
import re
import subprocess
from pathlib import Path

__all__ = [
    "analyze_reference", "detect_cuts", "main", "parse_ebur128",
    "parse_scdet", "parse_silences", "probe_video", "render_contact_sheet", "shot_stats",
]

DEFAULT_HARD_SCORE, DEFAULT_SOFT_SCORE = 10.0, 4.0
ISOLATION_RATIO, ISOLATION_WINDOW_S, SCORE_FLOOR = 2.0, 0.3, 2.0
EDGE_S, CURVE_WINDOW_S, SILENT_LUFS = 0.05, 10.0, -70.0

_SCDET_RE = re.compile(
    r"lavfi\.scd\.score\s*[:=]\s*(-?\d+(?:\.\d+)?)\s*,?\s*lavfi\.scd\.time\s*[:=]\s*(-?\d+(?:\.\d+)?)"
)
_EBU_FRAME_RE = re.compile(r"\bt:\s*(\d+(?:\.\d+)?)\s.*?\bS:\s*(-?\d+(?:\.\d+)?|-?inf|nan)")
_SUMMARY_I_RE = re.compile(r"\bI:\s*(-?\d+(?:\.\d+)?)\s*LUFS")
_SUMMARY_LRA_RE = re.compile(r"\bLRA:\s*(-?\d+(?:\.\d+)?)\s*LU\b")
_SUMMARY_PEAK_RE = re.compile(r"True peak:\s*\n\s*Peak:\s*(-?\d+(?:\.\d+)?|-inf)")
_SILENCE_START_RE = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")


def _number(text: str) -> float | None:
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(item) for item in cmd],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )


def _parse_fps(raw: str | None) -> float | None:
    if not raw or raw in {"0/0", "0"}:
        return None
    if "/" in raw:
        num_s, _, den_s = raw.partition("/")
        num, den = _number(num_s), _number(den_s)
        return round(num / den, 4) if num and den and den > 0 else None
    return _number(raw)


def probe_video(video: str | Path) -> dict:
    """Return geometry, frame rate, duration, and audio presence via ffprobe."""
    path = Path(video)
    if not path.is_file():
        raise FileNotFoundError(f"Video not found: {path}")
    result = _run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration:stream=codec_type,width,height,avg_frame_rate,r_frame_rate",
        "-of", "json", str(path),
    ])
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr.strip()[-400:]}")
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams") or []
    v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    if v_stream is None:
        raise RuntimeError(f"No video stream found in {path}")
    duration = _number(str((data.get("format") or {}).get("duration", ""))) or 0.0
    fps = _parse_fps(v_stream.get("avg_frame_rate")) or _parse_fps(v_stream.get("r_frame_rate"))
    return {
        "path": str(path), "bytes": path.stat().st_size, "duration_s": round(duration, 3),
        "width": int(v_stream.get("width") or 0), "height": int(v_stream.get("height") or 0),
        "fps": fps, "has_audio": any(s.get("codec_type") == "audio" for s in streams),
    }


def parse_scdet(stderr: str, duration: float | None = None) -> list[list[float]]:
    """Extract sorted [time_s, score] pairs from ffmpeg scdet stderr."""
    scores: dict[float, float] = {}
    for match in _SCDET_RE.finditer(stderr or ""):
        score, t = float(match.group(1)), round(float(match.group(2)), 3)
        if t <= EDGE_S or (duration is not None and t >= duration - EDGE_S):
            continue
        scores[t] = max(score, scores.get(t, score))
    return [[t, round(scores[t], 3)] for t in sorted(scores)]


def _side_peaks(series: list[list[float]], times: list[float], idx: int, adjacent: float) -> float:
    t = series[idx][0]
    lo = bisect.bisect_left(times, t - ISOLATION_WINDOW_S - 1e-9)
    hi = bisect.bisect_right(times, t + ISOLATION_WINDOW_S + 1e-9)
    before = max((v for u, v in series[lo:idx] if t - u > adjacent), default=0.0)
    after = max((v for u, v in series[idx + 1:hi] if u - t > adjacent), default=0.0)
    return max(before, after)


def detect_cuts(
    scores: list[list[float]], fps: float | None, *,
    hard: float = DEFAULT_HARD_SCORE, soft: float = DEFAULT_SOFT_SCORE,
) -> tuple[list[float], list[list[float]]]:
    """Return (cut_times_s, review_windows) using hard + isolated-soft scdet thresholds."""
    adjacent = 1.5 / fps if fps else 0.06
    times = [t for t, _ in scores]
    cuts: list[tuple[float, float]] = []
    suppressed: list[float] = []
    for idx, (t, score) in enumerate(scores):
        if score < soft:
            continue
        if score < hard and score < ISOLATION_RATIO * _side_peaks(scores, times, idx, adjacent):
            suppressed.append(t)
            continue
        if cuts and t - cuts[-1][0] <= adjacent:
            if score > cuts[-1][1]:
                cuts[-1] = (t, score)
            continue
        cuts.append((t, score))
    cut_times = [t for t, _ in cuts]
    windows: list[list[float]] = []
    for t in sorted(set(suppressed)):
        if any(abs(t - c) <= adjacent for c in cut_times):
            continue
        if windows and t - windows[-1][1] <= 0.5:
            windows[-1][1] = t
        else:
            windows.append([t, t])
    return cut_times, [[round(max(0.0, a - 0.2), 2), round(b + 0.2, 2)] for a, b in windows]


def _percentile(sorted_vals: list[float], q: float) -> float | None:
    if not sorted_vals:
        return None
    pos = (len(sorted_vals) - 1) * q
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def shot_stats(cuts: list[float], duration: float) -> dict:
    """Compute shot-length percentiles and 10-second cut density curve."""
    bounds = [0.0, *[c for c in cuts if 0.0 < c < duration], duration]
    lengths = sorted(b - a for a, b in zip(bounds, bounds[1:]) if b > a)
    count = len(lengths)
    minutes = duration / 60.0 if duration > 0 else 0.0
    win_count = max(1, math.ceil(duration / CURVE_WINDOW_S)) if duration > 0 else 1
    curve = [0] * win_count
    for cut in cuts:
        if 0.0 <= cut < duration:
            curve[min(win_count - 1, int(cut // CURVE_WINDOW_S))] += 1
    curve_cpm = [
        round(c * 60.0 / max(1.0, min(CURVE_WINDOW_S, max(0.0, duration - i * CURVE_WINDOW_S))), 1)
        for i, c in enumerate(curve)
    ]
    return {
        "cuts": list(cuts), "shot_count": count,
        "mean_s": round(sum(lengths) / count, 3) if count else None,
        "median_s": round(_percentile(lengths, 0.5), 3) if count else None,
        "p10_s": round(_percentile(lengths, 0.1), 3) if count else None,
        "p90_s": round(_percentile(lengths, 0.9), 3) if count else None,
        "share_under_1s": round(sum(1 for x in lengths if x < 1.0) / count, 3) if count else None,
        "share_over_8s": round(sum(1 for x in lengths if x > 8.0) / count, 3) if count else None,
        "cuts_per_min": round(len(cuts) / minutes, 2) if minutes > 0 else None,
        "density_curve_10s_cpm": curve_cpm,
    }


def parse_ebur128(stderr: str, duration: float) -> dict | None:
    """Parse integrated LUFS, LRA, true peak dBTP, and 1-second short-term LUFS series."""
    head, marker, summary = (stderr or "").rpartition("Summary:")
    if not marker:
        return None
    seconds = max(1, math.ceil(duration))
    series: list[float | None] = [None] * seconds
    for match in _EBU_FRAME_RE.finditer(head):
        bucket, val = int(float(match.group(1))), _number(match.group(2))
        if 0 <= bucket < seconds:
            series[bucket] = None if val is None or val < SILENT_LUFS else round(val, 1)

    def pick(pattern: re.Pattern[str]) -> float | None:
        m = pattern.search(summary)
        return None if m is None else _number(m.group(1))

    return {
        "integrated_lufs": pick(_SUMMARY_I_RE), "lra_lu": pick(_SUMMARY_LRA_RE),
        "true_peak_dbtp": pick(_SUMMARY_PEAK_RE), "short_term_1s": series,
    }


def parse_silences(stderr: str, duration: float) -> list[list[float]]:
    """Parse silencedetect start/end spans from ffmpeg stderr."""
    spans: list[list[float]] = []
    pending: float | None = None
    for line in (stderr or "").splitlines():
        if m := _SILENCE_START_RE.search(line):
            pending = _number(m.group(1))
        elif (m := _SILENCE_END_RE.search(line)) and pending is not None:
            end = _number(m.group(1))
            if end is not None and end > pending:
                spans.append([round(max(0.0, pending), 3), round(min(duration, end), 3)])
            pending = None
    if pending is not None and duration > pending:
        spans.append([round(max(0.0, pending), 3), round(duration, 3)])
    return spans


def render_contact_sheet(
    video: str | Path, out_path: str | Path, *,
    duration: float, cols: int = 4, rows: int = 4, cell_width: int = 320,
) -> dict:
    """Render an evenly sampled CxR contact sheet JPG and return its tile timestamps."""
    count = max(1, cols * rows)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    step = duration / count if duration > 0 else 1.0
    times = [round(min(max(0.0, (i + 0.5) * step), max(0.0, duration - 0.05)), 2) for i in range(count)]
    vf = f"fps={count / max(duration, 0.5):.6f},scale={cell_width}:-2,tile={cols}x{rows}"
    result = _run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(video), "-vf", vf, "-frames:v", "1", str(out),
    ])
    if result.returncode != 0 or not out.is_file():
        raise RuntimeError(f"Contact sheet render failed: {result.stderr.strip()[-400:]}")
    return {"path": str(out), "grid": f"{cols}x{rows}", "tile_times_s": times}


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def analyze_reference(
    video: str | Path, out_dir: str | Path, *,
    hard: float = DEFAULT_HARD_SCORE, soft: float = DEFAULT_SOFT_SCORE,
    cols: int = 4, rows: int = 4, no_sheet: bool = False,
) -> dict:
    """Measure shot rhythm, loudness/audio density, and optional contact sheet."""
    if not SCORE_FLOOR * ISOLATION_RATIO <= soft <= hard:
        raise ValueError(f"Expected {SCORE_FLOOR * ISOLATION_RATIO:g} <= soft <= hard")
    source = probe_video(video)
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(video),
           "-map", "0:v:0", "-vf", f"scale=320:-2,scdet=threshold={SCORE_FLOOR:g}"]
    if source["has_audio"]:
        cmd += ["-map", "0:a:0", "-af", "ebur128=peak=true:framelog=info,silencedetect=noise=-42dB:d=0.4"]
    result = _run(cmd + ["-f", "null", "-"])
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg analysis failed: {result.stderr.strip()[-600:]}")

    duration = source["duration_s"]
    cuts, review_windows = detect_cuts(parse_scdet(result.stderr, duration), source["fps"], hard=hard, soft=soft)
    loudness = parse_ebur128(result.stderr, duration) if source["has_audio"] else None
    silences = parse_silences(result.stderr, duration) if source["has_audio"] else []
    silent_total = sum(b - a for a, b in silences)
    active_share = round(max(0.0, 1.0 - silent_total / duration), 3) if source["has_audio"] and duration > 0 else None

    out_root = Path(out_dir)
    sheet = None if no_sheet else render_contact_sheet(
        video, out_root / "contact_sheet.jpg", duration=duration, cols=cols, rows=rows,
    )
    payload = {
        "schema_version": 1, "source": source,
        "shots": {**shot_stats(cuts, duration), "review_windows": review_windows},
        "audio": {"loudness": loudness, "silences": silences, "active_audio_share": active_share},
        "contact_sheet": sheet,
    }
    _atomic_write_json(out_root / "reference_metrics.json", payload)
    return payload


def _parse_grid(raw: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d+)x(\d+)", raw.strip().lower())
    if not match or int(match.group(1)) < 1 or int(match.group(2)) < 1:
        raise argparse.ArgumentTypeError("Grid must look like CxR with positive integers (e.g. 4x4)")
    return int(match.group(1)), int(match.group(2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze a finished reference video locally.")
    parser.add_argument("video", help="Path to the finished reference video.")
    parser.add_argument("--out-dir", required=True, help="Output directory for metrics and contact sheet.")
    parser.add_argument("--hard", type=float, default=DEFAULT_HARD_SCORE, help="Hard scdet threshold.")
    parser.add_argument("--soft", type=float, default=DEFAULT_SOFT_SCORE, help="Isolated soft scdet threshold.")
    parser.add_argument("--grid", type=_parse_grid, default=(4, 4), help="Contact sheet CxR grid.")
    parser.add_argument("--no-sheet", action="store_true", help="Skip contact sheet rendering.")
    args = parser.parse_args(argv)
    cols, rows = args.grid
    payload = analyze_reference(
        args.video, args.out_dir, hard=args.hard, soft=args.soft, cols=cols, rows=rows, no_sheet=args.no_sheet
    )
    shots = payload["shots"]
    print(
        f"Analyzed {payload['source']['duration_s']:.1f}s: {len(shots['cuts'])} cuts "
        f"({shots['cuts_per_min']} cuts/min), median shot {shots['median_s']}s -> "
        f"{Path(args.out_dir) / 'reference_metrics.json'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
