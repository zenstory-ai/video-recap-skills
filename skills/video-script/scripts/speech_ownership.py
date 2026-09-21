"""Load measured source-speech evidence and classify narration ownership."""

import json
from pathlib import Path

from lib import CONFIG, file_identity


def _empty_evidence(mode):
    return {
        "anchors": [],
        "speech_spans": [],
        "quiet_windows": [],
        "require_measured": mode == "cut_output",
    }


def _read_json(path):
    """JSON artifact, or None when the optional file was never written."""
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


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
        speech_spans = [
            row for row in _read_json(work_dir / "asr_result.json") or [] if row["text"]
        ]
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
    anchors = [
        anchor
        for anchor in payload["sentence_anchors"]
        if anchor["confidence"] in {"high", "medium"}
    ]
    return {
        "anchors": sorted(anchors, key=lambda item: item["time"]),
        "speech_spans": speech_spans,
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
        for row in evidence["speech_spans"]
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
