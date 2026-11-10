#!/usr/bin/env python3
"""video-script validation entrypoint.

Validate an agent-written narration.json against the local understanding index.
Validation never rewrites the agent's text, timing, order or metadata. Full-mode text
that does not fit its window fails lint and goes back to the author. full and cut_output
persist only the measured ``overlaps_speech`` ownership.
"""

import argparse
import json
import math
from pathlib import Path

from lib import log
from narration_lint import NarrationLintError, validate_narration_or_raise
from speech_ownership import measure_narration_speech_ownership


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
                f"段 {idx + 1} [{start:.3f},{end:.3f}] fully outside output_duration={duration:.3f}"
            )
            continue
        if start < -tolerance:
            problems.append(
                f"段 {idx + 1} start={start:.3f} before output timeline (output_duration={duration:.3f})"
            )
        if end > duration + tolerance:
            problems.append(
                f"段 {idx + 1} end={end:.3f} exceeds output_duration={duration:.3f}"
            )
    if problems:
        raise SystemExit(
            "cut_output narration exceeds rendered output timeline: "
            + "; ".join(problems)
        )


def main():
    try:
        _validate(_parse_args())
    except NarrationLintError as exc:
        # A lint failure is an ordinary result the author fixes from the summary,
        # not a crash: exit non-zero with the summary only, no traceback.
        raise SystemExit(str(exc)) from None


def _parse_args():
    ap = argparse.ArgumentParser(
        description="Validate agent-written narration.json and measure speech ownership."
    )
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--mode", default="full", choices=["full", "cut", "cut_output"])
    ap.add_argument(
        "--output-duration",
        type=float,
        default=None,
        help="cut_output: rendered edited_source.mp4 duration in seconds",
    )
    return ap.parse_args()


def _validate(args):
    work_dir = Path(args.work_dir)
    narration_path = work_dir / "narration.json"
    narration = _load(narration_path)
    if narration is None:
        raise SystemExit(f"缺少 {narration_path}；请先按 video-script 规则写解说词")
    vlm_analysis = _load(work_dir / "vlm_analysis.json")
    if args.mode == "cut_output":
        # Two-pass cut: narration is authored in OUTPUT time against edited_source.mp4 — there is
        # no source-time clip membership check. Lint the authored shape first, then derive speech
        # ownership from the mapped output evidence and persist that measured flag for
        # voiceover/assemble instead of trusting JSON.
        report = validate_narration_or_raise(
            narration, None, clip_plan=None, mode="cut_output", work_dir=work_dir,
            output_duration=args.output_duration,
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
    else:
        # Same contract as cut_output: lint the authored shape, then persist only the
        # measured speech ownership (speech spans minus quiet windows).
        validate_narration_or_raise(
            narration, vlm_analysis, clip_plan=None, mode="full", work_dir=work_dir
        )
        narration = measure_narration_speech_ownership(narration, work_dir, mode="full")
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
