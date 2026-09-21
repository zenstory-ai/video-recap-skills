"""Collect and redact local evidence for MiMo QC."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from collections.abc import Mapping, Sequence

import qc_contract
from qc.mimo_client import DEFAULT_CONFIG

_JSON_ARTIFACTS = (
    "narration.json",
    "visual_overlays.json",
    "clip_plan_validated.json",
    "clip_plan.json",
    "assembly_manifest.json",
    "tts_meta.json",
)

_SOURCE_ASR_ARTIFACTS = (
    "asr_clean.json",
    "asr.json",
    "asr_result.json",
    "asr_segments.json",
)

_GENERATED_SUBTITLE_ARTIFACTS = (
    "subtitles.json",
    "subtitle.json",
    "subtitles.srt",
    "subtitle.srt",
    "subtitles.vtt",
    "subtitle.vtt",
    "output.srt",
    "output.vtt",
)

_OPTIONAL_VISUAL_METADATA = (
    "sampled_frames.json",
    "frame_samples.json",
    "storyboard.json",
    "storyboard_meta.json",
    "frames_manifest.json",
)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_text_sample(path: Path, *, max_chars: int = 4000) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        "kind": "text",
        "bytes": path.stat().st_size,
        "truncated": len(text) > max_chars,
        "sample": text[:max_chars],
    }


def _summarize(
    value: Any, *, max_items: int = 8, max_string: int = 700, depth: int = 0
) -> Any:
    """Keep request/report evidence bounded while retaining useful structure."""
    if depth >= 4:
        return {"type": type(value).__name__, "omitted": True}
    if isinstance(value, Mapping):
        out = {
            str(key): _summarize(
                item, max_items=max_items, max_string=max_string, depth=depth + 1
            )
            for key, item in list(value.items())[:max_items]
        }
        if len(value) > max_items:
            out["_omitted_keys"] = len(value) - max_items
        return out
    if isinstance(value, list):
        return {
            "count": len(value),
            "items": [
                _summarize(
                    item, max_items=max_items, max_string=max_string, depth=depth + 1
                )
                for item in value[:max_items]
            ],
            "omitted": max(0, len(value) - max_items),
        }
    if isinstance(value, str) and len(value) > max_string:
        return {"text": value[:max_string], "truncated": True, "chars": len(value)}
    return value


def _collect_file(work_dir: Path, name: str) -> dict[str, Any] | None:
    path = work_dir / name
    if not path.is_file():
        return None
    if path.suffix.lower() == ".json":
        kind, summary = "json", _summarize(_load_json(path))
    else:
        kind, summary = "text", _summarize(_read_text_sample(path))
    stat = path.stat()
    return {
        "path": name,
        "kind": kind,
        "bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "summary": summary,
    }


def _first_existing(work_dir: Path, names: Sequence[str]) -> str | None:
    return next((name for name in names if (work_dir / name).is_file()), None)


def _collect_multi_source_asr(work_dir: Path) -> dict[str, Any]:
    """Collect per-source ASR when a multi-source project has no root transcript."""
    manifest_path = work_dir / "multi_source_manifest.json"
    if not manifest_path.is_file():
        return {}
    collected = {}
    for source in _load_json(manifest_path)["sources"]:
        relative_dir = source["source_work_dir"]  # recap writes sources/<source_id>
        name = _first_existing(work_dir / relative_dir, _SOURCE_ASR_ARTIFACTS)
        if name is not None:
            collected[source["source_id"]] = _collect_file(work_dir, f"{relative_dir}/{name}")
    return collected


def _resolve_candidate(work_dir: Path, candidate: str | Path) -> Path:
    path = Path(candidate)
    return path if path.is_absolute() else work_dir / path


def _final_output_path(
    work_dir: Path, final_output: str | Path | None
) -> tuple[Path, str] | None:
    """(path, display) of the final output: the caller's, else assembly_manifest.final_output
    (video-assemble always writes it); None before the assembler has run."""
    if final_output is None:
        manifest_path = work_dir / "assembly_manifest.json"
        if not manifest_path.is_file():
            return None
        final_output = _load_json(manifest_path)["final_output"]
    return _resolve_candidate(work_dir, final_output), str(final_output)


def _final_output_metadata(
    work_dir: Path, final_output: str | Path | None = None
) -> dict[str, Any]:
    resolved = _final_output_path(work_dir, final_output)
    if resolved is None:
        return {"candidates": []}
    path, display = resolved
    item: dict[str, Any] = {"path": display, "exists": path.is_file()}
    if item["exists"]:
        stat = path.stat()
        item.update({"bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns})
    return {"candidates": [item]}


def _existing_final_output(
    work_dir: Path, final_output: str | Path | None
) -> Path | None:
    resolved = _final_output_path(work_dir, final_output)
    return resolved[0] if resolved is not None and resolved[0].is_file() else None


def collect_evidence(
    work_dir: str | Path, *, final_output: str | Path | None = None
) -> dict[str, Any]:
    """Collect lightweight, secret-scrubbed evidence from one work directory."""
    root = Path(work_dir)
    artifacts: dict[str, Any] = {}
    preferred_plan = _first_existing(
        root, ("clip_plan_validated.json", "clip_plan.json")
    )
    for name in _JSON_ARTIFACTS:
        if (
            name in {"clip_plan_validated.json", "clip_plan.json"}
            and name != preferred_plan
        ):
            continue
        item = _collect_file(root, name)
        if item is not None:
            artifacts[name] = item
    evidence = {
        # Display only; excluded from cache_input so moving the work directory is a cache hit.
        "work_dir": str(root),
        "artifacts": artifacts,
        "source_asr": {
            name: item
            for name in _SOURCE_ASR_ARTIFACTS
            if (item := _collect_file(root, name)) is not None
        },
        "generated_subtitles": {
            name: item
            for name in _GENERATED_SUBTITLE_ARTIFACTS
            if (item := _collect_file(root, name)) is not None
        },
        "visual_metadata": {
            name: item
            for name in _OPTIONAL_VISUAL_METADATA
            if (item := _collect_file(root, name)) is not None
        },
        "final_output": _final_output_metadata(root, final_output),
    }
    if not evidence["source_asr"]:
        evidence["source_asr"] = _collect_multi_source_asr(root)
    # The evidence goes into the MiMo request as well as the persisted report, so it is
    # redacted here, before either.
    return qc_contract.redact_secrets(evidence)


def _cache_file_group(group: Mapping[str, Any]) -> dict[str, Any]:
    return {
        name: {key: item[key] for key in ("kind", "bytes", "mtime_ns")}
        for name, item in sorted(group.items())
    }


def _cache_evidence(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """The per-file identities ({kind, bytes, mtime_ns}) a cached stage report was built
    from; work_dir is excluded so moving the directory is still a cache hit."""
    return {
        "artifacts": _cache_file_group(evidence["artifacts"]),
        "source_asr": _cache_file_group(evidence["source_asr"]),
        "generated_subtitles": _cache_file_group(evidence["generated_subtitles"]),
        "visual_metadata": _cache_file_group(evidence["visual_metadata"]),
        "final_output": [
            {key: item[key] for key in ("exists", "bytes", "mtime_ns") if key in item}
            for item in evidence["final_output"]["candidates"]
        ],
    }


def _effective_config(config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    source = dict(DEFAULT_CONFIG)
    if config:
        source.update(dict(config))
        if not config.get("mimo_qc_model"):
            source["mimo_qc_model"] = (
                config.get("mimo_video_model")
                or config.get("mimo_model")
                or source["mimo_qc_model"]
            )
    # MIMO_QC_MODEL is intentionally read at call time for embedded/CLI tests and
    # long-running agent processes whose environment may be adjusted between runs.
    if os.environ.get("MIMO_QC_MODEL") and not (config and config.get("mimo_qc_model")):
        source["mimo_qc_model"] = os.environ["MIMO_QC_MODEL"]
    return source


def safe_mimo_config(config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return non-secret settings suitable for persisted report metadata."""
    source = _effective_config(config)
    keep = (
        "api_provider",
        "mimo_api_url",
        "mimo_api_url_source",
        "mimo_video_api_url",
        "mimo_video_api_url_source",
        "mimo_qc_model",
        "mimo_qc_model_source",
        "mimo_model",
        "mimo_model_source",
        "mimo_video_model",
        "mimo_video_model_source",
        "mimo_disable_thinking",
        "mimo_disable_thinking_source",
        "mimo_media_resolution",
        "mimo_media_resolution_source",
    )
    safe = {key: source[key] for key in keep}
    safe["model"] = (
        source["mimo_qc_model"] or source["mimo_video_model"] or source["mimo_model"]
    )
    key_present = bool(
        source.get("mimo_video_api_key")
        or source.get("mimo_api_key")
        or source.get("api_key")
    )
    safe = qc_contract.redact_secrets(safe)
    safe["key_present"] = key_present
    return safe
