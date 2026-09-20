#!/usr/bin/env python3
"""video-script validation entrypoint.

Validate an agent-written narration.json against the local understanding index.
Legacy full mode performs budget cleanup/deduplication and derives speech ownership.
Protected mode keeps the approved timeline and metadata exact except for measured
``overlaps_speech`` ownership.
"""

import argparse
import copy
import json
import math
from pathlib import Path

from deslop_qc import analyze_deslop_qc
from lib import CONFIG, log, stable_hash
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

    raw = _load(raw_plan)
    validated = _load(validated_plan)
    if isinstance(validated, dict) and validated.get(
        "raw_plan_fingerprint"
    ) == stable_hash(raw):
        return validated
    # Validation may run before the cut stage refreshes clip_plan_validated.json.
    # Without a matching raw-plan provenance fingerprint, lint against the current
    # raw plan even when mtimes are equal or misleading.
    return raw


def _validate_output_timeline_bounds(narration, output_duration, tolerance=0.05):
    """Hard-gate cut_output narration against the rendered output timeline.

    cut_output narration is authored in edited_source.mp4 time. If any segment falls outside
    that media duration, fail before TTS/render instead of spending time on unusable audio.
    """
    try:
        duration = float(output_duration)
    except (TypeError, ValueError):
        raise SystemExit(f"output_duration must be numeric, got {output_duration!r}")
    if not math.isfinite(duration) or duration <= 0:
        raise SystemExit(
            f"output_duration must be finite and positive, got output_duration={duration:.3f}"
        )
    if not isinstance(narration, list):
        return

    problems = []
    for idx, seg in enumerate(narration):
        if not isinstance(seg, dict):
            continue
        try:
            start = float(seg.get("start"))
            end = float(seg.get("end"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(start) or not math.isfinite(end):
            problems.append(f"segment {idx} has non-finite time [{start!r},{end!r}]")
            continue
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


def _validate_approved_shape(narration):
    """Reject malformed approved input before any helper can coerce or reorder it."""
    if not isinstance(narration, list) or not narration:
        raise ValueError("approved narration must be a non-empty JSON array")
    previous_start = None
    for index, segment in enumerate(narration):
        if not isinstance(segment, dict):
            raise ValueError(f"approved narration segment #{index} must be an object")
        text = segment.get("narration")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(
                f"approved narration segment #{index} narration must be a non-empty string"
            )
        start, end = segment.get("start"), segment.get("end")
        for name, value in (("start", start), ("end", end)):
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
            ):
                raise ValueError(
                    f"approved narration segment #{index} {name} must be finite numeric"
                )
        if not end > start:
            raise ValueError(
                f"approved narration segment #{index} end must be greater than start"
            )
        pause = segment.get("pause_after_ms")
        if pause is not None and (
            not isinstance(pause, int) or isinstance(pause, bool) or pause < 0
        ):
            raise ValueError(
                f"approved narration segment #{index} pause_after_ms must be a non-negative integer"
            )
        if previous_start is not None and start < previous_start:
            raise ValueError("approved narration segments must remain in chronological order")
        previous_start = start


def _write_approved_shape_failure(work_dir, narration, error):
    """Replace any stale lint PASS with a current strict-shape failure report."""
    deslop_qc = analyze_deslop_qc([], work_dir=work_dir)
    report = {
        "ok": False,
        "error_count": 1,
        "warning_count": 0,
        "metrics": {"input_fingerprint": stable_hash(narration)},
        "deslop_qc": deslop_qc,
        "errors": [
            {
                "level": "error",
                "index": None,
                "code": "invalid_approved_shape",
                "message": str(error),
            }
        ],
        "warnings": [],
    }
    Path(work_dir, "deslop_qc.json").write_text(
        json.dumps(deslop_qc, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    Path(work_dir, "narration_lint.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
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
    if args.preserve_approved_text:
        try:
            _validate_approved_shape(narration)
        except ValueError as exc:
            _write_approved_shape_failure(work_dir, narration, exc)
            raise SystemExit(f"批准稿结构无效：{exc}") from exc
        approved = copy.deepcopy(narration)
    if args.mode == "cut_output":
        # Two-pass cut: narration is authored in OUTPUT time against edited_source.mp4 — there is
        # no source-time clip membership check. Derive speech ownership from the mapped output
        # evidence, then persist that measured flag for voiceover/assemble instead of trusting JSON.
        narration = measure_narration_speech_ownership(
            approved if args.preserve_approved_text else narration,
            work_dir,
            mode="cut_output",
        )
        report = validate_narration_or_raise(
            narration, None, clip_plan=None, mode="cut_output", work_dir=work_dir
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
        if args.preserve_approved_text:
            narration = measure_narration_speech_ownership(
                approved, work_dir, mode="full"
            )
        validate_narration_or_raise(
            narration, vlm_analysis, clip_plan=None, mode="full", work_dir=work_dir
        )
        if not args.preserve_approved_text:
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
