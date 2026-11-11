"""Enforce narration timing, evidence, budget, and sentence-integrity rules.

This is the single validator for the agent-authored narration.json: shape, numeric
timing and text checks live here, and every later consumer trusts its result.
"""

import json
import re
from pathlib import Path

from lib import CONFIG, log
from agent_text import (
    TTS_UTTERANCE_OVERHEAD_SECONDS,
    _lint_char_budget,
    _scene_available_seconds,
    _text_char_count,
)
from entry_suggestion import (
    SUGGESTED_ANCHOR_MAX_SHIFT_SECONDS,
    _has_connected_predecessor,
    _is_number,
    _nearest_safe_anchor,
    _other_block_spans,
    _suggestion_window,
)
from lint_summary import format_lint_failure, format_lint_warnings
from speech_ownership import (
    anchor_boundary_use,
    entry_overlaps_source_speech,
    load_source_sentence_evidence,
)

PLACEHOLDER_RE = re.compile(r"【主角】|TA要赌上")


def _lint_issue(level, index, code, message, **extra):
    issue = {"level": level, "index": index, "code": code, "message": message}
    issue.update(extra)
    return issue


# Copy of recap_timeline._ALLOWED_VISUAL_OVERLAY_TYPES (recap drops any other type after TTS);
# test_audio_policy_parity.py holds the two equal.
_RENDERED_OVERLAY_TYPES = {"top_title", "inline_label_or_callout"}


def _unsupported_overlay_warnings(index, seg):
    overlays = seg.get("visual_overlays")
    return [
        _lint_issue(
            "warning", index, "unsupported_visual_overlay_type",
            f"visual_overlays[].type {overlay['type']!r} 不会渲染，recap 会丢弃它；"
            f"只支持 {', '.join(sorted(_RENDERED_OVERLAY_TYPES))}",
            overlay_index=position,
        )
        for position, overlay in enumerate(overlays if isinstance(overlays, list) else [])
        if isinstance(overlay, dict) and isinstance(overlay.get("type"), str)
        and overlay["type"].strip() and overlay["type"] not in _RENDERED_OVERLAY_TYPES
    ]


def _visual_overlay_issues(index, seg):
    """Shape-check the renderer handoff recap reads verbatim from a validated segment.

    recap owns which overlay `type` values it actually renders and drops the rest; lint owns
    the shape, because nothing between here and that filter re-checks it — and the filter runs
    after TTS, so a malformed overlay caught there would already have cost a synthesis pass.
    """
    overlays = seg.get("visual_overlays")
    if overlays is None:
        return []
    if not isinstance(overlays, list):
        return [
            _lint_issue(
                "error", index, "invalid_visual_overlays", "visual_overlays must be an array"
            )
        ]
    issues = []
    for position, overlay in enumerate(overlays):
        if not isinstance(overlay, dict):
            issues.append(
                _lint_issue(
                    "error", index, "invalid_visual_overlay",
                    "visual_overlays entries must be objects",
                    overlay_index=position,
                )
            )
            continue
        for key in ("type", "text"):
            value = overlay.get(key)
            if not isinstance(value, str) or not value.strip():
                issues.append(
                    _lint_issue(
                        "error", index, "invalid_visual_overlay",
                        f"visual_overlays[].{key} must be a non-empty string",
                        overlay_index=position,
                    )
                )
        for key in ("start", "end"):
            if key in overlay and not _is_number(overlay[key]):
                issues.append(
                    _lint_issue(
                        "error", index, "invalid_visual_overlay",
                        f"visual_overlays[].{key} must be numeric",
                        overlay_index=position,
                    )
                )
    return issues


def _source_sentence_entry_issue(
    index, start, end, anchors, speech_owned, other_spans=(), window=(None, None),
):
    """Return a blocking issue when narration enters midway through source speech.

    The validator reports a correction instead of silently moving audio. Source
    sentence integrity is invariant; an editorial policy string cannot bypass it.
    `other_spans` are the other blocks' (start, end) and `window` the open range between
    the neighbours (see _nearest_safe_anchor): the suggested entry never moves this block
    onto, flush against, or past one of them.
    """
    if not speech_owned:
        return None
    # A cold-open at the first frame is not an interruption: the source sentence
    # has not been allowed to start. All later entries must use a measured anchor.
    if start <= 0.25:
        return None
    if not anchors:
        return _lint_issue(
            "error",
            index,
            "source_sentence_anchors_unavailable",
            "Source speech owns this entry but no verified sentence-end anchor is available. "
            "Move/remove the narration or regenerate output speech evidence before TTS.",
            entry_time=round(start, 3),
            suggested_start=None,
        )
    # Only the measured acoustic pause owns a safe entry. A tiny 80ms post-anchor
    # tolerance covers timestamp/sample rounding; the old +450ms allowance could
    # already be several Chinese syllables into the next sentence.
    for anchor in anchors:
        when = anchor["time"]
        pause_start = anchor.get("pause_start", when - 0.12)
        if pause_start - 0.05 <= start <= when + 0.08:
            return None

    suggested = _nearest_safe_anchor(start, end, anchors, other_spans, window)
    return _lint_issue(
        "error",
        index,
        "interrupts_source_sentence",
        "Narration enters before the source sentence finishes. Move the whole block (same "
        "duration) to the suggested sentence-end anchor, or shorten/move/remove it when no "
        f"anchor within {SUGGESTED_ANCHOR_MAX_SHIFT_SECONDS:g}s keeps it between its neighbours "
        "and clear of the other blocks, then rerun lint before TTS. The window stays inside the "
        "output duration (cut_output); full mode does not know the video's end, so keep it "
        "inside the video. Source sentence interruption has no override.",
        entry_time=round(start, 3),
        suggested_start=round(suggested["time"], 3) if suggested else None,
        suggested_end=round(suggested["time"] + end - start, 3) if suggested else None,
        max_shift_seconds=SUGGESTED_ANCHOR_MAX_SHIFT_SECONDS,
        source_text_tail=str(suggested.get("text_tail", "")).strip() if suggested else "",
        anchor_confidence=suggested["confidence"] if suggested else None,
        anchor_boundary_use=anchor_boundary_use(suggested) if suggested else None,
    )


def _suggestion_limits(output_duration):
    """Closed range a suggested move must stay in: [0, output_duration] when that is known
    (cut_output)."""
    usable = _is_number(output_duration) and output_duration > 0
    return 0.0, output_duration if usable else None


# Full-mode hard text budget: a segment longer than this multiple of its recommended
# character budget cannot be spoken in its window, so lint returns it to the author
# instead of anything downstream shortening it.
OVER_BUDGET_ERROR_RATIO = 1.25


def _over_budget_error(index, start, end, char_count, budget):
    limit = int(budget * OVER_BUDGET_ERROR_RATIO)
    return _lint_issue(
        "error",
        index,
        "over_budget",
        f"段 {index + 1} [{start:.2f}-{end:.2f}s] has {char_count} chars; the window holds "
        f"about {budget} (hard limit {limit}), {char_count - limit} over the limit. "
        "Shorten the text or widen/move the window, then rerun validate; "
        "validation never shortens approved text.",
        start=start,
        end=end,
        budget_chars=budget,
        limit_chars=limit,
        actual_chars=char_count,
        over_chars=char_count - limit,
        tts_overhead_seconds=TTS_UTTERANCE_OVERHEAD_SECONDS,
    )


def _placeholder_leakage_issues(narration, work_dir):
    issues = []
    sources = [("narration", narration if isinstance(narration, list) else [])]
    if work_dir is not None:
        sub_path = Path(work_dir) / "original_subtitles.json"
        if sub_path.exists():
            try:
                subs = json.loads(sub_path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                subs = []
            if isinstance(subs, list):
                sources.append(("original_subtitles", subs))
    for source, items in sources:
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            text = str(item.get("narration" if source == "narration" else "text", "") or "").strip()
            matches = PLACEHOLDER_RE.findall(text)
            if matches:
                issues.append(
                    _lint_issue(
                        "error",
                        idx,
                        "placeholder_leakage",
                        "示例占位内容泄漏到成稿中（如【主角】/TA要赌上）",
                        source=source,
                        matches=matches,
                    )
                )
    return issues


def lint_narration(
    narration, scenes_analysis=None, *, mode="full", work_dir=None, output_duration=None,
):
    """Preflight-check agent narration before TTS; write narration_lint.json when work_dir is set.

    Segments must already be in chronological order; validation never reorders them.
    `output_duration` (cut_output) bounds where interrupt suggestions may move a block."""
    del scenes_analysis
    errors = []
    warnings = []
    normalized = []
    source_evidence = load_source_sentence_evidence(work_dir, mode=mode)
    source_sentence_anchors = source_evidence["anchors"]
    if not isinstance(narration, list):
        errors.append(
            _lint_issue(
                "error",
                None,
                "invalid_json_shape",
                "narration.json must be a JSON array",
            )
        )
    else:
        previous_start = None
        # Suggested (start, end) of blocks already told to move; later blocks keep clear.
        suggested_spans = {}
        for idx, seg in enumerate(narration):
            if not isinstance(seg, dict):
                errors.append(
                    _lint_issue(
                        "error",
                        idx,
                        "invalid_segment",
                        "Narration segment must be an object",
                    )
                )
                continue
            start, end = seg.get("start"), seg.get("end")
            if not _is_number(start) or not _is_number(end):
                errors.append(
                    _lint_issue(
                        "error", idx, "invalid_time", "start/end must be finite numbers"
                    )
                )
                continue
            if previous_start is not None and start < previous_start:
                errors.append(
                    _lint_issue(
                        "error",
                        idx,
                        "out_of_order",
                        "Segments must be listed in chronological order",
                        start=start,
                        previous_start=previous_start,
                    )
                )
            previous_start = start
            text = seg.get("narration")
            if not isinstance(text, str):
                errors.append(
                    _lint_issue(
                        "error",
                        idx,
                        "invalid_narration",
                        "narration must be a string",
                        start=start,
                        end=end,
                    )
                )
                continue
            text = text.strip()
            pause = seg.get("pause_after_ms", CONFIG["breath_ms"])
            if not isinstance(pause, int) or isinstance(pause, bool) or pause < 0:
                errors.append(
                    _lint_issue(
                        "error",
                        idx,
                        "invalid_pause",
                        "pause_after_ms must be a non-negative integer number of milliseconds",
                    )
                )
                continue
            errors.extend(_visual_overlay_issues(idx, seg))
            warnings.extend(_unsupported_overlay_warnings(idx, seg))
            if end <= start:
                errors.append(
                    _lint_issue(
                        "error",
                        idx,
                        "invalid_time_range",
                        "end must be greater than start",
                        start=start,
                        end=end,
                    )
                )
                continue
            if not text:
                errors.append(
                    _lint_issue(
                        "error",
                        idx,
                        "empty_narration",
                        "narration text must not be empty",
                        start=start,
                        end=end,
                    )
                )
                continue

            char_count = _text_char_count(text)
            budget = _lint_char_budget(start, end)
            # estimate at the REAL playback rate (after the narration_speed atempo); otherwise a
            # beat sized to its 1.3x-sped slot looks "over budget" when it actually fits. The
            # utterance's TTS edge silence takes room too.
            play_rate = CONFIG["speech_rate"] * CONFIG["narration_speed"]
            estimated_tts_seconds = char_count / play_rate + TTS_UTTERANCE_OVERHEAD_SECONDS
            slot_seconds = _scene_available_seconds(start, end)
            over_limit = (
                mode == "full" and char_count > budget * OVER_BUDGET_ERROR_RATIO
            )
            if over_limit:
                errors.append(_over_budget_error(idx, start, end, char_count, budget))
            if budget < 5:
                warnings.append(
                    _lint_issue(
                        "warning",
                        idx,
                        "slot_too_short",
                        "Narration slot is very short; TTS may be clipped",
                        start=start,
                        end=end,
                        budget_chars=budget,
                    )
                )
            # Independent of slot_too_short: a short slot can also be overfilled, and the
            # author needs both numbers.
            if not over_limit and estimated_tts_seconds > slot_seconds:
                warnings.append(
                    _lint_issue(
                        "warning",
                        idx,
                        "over_budget",
                        "Text may exceed the available TTS slot",
                        start=start,
                        end=end,
                        budget_chars=budget,
                        actual_chars=char_count,
                        estimated_tts_seconds=round(estimated_tts_seconds, 2),
                        slot_seconds=round(slot_seconds, 2),
                    )
                )
            if text[-1] not in "。！？!?….":
                warnings.append(
                    _lint_issue(
                        "warning",
                        idx,
                        "incomplete_sentence",
                        "Narration should end with a complete sentence punctuation",
                        text_tail=text[-8:],
                    )
                )

            if not _has_connected_predecessor(narration, idx, start):
                entry_issue = _source_sentence_entry_issue(
                    idx,
                    start,
                    end,
                    source_sentence_anchors,
                    entry_overlaps_source_speech(
                        start,
                        source_evidence,
                        authored_overlap=seg.get("overlaps_speech", True),
                    ),
                    _other_block_spans(narration, idx),
                    _suggestion_window(
                        narration, idx, suggested_spans,
                        _suggestion_limits(output_duration),
                    ),
                )
                if entry_issue:
                    errors.append(entry_issue)
                    if entry_issue.get("suggested_end") is not None:
                        suggested_spans[idx] = (
                            entry_issue["suggested_start"], entry_issue["suggested_end"],
                        )

            normalized.append(
                {"index": idx, "start": start, "end": end, "char_count": char_count}
            )

    sorted_segments = sorted(normalized, key=lambda item: item["start"])
    if isinstance(narration, list) and not sorted_segments:
        errors.append(
            _lint_issue(
                "error",
                None,
                "empty_narration_file",
                "narration.json must contain at least one valid narration segment",
            )
        )
    for prev, curr in zip(sorted_segments, sorted_segments[1:]):
        if curr["start"] < prev["end"]:
            errors.append(
                _lint_issue(
                    "error",
                    curr["index"],
                    "time_overlap",
                    "Segment overlaps the previous narration segment",
                    previous_index=prev["index"],
                    previous_end=prev["end"],
                    start=curr["start"],
                    end=curr["end"],
                )
            )

    errors.extend(_placeholder_leakage_issues(narration, work_dir))

    report = {
        "ok": not errors,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
    }
    if work_dir is not None:
        Path(work_dir, "narration_lint.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return report


class NarrationLintError(ValueError):
    """Lint found blocking errors: an ordinary result for the author to fix, not a crash.

    str() is the compact per-block summary; `report` is the full lint report."""

    def __init__(self, report, lint_path=None):
        self.report = report
        super().__init__(format_lint_failure(report, lint_path))


def validate_narration_or_raise(
    narration, scenes_analysis=None, *, mode="full", work_dir=None, output_duration=None,
):
    report = lint_narration(
        narration, scenes_analysis, mode=mode, work_dir=work_dir, output_duration=output_duration,
    )
    lint_path = None if work_dir is None else Path(work_dir, "narration_lint.json")
    if report["errors"]:
        raise NarrationLintError(report, lint_path)
    if report["warnings"]:
        log(format_lint_warnings(report, lint_path))
    else:
        log("narration lint：通过")
    return report

