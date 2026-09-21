#!/usr/bin/env python3
"""Read-only internal-shot recall on the actual rendered video; never repair an EDL."""
import argparse
from bisect import bisect_left
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile

from cut_contract import cut_plan_fingerprint, edited_source_render_fingerprint


def sha256_file(path):
    """Fresh content read: do not infer identity from a filename or mtime."""
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _positive(value, name, *, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a positive number")
    if not math.isfinite(value) or value <= 0 or (integer and not isinstance(value, int)):
        raise ValueError(f"invalid {name}")


def _validate_clock(pts, end):
    if not pts or any(not isinstance(p, Fraction) for p in [*pts, end]):
        raise ValueError("frame clock requires exact rational PTS")
    if pts[0] != 0 or any(a >= b for a, b in zip(pts, pts[1:])) or end <= pts[-1]:
        raise ValueError("invalid/nonmonotonic frame clock")


def summarize_candidates(pts, end, cut_frames, *, max_short_frames=None,
                         max_short_seconds=1.0, dense_window_seconds=2.0,
                         min_dense_cuts=4):
    """Half-open frame spans including the head and tail; thresholds are recall policy."""
    _validate_clock(pts, end)
    _positive(max_short_seconds, "max_short_seconds")
    if max_short_frames is None:
        # A fixed frame count only coincides with the seconds limit at one frame rate, so
        # derive it from the measured clock; --max-short-frames stays an explicit override.
        measured_fps = Fraction(len(pts), 1) / end
        max_short_frames = max(1, round(measured_fps * Fraction(str(max_short_seconds))))
    _positive(max_short_frames, "max_short_frames", integer=True)
    _positive(dense_window_seconds, "dense_window_seconds")
    _positive(min_dense_cuts, "min_dense_cuts", integer=True)
    if any(type(f) is not int or not 0 < f < len(pts) for f in cut_frames):
        raise ValueError("invalid candidate frame index")
    cuts = sorted(set(cut_frames))
    boundaries = [0, *cuts, len(pts)]
    clock = [*pts, end]
    short = []
    for start, stop in zip(boundaries, boundaries[1:]):
        duration = clock[stop] - clock[start]
        if stop - start <= max_short_frames and duration <= Fraction(str(max_short_seconds)):
            short.append({
                "start_frame": start, "end_frame": stop, "frame_count": stop - start,
                "start_exact": str(clock[start]), "end_exact": str(clock[stop]),
                "duration_exact": str(duration), "duration_seconds": float(duration),
                "review_status": "NEEDS_DYNAMIC_REVIEW",
            })
    windows = []
    right = 0
    window_duration = Fraction(str(dense_window_seconds))
    for left in range(len(cuts)):
        right = max(right, left)
        while right < len(cuts) and pts[cuts[right]] - pts[cuts[left]] <= window_duration:
            right += 1
        if right - left < min_dense_cuts:
            continue
        members = cuts[left:right]
        if windows and members[0] <= windows[-1]["cut_frames"][-1]:
            windows[-1]["cut_frames"] = sorted(set(windows[-1]["cut_frames"]) | set(members))
        else:
            windows.append({"cut_frames": members, "review_status": "NEEDS_DYNAMIC_REVIEW"})
    for window in windows:
        window.update({"start_exact": str(pts[window["cut_frames"][0]]),
                       "end_exact": str(pts[window["cut_frames"][-1]])})
    return {
        "status": "NEEDS_REVIEW" if short or windows else "NO_CANDIDATES",
        "normal_speed_review": "NOT_CHECKED", "automatic_repairs": [],
        "candidates": [{"frame": f, "pts_exact": str(pts[f]), "origin": "UNKNOWN"} for f in cuts],
        "short_spans": short, "dense_windows": windows,
        "policy": {"max_short_frames": max_short_frames, "max_short_seconds": max_short_seconds,
                   "dense_window_seconds": dense_window_seconds, "min_dense_cuts": min_dense_cuts},
    }


def probe_frame_clock(video):
    result = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames",
        "-show_entries", "stream=time_base,start_pts,duration_ts:frame=pts,duration,pkt_duration",
        "-of", "json", str(video),
    ], capture_output=True, text=True, timeout=600)
    if result.returncode or result.stderr.strip():
        raise RuntimeError(f"frame decode probe failed: {result.stderr[-2000:]}")
    try:
        data = json.loads(result.stdout)
        stream = data["streams"][0]
        tb = Fraction(stream["time_base"])
        if tb <= 0:
            raise ValueError("nonpositive time base")
        frames = data["frames"]
        absolute = [int(f["pts"]) * tb for f in frames]
        origin = absolute[0]
        pts = [p - origin for p in absolute]
        last_duration = frames[-1].get("duration", frames[-1].get("pkt_duration"))
        if last_duration is None or int(last_duration) <= 0:
            raise ValueError("missing/invalid terminal frame duration")
        decoded_end = pts[-1] + int(last_duration) * tb
        if "duration_ts" in stream:
            end = (int(stream.get("start_pts", frames[0]["pts"])) + int(stream["duration_ts"])) * tb - origin
            if abs(end - decoded_end) > tb:
                raise ValueError("decoded frame coverage does not reach declared stream end")
        else:
            end = decoded_end
        _validate_clock(pts, end)
    except (KeyError, IndexError, ValueError, TypeError, ZeroDivisionError) as exc:
        raise ValueError(f"cannot establish exact frame clock: {exc}") from exc
    return pts, end, origin


def _validate_scene_roi(roi, video):
    """Validate an ROI (four ints from the CLI) against the video's auto-oriented native frame."""
    if len(roi) != 4:
        raise ValueError("scene ROI must contain exactly four integers")
    x, y, width, height = roi
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        raise ValueError("scene ROI requires x/y >= 0 and width/height > 0")

    from media_geometry import _probe_video_geometry
    facts = _probe_video_geometry(video).facts
    rotation = facts["rotation"]
    if rotation not in {0, 90, 180, 270}:
        raise ValueError("scene ROI requires a right-angle video rotation")
    canvas_width, canvas_height = (
        (facts["coded_height"], facts["coded_width"]) if rotation in {90, 270}
        else (facts["coded_width"], facts["coded_height"])
    )
    if x + width > canvas_width or y + height > canvas_height:
        raise ValueError(
            f"scene ROI exceeds auto-oriented native frame {canvas_width}x{canvas_height}"
        )


def detect_scene_pts(video, threshold, roi=None):
    """No seek or float pts_time: showinfo integer pts + its actual filter timebase.

    The single library-side check of threshold/ROI; the CLIs pre-check with parser.error."""
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("scene threshold must be finite and in [0,1]")
    filters = []
    if roi is not None:
        _validate_scene_roi(roi, video)
        x, y, width, height = roi
        filters.append(f"crop={width}:{height}:{x}:{y}:exact=1")
    filters.extend([f"select='gt(scene,{threshold})'", "showinfo"])
    result = subprocess.run([
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "info", "-xerror", "-copyts",
        "-threads", "2", "-i", str(video), "-map", "0:v:0",
        "-vf", ",".join(filters),
        "-an", "-fps_mode", "passthrough", "-f", "null", "-",
    ], capture_output=True, text=True, timeout=600)
    if result.returncode:
        raise RuntimeError(f"scene decode failed: {result.stderr[-2000:]}")
    bases = set(re.findall(r"config in time_base:\s*([0-9]+/[0-9]+)", result.stderr))
    if len(bases) != 1:
        raise ValueError("ambiguous or missing scene filter timebase")
    tb = Fraction(bases.pop())
    if tb <= 0:
        raise ValueError("invalid scene filter timebase")
    values = re.findall(r"\bn:\s*\d+\s+pts:\s*(-?\d+)\s+pts_time:", result.stderr)
    return [int(p) * tb for p in values]


def load_bound_plan(video, plan_path):
    """Only associate with the current cut-render cache, including every source hash."""
    from cut_contract import _edited_source_meta_path
    try:
        plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
        meta = json.loads(_edited_source_meta_path(video).read_text(encoding="utf-8"))
        sources = meta["source_fingerprints"]
        if not isinstance(sources, dict) or not sources:
            raise ValueError("source identities missing")
        if (meta["clip_plan_fingerprint"] != cut_plan_fingerprint(plan)
                or meta["render_fingerprint"] != edited_source_render_fingerprint()
                or meta["edited_source_fingerprint"] != sha256_file(video)
                or any(sha256_file(p) != fp for p, fp in sources.items())):
            raise ValueError("stale media, plan or render settings")
        declared = {c["source_path"] for c in plan["clips"] if "source_path" in c}
        if declared and declared != set(sources):
            raise ValueError("source set mismatch")
        if not declared and len(sources) != 1:
            raise ValueError("ambiguous single source")
    except (OSError, KeyError, ValueError, TypeError) as exc:
        raise ValueError(f"cut plan binding failed: {exc}") from exc
    return plan


def _associate_plan(report, plan, pts, end):
    """Legacy seconds are only a coarse locator, not exact source-frame proof."""
    spans = []
    previous = Fraction(0)
    for clip in plan["clips"]:
        start, stop = Fraction(str(clip["output_start"])), Fraction(str(clip["output_end"]))
        if start != previous or stop <= start:
            raise ValueError("bound cut plan must be contiguous and ordered")
        spans.append((start, stop, clip))
        previous = stop
    tolerance = max(b - a for a, b in zip(pts, [*pts[1:], end]))
    if not spans or abs(previous - end) > tolerance:
        raise ValueError("bound plan duration disagrees with actual frame clock")
    joins = [bisect_left(pts, s[0]) for s in spans[1:]]
    for candidate in report["candidates"]:
        f, t = candidate["frame"], pts[candidate["frame"]]
        if any(abs(f - join) <= 1 for join in joins):
            candidate["origin"] = "EDIT_JOIN_CANDIDATE"
        else:
            for start, stop, clip in spans:
                if start < t < stop:
                    candidate["inside_bound_clip"] = clip["clip_id"]
                    candidate["source_time_estimate"] = str(Fraction(str(clip["source_start"])) + t - start)
                    candidate["mapping_precision"] = "legacy_seconds_estimate"
                    # No source scan: motion, exposure, overlay animation and native cuts
                    # remain indistinguishable from output scene score alone.
                    break
    report["plan_joins"] = joins


def scan_video(video, *, threshold=0.35, plan_path=None, roi=None, **policy):
    video = Path(video).resolve()
    before = sha256_file(video)
    plan_hash = sha256_file(plan_path) if plan_path is not None else None
    plan = load_bound_plan(video, plan_path) if plan_path is not None else None
    pts, end, origin = probe_frame_clock(video)
    scenes = detect_scene_pts(video, threshold, roi)
    frames_by_pts = {p + origin: i for i, p in enumerate(pts)}
    if any(p not in frames_by_pts for p in scenes):
        raise ValueError("scene candidate does not match a unique decoded frame PTS")
    report = summarize_candidates(pts, end, [frames_by_pts[p] for p in scenes if frames_by_pts[p] > 0], **policy)
    if plan is not None:
        _associate_plan(report, plan, pts, end)
        # The scan can take minutes: recheck inputs rather than signing a mixed revision.
        if plan_hash != sha256_file(plan_path):
            raise ValueError("cut plan changed during scan")
        load_bound_plan(video, plan_path)
    if sha256_file(video) != before:
        raise ValueError("video changed during scan")
    report.update({
        "schema_version": 1, "artifact": "shot_review", "algorithm": "scene-frame-recall-v1", "scan_complete": True,
        "media": {"path": str(video), "sha256": before, "frame_count": len(pts),
                  "origin_pts_exact": str(origin), "duration_exact": str(end),
                  "frame_clock_sha256": hashlib.sha256(
                      json.dumps([str(p) for p in [*pts, end]]).encode()).hexdigest()},
        "plan_binding": {"path": str(Path(plan_path).resolve()), "sha256": plan_hash} if plan is not None else None,
        "scene_threshold": threshold,
        "scene_roi": None if roi is None else list(roi),
        "limits": ["scene score is not a confirmed shot or flash-frame defect",
                   "source-origin confirmation requires independent source footage review",
                   "NO_CANDIDATES is not perceptual approval or listening evidence"],
    })
    return report


def _atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temp = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def _report_target(video, output, plan_path):
    """Unknown existing files are never report targets, even with corrupt source metadata."""
    from cut_contract import _edited_source_meta_path
    protected = {Path(video).resolve(), _edited_source_meta_path(video).resolve()}
    if plan_path is not None:
        protected.add(Path(plan_path).resolve())
    target = Path(output).resolve()
    if target in protected:
        raise ValueError("report must not overwrite media, source, plan or render metadata")
    if Path(output).exists():
        try:
            old = json.loads(Path(output).read_text(encoding="utf-8"))
            if (not isinstance(old, dict) or type(old.get("schema_version")) is not int
                    or old["schema_version"] != 1 or old.get("artifact") != "shot_review"):
                raise ValueError("not a shot-review artifact")
        except (OSError, UnicodeError, ValueError) as exc:
            raise ValueError("report must not overwrite an unknown existing file") from exc
    return target


def _protect_declared_sources(video, plan_path, target):
    """Use both plan and metadata declarations, even when their identities disagree."""
    from cut_contract import _edited_source_meta_path
    if plan_path is None:
        return
    paths, errors = set(), []
    for path, kind in [(Path(plan_path), "plan"), (_edited_source_meta_path(video), "meta")]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if kind == "plan":
                paths.update(c["source_path"] for c in data["clips"] if "source_path" in c)
            else:
                paths.update(data["source_fingerprints"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(exc)
    if target in {Path(p).resolve() for p in paths}:
        raise UnsafeReportTarget("report must not overwrite a declared source")
    if errors:
        raise ValueError(f"cannot verify declared source protection: {errors[0]}")


class UnsafeReportTarget(ValueError):
    """Do not write even failure evidence to an input file."""


def write_scan(video, output, **options):
    target = _report_target(video, output, options.get("plan_path"))
    roi = options.get("roi")
    base = {"schema_version": 1, "artifact": "shot_review", "scan_complete": False,
            "normal_speed_review": "NOT_CHECKED",
            "scene_threshold": options.get("threshold", 0.35),
            "scene_roi": None if roi is None else list(roi)}
    try:
        _protect_declared_sources(video, options.get("plan_path"), target)
        _atomic_json(output, {**base, "status": "SCANNING"})
        report = scan_video(video, **options)
    except UnsafeReportTarget:
        raise
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        _atomic_json(output, {**base, "status": "SCAN_FAILED", "error": str(exc)})
        raise
    _atomic_json(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video")
    parser.add_argument("--output", required=True)
    parser.add_argument("--plan", default=None, help="optional current clip_plan_validated.json; stale bindings fail")
    parser.add_argument("--threshold", type=float, default=0.35)
    parser.add_argument("--roi", nargs=4, type=int, metavar=("X", "Y", "WIDTH", "HEIGHT"))
    parser.add_argument("--max-short-frames", type=int, default=None,
                        help="explicit frame cap; default derives round(fps * max_short_seconds)")
    parser.add_argument("--max-short-seconds", type=float, default=1.0)
    parser.add_argument("--dense-window-seconds", type=float, default=2.0)
    parser.add_argument("--min-dense-cuts", type=int, default=4)
    args = parser.parse_args()
    report = write_scan(args.video, args.output, threshold=args.threshold, plan_path=args.plan, roi=args.roi,
                        max_short_frames=args.max_short_frames, max_short_seconds=args.max_short_seconds,
                        dense_window_seconds=args.dense_window_seconds, min_dense_cuts=args.min_dense_cuts)
    print(json.dumps({"status": report["status"], "short_spans": len(report["short_spans"]),
                      "dense_windows": len(report["dense_windows"]), "report": args.output}))


if __name__ == "__main__":
    main()
