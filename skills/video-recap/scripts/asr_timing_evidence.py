"""Write and check timing/provenance evidence for coarse MiMo ASR."""

import json
import os
import tempfile
from pathlib import Path

from lib import file_identity


EVIDENCE_FILENAME = "asr_timing_evidence.json"
SCHEMA_VERSION = 2
VALID_STATUSES = {
    "AVAILABLE_COARSE",
    "EXPLICITLY_SKIPPED",
    "UNAVAILABLE_NO_KEY",
    "UNAVAILABLE_NO_DURATION",
    "FAILED_AUDIO_EXTRACTION",
    "FAILED_PROVIDER",
    "EMPTY_UNKNOWN",
}
_PRECISION = {
    "window_timing": "COARSE_SEGMENT_WINDOWS",
    "dialogue_boundaries": "NOT_VERIFIED",
    "word_alignment": "NOT_PERFORMED",
    "empty_text_meaning": "UNKNOWN_NOT_PROVEN_SILENCE",
}


def _identity(path):
    """{size, mtime_ns} of a bound file, or None when it does not exist."""
    path = Path(path)
    return file_identity(path) if path.exists() else None


def _atomic_json_write(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _window_evidence(final_segments):
    windows = []
    for index, final in enumerate(final_segments or []):
        final_text = str(final.get("text") or "")
        windows.append({
            "index": index,
            "start": final.get("start"),
            "end": final.get("end"),
            "text_availability": "AVAILABLE" if final_text else "UNAVAILABLE_UNKNOWN",
            "text": final_text,
        })
    return windows


def write_asr_timing_evidence(
    work_dir, video_path, status, *, final_segments=None, audio_path=None,
):
    """Write a sidecar recording which source/audio/result files it describes."""
    if status not in VALID_STATUSES:
        raise ValueError(f"unknown ASR timing evidence status: {status}")
    work_dir = Path(work_dir)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "source_video": _identity(video_path),
        "audio": _identity(audio_path) if audio_path else None,
        "asr_result": _identity(work_dir / "asr_result.json"),
        "precision": dict(_PRECISION),
        "windows": _window_evidence(final_segments),
    }
    path = work_dir / EVIDENCE_FILENAME
    _atomic_json_write(path, payload)
    return path


def validate_asr_timing_evidence(evidence_path, video_path, asr_result_path):
    """Return whether a sidecar still matches the current source/audio/result file identities."""
    evidence_path = Path(evidence_path)
    result_path = Path(asr_result_path)
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    if not isinstance(evidence, dict):
        return False
    status = evidence.get("status")
    if status not in VALID_STATUSES or status.startswith("FAILED_"):
        return False
    source_identity = _identity(video_path)
    result_identity = _identity(result_path)
    if not source_identity or evidence.get("source_video") != source_identity:
        return False
    if not result_identity or evidence.get("asr_result") != result_identity:
        return False
    audio = evidence.get("audio")
    if audio is not None:
        audio_path = evidence_path.parent / "audio.wav"
        if audio != _identity(audio_path):
            return False
    return True


def asr_evidence_summary_for_brief(work_dir, video_path):
    """Return a compact validated banner; never upgrade missing/stale evidence."""
    work_dir = Path(work_dir)
    evidence_path = work_dir / EVIDENCE_FILENAME
    result_path = work_dir / "asr_result.json"
    if video_path and validate_asr_timing_evidence(
        evidence_path, video_path, result_path
    ):
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
        return {"status": payload["status"]}
    return {"status": "MISSING_OR_STALE"}
