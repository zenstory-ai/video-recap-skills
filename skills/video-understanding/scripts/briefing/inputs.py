"""Read optional stage-status sidecars for the brief."""

import json
from pathlib import Path


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


def _format_optional_stage_warnings(
    work_dir,
    *,
    consolidation_index=None,
):
    """Surface fail-open consolidation loss near the top of the brief."""
    status = _load_optional_stage_status(work_dir, "consolidation.status.json")
    if status is None or not status["enabled"]:
        return []
    if status["status"] in {"failed", "skipped_no_key"}:
        state, message = status["status"], status["message"]
    elif not consolidation_index:
        state = "missing_index"
        message = "enabled but no valid understanding_index.json is available to this brief"
    else:
        return []
    warning = f"- consolidation: {state}"
    return [
        "## Optional stage warnings",
        "",
        "These stages are fail-open; continue, but do not assume their missing context exists.",
        f"{warning} — {message[:180]}" if message else warning,
        "",
    ]
