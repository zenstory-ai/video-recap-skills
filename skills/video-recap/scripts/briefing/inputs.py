"""Validate optional MiMo and stage-status inputs for the brief."""

import json
from pathlib import Path

from lib import (
    CONFIG,
    file_identity,
    is_usable_overview_chunk,
    offline_ignored_settings,
    settings_match,
)
from vlm import (
    _mimo_chunk_cache_key,
    _mimo_video_chunks,
    mimo_video_settings as _mimo_video_settings,
)


def _mimo_overview_matches_current_inputs(overview, scenes, video_path=None):
    """True only when mimo_video_overview.json was produced from the current settings,
    source video and scene plan, and none of its chunks was moderation-rejected.
    Provenance keys are read with .get: a stale or partial file simply does not match."""
    if not isinstance(overview, dict) or overview.get("input") != "scene_chunks":
        return False
    settings = _mimo_video_settings()
    expected_keys = (
        [_mimo_chunk_cache_key(chunk) for chunk in _mimo_video_chunks(scenes)]
        if scenes
        else []
    )
    chunks = overview.get("chunks")
    if not isinstance(chunks, list) or not all(
        isinstance(chunk, dict)
        and isinstance(chunk.get("content"), str)
        and is_usable_overview_chunk(chunk["content"])
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
    ignored = offline_ignored_settings(CONFIG["mimo_video_api_key"], "mimo_video_api_url")
    return settings_match(overview.get("settings"), settings, ignored) and cached_keys == expected_keys


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

    if not warnings:
        return []
    return [
        "## Optional stage warnings",
        "",
        "These stages are fail-open; continue, but do not assume their missing context exists.",
        *warnings,
        "",
    ]
