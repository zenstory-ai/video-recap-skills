"""Write and validate honest timing/provenance evidence for coarse MiMo ASR."""

import json
import math
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
    "LEGACY_UNVERIFIED",
}
_TOP_KEYS = {
    "schema_version", "status", "source_video", "audio", "asr_result",
    "glossary", "precision", "windows",
}
_WINDOW_KEYS = {
    "index", "start", "end", "text_availability", "observed_text",
    "post_glossary_text", "glossary_modified",
}
_GLOSSARY_KEYS = {"names", "name_count"}
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


def _glossary_binding(work_dir, *, legacy=False):
    """The glossary names that could have corrected this transcription (legacy: unknown)."""
    if legacy:
        return {"names": None, "name_count": None}
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


def _window_evidence(observed_segments, final_segments, legacy):
    observed_segments = observed_segments or []
    windows = []
    for index, final in enumerate(final_segments or []):
        observed = observed_segments[index] if index < len(observed_segments) else None
        observed_text = None if legacy or observed is None else str(observed.get("text") or "")
        final_text = str(final.get("text") or "")
        windows.append({
            "index": index,
            "start": final.get("start"),
            "end": final.get("end"),
            "text_availability": "AVAILABLE" if final_text else "UNAVAILABLE_UNKNOWN",
            "observed_text": observed_text,
            "post_glossary_text": final_text,
            "glossary_modified": None if observed_text is None else observed_text != final_text,
        })
    return windows


def write_asr_timing_evidence(
    work_dir, video_path, status, *, observed_segments=None, final_segments=None,
    audio_path=None,
):
    """Write a sidecar recording which source/audio/result files it describes, without
    changing the legacy result."""
    if status not in VALID_STATUSES:
        raise ValueError(f"unknown ASR timing evidence status: {status}")
    work_dir = Path(work_dir)
    legacy = status == "LEGACY_UNVERIFIED"
    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "source_video": _identity(video_path),
        "audio": _identity(audio_path) if audio_path else None,
        "asr_result": _identity(work_dir / "asr_result.json"),
        "glossary": _glossary_binding(work_dir, legacy=legacy),
        "precision": dict(_PRECISION),
        "windows": _window_evidence(observed_segments, final_segments, legacy),
    }
    path = work_dir / EVIDENCE_FILENAME
    _atomic_json_write(path, payload)
    return path


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _is_identity(value):
    return (
        isinstance(value, dict) and set(value) == {"size", "mtime_ns"}
        and _is_int(value["size"]) and _is_int(value["mtime_ns"])
    )


def _valid_glossary(payload, work_dir, legacy):
    if not isinstance(payload, dict) or set(payload) != _GLOSSARY_KEYS:
        return False
    if legacy:
        return payload == _glossary_binding(work_dir, legacy=True)
    return payload == _glossary_binding(work_dir) and _is_int(payload.get("name_count"))


def _valid_status_relationships(status, result, audio):
    texts = [str(segment.get("text") or "") for segment in result]
    if status == "AVAILABLE_COARSE":
        return bool(result) and any(texts) and _is_identity(audio)
    if status == "EMPTY_UNKNOWN":
        return bool(result) and not any(texts) and _is_identity(audio)
    if status == "UNAVAILABLE_NO_DURATION":
        return result == [] and _is_identity(audio)
    if status in {"EXPLICITLY_SKIPPED", "UNAVAILABLE_NO_KEY", "LEGACY_UNVERIFIED"}:
        return audio is None and (status == "LEGACY_UNVERIFIED" or result == [])
    return False


def validate_asr_timing_evidence(evidence_path, video_path, asr_result_path):
    """Return whether a sidecar still describes the current source/audio/result files
    (by size + mtime_ns) and the limited timing it claims."""
    evidence_path = Path(evidence_path)
    result_path = Path(asr_result_path)
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    if not isinstance(evidence, dict) or set(evidence) != _TOP_KEYS:
        return False
    if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
        return False
    if not _is_int(evidence.get("schema_version")) or evidence["schema_version"] != SCHEMA_VERSION:
        return False
    status = evidence.get("status")
    if status not in VALID_STATUSES or status.startswith("FAILED_"):
        return False
    if evidence.get("precision") != _PRECISION:
        return False
    source_identity = _identity(video_path)
    result_identity = _identity(result_path)
    if not _is_identity(source_identity) or evidence.get("source_video") != source_identity:
        return False
    if not _is_identity(result_identity) or evidence.get("asr_result") != result_identity:
        return False
    audio = evidence.get("audio")
    if audio is not None and not _is_identity(audio):
        return False
    if not _valid_status_relationships(status, result, audio):
        return False
    legacy = status == "LEGACY_UNVERIFIED"
    if not _valid_glossary(evidence.get("glossary"), evidence_path.parent, legacy):
        return False

    if audio is not None:
        audio_path = evidence_path.parent / "audio.wav"
        if audio != _identity(audio_path):
            return False
        try:
            audio_meta = json.loads(
                (evidence_path.parent / "audio.wav.meta.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError, TypeError):
            return False
        if not isinstance(audio_meta, dict):
            return False
        if audio_meta.get("source_video_identity") != source_identity:
            return False

    windows = evidence.get("windows")
    if not isinstance(windows, list) or len(windows) != len(result):
        return False
    any_modified = False
    for index, (window, segment) in enumerate(zip(windows, result)):
        if not isinstance(window, dict) or set(window) != _WINDOW_KEYS:
            return False
        start, end = segment.get("start"), segment.get("end")
        text = str(segment.get("text") or "")
        observed = window.get("observed_text")
        modified = window.get("glossary_modified")
        if not _is_number(start) or not _is_number(end) or end <= start:
            return False
        if (
            not _is_int(window.get("index")) or window["index"] != index
            or not _is_number(window.get("start")) or not _is_number(window.get("end"))
            or window["start"] != start or window["end"] != end
            or not isinstance(window.get("post_glossary_text"), str)
            or window["post_glossary_text"] != text
            or window.get("text_availability") != ("AVAILABLE" if text else "UNAVAILABLE_UNKNOWN")
        ):
            return False
        if legacy:
            if observed is not None or modified is not None:
                return False
        elif (
            not isinstance(observed, str) or not isinstance(modified, bool)
            or modified != (observed != text)
        ):
            return False
        any_modified = any_modified or modified is True
    if any_modified and evidence["glossary"]["name_count"] == 0:
        return False
    return True


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
                "glossary_modifications": None if payload["status"] == "LEGACY_UNVERIFIED"
                else sum(w["glossary_modified"] is True for w in payload["windows"])}
    return {"status": "MISSING_OR_STALE", "glossary_modifications": None}
