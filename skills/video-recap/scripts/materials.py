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
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from lib import load_json, material_id_for, read_json_object, source_id_for

# asr_timing_evidence.json stays out on purpose: it binds asr_result.json and audio.wav by
# {size, mtime_ns}, and a restore rewrites the former (redacted copy) and never copies the
# latter, so a restored sidecar could only ever read as MISSING_OR_STALE.
ALLOWED_ARTIFACTS = {
    "scenes.json",
    "asr_result.json",
    "vlm_analysis.json",
    "silence_periods.json",
    "speech_boundary_anchors.json",
    "speech_boundary_anchors_output.json",
    "agent_narration_brief.md",
    "background_research.json",
}
# The agent writes this one before understanding, so a restore must not take it away.
_RESEARCH = "background_research.json"
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
    """Grep-friendly tags for material.md/index: character names plus artifact counts."""
    work = Path(work_dir)
    summary = f"Analyzed video material: {source_name or work.name}"
    tags = []
    research_path = work / "background_research.json"
    if research_path.exists():
        research = read_json_object(research_path) or {}
        characters = research.get("characters")
        if isinstance(characters, dict):
            for name in list(characters.keys())[:12]:
                tags.append(_redact_text(str(name))[:80])
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


def _material_cache_entry(path: Path) -> dict | None:
    data = read_json_object(path)
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
    source_id: str | None = None,
    material_id: str | None = None,
    series: dict | None = None,
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
    # Reconcile: drop current ALLOWED_ARTIFACTS names left from a previous (larger) save, so a
    # stale orphan does not surface in greps. Files of retired names are left where they are.
    for stale in artifacts_dir.iterdir():
        if stale.is_file() and stale.name in ALLOWED_ARTIFACTS and stale.name not in fresh_names:
            stale.unlink()
    copied = []
    for src in sources:
        dst = artifacts_dir / src.name
        copy_artifact_redacted(src, dst)
        copied.append({"name": src.name, "path": f"artifacts/{src.name}", "bytes": dst.stat().st_size})

    meta_path = dest / "material.json"
    previous = read_json_object(meta_path)
    created_at = previous.get("created_at") if previous else None
    if not isinstance(created_at, str) or not created_at:
        created_at = now
    effective_series = dict(series) if isinstance(series, dict) else (
        dict(previous["series"]) if previous and isinstance(previous.get("series"), dict) else None
    )
    source_path = Path(source_path).resolve()
    summary_info = summarize_work_dir(work_dir, source_name=source_path.name)
    metadata = {
        "schema_version": 1,
        "material_id": mid,
        "source_id": source_id,
        "source_name": source_path.name,
        "source_path": str(source_path),
        "source_video_identity": dict(source_identity),
        "settings": dict(settings),
        "artifacts": copied,
        "created_at": created_at,
        "updated_at": now,
    }
    if effective_series:
        metadata["series"] = effective_series
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


def _comparable_settings(settings: dict) -> dict:
    return {k: v for k, v in settings.items() if k != "consolidate"}


def restore_material(
    library_dir: str | Path,
    work_dir: str | Path,
    *,
    source_path: str | Path,
    source_identity: dict,
    settings: dict,
    material_id: str,
) -> dict:
    """Restore allowed artifacts when source path, identity and settings all match.

    Returns a status dict and never partially restores on mismatch.

    The restored material is treated as the authoritative analysis
    snapshot for ``work_dir``: allowed analysis artifacts are staged first, then
    stale allowed artifacts in the destination are pruned before replacement.
    This prevents reused work dirs from mixing old scenes/ASR/VLM files with a
    newly restored material. Non-allowed files (for example narration.json or
    clip_plan.json) are never removed here, and neither is an agent-written
    background_research.json: it survives an entry without research, and a work_dir
    copy newer than the entry's wins.
    """
    meta_path = material_dir(Path(library_dir), material_id) / "material.json"
    meta = _material_cache_entry(meta_path)
    if meta is None:
        return {"restored": False, "reason": "material missing or invalid"}
    if meta["source_path"] != str(Path(source_path).resolve()) \
            or meta["source_video_identity"] != source_identity:
        return {"restored": False, "reason": "source identity mismatch", "material_id": meta["material_id"]}
    if _comparable_settings(meta["settings"]) != _comparable_settings(settings):
        return {"restored": False, "reason": "settings mismatch", "material_id": meta["material_id"]}

    src_dir = meta_path.parent / "artifacts"
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
        for name in sorted(ALLOWED_ARTIFACTS - set(staged) - {_RESEARCH}):
            out = dest / name
            if out.exists():
                out.unlink()
                pruned.append(name)

        kept_local_research = (
            _RESEARCH not in staged and (dest / _RESEARCH).is_file()
        )
        for name in staged:
            if name == _RESEARCH and (dest / name).is_file() \
                    and (dest / name).stat().st_mtime_ns > (src_dir / name).stat().st_mtime_ns:
                kept_local_research = True
                continue
            (tmp / name).replace(dest / name)
    return {
        "restored": True,
        "material_id": meta["material_id"],
        "artifacts": staged,
        "pruned_artifacts": pruned,
        "kept_local_research": kept_local_research,
    }


_NOISY_TAG_RE = re.compile(r"^(?:scenes|asr):\d+$", re.IGNORECASE)
_EPISODE_RE = re.compile(r"(?:ep|episode|e|第)\s*0*(\d{1,3})", re.IGNORECASE)


def _clean_synopsis(meta: dict, research: dict, vlm_raw) -> str:
    for key in ("synopsis", "summary_zh", "plot_summary"):
        val = meta.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    series_meta = meta.get("series") if isinstance(meta.get("series"), dict) else {}
    for key in ("episode_synopsis", "synopsis"):
        val = series_meta.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    for key in ("synopsis", "summary", "story_background", "overview", "premise", "plot_summary"):
        val = research.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    if isinstance(vlm_raw, dict):
        for key in ("overall_summary", "summary", "synopsis"):
            val = vlm_raw.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        scenes = vlm_raw.get("scenes")
    elif isinstance(vlm_raw, list):
        scenes = vlm_raw
    else:
        scenes = None
    if isinstance(scenes, list):
        parts = []
        for item in scenes[:3]:
            if not isinstance(item, dict):
                continue
            desc = item.get("summary") or item.get("description") or item.get("action")
            if isinstance(desc, str) and desc.strip():
                parts.append(desc.strip().rstrip("。；;"))
        if parts:
            return "；".join(parts)[:180] + "。"
    return "已完成分镜切分与台词锚点解析，可直接复用。"


def _extract_characters(meta: dict, research: dict) -> list[str]:
    out: list[str] = []
    series_meta = meta.get("series") if isinstance(meta.get("series"), dict) else {}
    for raw in (series_meta.get("characters"), research.get("characters")):
        if isinstance(raw, dict):
            out.extend(str(k).strip() for k in raw.keys() if str(k).strip())
        elif isinstance(raw, list):
            for item in raw:
                name = item.get("name") if isinstance(item, dict) else item
                if isinstance(name, str) and name.strip():
                    out.append(name.strip())
    return list(dict.fromkeys(out))[:10]


def _safe_list_stats(path: Path) -> tuple[int, float | None]:
    if not path.is_file():
        return 0, None
    try:
        items = load_json(path)
    except (OSError, ValueError, TypeError):
        return 0, None
    if not isinstance(items, list):
        return 0, None
    max_end = None
    for item in items:
        if isinstance(item, dict):
            end = item.get("end_time") if "end_time" in item else item.get("end")
            if isinstance(end, (int, float)) and not isinstance(end, bool):
                max_end = max(max_end or 0.0, float(end))
    return len(items), (round(max_end, 1) if max_end else None)


def scan_materials(library_dir: str | Path) -> dict:
    """Read-only, deduplicated, series-grouped view of ``<library>/materials``."""
    root = Path(library_dir) / "materials"
    if not root.is_dir():
        return {"total_count": 0, "series": [], "standalone": []}

    raw_entries: dict[str, tuple[str, Path, dict]] = {}
    for meta_path in sorted(root.glob("*/material.json")):
        meta = _material_cache_entry(meta_path)
        if meta is None:
            continue
        ident_key = f"{meta['source_path']}::{json.dumps(meta['source_video_identity'], sort_keys=True)}"
        updated = str(meta.get("updated_at", ""))
        prev = raw_entries.get(ident_key)
        if prev is None or updated >= prev[0]:
            raw_entries[ident_key] = (updated, meta_path.parent, meta)

    series_groups: dict[str, dict] = {}
    standalone: list[dict] = []

    for _, mdir, meta in sorted(raw_entries.values(), key=lambda x: str(x[2].get("source_name", ""))):
        art_dir = mdir / "artifacts"
        research = read_json_object(art_dir / "background_research.json") or {}
        vlm_raw = None
        if (art_dir / "vlm_analysis.json").is_file():
            try:
                vlm_raw = load_json(art_dir / "vlm_analysis.json")
            except (OSError, ValueError, TypeError):
                vlm_raw = None
        scene_count, scene_dur = _safe_list_stats(art_dir / "scenes.json")
        asr_count, asr_dur = _safe_list_stats(art_dir / "asr_result.json")
        dur_raw = meta.get("duration_sec")
        duration = float(dur_raw) if isinstance(dur_raw, (int, float)) and dur_raw > 0 else (scene_dur or asr_dur)
        series_info = meta.get("series") if isinstance(meta.get("series"), dict) else {}
        series_id = str(series_info.get("series_id") or "").strip()
        series_title = str(
            series_info.get("series_title") or research.get("series_title") or research.get("title") or ""
        ).strip()
        ep_no = series_info.get("ep_no")
        if not isinstance(ep_no, int) or isinstance(ep_no, bool):
            m_ep = _EPISODE_RE.search(str(meta.get("source_name", "")))
            ep_no = int(m_ep.group(1)) if m_ep else None
        ep_title = str(series_info.get("episode_title") or "").strip() or (
            f"第 {ep_no:02d} 集" if isinstance(ep_no, int) else Path(meta["source_name"]).stem
        )
        characters = _extract_characters(meta, research)
        raw_tags = meta.get("tags") if isinstance(meta.get("tags"), list) else []
        clean_tags = [
            str(t).strip() for t in raw_tags
            if isinstance(t, str) and str(t).strip() and not _NOISY_TAG_RE.match(str(t).strip())
        ]
        episode = {
            "material_id": meta["material_id"],
            "source_name": meta["source_name"],
            "source_online": Path(meta["source_path"]).is_file(),
            "ep_no": ep_no,
            "episode_title": ep_title,
            "synopsis": _clean_synopsis(meta, research, vlm_raw),
            "characters": characters,
            "tags": clean_tags,
            "duration_sec": duration,
            "resolution": str(meta.get("resolution") or series_info.get("resolution") or "").strip(),
            "scene_count": scene_count,
            "asr_count": asr_count,
            "updated_at": meta["updated_at"],
        }
        if series_id:
            grp = series_groups.setdefault(series_id, {
                "series_id": series_id,
                "title": series_title or series_id,
                "era": str(series_info.get("era") or research.get("era") or "").strip(),
                "genre": str(series_info.get("genre") or research.get("genre") or "").strip(),
                "synopsis": str(series_info.get("series_synopsis") or research.get("synopsis") or "").strip(),
                "characters": [],
                "episodes": [],
            })
            if not grp["synopsis"] and episode["synopsis"]:
                grp["synopsis"] = episode["synopsis"]
            for ch in characters:
                if ch not in grp["characters"] and len(grp["characters"]) < 10:
                    grp["characters"].append(ch)
            grp["episodes"].append(episode)
        else:
            if series_title:
                episode["series_title"] = series_title
            standalone.append(episode)

    series_list = []
    for grp in series_groups.values():
        grp["episodes"].sort(key=lambda e: (e["ep_no"] is None, e["ep_no"] or 0, e["source_name"]))
        grp["episode_count"] = len(grp["episodes"])
        series_list.append(grp)
    series_list.sort(key=lambda g: (-g["episode_count"], g["title"]))
    total = sum(g["episode_count"] for g in series_list) + len(standalone)
    return {"total_count": total, "series": series_list, "standalone": standalone}
