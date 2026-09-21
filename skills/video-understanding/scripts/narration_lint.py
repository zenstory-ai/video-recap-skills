"""Enforce narration timing, evidence, budget, and sentence-integrity rules.

This is the single validator for the agent-authored narration.json: shape, numeric
timing and text checks live here, and every later consumer trusts its result.
"""

import json
import math
from pathlib import Path

from lib import CONFIG, log
from agent_text import (
    _clean_narration_punctuation,
    _find_scene_for_midpoint,
    _normalise_narration_segment,
    _post_dedup_narration,
    _recommended_char_budget,
    _scene_available_seconds,
    _text_char_count,
    _truncate_at_sentence,
)
from deslop_qc import analyze_deslop_qc
from speech_ownership import (
    entry_overlaps_source_speech,
    load_source_sentence_evidence,
)


def _lint_issue(level, index, code, message, **extra):
    issue = {"level": level, "index": index, "code": code, "message": message}
    issue.update(extra)
    return issue


def _is_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


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


def _scene_bounds_for_midpoint(scenes_analysis, start, end):
    scene = _find_scene_for_midpoint(scenes_analysis, start, end)
    if not scene:
        return None
    return scene["start"], scene["end"]


def _frame_fact_times_for_segment(scenes_analysis, start, end):
    """Return frame-fact timestamps covered by a narration segment.

    These timestamps are cheap visual anchors. A narration slot that spans too
    many anchors is more likely to drift into "general story summary" instead
    of staying attached to what is on screen.
    """
    scene = _find_scene_for_midpoint(scenes_analysis, start, end)
    if not scene:
        return []
    return sorted(
        ts
        for ts in map(float, scene.get("frame_facts", {}))
        if start <= ts <= end
    )


def _clip_span(clip):
    """A validated plan clip carries source_start/source_end; a raw agent plan start/end."""
    if "source_start" in clip:
        return clip["source_start"], clip["source_end"]
    return clip["start"], clip["end"]


def _clip_matches_for_segment(seg, clip_plan, requested_clip_id):
    if not clip_plan:
        return []
    clips = clip_plan["clips"]
    midpoint = (seg["start"] + seg["end"]) / 2
    if requested_clip_id is not None:
        clips = [clip for clip in clips if clip.get("clip_id") == requested_clip_id]
    return [
        clip
        for clip in clips
        if _clip_span(clip)[0] <= midpoint <= _clip_span(clip)[1]
    ]


def _source_sentence_entry_issue(index, start, anchors, speech_owned):
    """Return a blocking issue when narration enters midway through source speech.

    The validator reports a correction instead of silently moving audio. Source
    sentence integrity is invariant; an editorial policy string cannot bypass it.
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

    suggested = next(
        (anchor for anchor in anchors if anchor["time"] > start + 0.08), None
    )
    return _lint_issue(
        "error",
        index,
        "interrupts_source_sentence",
        "Narration enters before the source sentence finishes. Move this block to the suggested "
        "sentence-end anchor, or shorten/move/remove it when there is no later verified anchor, "
        "then rerun lint before TTS. Source sentence interruption has no override.",
        entry_time=round(start, 3),
        suggested_start=round(suggested["time"], 3) if suggested else None,
        source_text_tail=str(suggested.get("text_tail", "")).strip() if suggested else "",
        anchor_confidence=suggested["confidence"] if suggested else None,
    )


def _has_connected_predecessor(narration, idx, start):
    """A back-to-back narration handoff (<=150ms gap) does not expose the source track,
    so the next TTS block is not a new source-speech entry."""
    for other_idx, other in enumerate(narration):
        if other_idx == idx or not isinstance(other, dict):
            continue
        other_start, other_end = other.get("start"), other.get("end")
        if not _is_number(other_start) or not _is_number(other_end):
            continue
        if other_start < start and -0.001 <= start - other_end <= 0.15:
            return True
    return False


def lint_narration(
    narration, scenes_analysis=None, *, clip_plan=None, mode="full", work_dir=None
):
    """Preflight-check agent narration before TTS; write narration_lint.json when work_dir is set."""
    scenes_analysis = scenes_analysis or []
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
            budget = _recommended_char_budget(start, end)
            # estimate at the REAL playback rate (after the narration_speed atempo); otherwise a
            # beat sized to its 1.3x-sped slot looks "over budget" when it actually fits.
            play_rate = CONFIG["speech_rate"] * CONFIG["narration_speed"]
            estimated_tts_seconds = char_count / play_rate
            slot_seconds = _scene_available_seconds(start, end)
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
            elif estimated_tts_seconds > slot_seconds:
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
                    source_sentence_anchors,
                    entry_overlaps_source_speech(
                        start,
                        source_evidence,
                        authored_overlap=seg.get("overlaps_speech", True),
                    ),
                )
                if entry_issue:
                    errors.append(entry_issue)

            scene_bounds = _scene_bounds_for_midpoint(scenes_analysis, start, end)
            if scenes_analysis and not scene_bounds:
                warnings.append(
                    _lint_issue(
                        "warning",
                        idx,
                        "outside_scene",
                        "Narration midpoint does not match any detected scene",
                        start=start,
                        end=end,
                    )
                )
            elif scene_bounds and (start < scene_bounds[0] or end > scene_bounds[1]):
                warnings.append(
                    _lint_issue(
                        "warning",
                        idx,
                        "crosses_scene_boundary",
                        "Narration extends outside its midpoint scene boundary",
                        start=start,
                        end=end,
                        scene_start=scene_bounds[0],
                        scene_end=scene_bounds[1],
                    )
                )

            frame_fact_times = _frame_fact_times_for_segment(
                scenes_analysis, start, end
            )
            if (end - start) > CONFIG["visual_beat_max_seconds"] and len(
                frame_fact_times
            ) > CONFIG["visual_beat_max_facts"]:
                warnings.append(
                    _lint_issue(
                        "warning",
                        idx,
                        "visual_beat_too_broad",
                        "Narration spans many visual anchors; split or tighten timing so the voiceover stays tied to current pictures",
                        start=start,
                        end=end,
                        duration=round(end - start, 2),
                        frame_fact_times=[round(ts, 2) for ts in frame_fact_times[:8]],
                    )
                )

            if mode == "cut":
                requested_clip_id = seg.get("source_clip_id")
                if requested_clip_id is not None:
                    try:
                        requested_clip_id = int(requested_clip_id)
                    except (TypeError, ValueError):
                        errors.append(
                            _lint_issue(
                                "error",
                                idx,
                                "invalid_source_clip_id",
                                "source_clip_id must be an integer",
                            )
                        )
                        continue
                matches = _clip_matches_for_segment(seg, clip_plan, requested_clip_id)
                if not matches:
                    errors.append(
                        _lint_issue(
                            "error",
                            idx,
                            "outside_clip_plan",
                            "Cut-mode narration must fall inside a selected clip",
                            start=start,
                            end=end,
                        )
                    )
                elif len(matches) > 1 and requested_clip_id is None:
                    errors.append(
                        _lint_issue(
                            "error",
                            idx,
                            "ambiguous_source_clip",
                            "Repeated/overlapping clips require source_clip_id",
                            start=start,
                            end=end,
                        )
                    )
                if len(matches) == 1:
                    clip_start, clip_end = _clip_span(matches[0])
                    if start < clip_start or end > clip_end:
                        warnings.append(
                            _lint_issue(
                                "warning",
                                idx,
                                "crosses_clip_boundary",
                                "Narration extends beyond its clip; it will be trimmed to the clip and may describe footage that was cut",
                                start=start,
                                end=end,
                                clip_start=round(clip_start, 3),
                                clip_end=round(clip_end, 3),
                            )
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

    # Block-coverage check (full mode only; cut-mode density is measured on the mapped output
    # timeline, not the source timestamps used here). Coverage is diagnostic, not a creative quota:
    # flag wall-to-wall or sparse drafts so the Agent consciously checks the visual/audio board,
    # plus missing original-audio gaps and fragmented one-sentence TTS blocks.
    metrics = {}
    if mode == "full" and len(sorted_segments) >= 2:
        timeline_start = 0.0
        timeline_end = sorted_segments[-1]["end"]
        scene_ends = [scene["end"] for scene in scenes_analysis]
        if scene_ends:
            timeline_end = max(timeline_end, max(scene_ends))
        span = max(0.0, timeline_end - timeline_start)
        # Score coverage at the SAME conservative rate the writer is budgeted at
        # (_recommended_char_budget uses speech_rate * speech_safety_margin * narration_speed),
        # else the metric scores ~18% stricter than its own budget and false-flags under_narrated.
        play_rate = (
            CONFIG["speech_rate"] * CONFIG["speech_safety_margin"] * CONFIG["narration_speed"]
        )
        spoken = [s["char_count"] / play_rate for s in sorted_segments]
        spoken_ends = [
            min(sorted_segments[i]["end"], sorted_segments[i]["start"] + spoken[i])
            for i in range(len(sorted_segments))
        ]
        # Measure the UNION of estimated playback intervals. Adjacent authored
        # blocks can overlap the conservative text-duration estimate; summing them
        # double-counted time and falsely called a recap wall-to-wall.
        estimated_intervals = sorted(
            (sorted_segments[i]["start"], spoken_ends[i])
            for i in range(len(sorted_segments))
            if spoken_ends[i] > sorted_segments[i]["start"]
        )
        merged_intervals = []
        for start, end in estimated_intervals:
            if merged_intervals and start <= merged_intervals[-1][1]:
                merged_intervals[-1][1] = max(merged_intervals[-1][1], end)
            else:
                merged_intervals.append([start, end])
        narrated_seconds = sum(end - start for start, end in merged_intervals)
        coverage = narrated_seconds / span if span > 0 else 0.0
        orig_min = CONFIG["original_block_min_seconds"]
        orig_gaps = [sorted_segments[0]["start"] - timeline_start]
        orig_gaps.extend(
            sorted_segments[i + 1]["start"] - spoken_ends[i]
            for i in range(len(sorted_segments) - 1)
        )
        orig_gaps.append(timeline_end - spoken_ends[-1])
        original_blocks = sum(1 for g in orig_gaps if g >= orig_min)
        avg_chars = sum(s["char_count"] for s in sorted_segments) / len(sorted_segments)
        cov_target = CONFIG["narration_coverage_target"]
        cov_max = CONFIG["narration_coverage_max"]
        cov_min = CONFIG["narration_coverage_min"]
        block_min_chars = CONFIG["narration_block_min_chars"]
        metrics = {
            "segment_count": len(sorted_segments),
            "timeline_span_seconds": round(span, 2),
            "narrated_seconds": round(narrated_seconds, 2),
            "narration_coverage": round(coverage, 2),
            "coverage_target": cov_target,
            "original_block_count": original_blocks,
            "avg_block_chars": round(avg_chars, 1),
        }
        if coverage > cov_max:
            warnings.append(
                _lint_issue(
                    "warning",
                    None,
                    "no_original_blocks",
                    "Narration is nearly wall-to-wall — the original audio never gets to breathe. Pull back at a "
                    "few strong moments and write NO narration there so the original plays at full volume. "
                    "Choose those moments by audio_owner, not by a fixed ratio.",
                    narration_coverage=round(coverage, 2),
                    coverage_max=cov_max,
                )
            )
        elif coverage < cov_min:
            warnings.append(
                _lint_issue(
                    "warning",
                    None,
                    "under_narrated",
                    "Narration coverage is sparse. This is not automatically wrong: verify that every long gap is "
                    "intentionally owned by original dialogue/action/ambience/music/silence in visual_audio_board.json. "
                    "Only add a block when it has a specific narration job.",
                    narration_coverage=round(coverage, 2),
                    coverage_min=cov_min,
                )
            )
        if original_blocks == 0 and span >= 3 * orig_min:
            warnings.append(
                _lint_issue(
                    "warning",
                    None,
                    "no_original_breaks",
                    "No deliberate original-audio blocks — narration runs end-to-end with no gap for a strong "
                    "original moment (a key line, an action beat, the music). Leave a few multi-second gaps "
                    "between blocks where the original plays alone.",
                    original_block_min_seconds=orig_min,
                )
            )
        if len(sorted_segments) >= 8 and avg_chars < block_min_chars:
            warnings.append(
                _lint_issue(
                    "warning",
                    None,
                    "fragmented_beats",
                    "Beats are fragmented into single short sentences; each is synthesized as a separate TTS "
                    "utterance, which sounds choppy. Merge adjacent related sentences into one fluent BLOCK "
                    "that completes a continuous thought; sentence count is not the target.",
                    avg_block_chars=round(avg_chars, 1),
                    block_min_chars=block_min_chars,
                )
            )

    deslop_qc = analyze_deslop_qc(
        [seg for seg in narration if isinstance(seg, dict)]
        if isinstance(narration, list)
        else [],
        work_dir=work_dir,
    )
    for blocker in deslop_qc["blockers"]:
        errors.append(
            _lint_issue(
                "error",
                blocker["index"],
                blocker["code"],
                blocker["message"],
                source=blocker["source"],
                matches=blocker.get("matches"),
                sentence=blocker.get("sentence"),
            )
        )

    report = {
        "ok": not errors,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "metrics": metrics,
        "deslop_qc": deslop_qc,
        "errors": errors,
        "warnings": warnings,
    }
    if work_dir is not None:
        Path(work_dir, "deslop_qc.json").write_text(
            json.dumps(deslop_qc, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        Path(work_dir, "narration_lint.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return report


def validate_narration_or_raise(
    narration, scenes_analysis=None, *, clip_plan=None, mode="full", work_dir=None
):
    report = lint_narration(
        narration, scenes_analysis, clip_plan=clip_plan, mode=mode, work_dir=work_dir
    )
    if report["errors"]:
        sample = "; ".join(
            f"#{e['index']}: {e['code']}" for e in report["errors"][:3]
        )
        raise ValueError(f"narration.json 预检失败: {sample}; 详见 narration_lint.json")
    if report["warnings"]:
        log(
            f"narration lint: {len(report['warnings'])} warnings (see narration_lint.json)"
        )
    else:
        log("narration lint: ok")
    return report


def _validate_narration_budget(narration, scenes_analysis):
    """Trim lint-validated narration to its timing budgets; drop what cannot be spoken."""
    del scenes_analysis  # scene boundaries are advisory (lint warns); authored timing is kept
    cleaned = []
    for raw in narration:
        item = _normalise_narration_segment(raw)
        max_chars = _recommended_char_budget(item["start"], item["end"])
        if max_chars < 5:
            log(f"  丢弃过短解说段 {item['start']:.1f}-{item['end']:.1f}s")
            continue
        if _text_char_count(item["narration"]) > max_chars * 1.25:
            truncated = _truncate_at_sentence(item["narration"], max_chars)
            if truncated and _text_char_count(truncated) >= 5:
                log(f"  解说超预算，已截短: {item['start']:.1f}-{item['end']:.1f}s")
                item["narration"] = truncated
            else:
                log(
                    f"  解说超预算且无法安全截断，已丢弃: {item['start']:.1f}-{item['end']:.1f}s"
                )
                continue
        item["narration"] = _clean_narration_punctuation(item["narration"])
        stripped = item["narration"].strip()
        if stripped and stripped[-1] in "，：、；,—":
            item["narration"] = stripped.rstrip("，：、；,—") + "。"
        cleaned.append(item)

    cleaned.sort(key=lambda n: n["start"])
    deduped = []
    for item in cleaned:
        if deduped and item["start"] < deduped[-1]["end"]:
            prev = deduped[-1]
            log(
                f"  解说时间重叠: {item['start']:.1f}-{item['end']:.1f}s vs "
                f"{prev['start']:.1f}-{prev['end']:.1f}s"
            )
            if _text_char_count(item["narration"]) > _text_char_count(
                prev["narration"]
            ):
                deduped[-1] = item
        else:
            deduped.append(item)
    return _post_dedup_narration(deduped)
