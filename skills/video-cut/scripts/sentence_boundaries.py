"""Snap clip boundaries to complete speech and clean shot transitions."""

import json
import re
import subprocess
from pathlib import Path

from lib import log


def _find_source_artifact(work_dir, filename, source_id=None, source_work_dir=None):
    """First existing copy of a per-source understanding artifact, by layout precedence."""
    candidates = []
    if source_work_dir:
        candidates.append(Path(work_dir) / source_work_dir / filename)
    if source_id is not None:
        candidates.append(Path(work_dir) / "sources" / source_id / filename)
    candidates.append(Path(work_dir) / filename)
    return next((path for path in candidates if path.exists()), None)


def _load_sentence_boundary_windows(work_dir, source_id=None, source_work_dir=None):
    """Load reliable sentence-end pause windows produced by video-understanding.

    A sentence anchor's `time` is the acoustic pause end, while `pause_start` is already
    after the final spoken sample. Any cut within that closed interval preserves the sentence.
    Low-confidence anchors are deliberately excluded from the hard-safety path.
    """
    path = _find_source_artifact(
        work_dir, "speech_boundary_anchors.json", source_id, source_work_dir
    )
    if path is None:
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    windows = [
        {
            "start": round(anchor["pause_start"], 3),
            "end": round(anchor["time"], 3),
            "kind": "sentence_anchor",
            "confidence": anchor["confidence"],
        }
        for anchor in payload["sentence_anchors"]
        if anchor["confidence"] in {"high", "medium"}
    ]
    return sorted(windows, key=lambda row: (row["start"], row["end"]))


def _load_source_speech_spans(work_dir, source_id=None, source_work_dir=None):
    """Merged ASR speech spans (asr_clean.json wins over asr_result.json).

    Only used to decide whether an unsafe edge blocks; a missing transcript means unchecked.
    """
    rows = []
    for filename in ("asr_clean.json", "asr_result.json"):
        path = _find_source_artifact(work_dir, filename, source_id, source_work_dir)
        if path is not None:
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows = payload["segments"] if filename == "asr_clean.json" else payload
            break
    spans = sorted(
        ({"start": row["start"], "end": row["end"]} for row in rows if row["text"].strip()),
        key=lambda row: (row["start"], row["end"]),
    )
    merged = []
    for span in spans:
        if merged and span["start"] <= merged[-1]["end"] + 0.05:
            merged[-1]["end"] = max(merged[-1]["end"], span["end"])
        else:
            merged.append(span)
    return merged


def _combine_boundary_windows(*groups):
    unique = {
        (round(row["start"], 3), round(row["end"], 3)) for group in groups for row in group
    }
    return [{"start": start, "end": end} for start, end in sorted(unique)]


def _same_source(left, right):
    # Single-source normalized plans omit source identity; both sides then read as None.
    return left.get("source_id") == right.get("source_id") and left.get(
        "source_path"
    ) == right.get("source_path")


def _continuous_source_join(left, right, tolerance=0.05):
    return (
        _same_source(left, right)
        and abs(left["source_end"] - right["source_start"]) <= tolerance
        and abs(left["output_end"] - right["output_start"]) <= tolerance
    )


def enforce_clip_sentence_boundaries(
    plan, boundary_windows, speech_spans, video_duration, tolerance=0.05
):
    """Block any audible clip edge that falls inside detected source speech.

    Safe edges are: source start/end, a reliable sentence/quiet pause, or a truly contiguous
    same-source join (no media is removed). Missing ASR timing degrades to `unchecked` rather
    than inventing speech. Once ASR says an edge is speech-owned, failure to snap is blocking.
    """
    clips = plan["clips"]
    checks, new_blockers = [], []

    def inside(rows, ts):
        return any(row["start"] - tolerance <= ts <= row["end"] + tolerance for row in rows)

    for idx, clip in enumerate(clips):
        for edge, ts in (("start", clip["source_start"]), ("end", clip["source_end"])):
            contiguous = (
                edge == "start"
                and idx > 0
                and _continuous_source_join(clips[idx - 1], clip, tolerance)
            ) or (
                edge == "end"
                and idx + 1 < len(clips)
                and _continuous_source_join(clip, clips[idx + 1], tolerance)
            )
            if edge == "start" and ts <= tolerance:
                status, reason = "safe", "source_start"
            elif edge == "end" and ts >= video_duration - tolerance:
                status, reason = "safe", "source_end"
            elif contiguous:
                status, reason = "safe", "continuous_source_join"
            elif inside(boundary_windows, ts):
                status, reason = "safe", "sentence_or_quiet_boundary"
            elif not speech_spans:
                status, reason = "unchecked", "speech_timing_unavailable"
            elif not inside(speech_spans, ts):
                status, reason = "safe", "outside_detected_speech"
            else:
                status, reason = "blocking", "inside_detected_speech"
            check = {
                "clip_id": clip["clip_id"],
                "source_id": clip.get("source_id"),
                "edge": edge,
                "time": round(ts, 3),
                "status": status,
                "reason": reason,
            }
            checks.append(check)
            if status == "blocking":
                new_blockers.append(
                    {
                        "code": "unsafe_clip_sentence_boundary",
                        **check,
                        "message": "剪辑边界仍落在原声讲话区间内，必须移动到句末锚点，不能截断原声句子。",
                    }
                )

    qc = plan.setdefault("qc", {})
    qc.setdefault("boundary_status", {})["sentence_checks"] = checks
    existing = [
        row
        for row in qc.get("blocking", [])
        if row["code"] != "unsafe_clip_sentence_boundary"
    ]
    if existing or new_blockers:
        qc["blocking"] = existing + new_blockers
    else:
        qc.pop("blocking", None)
    return plan


def _recompute_clip_timeline(clips):
    cursor = 0.0
    for clip in clips:
        duration = round(clip["source_end"] - clip["source_start"], 3)
        clip["duration"] = duration
        clip["output_start"] = round(cursor, 3)
        clip["output_end"] = round(cursor + duration, 3)
        cursor += duration
    return round(cursor, 3)


def _plan_with_snapped_clips(plan, clips, boundary_key, events):
    """Copy of `plan` carrying the snapped clips, a cursor-based output timeline, and the
    per-pass boundary events under qc.boundary_status[boundary_key]."""
    result = dict(plan)
    result["clips"] = clips
    result["total_duration"] = _recompute_clip_timeline(clips)
    qc = dict(plan.get("qc", {}))
    boundary = dict(qc.get("boundary_status", {}))
    boundary[boundary_key] = events
    qc["boundary_status"] = boundary
    result["qc"] = qc
    return result


def _candidate_overlaps(clips, idx, new_start, new_end):
    return any(
        new_start < other["source_end"] and new_end > other["source_start"]
        for j, other in enumerate(clips)
        if j != idx
    )


def snap_clip_starts_to_lines(
    plan,
    silence_periods,
    video_duration,
    max_prepend,
    max_trim=0.35,
    min_clip_duration=0.3,
):
    """Snap clip starts to natural quiet boundaries, preferring safe prepend over trim.

    Policy:
    - start already inside a quiet window: keep.
    - speech start: prepend to nearest prior quiet window end if within max_prepend.
    - no usable prior quiet: keep and warn, except an extremely near next quiet start
      (<= max_trim) may trim forward if duration/overlap safety holds.
    - never overlap/collapse when allow_overlap is false; unsafe attempts keep-and-warn.
    """
    if not silence_periods:
        return plan

    clips = [dict(c) for c in plan["clips"]]
    allow_overlap = plan["allow_overlap"]
    events = []

    for i, clip in enumerate(clips):
        original_start = clip["source_start"]
        source_end = clip["source_end"]
        event = {
            "clip_id": clip["clip_id"],
            "source_id": clip.get("source_id"),
            "original_start": round(original_start, 3),
            "action": "kept",
        }

        if any(w["start"] <= original_start <= w["end"] for w in silence_periods):
            event["reason"] = "already_quiet"
            events.append(event)
            continue

        prior_ends = [w["end"] for w in silence_periods if w["end"] <= original_start]
        if prior_ends:
            candidate_start = max(prior_ends)
            if original_start - candidate_start <= max_prepend:
                candidate_start = round(candidate_start, 3)
                safe = candidate_start < source_end - min_clip_duration + 1e-9
                if safe and not allow_overlap:
                    safe = not _candidate_overlaps(clips, i, candidate_start, source_end)
                if safe:
                    clip["source_start"] = candidate_start
                    event.update(
                        {
                            "action": "prepended",
                            "new_start": candidate_start,
                            "delta": round(original_start - candidate_start, 3),
                        }
                    )
                    events.append(event)
                    continue
                reason = "overlap_or_collapse"
            else:
                reason = "prior_quiet_too_far"
        else:
            reason = "no_prior_quiet"

        next_starts = [w["start"] for w in silence_periods if w["start"] >= original_start]
        if next_starts:
            candidate_start = min(next_starts)
            trim_delta = candidate_start - original_start
            if 0 < trim_delta <= max_trim:
                candidate_start = round(min(video_duration, candidate_start), 3)
                safe = source_end - candidate_start >= min_clip_duration
                if safe and not allow_overlap:
                    safe = not _candidate_overlaps(clips, i, candidate_start, source_end)
                if safe:
                    clip["source_start"] = candidate_start
                    event.update(
                        {
                            "action": "trimmed",
                            "new_start": candidate_start,
                            "delta": round(trim_delta, 3),
                            "fallback_from": reason,
                        }
                    )
                    events.append(event)
                    continue
                reason = "unsafe_forward_trim"

        event["start_unsnapped_reason"] = reason
        event["warning_code"] = "clip_start_unsnapped"
        events.append(event)

    result = _plan_with_snapped_clips(plan, clips, "start_snaps", events)
    warnings = list(result["qc"].get("warnings", []))
    for event in events:
        if "warning_code" in event:
            warnings.append(
                {
                    "code": event["warning_code"],
                    "clip_id": event["clip_id"],
                    "source_id": event["source_id"],
                    "start_unsnapped_reason": event["start_unsnapped_reason"],
                }
            )
    if warnings:
        result["qc"]["warnings"] = warnings
    return result


def snap_clip_ends_to_lines(plan, silence_periods, video_duration, max_extend):
    """Extend each clip's source_end forward to the next natural pause, preventing mid-sentence cuts.

    - No quiet windows: the plan is returned unchanged.
    - A source_end already inside a quiet window is left alone.
    - Otherwise extend to the next quiet window start, capped by max_extend and video_duration.
    - When plan["allow_overlap"] is False, never extend into another clip's source range.
    - Recomputes output_start/output_end/duration for all clips cursor-based.
    """
    if not silence_periods:
        return plan

    clips = [dict(c) for c in plan["clips"]]
    allow_overlap = plan["allow_overlap"]
    events = []

    for i, clip in enumerate(clips):
        source_end = clip["source_end"]
        event = {
            "clip_id": clip["clip_id"],
            "source_id": clip.get("source_id"),
            "original_end": round(source_end, 3),
            "action": "kept",
        }

        if any(w["start"] <= source_end <= w["end"] for w in silence_periods):
            event["reason"] = "already_quiet"
            events.append(event)
            continue

        candidates = [w["start"] for w in silence_periods if w["start"] >= source_end]
        if not candidates:
            event["end_unsnapped_reason"] = "no_next_quiet"
            events.append(event)
            continue
        next_quiet_start = min(candidates)

        if next_quiet_start > source_end + max_extend:
            event["end_unsnapped_reason"] = "next_quiet_too_far"
            events.append(event)
            continue
        candidate_end = min(next_quiet_start, video_duration)
        if candidate_end <= source_end:
            event["end_unsnapped_reason"] = "non_forward_candidate"
            events.append(event)
            continue

        # When overlaps are forbidden, cap against every other clip's source range.
        if not allow_overlap:
            other_starts = [
                c["source_start"]
                for j, c in enumerate(clips)
                if j != i and c["source_start"] > source_end
            ]
            if other_starts:
                candidate_end = min(candidate_end, min(other_starts))
            if candidate_end <= source_end:
                event["end_unsnapped_reason"] = "overlap_or_collapse"
                events.append(event)
                continue

        clip["source_end"] = round(candidate_end, 3)
        event.update(
            {
                "action": "extended",
                "new_end": clip["source_end"],
                "delta": round(clip["source_end"] - source_end, 3),
            }
        )
        events.append(event)

    return _plan_with_snapped_clips(plan, clips, "end_snaps", events)


def _detect_shot_changes(video, win_start, win_end, threshold, lead=0.25):
    """Absolute source-time hard cuts inside [win_start, win_end] via ffmpeg's scene metric.

    Input-seek to a little before the window: the rebased output PTS restarts at ~0 at the seek
    target, so `seek + pts_time` recovers absolute source time. The `lead` keeps the seek/keyframe
    settling artifact frame outside [win_start, win_end] so it is filtered out, not mistaken for a
    cut.
    """
    if win_end - win_start < 1e-3:
        return []
    seek = max(0.0, win_start - lead)
    dur = (win_end - seek) + 0.1
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-ss",
        f"{seek:.3f}",
        "-i",
        str(video),
        "-t",
        f"{dur:.3f}",
        "-an",
        "-sn",
        "-filter:v",
        f"select='gt(scene,{threshold})',showinfo",
        "-f",
        "null",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 切镜头检测失败: {video}: {proc.stderr.strip()[-500:]}")
    changes = set()
    for m in re.finditer(r"pts_time:([0-9.]+)", proc.stderr):
        t = seek + float(m.group(1))
        if win_start <= t <= win_end:
            changes.add(round(t, 3))
    return sorted(changes)


def snap_clips_off_shot_changes(plan, video, margin, threshold, min_keep=0.5):
    """Nudge each clip's boundaries clear of the ORIGINAL footage's hard cuts to avoid 闪烁.

    A clip whose source_start sits just before a shot-change opens on a brief sliver of the old
    shot that then hard-cuts again; one whose source_end sits just after a shot-change closes on a
    sliver of the next shot. Both flash at the edit point. So:
      - move source_start FORWARD onto a shot-change in (start, start+margin]  → clean open
      - move source_end   BACK   onto a shot-change in [end-margin, end)       → clean close
    Boundaries already on a cut, or with no nearby cut, are left untouched. Snaps that would shrink
    a clip below `min_keep` are skipped. Recomputes the output timeline cursor-based.
    """
    if margin <= 0:
        return plan
    clips = [dict(c) for c in plan["clips"]]
    n_start = n_end = 0
    events = []
    for clip in clips:
        s = clip["source_start"]
        e = clip["source_end"]
        new_s, new_e = s, e
        event = {
            "clip_id": clip["clip_id"],
            "source_id": clip.get("source_id"),
            "original_start": round(s, 3),
            "original_end": round(e, 3),
            "start_action": "kept",
            "end_action": "kept",
        }
        # Opening: a shot-change just AFTER source_start leaves an old-shot sliver before it.
        start_changes = [
            c
            for c in _detect_shot_changes(video, s, min(e, s + margin), threshold)
            if c > s + 1e-3
        ]
        if start_changes:
            cand = max(start_changes)  # open after the last rapid cut in the window
            if cand < e - min_keep:
                new_s = round(cand, 3)
                event["start_action"] = "moved_forward"
                event["new_start"] = new_s
            else:
                event["start_unsnapped_reason"] = "collapse"
        # Closing: a shot-change just BEFORE source_end leaves a next-shot sliver after it.
        end_changes = [
            c
            for c in _detect_shot_changes(video, max(new_s, e - margin), e, threshold)
            if c < e - 1e-3
        ]
        if end_changes:
            cand = min(end_changes)  # close before the first rapid cut in the window
            if cand > new_s + min_keep:
                new_e = round(cand, 3)
                event["end_action"] = "moved_back"
                event["new_end"] = new_e
            else:
                event["end_unsnapped_reason"] = "collapse"
        n_start += new_s != s
        n_end += new_e != e
        clip["source_start"] = new_s
        clip["source_end"] = new_e
        events.append(event)

    if n_start or n_end:
        log(
            f"避让原片切镜头: {n_start} 个起点前移、{n_end} 个终点回收 (margin={margin}s, 阈值={threshold})"
        )
    return _plan_with_snapped_clips(plan, clips, "shot_snaps", events)


def _load_silence_for_source(work_dir, source_id, source_work_dir=None):
    """Read a source's silence_periods.json (project layout when source_id is None); [] when absent."""
    path = _find_source_artifact(work_dir, "silence_periods.json", source_id, source_work_dir)
    return json.loads(path.read_text(encoding="utf-8")) if path else []


def snap_source_clips(
    plan,
    video,
    duration,
    work_dir,
    *,
    line_max_extend,
    scene_margin,
    scene_threshold,
    start_max_prepend,
    start_max_trim,
    do_line_snap=True,
    do_scene_snap=True,
    source_id=None,
    source_work_dir=None,
):
    """Snap every clip of ONE source: shot changes first, then quiet starts/ends, then the gate.

    Visual cleanup goes first. Sentence/quiet snapping is the final authority because a
    clean picture is never allowed to reintroduce a mid-sentence audio cut. source_id None
    reads the project-level understanding artifacts (single-source layout).
    """
    if do_scene_snap:
        plan = snap_clips_off_shot_changes(
            plan, video, margin=scene_margin, threshold=scene_threshold
        )
    boundaries = _combine_boundary_windows(
        _load_silence_for_source(work_dir, source_id, source_work_dir),
        _load_sentence_boundary_windows(work_dir, source_id, source_work_dir),
    )
    if do_line_snap:
        plan = snap_clip_starts_to_lines(
            plan, boundaries, duration, start_max_prepend, max_trim=start_max_trim
        )
        plan = snap_clip_ends_to_lines(plan, boundaries, duration, line_max_extend)
    return enforce_clip_sentence_boundaries(
        plan,
        boundaries,
        _load_source_speech_spans(work_dir, source_id, source_work_dir),
        duration,
    )


def snap_multi_source_clips(plan, sources, work_dir, **snap_options):
    """Per-source snap_source_clips for a multi-source validated plan.

    Each clip is snapped using ITS OWN source's silence windows / shot changes and duration
    (a clip in source B never constrains a clip in source A), then the global OUTPUT timeline
    is recomputed once in plan order. Missing silence data leaves a boundary unchanged.
    `snap_options` are snap_source_clips' keyword options.
    """
    clips = plan["clips"]
    allow_overlap = plan["allow_overlap"]
    groups = {}
    for clip in clips:
        groups.setdefault(clip["source_id"], []).append(clip)
    boundary_accum = {
        "start_snaps": [],
        "end_snaps": [],
        "shot_snaps": [],
        "sentence_checks": [],
    }
    blocking_accum = []
    for sid, group in groups.items():
        source = sources[sid]
        mini = snap_source_clips(
            {"clips": [dict(c) for c in group], "allow_overlap": allow_overlap},
            source["source_path"],
            source["duration"],
            work_dir,
            source_id=sid,
            source_work_dir=source.get("source_work_dir"),
            **snap_options,
        )
        mini_boundary = mini["qc"]["boundary_status"]
        for key, events in boundary_accum.items():
            events.extend(mini_boundary.get(key, []))
        blocking_accum.extend(mini["qc"].get("blocking", []))
        for original, snapped in zip(group, mini["clips"]):
            original["source_start"] = snapped["source_start"]
            original["source_end"] = snapped["source_end"]
    # Recompute the global output timeline cursor-based, in plan order (not group order).
    plan["total_duration"] = _recompute_clip_timeline(clips)

    qc = plan.setdefault("qc", {})
    boundary = qc.setdefault("boundary_status", {})
    for key, events in boundary_accum.items():
        if events:
            boundary.setdefault(key, []).extend(events)
    warnings = qc.setdefault("warnings", [])
    for event in boundary_accum["start_snaps"]:
        if "warning_code" in event:
            warnings.append(
                {
                    "code": event["warning_code"],
                    "clip_id": event["clip_id"],
                    "source_id": event["source_id"],
                    "start_unsnapped_reason": event["start_unsnapped_reason"],
                }
            )
    # A mid-sentence cut must never be dropped because some sibling QC list came back empty.
    if blocking_accum:
        qc.setdefault("blocking", []).extend(blocking_accum)
    return plan
