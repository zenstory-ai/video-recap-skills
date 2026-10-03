"""Command-line orchestration for the video-cut skill."""

import json
import math
import os


from pathlib import Path

from lib import CONFIG, get_video_duration, log
import shot_review

from cut_contract import (
    SOURCES_MANIFEST_SHAPE,
    _write_edited_source_meta,
    load_clip_plan,
    normalize_clip_plan,
    normalize_multi_source_clip_plan,
    parse_duration_seconds,
    should_reuse_edited_source,
)
from cut_render import build_edited_source_video
from frame_grid import record_frame_grid, source_frame_grids
from media_geometry import _has_audio_stream, _select_output_geometry
from narrative_selection import check_required_evidence
from cut_qc import update_cut_qc
from sentence_boundaries import snap_multi_source_clips, snap_source_clips


def _write_validated_plan(path, plan, raw_plan_paths):
    """Write clip_plan_validated.json unless the file already holds exactly this plan.

    Downstream output-clock evidence binds to this file's {size, mtime_ns}, so a resumed
    run that re-validates an unchanged plan must leave it alone. The file is still
    rewritten when any raw plan is newer, so "validated older than raw = stale" holds."""
    text = json.dumps(plan, ensure_ascii=False, indent=2)
    if path.exists() and path.read_text(encoding="utf-8") == text:
        written = path.stat().st_mtime_ns
        if all(not raw.exists() or raw.stat().st_mtime_ns <= written for raw in raw_plan_paths):
            return
    path.write_text(text, encoding="utf-8")


def _required_ranges_by_source(raw_plan, source_paths):
    """{plan source path: [(start, end)]} of declared required-evidence nodes.

    Only a hint for frame snapping; check_required_evidence validates the contract itself.
    """
    nodes = raw_plan.get("required_evidence") if isinstance(raw_plan, dict) else None
    nodes = nodes.get("nodes") if isinstance(nodes, dict) else None
    by_realpath = {os.path.realpath(path): path for path in source_paths}
    ranges = {}
    for node in nodes if isinstance(nodes, list) else []:
        try:
            path = by_realpath.get(os.path.realpath(node["source"]))
            start, end = float(node["start"]), float(node["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if path is not None:
            ranges.setdefault(path, []).append((start, end))
    return ranges


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="video-cut: build edited_source.mp4 from an agent clip plan; narration is authored afterwards on the output timeline."
    )
    parser.add_argument("video", help="source video path")
    parser.add_argument(
        "--work-dir",
        required=True,
        help="dir holding clip_plan.json",
    )
    parser.add_argument(
        "--clip-plan",
        default=None,
        help="clip plan json (default: <work-dir>/clip_plan.json)",
    )
    parser.add_argument(
        "--sources-manifest",
        default=None,
        help=f"multi-source manifest json: {SOURCES_MANIFEST_SHAPE}",
    )
    parser.add_argument(
        "--target-duration",
        default=None,
        help="target output duration, e.g. 10m / 600 / 00:10:00",
    )
    parser.add_argument(
        "--allow-overlap",
        action="store_true",
        help="allow overlapping/duplicate source ranges",
    )
    parser.add_argument(
        "--normalize-only",
        action="store_true",
        help="normalize, snap and QC the clip plan, write clip_plan_validated.json, then exit "
        "without rendering",
    )
    parser.add_argument(
        "--review-shots", action="store_true",
        help="scan actual rendered/reused video for internal short-shot and dense-cut candidates; never repair",
    )
    parser.add_argument(
        "--shot-scene-threshold", type=float, default=None,
        help="explicit scene recall threshold for --review-shots (default 0.35; not an acceptance criterion)",
    )
    parser.add_argument(
        "--shot-roi", nargs=4, type=int, metavar=("X", "Y", "WIDTH", "HEIGHT"),
        help="scan only this pixel rectangle with --review-shots; never crop the rendered video",
    )
    parser.add_argument(
        "--allow-duration-drift",
        action="store_true",
        help="do not block when validated clip duration is far from --target-duration",
    )
    args = parser.parse_args()
    if args.shot_scene_threshold is not None and (
        not args.review_shots or not math.isfinite(args.shot_scene_threshold)
        or not 0 <= args.shot_scene_threshold <= 1
    ):
        parser.error("--shot-scene-threshold requires --review-shots and a finite value in [0,1]")
    if args.shot_roi is not None and (
        not args.review_shots or min(args.shot_roi[:2]) < 0 or min(args.shot_roi[2:]) <= 0
    ):
        parser.error("--shot-roi requires --review-shots, nonnegative X/Y and positive WIDTH/HEIGHT")

    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    clip_plan_path = (
        Path(args.clip_plan) if args.clip_plan else work_dir / "clip_plan.json"
    )
    raw_plan = load_clip_plan(clip_plan_path)

    target_seconds = (
        parse_duration_seconds(args.target_duration) if args.target_duration else None
    )
    sources_manifest = (
        json.loads(Path(args.sources_manifest).read_text(encoding="utf-8"))
        if args.sources_manifest
        else None
    )
    # Visual shot-change cleanup runs first; the sentence/quiet pass is the final authority,
    # so a prettier edit point never moves a boundary back inside a spoken sentence.
    snap_options = {
        "line_max_extend": CONFIG["clip_snap_max_extend"],
        "scene_margin": CONFIG["scene_cut_snap_margin"],
        "scene_threshold": CONFIG["scene_cut_detect_threshold"],
        "start_max_prepend": CONFIG["clip_start_snap_max_prepend"],
        "start_max_trim": CONFIG["clip_start_snap_max_trim"],
        "do_line_snap": CONFIG["snap_clip_line_end"],
        "do_scene_snap": CONFIG["scene_cut_snap"],
    }
    if sources_manifest is None:
        video_duration = get_video_duration(args.video)
        validated_plan = normalize_clip_plan(
            raw_plan,
            video_duration,
            target_duration=target_seconds,
            allow_overlap=args.allow_overlap,
        )
    else:
        validated_plan = normalize_multi_source_clip_plan(
            raw_plan,
            sources_manifest,
            target_duration=target_seconds,
            allow_overlap=args.allow_overlap,
        )
    # The canvas (and so the output frame rate) is chosen before snapping because the
    # frame-grid pass snaps clip lengths to whole output frames; the same geometry is
    # recorded in clip_plan_validated.json and used for the render.
    # Single-source clips carry no source_path; the CLI video is the only input.
    source_paths = list(
        dict.fromkeys(
            clip["source_path"] for clip in validated_plan["clips"] if "source_path" in clip
        )
    ) or [str(args.video)]
    _, _, _, geometry_qc = _select_output_geometry(source_paths, validated_plan["clips"])
    frame_grids = source_frame_grids(geometry_qc)
    for path, ranges in _required_ranges_by_source(raw_plan, source_paths).items():
        frame_grids[path]["keep_ranges"] = ranges
    if sources_manifest is None:
        validated_plan = snap_source_clips(
            validated_plan, args.video, video_duration, work_dir,
            frame_grid=frame_grids[str(args.video)], **snap_options,
        )
    else:
        # Each clip snaps against ITS OWN source's pauses, shot changes and frame grid.
        validated_plan = snap_multi_source_clips(
            validated_plan, validated_plan["sources"], work_dir,
            frame_grids={
                sid: frame_grids[source["source_path"]]
                for sid, source in validated_plan["sources"].items()
                if source["source_path"] in frame_grids
            },
            **snap_options,
        )

    validated_plan.setdefault("qc", {})["join_fade_ms"] = round(
        CONFIG["clip_join_audio_fade_ms"], 3
    )
    validated_plan["qc"]["output_geometry"] = geometry_qc
    validated_plan["qc"]["output_geometry_reason"] = geometry_qc["reason"]
    record_frame_grid(validated_plan, geometry_qc)
    update_cut_qc(
        validated_plan,
        allow_duration_drift=bool(args.allow_duration_drift),
        duration_drift_allowed_by="--allow-duration-drift" if args.allow_duration_drift else None,
    )
    if isinstance(raw_plan, dict) and 'required_evidence' in raw_plan:
        # Re-evaluate the final snapped ranges even when the media cache can be reused.
        contract = raw_plan['required_evidence']
        plan_sources = {str(Path(path).resolve()): path for path in source_paths}
        source_audio = {}

        def has_source_audio(source):
            # Probed lazily, once per source, only for validated audio nodes.
            if source not in source_audio:
                source_audio[source] = source in plan_sources and _has_audio_stream(plan_sources[source])
            return source_audio[source]

        report = check_required_evidence(contract, validated_plan, input_video=args.video,
                                         source_audio=has_source_audio)
        validated_plan['qc']['required_evidence'] = {**report, 'contract': contract}
        if report['selection_status'] == 'BLOCK':
            validated_plan['qc'].setdefault('blocking', []).extend(report['findings'])
    plan_path = work_dir / "clip_plan_validated.json"
    raw_plan_paths = {clip_plan_path, work_dir / "clip_plan.json"}
    edited_source_path = work_dir / "edited_source.mp4"
    reuse = (
        not validated_plan["qc"].get("blocking")
        and not args.normalize_only
        and should_reuse_edited_source(edited_source_path, validated_plan, args.video)
    )
    if not reuse:
        _write_validated_plan(plan_path, validated_plan, raw_plan_paths)
    if validated_plan["qc"].get("blocking"):
        raise SystemExit(
            "clip_plan QC blocking: fix required source evidence, unsafe sentence boundaries or target-duration drift. "
            "Only duration drift can be explicitly accepted with --allow-duration-drift; "
            "sentence truncation is never allowed. See clip_plan_validated.json['qc']."
        )
    if args.normalize_only:
        print(
            json.dumps(
                {
                    "status": "normalized",
                    "clips": len(validated_plan["clips"]),
                    "total_duration": validated_plan["total_duration"],
                },
                ensure_ascii=False,
            )
        )
        return

    if reuse:
        log(f"复用剪辑源视频: {edited_source_path}")
        _write_edited_source_meta(edited_source_path, validated_plan, args.video)
    else:
        build_edited_source_video(
            args.video, validated_plan, work_dir, edited_source_path
        )
    _write_validated_plan(plan_path, validated_plan, raw_plan_paths)

    if args.review_shots:
        review_options = {"plan_path": work_dir / "clip_plan_validated.json"}
        if args.shot_scene_threshold is not None:
            review_options["threshold"] = args.shot_scene_threshold
        if args.shot_roi is not None:
            review_options["roi"] = args.shot_roi
        shot_review.write_scan(
            edited_source_path, work_dir / "shot_review.json",
            **review_options,
        )

    log(
        f"剪辑模式: {len(validated_plan['clips'])} 个片段 → {validated_plan['total_duration']:.1f}s"
    )
