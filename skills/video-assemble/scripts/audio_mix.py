"""Loudness, source handoffs, ducking envelopes, and audio mix graphs."""

import json
import re
from pathlib import Path

from artifacts import _load_work_json, _value_fingerprint
from audio_automation import (
    coalesce_duck_windows,
    ducking_expression,
    release_ducking_expression,
)
from lib import CONFIG, log, run_cmd

def _limiter_filter():
    return f"alimiter=limit={CONFIG['final_limiter_peak']:.2f}:level=false"


def _loudness_mode(measured=None):
    if not CONFIG["final_loudnorm"]:
        return "limiter_only"
    return "two_pass_linear" if measured else "equivalent"


def final_loudnorm_filter(measured=None):
    """Final-mix loudness normalization/limiter filter from CONFIG.

    Ducking branches set only relative balance; this single stage owns the
    absolute output loudness so the recap is not left too quiet. When `measured`
    is supplied from a first loudnorm pass, ffmpeg runs the deterministic second
    pass; without it we still force the same target and peak limiter as a
    documented equivalent/fallback path.
    """
    if not CONFIG["final_loudnorm"]:
        return _limiter_filter()
    filt = (
        f"loudnorm=I={CONFIG['target_lufs']}"
        f":TP={CONFIG['target_true_peak']}"
        f":LRA={CONFIG['target_lra']}"
        f":linear=true"
    )
    if measured:
        for src, dst in (
            ("input_i", "measured_I"),
            ("input_tp", "measured_TP"),
            ("input_lra", "measured_LRA"),
            ("input_thresh", "measured_thresh"),
            ("target_offset", "offset"),
        ):
            if src in measured:
                filt += f":{dst}={measured[src]}"
    filt += ":print_format=summary"
    return f"{filt},{_limiter_filter()}"


def _parse_loudnorm_json(text):
    """Extract ffmpeg loudnorm JSON from stderr/stdout."""
    for match in reversed(list(re.finditer(r"\{[\s\S]*?\}", text))):
        try:
            data = json.loads(match.group(0))
        except ValueError:
            continue
        if {"input_i", "input_tp", "input_lra", "input_thresh", "target_offset"} <= set(data):
            return data
    return None


def _loudnorm_first_pass_filter():
    return (
        f"loudnorm=I={CONFIG['target_lufs']}"
        f":TP={CONFIG['target_true_peak']}"
        f":LRA={CONFIG['target_lra']}"
        f":print_format=json"
    )


def _run_loudnorm_first_pass(input_video, narration_wav, original_audio_input,
                             bgm_input, filter_complex, work_dir):
    """Measure the exact mixed audio graph before final render.

    Returns ffmpeg loudnorm JSON, or None when probing fails. The caller then
    falls back to the documented equivalent single-pass target+limiter filter.
    """
    if not CONFIG["final_loudnorm"]:
        return None
    probe_fc = f"{filter_complex};[aout]{_loudnorm_first_pass_filter()}[lnprobe]"
    probe_script = Path(work_dir) / ".filter_complex_loudnorm_probe.txt"
    probe_script.write_text(probe_fc, encoding="utf-8")
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_video),
        "-i", str(narration_wav),
        *original_audio_input,
        *bgm_input,
        "-filter_complex_script", str(probe_script),
        "-map", "[lnprobe]",
        "-f", "null", "-",
    ]
    try:
        result = run_cmd(cmd)
    finally:
        probe_script.unlink(missing_ok=True)
    if result.returncode != 0:
        log(f"  ⚠️ loudnorm 首遍测量失败，降级到目标滤镜+limiter: {result.stderr}")
        return None
    measured = _parse_loudnorm_json(result.stdout + "\n" + result.stderr)
    if not measured:
        log("  ⚠️ loudnorm 首遍未返回 JSON，降级到目标滤镜+limiter")
        return None
    return measured


def _seg_place_window(seg):
    """A segment's actual placed (start, end) on the output timeline; zero-width when unplaced."""
    return seg["actual_place_start"], seg["actual_place_end"]


def _load_sentence_handoff_anchors(work_dir):
    """Load high/medium sentence anchors and their measured pause windows."""
    work_dir = Path(work_dir)
    cut_mode = (work_dir / "edited_source.mp4").exists() or (
        work_dir / "clip_plan_validated.json"
    ).exists()
    artifact = "speech_boundary_anchors_output.json" if cut_mode else "speech_boundary_anchors.json"
    payload = _load_work_json(work_dir, artifact)
    if payload is None:
        return [], None, {"require_measured": cut_mode}
    if cut_mode:
        # Output-clock anchors are only trusted when they were derived from the current cut plan.
        plan = _load_work_json(work_dir, "clip_plan_validated.json")
        fresh = (
            payload.get("schema_version") == 2
            and payload.get("timeline") == "cut_output"
            and plan is not None
            and payload.get("clip_plan_fingerprint") == _value_fingerprint(plan)
        )
        if not fresh:
            return [], None, {"require_measured": True}
        payload = {**payload, "require_measured": True}
    anchors = {}
    for item in payload["sentence_anchors"]:
        if item["confidence"] not in {"high", "medium"}:
            continue
        when = float(item["time"])
        pause_start = float(item.get("pause_start", when - 0.12))
        row = {
            "time": round(when, 4),
            "pause_start": round(max(0.0, min(pause_start, when)), 4),
        }
        anchors[(row["time"], row["pause_start"])] = row
    return sorted(anchors.values(), key=lambda row: row["time"]), artifact, payload


def _timed_rows(rows):
    return [{"start": float(row["start"]), "end": float(row["end"])} for row in rows]


def _asr_segments(work_dir):
    """Cleaned ASR segments (asr_clean.json) when present, else raw asr_result.json; [] when absent."""
    clean = _load_work_json(work_dir, "asr_clean.json")
    if clean is not None:
        return clean["segments"]
    return _load_work_json(work_dir, "asr_result.json") or []


def _handoff_speech_evidence(work_dir, payload):
    speech = _timed_rows(payload.get("speech_spans", []))
    quiet = _timed_rows(payload.get("quiet_windows", []))
    if payload.get("require_measured"):
        return speech, quiet
    if not speech:
        speech = _timed_rows(_asr_segments(work_dir))
    if not quiet:
        silence = _load_work_json(work_dir, "silence_periods.json") or []
        quiet = _timed_rows(row for row in silence if not row["has_speech"])
    return speech, quiet


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
    start, speech, quiet, anchors, authored, require_measured=False, tolerance=0.05
):
    if any(row["start"] - tolerance <= start <= row["end"] + tolerance for row in quiet):
        return False
    if any(row["start"] - tolerance <= start < row["end"] - tolerance for row in speech):
        return True
    if speech:
        return False
    return True if anchors or require_measured else bool(authored)


def _work_has_source_speech(work_dir, speech_spans, require_measured):
    if speech_spans or require_measured:
        return True
    return any(item["text"].strip() for item in _asr_segments(work_dir))


def _apply_source_sentence_handoffs(tts_segments, work_dir, video_duration):
    """Keep source audio ducked until a safe sentence boundary after narration.

    This does not move or trim narration. It only extends the ORIGINAL-audio duck
    envelope so returning the source track cannot reveal the middle of a sentence.
    """
    fade = CONFIG["duck_fade_seconds"]
    bridge = CONFIG["duck_bridge_seconds"]
    anchors, artifact, evidence_payload = _load_sentence_handoff_anchors(work_dir)
    speech_spans, quiet_windows = _handoff_speech_evidence(work_dir, evidence_payload)
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
            quiet_windows,
            anchors,
            first["overlaps_speech"],
            require_measured=require_measured,
        )
        speech_owned = entry_owned or any(ownership)
        if not speech_owned:
            report.append({"start": run["start"], "end": run["end"], "status": "quiet_source"})
            continue
        last = run["segments"][-1]
        start_safe = run["start"] <= 0.25 or any(
            anchor["pause_start"] - 0.05 <= run["start"] <= anchor["time"] + 0.08
            for anchor in anchors
        )
        if entry_owned and anchors and not start_safe:
            first["source_handoff_blocking"] = True
            first["source_entry_status"] = "unsafe_entry"
        elif not entry_owned:
            first["source_entry_status"] = "quiet_source"
        else:
            first["source_entry_status"] = "sentence_boundary" if anchors else "unverified"

        restore_anchor = next(
            (anchor for anchor in anchors if anchor["time"] >= run["end"] - 0.01),
            None,
        )
        if restore_anchor is not None:
            # Hold the source low through its last spoken sample, then fit the release
            # entirely inside the measured pause. Never begin the ramp `fade` seconds
            # before the anchor when that would expose the final source phoneme.
            duck_end = max(run["end"], restore_anchor["pause_start"])
            restore_at = max(duck_end, restore_anchor["time"])
            status = "sentence_boundary"
        elif anchors:
            # No later complete source sentence: never expose a fragment at the tail.
            restore_at = float(video_duration)
            duck_end = float(video_duration)
            status = "held_to_timeline_end"
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
    CONFIG["ducking_mode"] (default "fixed") selects the original-track strategy:
    fixed = the gap-fill envelope above; sidechaincompress = auto-duck keyed off the
    narration; none = no ducking. Placement comes from actual_place_start/end.
    """
    ducking_mode = CONFIG["ducking_mode"]
    if ducking_mode == "sidechaincompress" and any(
        "source_duck_end" in seg and seg["source_duck_end"] > seg["actual_place_end"] + 1e-6
        for seg in tts_segments
    ):
        log("sidechaincompress 无法保持句末交接窗口，已回退 fixed ducking")
        ducking_mode = "fixed"
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

    if ducking_mode == "sidechaincompress":
        # The narration keys the compressor; split it so it can also be mixed in.
        head = (
            f"{original_in}aresample=48000[o0];"
            "[1:a]aresample=48000,asplit=2[sckey][scnarr];"
            f"[o0][sckey]sidechaincompress="
            f"threshold={CONFIG['ducking_threshold']}:ratio={CONFIG['ducking_ratio']}"
            f":attack={CONFIG['ducking_attack']}:release={CONFIG['ducking_release']}"
            f":knee=2.5:makeup={CONFIG['ducking_makeup']}:level_sc={CONFIG['ducking_level_sc']}[orig];"
        )
        narr = f"[scnarr]volume={narr_vol}[narr];"
        if bgm_chain:
            return head + bgm_chain + narr + "[orig][bgm][narr]amix=inputs=3:duration=first:dropout_transition=0:normalize=0[aout]"
        return head + narr + "[orig][narr]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"

    if ducking_mode == "none":
        return f"{original_in}aresample=48000[orig];" + _amix_tail(narr_vol, bgm_chain)

    # fixed (default): gap-fill ducking envelope on the original track.
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
