"""Write and validate honest timing/provenance evidence for coarse MiMo ASR."""

import json
import os
import tempfile
from pathlib import Path

from lib import file_identity, load_background_research


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


def load_glossary_names(work_dir):
    """Return normalized names that can actually affect ASR correction."""
    data = load_background_research(work_dir)
    names = set()
    characters = data.get("characters")
    if isinstance(characters, dict):
        names.update(characters.keys())
    details = data.get("character_details")
    if isinstance(details, dict):
        for name, info in details.items():
            names.add(name)
            if isinstance(info, dict) and isinstance(info.get("aliases"), list):
                names.update(alias for alias in info["aliases"] if isinstance(alias, str))
    return sorted(
        {name for name in names if isinstance(name, str) and len(name) >= 2},
        key=lambda value: (-len(value), value),
    )


def _glossary_binding(work_dir):
    """The glossary names that could have corrected this transcription."""
    names = load_glossary_names(work_dir)
    return {"names": names, "name_count": len(names)}


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


def _window_evidence(observed_segments, final_segments):
    observed_segments = observed_segments or []
    windows = []
    for index, final in enumerate(final_segments or []):
        observed = observed_segments[index] if index < len(observed_segments) else None
        observed_text = None if observed is None else str(observed.get("text") or "")
        final_text = str(final.get("text") or "")
        windows.append({
            "index": index,
            "observed_text": observed_text,
            "glossary_modified": None if observed_text is None else observed_text != final_text,
        })
    return windows


def write_asr_timing_evidence(
    work_dir, video_path, status, *, observed_segments=None, final_segments=None,
    audio_path=None,
):
    """Write a sidecar recording which source/audio/result files it describes, without
    changing the result itself."""
    if status not in VALID_STATUSES:
        raise ValueError(f"unknown ASR timing evidence status: {status}")
    work_dir = Path(work_dir)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "source_video": _identity(video_path),
        "audio": _identity(audio_path) if audio_path else None,
        "asr_result": _identity(work_dir / "asr_result.json"),
        "glossary": _glossary_binding(work_dir),
        "precision": dict(_PRECISION),
        "windows": _window_evidence(observed_segments, final_segments),
    }
    path = work_dir / EVIDENCE_FILENAME
    _atomic_json_write(path, payload)
    return path


def validate_asr_timing_evidence(evidence_path, video_path, asr_result_path):
    """Return whether a sidecar still describes the current source/audio/result files
    (by size + mtime_ns) and glossary. It is not a content check: a hand-edited sidecar whose
    bindings still match stays valid."""
    evidence_path = Path(evidence_path)
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    if not isinstance(evidence, dict) or evidence.get("schema_version") != SCHEMA_VERSION:
        return False
    status = evidence.get("status")
    if not isinstance(status, str) or status not in VALID_STATUSES or status.startswith("FAILED_"):
        return False
    source_identity = _identity(video_path)
    if source_identity is None or evidence.get("source_video") != source_identity:
        return False
    result_identity = _identity(asr_result_path)
    if result_identity is None or evidence.get("asr_result") != result_identity:
        return False
    audio = evidence.get("audio")
    if audio is not None and audio != _identity(evidence_path.parent / "audio.wav"):
        return False
    if evidence.get("glossary") != _glossary_binding(evidence_path.parent):
        return False
    # asr_evidence_summary_for_brief reads these; a broken shape is a miss, not a crash.
    windows = evidence.get("windows")
    return isinstance(windows, list) and all(
        isinstance(window, dict) and "glossary_modified" in window for window in windows
    )


def asr_evidence_summary_for_brief(work_dir, video_path):
    """Return a compact validated banner; never upgrade missing/stale evidence."""
    work_dir = Path(work_dir)
    evidence_path = work_dir / EVIDENCE_FILENAME
    result_path = work_dir / "asr_result.json"
    valid = bool(video_path) and validate_asr_timing_evidence(
        evidence_path, video_path, result_path
    )
    if valid:
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
        return {"status": payload["status"],
                "glossary_modifications": sum(
                    w["glossary_modified"] is True for w in payload["windows"]
                )}
    return {"status": "MISSING_OR_STALE", "glossary_modifications": None}
