"""Load measured source-speech evidence and classify narration ownership."""

import json
import re
from pathlib import Path

from lib import CONFIG, file_identity


def _empty_evidence(mode):
    return {
        "anchors": [],
        "speech_spans": [],
        "dialogue_spans": [],
        "quiet_windows": [],
        "require_measured": mode == "cut_output",
    }


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


def _read_json(path):
    """JSON artifact, or None when the optional file was never written."""
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _source_asr_rows(work_dir):
    """Cleaned ASR segments (asr_clean.json) when present, else raw asr_result.json.

    Same precedence as video-cut's edge gate and video-assemble's entry check, so one
    transcript decides lint, cut and assemble verdicts. Blank-text rows are timing-only
    evidence (ASR heard no words) and are not source speech.
    """
    clean = _read_json(work_dir / "asr_clean.json")
    rows = clean["segments"] if clean is not None else _read_json(work_dir / "asr_result.json")
    return [row for row in rows or [] if row["text"].strip()]


def _output_payload_is_current(payload, work_dir):
    plan_path = Path(work_dir) / "clip_plan_validated.json"
    return (
        payload is not None
        and plan_path.exists()
        and payload["clip_plan_identity"] == file_identity(plan_path)
    )


def load_source_sentence_evidence(work_dir, mode="full"):
    """Load sentence boundaries plus speech/quiet spans on the narration clock."""
    if work_dir is None:
        return _empty_evidence(mode)
    work_dir = Path(work_dir)
    if mode == "full":
        payload = _read_json(work_dir / "speech_boundary_anchors.json") or {
            "sentence_anchors": []
        }
        speech_spans = _source_asr_rows(work_dir)
        quiet_windows = [
            row
            for row in _read_json(work_dir / "silence_periods.json") or []
            if not row["has_speech"]
        ]
    else:
        payload = _read_json(work_dir / "speech_boundary_anchors_output.json")
        if mode == "cut_output" and not _output_payload_is_current(payload, work_dir):
            return _empty_evidence(mode)
        if payload is None:
            payload = {"sentence_anchors": [], "speech_spans": [], "quiet_windows": []}
        speech_spans = payload["speech_spans"]
        quiet_windows = payload["quiet_windows"]
    # `boundary_use` (schema 2) keeps coarse-ASR estimates usable as `unverified`; schema-1
    # anchors (no `boundary_use`) were all coarse estimates: high/medium are `unverified`.
    anchors = [
        anchor
        for anchor in payload["sentence_anchors"]
        if (
            anchor.get("boundary_use")
            or ("unverified" if anchor["confidence"] in {"high", "medium"} else "none")
        )
        != "none"
    ]
    return {
        "anchors": sorted(anchors, key=lambda item: item["time"]),
        "speech_spans": speech_spans,
        # Entry ownership only: an interjection-only window has no sentence to interrupt.
        "dialogue_spans": _dialogue_speech_spans(speech_spans),
        "quiet_windows": quiet_windows,
        "require_measured": mode == "cut_output",
    }


def _merged_intervals(start, end, rows):
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


def _interval_overlap(start, end, rows):
    return sum(right - left for left, right in _merged_intervals(start, end, rows))


def _speech_overlap_excluding_quiet(start, end, speech, quiet):
    speech_intervals = _merged_intervals(start, end, speech)
    quiet_intervals = _merged_intervals(start, end, quiet)
    overlap = sum(right - left for left, right in speech_intervals)
    for speech_left, speech_right in speech_intervals:
        overlap -= sum(
            max(0.0, min(speech_right, quiet_right) - max(speech_left, quiet_left))
            for quiet_left, quiet_right in quiet_intervals
        )
    return max(0.0, overlap)


def segment_overlaps_source_speech(seg, evidence):
    """Classify aggregate mix ownership across the complete narration interval."""
    start, end = seg["start"], seg["end"]
    quiet = evidence["quiet_windows"]
    quiet_min = max(0.3, (end - start) * CONFIG["quiet_overlap_min_ratio"])
    speech = evidence["speech_spans"]
    if speech:
        return _speech_overlap_excluding_quiet(start, end, speech, quiet) > 0.05
    if quiet and _interval_overlap(start, end, quiet) >= quiet_min:
        return False
    if evidence["anchors"] or evidence["require_measured"]:
        return True
    return bool(seg.get("overlaps_speech", True))


def entry_overlaps_source_speech(start, evidence, *, authored_overlap=True, tolerance=0.05):
    """Classify the entry instant; later quiet time cannot erase an unsafe start."""
    if any(
        row["start"] - tolerance <= start <= row["end"] + tolerance
        for row in evidence["quiet_windows"]
    ):
        return False
    if any(
        row["start"] - tolerance <= start < row["end"] - tolerance
        for row in evidence["dialogue_spans"]
    ):
        return True
    if evidence["speech_spans"]:
        return False
    if evidence["anchors"] or evidence["require_measured"]:
        return True
    return bool(authored_overlap)


def measure_narration_speech_ownership(narration, work_dir, mode="full"):
    """Return narration copies with aggregate ownership derived from evidence."""
    evidence = load_source_sentence_evidence(work_dir, mode=mode)
    return [
        {**seg, "overlaps_speech": segment_overlaps_source_speech(seg, evidence)}
        for seg in narration
    ]
