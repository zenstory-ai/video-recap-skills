"""Filesystem material library helpers for video-recap.

The library is intentionally grep-friendly: JSON/MD/JSONL files on disk, no DB,
no embeddings, no raw-media copies. Current metadata lives in each material
folder; the root ``materials_index.jsonl`` is an append-only journal for grep and
history.

Secret handling is BEST-EFFORT defense-in-depth, NOT a guarantee. ``_redact_text``
masks common credential value shapes (``tp-``/``sk-``/``gh*_``/``AKIA``/JWT and
``KEY=VALUE`` assignments) and ``_redact_json`` drops the value of exact
credential-named keys, but a secret in an unrecognized format can still slip
through. Keep secrets (API keys, tokens) out of the analysis artifacts in the
first place — the key is read from the environment/``.env`` and never needs to be
written into scenes/ASR/VLM/summary JSON.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from lib import load_json

ALLOWED_ARTIFACTS = {
    "scenes.json",
    "asr_result.json",
    "asr_clean.json",
    "vlm_analysis.json",
    "silence_periods.json",
    "speech_boundary_anchors.json",
    "speech_boundary_anchors_output.json",
    "timeline_fusion.json",
    "understanding_index.json",
    "understanding_index.md",
    "agent_narration_brief.md",
    "background_research.json",
    "reference_profile.json",
    "reference_match_report.json",
    "recap_run_manifest.json",
}
# Redaction targets credential VALUE shapes, not English/Chinese dictionary words — the
# library must stay a faithful copy of the analysis. Bare words like "secret"/"token"
# legitimately appear in transcripts/summaries and must NOT be touched.
SECRET_VALUE_RES = (
    re.compile(r"\btp-[A-Za-z0-9_-]{8,}\b"),     # MiMo Token Plan keys
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),    # OpenAI-style keys
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),  # GitHub tokens
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),         # AWS access key id
    re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\b"),  # JWT
)
# `KEY=VALUE` / `"key": "value"` assignments whose key denotes a credential -> mask the VALUE.
SECRET_ASSIGN_RE = re.compile(
    r"(?i)(\b(?:mimo(?:_\w+)?_api_key|api_key|secret_key|access_token|refresh_token|"
    r"authorization|password|passwd|bearer)\b\s*[:=]\s*)(\"?)([^\s\"',;]+)(\"?)"
)
# Exact JSON/dict key names whose VALUE is a credential and must be dropped (key name kept).
SECRET_KEY_NAMES = frozenset({
    "api_key", "mimo_api_key", "mimo_asr_api_key", "mimo_tts_api_key", "mimo_video_api_key",
    "secret_key", "access_token", "refresh_token", "authorization", "password", "passwd",
})


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def file_identity(path: str | Path) -> dict:
    """``{size, mtime_ns}`` of a file: the identity recap records and compares for a source
    video or adopted artifact. A file rewritten in place gets a new mtime_ns."""
    st = os.stat(os.fspath(path))
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


def _slug(text: str, max_len: int = 48) -> str:
    raw = Path(text).stem.lower()
    raw = re.sub(r"[^a-z0-9\u4e00-\u9fff._-]+", "-", raw).strip("-._")
    return (raw or "material")[:max_len].strip("-._") or "material"


def _id_stem(source_path: str | Path, max_len: int = 32) -> str:
    raw = re.sub(r"[^a-z0-9]+", "-", Path(source_path).stem.lower()).strip("-")
    return (raw or "source")[:max_len].strip("-") or "source"


def source_id_for(source_path: str | Path) -> str:
    """``src_<stem>_<size>``: readable, stable across runs, and distinct for a different cut
    of the same title (the size changes)."""
    return f"src_{_id_stem(source_path)}_{os.stat(os.fspath(source_path)).st_size}"


def assign_source_ids(sources: list[dict]) -> list[dict]:
    """Assign deterministic source_id values to manifest source records.

    Base id is ``source_id_for(source_path)``. When two records in one project share a
    base id, the first keeps it and later ones get ``_2``, ``_3``… suffixes in input order.
    """
    used: set[str] = set()
    assigned = []
    for raw in sources:
        item = dict(raw)
        path = str(Path(item["source_path"]).resolve())
        base = source_id_for(path)
        sid = base
        suffix = 2
        while sid in used:
            sid = f"{base}_{suffix}"
            suffix += 1
        used.add(sid)
        item["source_id"] = sid
        item["source_path"] = path
        assigned.append(item)
    return assigned


def material_id_for(source_path: str | Path, source_identity: dict) -> str:
    return f"{_slug(str(source_path))}-{source_identity['size']}"


def material_dir(library_dir: str | Path, material_id: str) -> Path:
    return Path(library_dir) / "materials" / material_id


def _redact_text(text: str) -> str:
    """Redact credential value shapes only; leave ordinary words (secret/token/…) intact."""
    for rx in SECRET_VALUE_RES:
        text = rx.sub("[redacted-token]", text)
    return SECRET_ASSIGN_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted-key]{m.group(4)}", text)


def _redact_json(value):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            # Drop only the value of an exact credential-named key; keep the key name and
            # never coalesce distinct keys (so benign fields like token_economy survive).
            if key.strip().lower() in SECRET_KEY_NAMES:
                out[key] = "[redacted]"
            else:
                out[key] = _redact_json(item)
        return out
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def copy_artifact_redacted(src: Path, dst: Path) -> None:
    """Copy an allowed JSON/MD artifact without persisting obvious secret markers."""
    text = src.read_text(encoding="utf-8")
    if src.suffix.lower() == ".json":
        dst.write_text(json.dumps(_redact_json(json.loads(text)), ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        dst.write_text(_redact_text(text), encoding="utf-8")


def summarize_work_dir(work_dir: str | Path, *, source_name: str = "") -> dict:
    """Grep-friendly tags for material.md/index: character and entity names plus artifact counts."""
    work = Path(work_dir)
    summary = f"Analyzed video material: {source_name or work.name}"
    tags = []
    index_path = work / "understanding_index.json"
    if index_path.exists():
        # consolidate.py writes {characters, relationships, plot_points, entities,
        # research_glossary} and no prose summary. The list ITEMS are MiMo output that
        # consolidate only list-checks, so their shape is still tolerated here.
        index = load_json(index_path)
        for key in ("characters", "entities"):
            for item in index[key][:12]:
                text = item.get("name", "") if isinstance(item, dict) else item
                tags.append(_redact_text(str(text))[:80])
    for name, label in (("scenes.json", "scenes"), ("asr_result.json", "asr")):
        if (work / name).exists():
            tags.append(f"{label}:{len(load_json(work / name))}")
    return {
        "summary": summary,
        "tags": list(dict.fromkeys(tag for tag in tags if tag))[:20],
    }


def allowed_artifact_paths(work_dir: str | Path) -> list[Path]:
    work = Path(work_dir)
    return [work / name for name in sorted(ALLOWED_ARTIFACTS) if (work / name).is_file()]


def write_material_md(path: Path, metadata: dict, summary: str, tags: list[str]) -> None:
    artifact_lines = "\n".join(f"- `{a['name']}` → `{a['path']}`" for a in metadata["artifacts"])
    tags_text = ", ".join(tags) if tags else "(none)"
    text = f"""# Material: {metadata['source_name']}

- material_id: `{metadata['material_id']}`
- source: `{metadata['source_path']}`
- source_identity: `{json.dumps(metadata['source_video_identity'], sort_keys=True)}`
- settings: `{json.dumps(metadata['settings'], ensure_ascii=False, sort_keys=True)}`
- updated_at: `{metadata['updated_at']}`
- tags: {tags_text}

## Summary
{summary}

## Artifacts
{artifact_lines or '- (none)'}
"""
    path.write_text(text, encoding="utf-8")


def _read_material_metadata(path: Path) -> dict | None:
    try:
        data = load_json(path)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _material_cache_entry(path: Path) -> dict | None:
    data = _read_material_metadata(path)
    if not data or not all(data.get(key) for key in (
        "source_path", "updated_at", "material_id",
    )) or not isinstance(data.get("artifacts"), list):
        return None
    if not isinstance(data.get("source_video_identity"), dict) \
            or not isinstance(data.get("settings"), dict):
        return None
    return data


def save_material(
    library_dir: str | Path,
    work_dir: str | Path,
    source_path: str | Path,
    source_identity: dict,
    settings: dict,
    *,
    duration: float | None = None,
    source_id: str | None = None,
    material_id: str | None = None,
    now: str | None = None,
) -> dict:
    """Persist small reusable analysis artifacts into the filesystem library."""
    lib = Path(library_dir)
    mid = material_id or material_id_for(source_path, source_identity)
    dest = material_dir(lib, mid)
    artifacts_dir = dest / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    now = now or utc_now_iso()

    sources = allowed_artifact_paths(work_dir)
    fresh_names = {src.name for src in sources}
    # Reconcile: drop allowed artifacts left from a previous (larger) save so the on-disk
    # artifacts/ dir always matches material.json — a stale orphan must not surface in greps.
    for stale in artifacts_dir.iterdir():
        if stale.is_file() and stale.name in ALLOWED_ARTIFACTS and stale.name not in fresh_names:
            stale.unlink()
    copied = []
    for src in sources:
        dst = artifacts_dir / src.name
        copy_artifact_redacted(src, dst)
        copied.append({"name": src.name, "path": f"artifacts/{src.name}", "bytes": dst.stat().st_size})

    meta_path = dest / "material.json"
    previous = _read_material_metadata(meta_path)
    created_at = previous.get("created_at") if previous else None
    if not isinstance(created_at, str) or not created_at:
        created_at = now
    source_path = Path(source_path).resolve()
    summary_info = summarize_work_dir(work_dir, source_name=source_path.name)
    metadata = {
        "schema_version": 1,
        "material_id": mid,
        "source_id": source_id,
        "source_name": source_path.name,
        "source_path": str(source_path),
        "source_video_identity": dict(source_identity),
        "duration": duration,
        "settings": dict(settings),
        "artifacts": copied,
        "created_at": created_at,
        "updated_at": now,
    }
    meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    write_material_md(dest / "material.md", metadata, summary_info["summary"], summary_info["tags"])

    index_record = {
        "schema_version": 1,
        "event": "saved",
        "material_id": mid,
        "source_name": metadata["source_name"],
        "source_path": metadata["source_path"],
        "source_video_identity": dict(source_identity),
        "summary": summary_info["summary"],
        "tags": summary_info["tags"],
        "material_dir": str(dest),
        "updated_at": now,
    }
    lib.mkdir(parents=True, exist_ok=True)
    with (lib / "materials_index.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(index_record, ensure_ascii=False, separators=(",", ":")) + "\n")
    return metadata


def find_material_by_source(
    library_dir: str | Path, source_path: str | Path, source_identity: dict
) -> dict | None:
    """The newest material saved for this exact source file (resolved path + identity)."""
    root = Path(library_dir) / "materials"
    if not root.exists():
        return None
    resolved = str(Path(source_path).resolve())
    candidates = []
    for meta_path in root.glob("*/material.json"):
        data = _material_cache_entry(meta_path)
        if (
            data is not None
            and data["source_path"] == resolved
            and data["source_video_identity"] == source_identity
        ):
            data["material_dir"] = str(meta_path.parent)
            candidates.append(data)
    if not candidates:
        return None
    # Deterministic fallback policy for legacy/manual callers that do not know
    # the expected material_id: newest wins, then material_id for stable ties.
    candidates.sort(key=lambda d: (d["updated_at"], d["material_id"]), reverse=True)
    return candidates[0]


def restore_material(
    library_dir: str | Path,
    work_dir: str | Path,
    *,
    source_path: str | Path,
    source_identity: dict,
    settings: dict,
    material_id: str | None = None,
    overwrite: bool = True,
    prune_stale_allowed: bool = True,
) -> dict:
    """Restore allowed artifacts when source path, identity and settings all match.

    Returns a status dict and never partially restores on mismatch.

    By default the restored material is treated as the authoritative analysis
    snapshot for ``work_dir``: allowed analysis artifacts are staged first, then
    stale allowed artifacts in the destination are pruned before replacement.
    This prevents reused work dirs from mixing old scenes/ASR/VLM files with a
    newly restored material. Non-allowed files (for example narration.json or
    clip_plan.json) are never removed here.
    """
    lib = Path(library_dir)
    if material_id:
        meta_path = material_dir(lib, material_id) / "material.json"
        meta = _material_cache_entry(meta_path)
        if meta is None:
            return {"restored": False, "reason": "material missing or invalid"}
        meta["material_dir"] = str(meta_path.parent)
    else:
        meta = find_material_by_source(lib, source_path, source_identity)
        if meta is None:
            return {"restored": False, "reason": "material not found"}
    if meta["source_path"] != str(Path(source_path).resolve()) \
            or meta["source_video_identity"] != source_identity:
        return {"restored": False, "reason": "source identity mismatch", "material_id": meta["material_id"]}
    if meta["settings"] != settings:
        return {"restored": False, "reason": "settings mismatch", "material_id": meta["material_id"]}

    src_dir = Path(meta["material_dir"]) / "artifacts"
    if not src_dir.exists():
        return {"restored": False, "reason": "material artifacts missing", "material_id": meta["material_id"]}
    dest = Path(work_dir)
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".material_restore_", dir=str(dest)) as tmp_name:
        tmp = Path(tmp_name)
        staged = [artifact["name"] for artifact in meta["artifacts"]]  # save_material's own list
        if not staged or any(not (src_dir / name).is_file() for name in staged):
            return {"restored": False, "reason": "material artifacts missing", "material_id": meta["material_id"]}
        for name in staged:
            copy_artifact_redacted(src_dir / name, tmp / name)

        pruned = []
        if prune_stale_allowed:
            # Never prune an artifact we are about to restore: the restore loop below honors
            # `overwrite`, so pruning a staged name would (with overwrite=False) delete it and
            # then skip the copy, losing the file.
            for name in sorted(ALLOWED_ARTIFACTS - set(staged)):
                out = dest / name
                if out.exists():
                    out.unlink()
                    pruned.append(name)

        restored = []
        for name in staged:
            out = dest / name
            if out.exists() and not overwrite:
                continue
            (tmp / name).replace(out)
            restored.append(name)
    return {
        "restored": bool(restored),
        "material_id": meta["material_id"],
        "artifacts": restored,
        "pruned_artifacts": pruned,
        "material": meta,
    }
