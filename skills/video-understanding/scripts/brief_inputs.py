"""Validate optional ASR, MiMo, and stage-status inputs for the brief."""

import json
from pathlib import Path

from lib import CONFIG, file_identity
from brief_context import _consolidation_model

# Shared with consolidate.py, which this byte-identical copy cannot import (the sibling
# skill ships no consolidate.py). Keep both literals in sync.
_ASR_SPAN_TOL = 0.05

_MIMO_REJECTION_MARKERS = (
    "request was rejected",
    "considered high risk",
    "high risk",
    "content policy",
    "cannot process",
    "无法处理",
    "内容审核",
    "违规",
)


def _load_clean_asr(work_dir, asr_result):
    """Return consolidate.py's cleaned ASR segments, or None to fall back to raw asr_result.

    Accepted only when the file is at least as fresh as asr_result.json, its provenance
    (source identity / model) matches, and every segment keeps its original span
    within _ASR_SPAN_TOL."""
    if not asr_result:
        return None
    work_dir = Path(work_dir)
    clean_path = work_dir / "asr_clean.json"
    src_path = work_dir / "asr_result.json"
    try:
        fresh = (
            clean_path.exists()
            and clean_path.stat().st_mtime >= src_path.stat().st_mtime
        )
    except OSError:
        return None
    if not fresh:
        return None
    try:
        payload = json.loads(clean_path.read_text(encoding="utf-8"))
        source = file_identity(src_path)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    provenance = {"source": source, "model": _consolidation_model()}
    if not payload.items() >= provenance.items():
        return None
    segments = payload.get("segments")
    if not isinstance(segments, list) or len(segments) != len(asr_result):
        return None
    for orig, clean in zip(asr_result, segments):
        if (
            not isinstance(clean, dict)
            or not isinstance(clean.get("start"), (int, float))
            or not isinstance(clean.get("end"), (int, float))
        ):
            return None
        if (
            abs(clean["start"] - orig["start"]) > _ASR_SPAN_TOL
            or abs(clean["end"] - orig["end"]) > _ASR_SPAN_TOL
        ):
            return None
    return segments


def _is_mimo_chunk_usable(content):
    """A chunk is usable only if MiMo returned real analysis (not empty / a moderation refusal)."""
    text = (content or "").strip()
    if not text:
        return False
    low = text.lower()
    return not any(marker in low for marker in _MIMO_REJECTION_MARKERS)


def _mimo_video_settings():
    """Non-secret MiMo video-overview settings that affect generated content."""
    return {
        "model": CONFIG["mimo_video_model"],
        "mimo_video_api_url": CONFIG["mimo_video_api_url"],
        "mimo_video_fps": CONFIG["mimo_video_fps"],
        "mimo_media_resolution": CONFIG["mimo_media_resolution"],
        "mimo_video_chunk_max_seconds": CONFIG["mimo_video_chunk_max_seconds"],
        "mimo_video_chunk_min_seconds": CONFIG["mimo_video_chunk_min_seconds"],
        "mimo_video_base64_max_mb": CONFIG["mimo_video_base64_max_mb"],
        "mimo_video_prompt": CONFIG["mimo_video_prompt"],
        "mimo_disable_thinking": CONFIG["mimo_disable_thinking"],
    }


def _mimo_chunk_cache_key(chunk):
    """Stable identifier for a MiMo chunk (index + scene span) for partial-cache reuse."""
    return f"{chunk['chunk_id']}|{chunk['scene_id']}|{chunk['start']:.3f}-{chunk['end']:.3f}"


def _mimo_video_chunks(scenes):
    """Split scene spans into MiMo video chunks. scenes.json rows carry no scene_id, so the
    row index stands in for it there."""
    max_seconds = CONFIG["mimo_video_chunk_max_seconds"]
    min_seconds = CONFIG["mimo_video_chunk_min_seconds"]
    chunks = []
    for scene_index, scene in enumerate(scenes):
        start, end = scene["start"], scene["end"]
        scene_id = scene.get("scene_id", scene_index)
        cursor = start
        while cursor < end:
            chunk_end = min(end, cursor + max_seconds)
            if end - chunk_end < min_seconds and chunk_end < end:
                chunk_end = end
            chunks.append(
                {
                    "chunk_id": len(chunks),
                    "scene_id": scene_id,
                    "start": round(cursor, 3),
                    "end": round(chunk_end, 3),
                }
            )
            cursor = chunk_end
    return chunks


def _mimo_overview_matches_current_inputs(overview, scenes, video_path=None):
    """True only when mimo_video_overview.json was produced from the current settings,
    source video and scene plan, and none of its chunks was moderation-rejected.
    Provenance keys are read with .get: a stale or partial file simply does not match."""
    if not isinstance(overview, dict) or overview.get("input") != "scene_chunks":
        return False
    settings = _mimo_video_settings()
    expected_keys = [
        _mimo_chunk_cache_key(chunk) for chunk in _mimo_video_chunks(scenes)
    ]
    chunks = overview.get("chunks")
    if not isinstance(chunks, list) or not all(
        isinstance(chunk, dict)
        and isinstance(chunk.get("content"), str)
        and _is_mimo_chunk_usable(chunk["content"])
        for chunk in chunks
    ):
        return False
    if video_path is not None:
        try:
            if overview.get("source_video_identity") != file_identity(video_path):
                return False
        except OSError:
            return False
    try:
        cached_keys = [_mimo_chunk_cache_key(chunk) for chunk in chunks]
    except (KeyError, TypeError, ValueError):
        return False
    return overview.get("settings") == settings and cached_keys == expected_keys


def _load_mimo_overview_for_brief(work_dir, scenes, enabled=None, video_path=None):
    """The current MiMo overview for the brief, or None when disabled, absent or stale."""
    if not (CONFIG["mimo_video_overview"] if enabled is None else enabled):
        return None
    path = Path(work_dir) / "mimo_video_overview.json"
    if not path.exists():
        return None
    try:
        overview = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not _mimo_overview_matches_current_inputs(
        overview, scenes, video_path=video_path
    ):
        return None
    return overview


def _load_optional_stage_status(work_dir, filename):
    """Optional-stage status sidecar, or None when the stage never ran."""
    path = Path(work_dir) / filename
    if not path.exists():
        return None
    try:
        status = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not (
        isinstance(status, dict)
        and isinstance(status.get("enabled"), bool)
        and isinstance(status.get("status"), str)
        and isinstance(status.get("message"), str)
    ):
        return None
    return status


def _optional_stage_warning(stage, status, message):
    line = f"- {stage}: {status}"
    return f"{line} — {message[:180]}" if message else line


def _format_optional_stage_warnings(
    work_dir,
    *,
    mimo_overview_enabled=None,
    mimo_overview=None,
    consolidation_index=None,
):
    """Surface fail-open optional-stage loss near the top of the brief."""
    work_dir = Path(work_dir)
    warnings = []

    overview_enabled = (
        CONFIG["mimo_video_overview"]
        if mimo_overview_enabled is None
        else mimo_overview_enabled
    )
    overview_status = _load_optional_stage_status(
        work_dir, "mimo_video_overview.status.json"
    )
    if (
        overview_status is not None
        and overview_status["enabled"]
        and overview_status["status"] in {"failed", "skipped_no_key"}
    ):
        warnings.append(
            _optional_stage_warning(
                "mimo_video_overview",
                overview_status["status"],
                overview_status["message"],
            )
        )
    elif overview_enabled and not mimo_overview:
        warnings.append(
            _optional_stage_warning(
                "mimo_video_overview",
                "missing_artifact",
                "enabled but no valid mimo_video_overview.json is available to this brief",
            )
        )

    consolidation_status = _load_optional_stage_status(
        work_dir, "consolidation.status.json"
    )
    if consolidation_status is not None and consolidation_status["enabled"]:
        if consolidation_status["status"] == "failed":
            warnings.append(
                _optional_stage_warning(
                    "consolidation", "failed", consolidation_status["message"]
                )
            )
        elif consolidation_status.get("do_index") is True and not consolidation_index:
            warnings.append(
                _optional_stage_warning(
                    "consolidation",
                    "missing_index",
                    "enabled but no valid understanding_index.json is available to this brief",
                )
            )

    if not warnings:
        return []
    return [
        "## Optional stage warnings",
        "",
        "These stages are fail-open; continue, but do not assume their missing context exists.",
        *warnings,
        "",
    ]
