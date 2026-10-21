"""Validate optional ASR, MiMo, and stage-status inputs for the brief."""

import json
from pathlib import Path

from lib import CONFIG, file_identity
from briefing.context import _consolidation_model
from vlm import mimo_video_overview_cache_fresh

# Consolidate preserves spans exactly, so this tolerance only guards hand-edited asr_clean.json.
_ASR_SPAN_TOL = 0.05


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


def _load_mimo_overview_for_brief(work_dir, scenes, enabled=None, video_path=None):
    """The current MiMo overview for the brief, or None when disabled, absent or stale."""
    if not (CONFIG["mimo_video_overview"] if enabled is None else enabled):
        return None
    path = Path(work_dir) / "mimo_video_overview.json"
    if not mimo_video_overview_cache_fresh(path, video_path, scenes):
        return None
    return json.loads(path.read_text(encoding="utf-8"))


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
