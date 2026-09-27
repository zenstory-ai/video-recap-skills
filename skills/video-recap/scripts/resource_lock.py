"""Record every resource and template a finished run used: ``work_dir/resource_lock.json``.

Facts come from artifacts the stages already wrote (run manifest, ``tts_meta.json``,
``assembly_manifest.json``) plus the optional project binding. When a library is
configured, each file-backed entry is matched to a registered resource by resolved path
so its licence and consent status travel with the run. Nothing here changes a render.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import library as library_lib
from materials import file_identity

LOCK_NAME = "resource_lock.json"
LOCK_SCHEMA = "video-recap.resource-lock.v1"


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _file_entry(role: str, path, detail=None) -> dict:
    entry = {"role": role, "path": None, "size": None, "mtime_ns": None,
             "detail": detail or {}, "library": None}
    if path:
        resolved = Path(path).expanduser().resolve()
        entry["path"] = str(resolved)
        if resolved.is_file():
            entry.update(file_identity(resolved))
    return entry


def _source_entries(work_dir: Path) -> list[dict]:
    manifest = _read(work_dir / "recap_run_manifest.json") or {}
    if manifest.get("mode") == "multi_source":
        return [_file_entry("source_video", s.get("source_path"), {"source_id": s.get("source_id")})
                for s in manifest.get("sources", [])]
    if manifest.get("source_video"):
        return [_file_entry("source_video", manifest["source_video"])]
    return []


def _voice_entry(work_dir: Path) -> dict | None:
    meta = _read(work_dir / "tts_meta.json")
    if not isinstance(meta, dict):
        return None
    voice = meta.get("voice") or {"provider": meta.get("engine")}
    reference = voice.get("reference") or {}
    entry = _file_entry("voice", reference.get("path"), {
        "provider": voice.get("provider"), "model": voice.get("model"),
        "voice_id": voice.get("voice_id"),
    })
    return entry


def _assembly_entries(work_dir: Path) -> list[dict]:
    manifest = _read(work_dir / "assembly_manifest.json") or {}
    settings = manifest.get("assembly_settings") or {}
    entries = []
    bgm = (settings.get("audio_mix") or {}).get("bgm_path")
    if bgm:
        entries.append(_file_entry("bgm", bgm))
    packaging = (settings.get("video_filters") or {}).get("packaging_layers") or {}
    for layer in packaging.get("layers", []):
        entries.append(_file_entry("packaging_layer", layer.get("path"), {"name": layer.get("name")}))
    style = settings.get("subtitle_style")
    if isinstance(style, dict):
        entries.append(_file_entry("subtitle_font", style.get("font_file"),
                                   {"family": style.get("font_name")}))
    return entries


def _match_library(entries: list[dict], index: dict) -> None:
    by_path = {}
    for resource in index["resources"]:
        for f in resource["files"]:
            by_path[str(Path(f["path"]).resolve())] = resource
    voices = [r for r in index["resources"] if r["kind"] == "voice"]
    records = {r["id"]: json.loads(Path(r["record"]).read_text(encoding="utf-8"))
               for r in index["resources"]}
    for entry in entries:
        resource = by_path.get(entry["path"]) if entry["path"] else None
        if (resource is None and entry["role"] == "voice" and not entry["path"]
                and isinstance(entry["detail"].get("voice_id"), str) and entry["detail"]["voice_id"]):
            detail = entry["detail"]
            resource = next((v for v in voices
                             if (records[v["id"]].get("voice") or {}).get("provider") == detail.get("provider")
                             and (records[v["id"]].get("voice") or {}).get("voice_id") == detail.get("voice_id")), None)
        if resource is None:
            continue
        record = records[resource["id"]]
        entry["library"] = {"id": resource["id"], "kind": resource["kind"],
                            "license": resource["license"],
                            "consent": (record.get("consent") or {}).get("status")}


def _attention(entries: list[dict], library_configured: bool) -> list[dict]:
    items = []
    for entry in entries:
        role, lib = entry["role"], entry["library"]
        if role == "source_video":
            continue
        if lib is None:
            if library_configured and (entry["path"] or role == "voice"):
                items.append({"code": "unregistered", "role": role,
                              "message": "资源库中没有登记这项资源，授权状态未知"})
            continue
        if lib["license"] in {"unknown", "restricted"}:
            items.append({"code": f"license_{lib['license']}", "role": role,
                          "message": f"{lib['id']} 的授权状态为 {lib['license']}，交付前需要人工确认"})
        if role == "voice" and entry["path"] and lib.get("consent") != "granted":
            items.append({"code": f"consent_{lib.get('consent') or 'unknown'}", "role": role,
                          "message": f"{lib['id']} 的参考音频声音授权未确认"})
    return items


def build_resource_lock(work_dir, *, library_dir=None, project=None) -> dict:
    """Assemble the lock payload from a finished work_dir; ``project`` is a resolved binding."""
    work_dir = Path(work_dir)
    entries = _source_entries(work_dir)
    voice = _voice_entry(work_dir)
    if voice:
        entries.append(voice)
    entries += _assembly_entries(work_dir)
    entries += list((project or {}).get("resources", []))
    library_root = None
    if library_dir and Path(library_dir).is_dir():
        library_root = str(Path(library_dir).resolve())
        index, _ = library_lib.scan_library(library_root)
        _match_library(entries, index)
    return {
        "schema": LOCK_SCHEMA,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "work_dir": str(work_dir.resolve()),
        "library": library_root,
        "project": ({"path": project["path"], "name": project.get("name", "")} if project else None),
        "templates": list((project or {}).get("templates", [])),
        "resources": entries,
        "attention": _attention(entries, library_root is not None),
    }


def write_resource_lock(work_dir, *, library_dir=None, project=None) -> dict:
    lock = build_resource_lock(work_dir, library_dir=library_dir, project=project)
    path = Path(work_dir) / LOCK_NAME
    path.write_text(json.dumps(lock, ensure_ascii=False, indent=2), encoding="utf-8")
    return lock


def summary_line(lock: dict) -> str:
    registered = sum(1 for e in lock["resources"] if e["library"])
    text = (f"[video-recap] 资源记录: {LOCK_NAME}（{len(lock['resources'])} 项，"
            f"资源库已登记 {registered} 项）")
    if lock["attention"]:
        text += "\n" + "\n".join(f"[video-recap]    ⚠ {a['role']}: {a['message']}" for a in lock["attention"])
    return text
