"""Command-line orchestration for the video-cut skill."""

import json
import math


from pathlib import Path

from lib import CONFIG, get_video_duration, log
import shot_review

from cut_contract import (
    _write_edited_source_meta,
    load_clip_plan,
    normalize_clip_plan,
    normalize_multi_source_clip_plan,
    parse_duration_seconds,
    should_reuse_edited_source,
)
from cut_render import (
    build_edited_source_video,
    update_delivery_qc,
    write_cut_delivery_qc,
)
from media_geometry import _has_audio_stream, _select_output_geometry
from narrative_selection import check_required_evidence
from narration_mapping import update_cut_qc
from sentence_boundaries import (
    _combine_boundary_windows,
    _load_sentence_boundary_windows,
    _load_silence_for_source,
    _load_source_speech_spans,
    enforce_clip_sentence_boundaries,
    snap_clip_ends_to_lines,
    snap_clip_starts_to_lines,
    snap_clips_off_shot_changes,
    snap_multi_source_clips,
)


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
        help="multi-source manifest json mapping source_id values to source media",
    )
    parser.add_argument(
        "--target-duration",
        default=None,
        help="target output duration, e.g. 10m / 600 / 00:10:00",
    )
    parser.add_argument(
        "--clip-padding",
        type=float,
        default=None,
        help="seconds to pad each clip on both ends (default: CLIP_PADDING env, else 0)",
    )
    parser.add_argument(
        "--allow-overlap",
        action="store_true",
        help="allow overlapping/duplicate source ranges",
    )
    parser.add_argument(
        "--normalize-only",
        action="store_true",
        help="only normalize the clip plan -> clip_plan_validated.json (no render); "
        "lets validate lint the SAME padded/pruned plan the render uses",
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

    # CLIP_PADDING is declared in every skill's CONFIG, but video-cut is the only place that
    # implements padding — and it used to read the CLI flag alone, so setting the env var did
    # nothing at all while `clip_padding_source: "env"` reported otherwise. CLI still wins.
    clip_padding = (
        args.clip_padding if args.clip_padding is not None else CONFIG["clip_padding"]
    )

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
    if sources_manifest is not None:
        validated_plan = normalize_multi_source_clip_plan(
            raw_plan,
            sources_manifest,
            target_duration=target_seconds,
            clip_padding=clip_padding,
            allow_overlap=args.allow_overlap,
        )
        video_duration = None
    else:
        video_duration = get_video_duration(args.video)
        validated_plan = normalize_clip_plan(
            raw_plan,
            video_duration,
            target_duration=target_seconds,
            clip_padding=clip_padding,
            allow_overlap=args.allow_overlap,
        )

    # Keep boundaries off the original footage's hard cuts (avoids 闪烁 at the edit point).
    # This visual-only pass runs FIRST. The sentence/quiet pass below is the final authority:
    # a prettier edit point must never move the final boundary back inside a spoken sentence.
    if sources_manifest is None and CONFIG["scene_cut_snap"]:
        validated_plan = snap_clips_off_shot_changes(
            validated_plan,
            args.video,
            margin=CONFIG["scene_cut_snap_margin"],
            threshold=CONFIG["scene_cut_detect_threshold"],
        )

    if sources_manifest is None:
        safe_boundaries = _combine_boundary_windows(
            _load_silence_for_source(work_dir, None),
            _load_sentence_boundary_windows(work_dir),
        )
        if CONFIG["snap_clip_line_end"]:
            validated_plan = snap_clip_starts_to_lines(
                validated_plan,
                safe_boundaries,
                video_duration,
                CONFIG["clip_start_snap_max_prepend"],
                max_trim=CONFIG["clip_start_snap_max_trim"],
            )
            validated_plan = snap_clip_ends_to_lines(
                validated_plan,
                safe_boundaries,
                video_duration,
                CONFIG["clip_snap_max_extend"],
            )
        validated_plan = enforce_clip_sentence_boundaries(
            validated_plan,
            safe_boundaries,
            _load_source_speech_spans(work_dir),
            video_duration,
        )

    # Multi-source: snap each clip against ITS OWN source's pauses/shot-changes (single-source
    # snaps above can't, since silence_periods.json and args.video are per-project, not per-source).
    if sources_manifest is not None:
        validated_plan = snap_multi_source_clips(
            validated_plan,
            validated_plan["sources"],
            work_dir,
            line_max_extend=CONFIG["clip_snap_max_extend"],
            scene_margin=CONFIG["scene_cut_snap_margin"],
            scene_threshold=CONFIG["scene_cut_detect_threshold"],
            do_line_snap=CONFIG["snap_clip_line_end"],
            do_scene_snap=CONFIG["scene_cut_snap"],
            start_max_prepend=CONFIG["clip_start_snap_max_prepend"],
            start_max_trim=CONFIG["clip_start_snap_max_trim"],
        )

    validated_plan.setdefault("qc", {})["join_fade_ms"] = round(
        CONFIG["clip_join_audio_fade_ms"], 3
    )
    # Single-source clips carry no source_path; the CLI video is the only input.
    source_paths = list(
        dict.fromkeys(
            clip["source_path"] for clip in validated_plan["clips"] if "source_path" in clip
        )
    ) or [str(args.video)]
    _, _, _, geometry_qc = _select_output_geometry(source_paths, validated_plan["clips"])
    validated_plan["qc"]["output_geometry"] = geometry_qc
    validated_plan["qc"]["output_geometry_reason"] = geometry_qc["reason"]
    update_cut_qc(
        validated_plan,
        allow_duration_drift=bool(args.allow_duration_drift),
        duration_drift_allowed_by="--allow-duration-drift" if args.allow_duration_drift else None,
    )
    if isinstance(raw_plan, dict) and 'required_evidence' in raw_plan:
        # Re-evaluate the final snapped ranges even when the media cache can be reused.
        # A prior rendered receipt must not survive a failed revision preflight.
        (work_dir / 'cut_delivery_qc.json').unlink(missing_ok=True)
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
    update_delivery_qc(
        validated_plan,
        source_paths=source_paths,
        output_path=work_dir / "edited_source.mp4",
    )
    (work_dir / "clip_plan_validated.json").write_text(
        json.dumps(validated_plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if validated_plan["qc"].get("blocking"):
        raise SystemExit(
            "clip_plan QC blocking: fix required source evidence, unsafe sentence boundaries or target-duration drift. "
            "Only duration drift can be explicitly accepted with --allow-duration-drift; "
            "sentence truncation is never allowed. See clip_plan_validated.json['qc']."
        )
    if args.normalize_only:
        # normalize-only produces planned delivery facts in clip_plan_validated.json, but no
        # rendered/reused media exists in this run, so remove any stale final delivery artifact.
        (work_dir / "cut_delivery_qc.json").unlink(missing_ok=True)
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

    edited_source_path = work_dir / "edited_source.mp4"
    if should_reuse_edited_source(edited_source_path, validated_plan, args.video):
        log(f"复用剪辑源视频: {edited_source_path}")
        update_delivery_qc(
            validated_plan,
            source_paths=source_paths,
            output_path=edited_source_path,
            rendered=True,
        )
        write_cut_delivery_qc(work_dir, validated_plan)
        _write_edited_source_meta(edited_source_path, validated_plan, args.video)
        (work_dir / "clip_plan_validated.json").write_text(
            json.dumps(validated_plan, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    else:
        build_edited_source_video(
            args.video, validated_plan, work_dir, edited_source_path
        )
        (work_dir / "clip_plan_validated.json").write_text(
            json.dumps(validated_plan, ensure_ascii=False, indent=2), encoding="utf-8"
        )

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
