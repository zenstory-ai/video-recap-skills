#!/usr/bin/env python3
"""video-script validation entrypoint.

Validate an agent-written narration.json against the local understanding index.
Legacy full mode performs budget cleanup/deduplication and derives speech ownership.
Protected mode keeps the approved timeline and metadata exact except for measured
``overlaps_speech`` ownership.
"""

import argparse
import json
import math
from pathlib import Path

from lib import CONFIG, log
from narration_lint import (
    _validate_narration_budget,
    validate_narration_or_raise,
)
from speech_ownership import measure_narration_speech_ownership
from timeline_fusion import _align_narration_to_quiet


def _load(path):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _load_cut_clip_plan(work_dir):
    raw_plan = Path(work_dir) / "clip_plan.json"
    validated_plan = Path(work_dir) / "clip_plan_validated.json"
    if not validated_plan.exists():
        return _load(raw_plan)
    if not raw_plan.exists():
        return _load(validated_plan)

    # Validation may run before the cut stage refreshes clip_plan_validated.json;
    # a validated plan older than the raw plan is stale, so lint against the raw plan.
    if validated_plan.stat().st_mtime_ns >= raw_plan.stat().st_mtime_ns:
        return _load(validated_plan)
    return _load(raw_plan)


def _validate_output_timeline_bounds(narration, duration, tolerance=0.05):
    """Hard-gate lint-validated cut_output narration against the rendered output timeline.

    cut_output narration is authored in edited_source.mp4 time. If any segment falls outside
    that media duration, fail before TTS/render instead of spending time on unusable audio.
    """
    if not math.isfinite(duration) or duration <= 0:
        raise SystemExit(
            f"output_duration must be finite and positive, got output_duration={duration:.3f}"
        )

    problems = []
    for idx, seg in enumerate(narration):
        start, end = seg["start"], seg["end"]
        if end <= -tolerance or start >= duration + tolerance:
            problems.append(
                f"segment {idx} [{start:.3f},{end:.3f}] fully outside output_duration={duration:.3f}"
            )
            continue
        if start < -tolerance:
            problems.append(
                f"segment {idx} start={start:.3f} before output timeline (output_duration={duration:.3f})"
            )
        if end > duration + tolerance:
            problems.append(
                f"segment {idx} end={end:.3f} exceeds output_duration={duration:.3f}"
            )
    if problems:
        raise SystemExit(
            "cut_output narration exceeds rendered output timeline: "
            + "; ".join(problems)
        )


def main():
    ap = argparse.ArgumentParser(
        description="Validate + align agent-written narration.json."
    )
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--mode", default="full", choices=["full", "cut", "cut_output"])
    ap.add_argument(
        "--output-duration",
        type=float,
        default=None,
        help="cut_output: rendered edited_source.mp4 duration in seconds",
    )
    ap.add_argument(
        "--preserve-approved-text",
        action="store_true",
        help="validate approved narration without rewriting, truncating, merging, or reordering it",
    )
    args = ap.parse_args()

    work_dir = Path(args.work_dir)
    CONFIG["edit_mode"] = args.mode
    narration_path = work_dir / "narration.json"
    narration = _load(narration_path)
    if narration is None:
        raise SystemExit(f"缺少 {narration_path}；请先按 video-script 规则写解说词")
    vlm_analysis = _load(work_dir / "vlm_analysis.json")
    silence_periods = _load(work_dir / "silence_periods.json") or []
    if args.mode == "cut_output":
        # Two-pass cut: narration is authored in OUTPUT time against edited_source.mp4 — there is
        # no source-time clip membership check. Lint the authored shape first, then derive speech
        # ownership from the mapped output evidence and persist that measured flag for
        # voiceover/assemble instead of trusting JSON.
        report = validate_narration_or_raise(
            narration, None, clip_plan=None, mode="cut_output", work_dir=work_dir
        )
        narration = measure_narration_speech_ownership(
            narration, work_dir, mode="cut_output"
        )
        try:
            if args.output_duration is None:
                raise SystemExit("--output-duration is required when --mode cut_output")
            _validate_output_timeline_bounds(narration, args.output_duration)
        except SystemExit as exc:
            report["errors"].append({
                "level": "error",
                "index": None,
                "code": "invalid_output_timeline",
                "message": str(exc),
            })
            report["ok"] = False
            report["error_count"] = len(report["errors"])
            (work_dir / "narration_lint.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            raise
        narration_path.write_text(
            json.dumps(narration, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    elif args.mode == "cut":
        clip_plan = _load_cut_clip_plan(work_dir)
        validate_narration_or_raise(
            narration, vlm_analysis, clip_plan=clip_plan, mode="cut", work_dir=work_dir
        )
        if not args.preserve_approved_text:
            narration = _validate_narration_budget(narration, vlm_analysis)
    else:
        validate_narration_or_raise(
            narration, vlm_analysis, clip_plan=None, mode="full", work_dir=work_dir
        )
        if args.preserve_approved_text:
            narration = measure_narration_speech_ownership(
                narration, work_dir, mode="full"
            )
        else:
            narration = _align_narration_to_quiet(
                narration, vlm_analysis, silence_periods
            )
        narration_path.write_text(
            json.dumps(narration, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    log(f"解说词验证完成: {len(narration)} 段")
    print(
        json.dumps(
            {
                "status": "validated",
                "segments": len(narration),
                "lint": str(work_dir / "narration_lint.json"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
