"""Source handoffs, ducking envelopes, and audio mix graphs."""

import re
from pathlib import Path

from artifacts import _load_work_json
from audio_automation import (
    coalesce_duck_windows,
    ducking_expression,
    release_ducking_expression,
)
from lib import CONFIG, log


def _seg_place_window(seg):
    """A segment's actual placed (start, end) on the output timeline; zero-width when unplaced."""
    return seg["actual_place_start"], seg["actual_place_end"]


# After narration ends, original audio may stay ducked this long waiting for a sentence-end
# anchor. Beyond it the duck releases at the narration end: coarse-ASR anchors are estimates,
# and holding a duck for tens of seconds buries dialogue the viewer must hear.
SOURCE_HANDOFF_MAX_HOLD_SECONDS = 3.0


def _load_sentence_handoff_anchors(work_dir):
    """Load usable sentence anchors (`boundary_use` verified/unverified) and their pauses."""
    work_dir = Path(work_dir)
    cut_mode = (work_dir / "edited_source.mp4").exists() or (
        work_dir / "clip_plan_validated.json"
    ).exists()
    artifact = "speech_boundary_anchors_output.json" if cut_mode else "speech_boundary_anchors.json"
    payload = _load_work_json(work_dir, artifact)
    if payload is None:
        return [], None, {"require_measured": cut_mode}
    if cut_mode:
        # Output-clock anchors are only trusted when they are at least as new as the cut plan.
        plan_path = work_dir / "clip_plan_validated.json"
        fresh = (
            payload.get("schema_version") == 2
            and payload.get("timeline") == "cut_output"
            and plan_path.exists()
            and (work_dir / artifact).stat().st_mtime_ns >= plan_path.stat().st_mtime_ns
        )
        if not fresh:
            return [], None, {"require_measured": True}
        payload = {**payload, "require_measured": True}
    anchors = {}
    for item in payload["sentence_anchors"]:
        # Schema-1 anchors (no `boundary_use`) came from the old coarse estimator: high/medium
        # labels there are usable but unverified.
        use = item.get("boundary_use") or (
            "unverified" if item["confidence"] in {"high", "medium"} else "none"
        )
        if use == "none":
            continue
        when = float(item["time"])
        pause_start = float(item.get("pause_start", when - 0.12))
        row = {
            "time": round(when, 4),
            "pause_start": round(max(0.0, min(pause_start, when)), 4),
            "verified": use == "verified",
        }
        key = (row["time"], row["pause_start"])
        row["verified"] = row["verified"] or anchors.get(key, {}).get("verified", False)
        anchors[key] = row
    return sorted(anchors.values(), key=lambda row: row["time"]), artifact, payload


def _prefer_verified(matches):
    """First verified anchor among time-ordered matches, else the first match, else None."""
    return next((anchor for anchor in matches if anchor["verified"]), None) or (
        matches[0] if matches else None
    )


def _timed_rows(rows):
    return [{"start": float(row["start"]), "end": float(row["end"])} for row in rows]


def _asr_segments(work_dir):
    """Cleaned ASR segments (asr_clean.json) when present, else raw asr_result.json; [] when absent."""
    clean = _load_work_json(work_dir, "asr_clean.json")
    if clean is not None:
        return clean["segments"]
    return _load_work_json(work_dir, "asr_result.json") or []


# Interjections and common ASR artifacts on screams/music. A window whose text is only these
# is not dialogue at a clip edge or narration entry; real short lines such as "救我！" still are.
# Same copy in video-cut, video-script and video-assemble (parity-tested by function).
_NON_DIALOGUE_TOKENS = frozenset(
    "啊 嗯 哼 哦 呃 唉 嘿 呦 哈 呀 hi yeah ok okay oh uh ah hmm".split()
)
_NON_DIALOGUE_CJK = frozenset("啊嗯哼哦呃唉嘿呦哈呀")
# Lines cross 15s ASR window edges (a line may run 13.2–15.4 while its window ends at 15.0),
# so an interjection-only window next to real dialogue keeps this much of its shared edge.
_INTERJECTION_GUARD_SECONDS = 1.0


def _interjection_only(text):
    tokens = [token for token in re.split(r"[\W_]+", text.lower()) if token]
    # Punctuation-only rows ("……", "？") are often ASR for unintelligible speech: keep them.
    return bool(tokens) and all(
        token in _NON_DIALOGUE_TOKENS or set(token) <= _NON_DIALOGUE_CJK for token in tokens
    )


def _dialogue_speech_spans(rows):
    """Merged dialogue spans from timed ASR rows.

    A row holding only interjections ("啊！", "Hi.") is not dialogue, except a
    `_INTERJECTION_GUARD_SECONDS` guard on an edge it shares with a dialogue row. A row
    whose text is empty or whitespace (ASR heard no words) is timing-only evidence: it is
    skipped here and guards nothing. A row with no `text` field is measured timing whose
    words are unknown and counts as dialogue.
    """
    rows = sorted(
        (
            {
                "start": row["start"],
                "end": row["end"],
                "dialogue": not _interjection_only(row.get("text", "")),
            }
            for row in rows
            if "text" not in row or row["text"].strip()
        ),
        key=lambda row: (row["start"], row["end"]),
    )
    spans = []
    for idx, row in enumerate(rows):
        if row["dialogue"]:
            spans.append({"start": row["start"], "end": row["end"]})
            continue
        before = rows[idx - 1] if idx > 0 else None
        after = rows[idx + 1] if idx + 1 < len(rows) else None
        if before and before["dialogue"] and row["start"] - before["end"] <= 0.05:
            end = min(row["end"], row["start"] + _INTERJECTION_GUARD_SECONDS)
            spans.append({"start": row["start"], "end": end})
        if after and after["dialogue"] and after["start"] - row["end"] <= 0.05:
            start = max(row["start"], row["end"] - _INTERJECTION_GUARD_SECONDS)
            spans.append({"start": start, "end": row["end"]})
    spans.sort(key=lambda row: (row["start"], row["end"]))
    merged = []
    for span in spans:
        if merged and span["start"] <= merged[-1]["end"] + 0.05:
            merged[-1]["end"] = max(merged[-1]["end"], span["end"])
        else:
            merged.append(span)
    return merged


def _handoff_speech_evidence(work_dir, payload):
    """(speech, quiet, dialogue) rows; `dialogue` drops interjection-only windows and
    decides only whether a narration entry interrupts source speech.

    ASR fallback rows with empty text are windows where nothing was recognized, not
    speech (video-cut and video-script drop them the same way). Output-clock
    `speech_spans` carry no `text` and stay timing-only dialogue evidence.
    """
    rows = payload.get("speech_spans", [])
    quiet = _timed_rows(payload.get("quiet_windows", []))
    if not payload.get("require_measured"):
        rows = rows or [row for row in _asr_segments(work_dir) if row["text"].strip()]
        if not quiet:
            silence = _load_work_json(work_dir, "silence_periods.json") or []
            quiet = _timed_rows(row for row in silence if not row["has_speech"])
    return _timed_rows(rows), quiet, _timed_rows(_dialogue_speech_spans(rows))


def _merged_handoff_intervals(start, end, rows):
    intervals = sorted(
        (max(start, row["start"]), min(end, row["end"]))
        for row in rows
        if row["end"] > start and row["start"] < end
    )
    merged = []
    for left, right in intervals:
        if right <= left:
            continue
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))
        else:
            merged.append((left, right))
    return merged


def _speech_overlap_excluding_quiet(start, end, speech, quiet):
    speech_intervals = _merged_handoff_intervals(start, end, speech)
    quiet_intervals = _merged_handoff_intervals(start, end, quiet)
    overlap = sum(right - left for left, right in speech_intervals)
    for speech_left, speech_right in speech_intervals:
        overlap -= sum(
            max(0.0, min(speech_right, quiet_right) - max(speech_left, quiet_left))
            for quiet_left, quiet_right in quiet_intervals
        )
    return max(0.0, overlap)


def _measured_speech_owned(
    start, end, speech, quiet, anchors, authored, require_measured=False
):
    duration = max(0.0, end - start)
    quiet_min = max(0.3, duration * CONFIG["quiet_overlap_min_ratio"])
    if speech:
        return _speech_overlap_excluding_quiet(start, end, speech, quiet) > 0.05
    quiet_overlap = sum(
        right - left for left, right in _merged_handoff_intervals(start, end, quiet)
    )
    if quiet and quiet_overlap >= quiet_min:
        return False
    return True if anchors or require_measured else bool(authored)


def _entry_speech_owned(
    start, speech, dialogue, quiet, anchors, authored, require_measured=False, tolerance=0.05
):
    if any(row["start"] - tolerance <= start <= row["end"] + tolerance for row in quiet):
        return False
    if any(row["start"] - tolerance <= start < row["end"] - tolerance for row in dialogue):
        return True
    if speech:
        return False
    return True if anchors or require_measured else bool(authored)


def _unowned_entry_status(start, speech, quiet, tolerance=0.05):
    """Entry status when a narration entry does not interrupt source dialogue.

    `non_dialogue_source` when the entry lands inside measured speech that holds only
    interjections (a scream, "Hi."); `quiet_source` for measured quiet or no speech.
    """
    if any(row["start"] - tolerance <= start <= row["end"] + tolerance for row in quiet):
        return "quiet_source"
    if any(row["start"] <= start < row["end"] for row in speech):
        return "non_dialogue_source"
    return "quiet_source"


def _dialogue_free_pull_start(candidate, written_start, dialogue, quiet, tolerance=0.05):
    """Earliest start in [candidate, written_start] whose pulled stretch holds no dialogue.

    Paragraph tightening plays a block up to `narration_max_pull_seconds` before its
    written `start`, after narration lint checked only that written entry. Measured quiet
    inside a dialogue span is not dialogue. When dialogue remains in the stretch, the
    block starts no earlier than the end of the last such piece.
    """
    safe = candidate
    quiet_intervals = _merged_handoff_intervals(candidate, written_start, quiet)
    for left, right in _merged_handoff_intervals(candidate, written_start, dialogue):
        pieces = [(left, right)]
        for quiet_left, quiet_right in quiet_intervals:
            pieces = [
                part
                for piece_left, piece_right in pieces
                for part in (
                    (piece_left, min(piece_right, quiet_left)),
                    (max(piece_left, quiet_right), piece_right),
                )
                if part[1] - part[0] > 0
            ]
        for piece_left, piece_right in pieces:
            if piece_right - piece_left > tolerance:
                safe = max(safe, piece_right)
    return safe


def _paragraph_pull_evidence(work_dir):
    """(dialogue, quiet) rows on the narration clock for `_dialogue_free_pull_start`."""
    _anchors, _artifact, payload = _load_sentence_handoff_anchors(work_dir)
    _speech, quiet, dialogue = _handoff_speech_evidence(work_dir, payload)
    return dialogue, quiet


def _work_has_source_speech(work_dir, speech_spans, require_measured):
    if speech_spans or require_measured:
        return True
    return any(item["text"].strip() for item in _asr_segments(work_dir))


def _apply_source_sentence_handoffs(tts_segments, work_dir, video_duration):
    """Keep source audio ducked until a nearby sentence boundary after narration.

    This does not move or trim narration. It only extends the ORIGINAL-audio duck
    envelope so returning the source track does not reveal the middle of a sentence —
    for at most SOURCE_HANDOFF_MAX_HOLD_SECONDS. With no anchor in that window the duck
    releases at the narration end (`bounded_release`) instead of burying source dialogue
    until a distant anchor.
    """
    fade = CONFIG["duck_fade_seconds"]
    bridge = CONFIG["duck_bridge_seconds"]
    anchors, artifact, evidence_payload = _load_sentence_handoff_anchors(work_dir)
    speech_spans, quiet_windows, dialogue_spans = _handoff_speech_evidence(
        work_dir, evidence_payload
    )
    require_measured = evidence_payload.get("require_measured", False)
    placed = []
    for seg in tts_segments:
        start, end = _seg_place_window(seg)
        if end > start:
            placed.append((start, end, seg))
    placed.sort(key=lambda item: (item[0], item[1]))
    if not placed:
        return []

    runs = []
    for start, end, seg in placed:
        if runs and start - runs[-1]["end"] <= bridge + 1e-6:
            runs[-1]["end"] = max(runs[-1]["end"], end)
            runs[-1]["segments"].append(seg)
        else:
            runs.append({"start": start, "end": end, "segments": [seg]})

    source_has_speech = _work_has_source_speech(work_dir, speech_spans, require_measured)
    report = []
    for run in runs:
        ownership = []
        for seg in run["segments"]:
            start, end = _seg_place_window(seg)
            measured = _measured_speech_owned(
                start,
                end,
                speech_spans,
                quiet_windows,
                anchors,
                seg["overlaps_speech"],
                require_measured=require_measured,
            )
            seg["overlaps_speech"] = measured
            ownership.append(measured)
        first = run["segments"][0]
        entry_owned = _entry_speech_owned(
            run["start"],
            speech_spans,
            dialogue_spans,
            quiet_windows,
            anchors,
            first["overlaps_speech"],
            require_measured=require_measured,
        )
        speech_owned = entry_owned or any(ownership)
        if not entry_owned:
            first["source_entry_status"] = _unowned_entry_status(
                run["start"], speech_spans, quiet_windows
            )
        if not speech_owned:
            report.append({"start": run["start"], "end": run["end"], "status": "quiet_source"})
            continue
        last = run["segments"][-1]
        entry_anchor = _prefer_verified([
            anchor
            for anchor in anchors
            if anchor["pause_start"] - 0.05 <= run["start"] <= anchor["time"] + 0.08
        ])
        start_safe = run["start"] <= 0.25 or entry_anchor is not None
        if entry_owned and anchors and not start_safe:
            first["source_handoff_blocking"] = True
            first["source_entry_status"] = "unsafe_entry"
        elif entry_owned and not anchors:
            first["source_entry_status"] = "unverified"
        elif entry_owned and entry_anchor is not None and not entry_anchor["verified"]:
            first["source_entry_status"] = "sentence_boundary_unverified"
        elif entry_owned:
            first["source_entry_status"] = "sentence_boundary"

        max_hold = SOURCE_HANDOFF_MAX_HOLD_SECONDS
        restore_anchor = _prefer_verified([
            anchor
            for anchor in anchors
            if run["end"] - 0.01 <= anchor["time"] <= run["end"] + max_hold
        ])
        if restore_anchor is not None:
            # Hold the source low through its last spoken sample, then fit the release
            # entirely inside the measured pause. Never begin the ramp `fade` seconds
            # before the anchor when that would expose the final source phoneme.
            duck_end = max(run["end"], restore_anchor["pause_start"])
            restore_at = max(duck_end, restore_anchor["time"])
            status = (
                "sentence_boundary"
                if restore_anchor["verified"]
                else "sentence_boundary_unverified"
            )
        elif anchors and float(video_duration) - run["end"] <= max_hold:
            # No complete source sentence before a near tail: never expose a fragment there.
            restore_at = float(video_duration)
            duck_end = float(video_duration)
            status = "held_to_timeline_end"
        elif anchors:
            # No anchor within the bound: release at the narration end, same values as
            # `no_source_speech`. Up to the ramp of a source tail is audible; a long duck
            # would instead bury dialogue that may matter more.
            restore_at = run["end"] + fade
            duck_end = run["end"]
            status = "bounded_release"
        elif source_has_speech:
            first["source_handoff_blocking"] = True
            first["source_entry_status"] = "anchors_unavailable"
            restore_at = run["end"] + fade
            duck_end = run["end"]
            status = "anchors_unavailable"
        else:
            restore_at = run["end"] + fade
            duck_end = run["end"]
            status = "no_source_speech"

        last["source_duck_end"] = round(min(float(video_duration), duck_end), 4)
        last["source_restore_at"] = round(min(float(video_duration), restore_at), 4)
        last["source_handoff_status"] = status
        report.append({
            "start": round(run["start"], 4),
            "end": round(run["end"], 4),
            "restore_at": last["source_restore_at"],
            "hold_seconds": round(max(0.0, last["source_restore_at"] - run["end"]), 4),
            "status": status,
            "anchor_artifact": artifact,
        })
    return report


def _amix_tail(narr_vol, bgm_chain=""):
    """Mix the prepared original track [orig] (+ optional BGM bed) with the boosted
    narration [narr] into [aout]. bgm_chain, when given, defines [bgm] from input [2:a]."""
    narr = f"[1:a]volume={narr_vol},aresample=48000[narr];"
    if bgm_chain:
        return bgm_chain + narr + "[orig][bgm][narr]amix=inputs=3:duration=first:dropout_transition=0:normalize=0[aout]"
    return narr + "[orig][narr]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"


def _duck_envelope(tts_segments, idle, speech_vol, quiet_vol, fade, bridge):
    """Per-beat ducking automation for the ORIGINAL track.

    Uses the shared ducking contract: [start-fade,start] pre-roll ramp down,
    [start,end] held at the selected duck level, and [end,end+fade] release.
    Bridged spans use the most-ducked (lowest) level, matching timeline.json /
    JianYing keyframes. Returns a volume= expression, or None when no beat was
    placed (caller falls back to a constant).
    """
    windows = []
    for seg in tts_segments:
        start, narration_end = _seg_place_window(seg)
        if narration_end <= start:
            continue
        hold_end = max(narration_end, seg.get("source_duck_end", narration_end))
        restore_at = max(hold_end, seg.get("source_restore_at", hold_end + fade))
        level = speech_vol if seg["overlaps_speech"] else quiet_vol
        windows.append((start, hold_end, level, restore_at))
    return release_ducking_expression(windows, idle, fade, bridge=bridge)


def _bgm_envelope(tts_segments, base, duck, fade, bridge):
    """Per-beat ducking automation for the BGM track using the shared contract."""
    windows = [
        (start, end, duck)
        for start, end in map(_seg_place_window, tts_segments)
        if end > start
    ]
    return ducking_expression(coalesce_duck_windows(windows, bridge), base, fade)


def _build_audio_filter_complex(
    tts_segments,
    has_bgm=False,
    *,
    original_audio_label="0:a",
    bgm_audio_label="2:a",
):
    """Compose the audio tracks into [aout], like a cut-software timeline.

    Tracks:
      - original (input [0:a], the video's own audio): ducked under each narration
        window by a per-beat volume envelope, but held up at `idle_orig_volume` in
        the gaps so the recap never drops to dead air between sentences.
      - bgm (input [2:a], optional): a looped music bed, gently ducked under narration.
      - narration (input [1:a]): the TTS, boosted and laid on top.
    Placement comes from actual_place_start/end.
    """
    narr_vol = CONFIG["ducking_narr_weight"]
    fade = CONFIG["duck_fade_seconds"]
    bridge = CONFIG["duck_bridge_seconds"]
    original_in = f"[{original_audio_label}]"
    bgm_in = f"[{bgm_audio_label}]"

    # BGM bed (input [2:a]): ducked under each narration window when present.
    bgm_chain = ""
    if has_bgm:
        base = CONFIG["bgm_volume"]
        bgm_expr = _bgm_envelope(tts_segments, base, CONFIG["bgm_ducking_volume"], fade, bridge)
        if bgm_expr:
            bgm_chain = f"{bgm_in}volume='{bgm_expr}':eval=frame,aresample=48000[bgm];"
        else:
            bgm_chain = f"{bgm_in}volume={base},aresample=48000[bgm];"

    # Gap-fill ducking envelope on the original track.
    idle = CONFIG["idle_orig_volume"]
    speech_vol = CONFIG["speech_ducking_volume"]
    quiet_vol = CONFIG["zone_ducking_volume"]
    expr = _duck_envelope(tts_segments, idle, speech_vol, quiet_vol, fade, bridge)
    if expr:
        n_overlap = sum(1 for s in tts_segments if s["overlaps_speech"])
        n_quiet = len(tts_segments) - n_overlap
        log(f"gap-fill ducking: 间隙原声={idle}, 对白段={speech_vol}({n_overlap}), 安静段={quiet_vol}({n_quiet}), 桥接间隙<{bridge}s")
        orig = f"{original_in}volume='{expr}':eval=frame,aresample=48000[orig];"
    else:
        # No placement info at all: hold the original at a constant level.
        orig = f"{original_in}volume={CONFIG['ducking_orig_volume']},aresample=48000[orig];"
    return orig + _amix_tail(narr_vol, bgm_chain)
