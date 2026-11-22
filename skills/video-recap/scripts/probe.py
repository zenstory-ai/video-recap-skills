#!/usr/bin/env python3
"""Atomic media prober: container/stream facts + optional scene cuts and silence intervals."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence

from _common import has_tool, probe_media, run_cmd, save_json

__all__ = ["detect_scenes", "detect_silences", "main", "probe_sources"]

_SCD_TIME_RE = re.compile(r"lavfi\.scd\.time[:=]\s*(\d+(?:\.\d+)?)")
_SILENCE_START_RE = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")


def detect_scenes(video_path: str | Path, *, threshold: float = 0.15) -> list[float]:
    """Return sorted scene-cut timestamps (seconds) detected via ffmpeg `scdet`."""
    if not has_tool("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH")
    scdet_th = max(1, min(100, int(round(float(threshold) * 100))))
    res = run_cmd([
        "ffmpeg", "-hide_banner", "-i", str(video_path),
        "-vf", f"scdet=threshold={scdet_th}", "-f", "null", "-",
    ], check=True)
    cuts: list[float] = []
    for match in _SCD_TIME_RE.finditer(res.stderr or ""):
        t_s = round(float(match.group(1)), 4)
        if t_s > 0 and (not cuts or t_s > cuts[-1]):
            cuts.append(t_s)
    return cuts


def detect_silences(
    media_path: str | Path,
    *,
    noise_db: float = -45.0,
    min_duration: float = 0.4,
    total_duration: float | None = None,
) -> list[dict[str, float]]:
    """Return silence intervals `[{"start", "end", "duration"}]` via ffmpeg `silencedetect`."""
    if not has_tool("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH")
    res = run_cmd([
        "ffmpeg", "-hide_banner", "-i", str(media_path),
        "-af", f"silencedetect=noise={float(noise_db):.1f}dB:d={float(min_duration):.3f}",
        "-f", "null", "-",
    ], check=True)
    intervals: list[dict[str, float]] = []
    open_start: float | None = None
    for line in (res.stderr or "").splitlines():
        m_start = _SILENCE_START_RE.search(line)
        if m_start:
            open_start = max(0.0, float(m_start.group(1)))
            continue
        m_end = _SILENCE_END_RE.search(line)
        if m_end and open_start is not None:
            end_s = max(open_start, float(m_end.group(1)))
            intervals.append({
                "start": round(open_start, 4),
                "end": round(end_s, 4),
                "duration": round(end_s - open_start, 4),
            })
            open_start = None
    if open_start is not None and total_duration is not None and total_duration > open_start:
        intervals.append({
            "start": round(open_start, 4),
            "end": round(total_duration, 4),
            "duration": round(total_duration - open_start, 4),
        })
    return intervals


def probe_sources(
    paths: Sequence[str | Path],
    *,
    scenes: bool = False,
    scene_threshold: float = 0.15,
    silence: bool = False,
    silence_db: float = -45.0,
    silence_min: float = 0.4,
) -> dict[str, Any]:
    """Probe one or more media files and optionally attach scene cuts and silence spans."""
    rows: list[dict[str, Any]] = []
    for idx, raw_path in enumerate(paths, start=1):
        info = probe_media(raw_path)
        entry: dict[str, Any] = {"id": f"s{idx}", **info}
        if scenes and info.get("has_video"):
            entry["cuts"] = detect_scenes(raw_path, threshold=scene_threshold)
        if silence and info.get("has_audio"):
            entry["silences"] = detect_silences(
                raw_path,
                noise_db=silence_db,
                min_duration=silence_min,
                total_duration=info.get("duration_s"),
            )
        rows.append(entry)
    return {"sources": rows}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe media metadata, scene cuts, and silences.")
    parser.add_argument("media", nargs="+", help="One or more input media file paths.")
    parser.add_argument("--scenes", action="store_true", help="Detect scene cut timestamps via scdet.")
    parser.add_argument("--scene-threshold", type=float, default=0.15, help="scdet threshold (0..1).")
    parser.add_argument("--silence", action="store_true", help="Detect silence intervals.")
    parser.add_argument("--silence-db", type=float, default=-45.0, help="Silence threshold in dB.")
    parser.add_argument("--silence-min", type=float, default=0.4, help="Minimum silence duration (s).")
    parser.add_argument("--out", help="Optional output JSON path (prints to stdout if omitted).")
    args = parser.parse_args(argv)

    payload = probe_sources(
        args.media,
        scenes=args.scenes,
        scene_threshold=args.scene_threshold,
        silence=args.silence,
        silence_db=args.silence_db,
        silence_min=args.silence_min,
    )
    if args.out:
        save_json(args.out, payload)
    else:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
