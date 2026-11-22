#!/usr/bin/env python3
"""Lightweight asset catalog, portable material cache, channel presets & timeline license audit.

Subcommands: init | add | list | find | preset | audit | cache-save | cache-restore.
Pure stdlib leaf CLI. Uses portable `<slug>-<bytes>` cache keys (no `mtime_ns` lock-in)
and audits asset licenses & voice consent directly from canonical `timeline.json` v3.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

__all__ = [
    "init_library", "load_catalog", "add_asset", "find_items",
    "apply_preset", "audit_timeline", "cache_save", "cache_restore", "main",
]

_ASSET_KINDS = ("bgm", "sfx", "voice", "font", "image", "theme")
_LICENSE_STATUSES = ("owned", "licensed", "cc0", "unknown", "restricted")
_CONSENT_STATUSES = ("granted", "self", "licensed", "synthetic", "unknown", "denied")
_CACHE_GLOBS = ("probe.json", "transcript.json", "*.srt", "MEDIA_NOTES.md", "notes.md", "PLAN.md", "contact_sheet*.jpg", "frames*.jpg")
_SECRET_VALUE_RES = (
    re.compile(r"\btp-[A-Za-z0-9_-]{8,}\b"), re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\b"),
)
_SECRET_ASSIGN_RE = re.compile(
    r"(?i)(\b(?:\w*_(?:api_key|token|secret)|api_key|secret_key|access_token|refresh_token|authorization|password|bearer)\b\s*[:=]\s*)(\"?)([^\s\"',;]+)(\"?)"
)
_SECRET_KEYS = frozenset({
    "api_key", "fish_api_key", "fish_audio_api_key", "openai_api_key", "groq_api_key",
    "asr_api_key", "mimo_api_key", "mimo_asr_api_key", "mimo_tts_api_key",
    "secret_key", "access_token", "refresh_token", "authorization", "password",
})


def _is_secret_key(key: str) -> bool:
    low = key.strip().lower()
    return low in _SECRET_KEYS or low.endswith(("_api_key", "_token", "_secret"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _material_key(source_video: str | Path) -> str:
    p = Path(source_video).expanduser().resolve()
    slug = (re.sub(r"[^a-z0-9\u4e00-\u9fff._-]+", "-", p.stem.lower()).strip("-._") or "material")[:48].strip("-._") or "material"
    return f"{slug}-{p.stat().st_size if p.is_file() else 0}"


def _redact_text(text: str) -> str:
    for rx in _SECRET_VALUE_RES:
        text = rx.sub("[redacted-token]", text)
    return _SECRET_ASSIGN_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted-key]{m.group(4)}", text)


def _redact_json(value):
    if isinstance(value, dict):
        return {k: ("[redacted]" if _is_secret_key(k) else _redact_json(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_json(v) for v in value]
    return _redact_text(value) if isinstance(value, str) else value


def init_library(lib_dir: str | Path, *, name: str = "") -> Path:
    """Create a fresh library layout (`catalog.json`, `assets/`, `materials/`, `presets/`)."""
    root = Path(lib_dir).expanduser().resolve()
    for sub in ("assets", "materials", "presets"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    cat_path = root / "catalog.json"
    if not cat_path.exists():
        cat_path.write_text(json.dumps({"schema_version": 1, "name": name or root.name, "assets": []}, ensure_ascii=False, indent=2), encoding="utf-8")
    return cat_path


def load_catalog(lib_dir: str | Path) -> dict:
    root = Path(lib_dir).expanduser().resolve()
    cat_path = root / "catalog.json"
    if not cat_path.is_file():
        return {"schema_version": 1, "name": root.name, "assets": []}
    data = json.loads(cat_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"catalog.json must be a JSON object: {cat_path}")
    data.setdefault("assets", [])
    return data


def add_asset(
    lib_dir: str | Path, *, asset_id: str, kind: str, file_path: str | Path | None = None,
    tags: list[str] | None = None, license_status: str = "unknown",
    consent: str | None = None, meta: dict | None = None,
) -> dict:
    """Register or update an asset in flat, extensible `catalog.json`."""
    if kind not in _ASSET_KINDS:
        raise ValueError(f"kind must be one of {_ASSET_KINDS}; got {kind!r}")
    if license_status not in _LICENSE_STATUSES:
        raise ValueError(f"license must be one of {_LICENSE_STATUSES}; got {license_status!r}")
    if consent is not None and consent not in _CONSENT_STATUSES:
        raise ValueError(f"consent must be one of {_CONSENT_STATUSES}; got {consent!r}")

    root = Path(lib_dir).expanduser().resolve()
    init_library(root)
    cat = load_catalog(root)
    rel_path = None
    if file_path:
        src = Path(file_path).expanduser().resolve()
        try:
            rel_path = src.relative_to(root).as_posix()
        except ValueError:
            rel_path = str(src)

    entry: dict = {"id": asset_id, "kind": kind, "path": rel_path, "tags": list(tags or []),
                   "license": license_status, "updated_at": _now_iso()}
    if consent is not None:
        entry["consent"] = consent
    if isinstance(meta, dict):
        for k, v in meta.items():
            entry.setdefault(k, v)

    cat["assets"] = sorted(
        [a for a in cat["assets"] if a.get("id") != asset_id] + [entry],
        key=lambda a: (str(a.get("kind", "")), str(a.get("id", ""))),
    )
    (root / "catalog.json").write_text(json.dumps(cat, ensure_ascii=False, indent=2), encoding="utf-8")
    return entry


def find_items(lib_dir: str | Path, query: str = "", *, kind: str | None = None, tag: str | None = None) -> dict:
    """Search catalog assets and cached material summaries (case-insensitive substring match)."""
    root = Path(lib_dir).expanduser().resolve()
    q = query.strip().lower()
    matched_assets = [
        a for a in (load_catalog(root).get("assets") or [])
        if (not kind or a.get("kind") == kind)
        and (not tag or tag in (a.get("tags") or []))
        and (not q or q in json.dumps(a, ensure_ascii=False).lower())
    ]
    matched_materials = []
    mat_root = root / "materials"
    if mat_root.is_dir() and not kind:
        for child in sorted(p for p in mat_root.iterdir() if p.is_dir()):
            blob = child.name.lower() + "\n" + "\n".join(
                (child / fn).read_text(encoding="utf-8", errors="replace").lower()
                for fn in ("material.md", "notes.md") if (child / fn).is_file()
            )
            if not q or q in blob:
                matched_materials.append({"id": child.name, "dir": str(child)})
    return {"assets": matched_assets, "materials": matched_materials}


def _deep_merge_defaults(base: dict, defaults: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in defaults.items():
        if k not in out or out[k] is None:
            out[k] = copy.deepcopy(v)
        elif isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge_defaults(out[k], v)
    return out


def apply_preset(lib_dir: str | Path, preset_name: str, timeline: dict) -> dict:
    """Merge a plain channel preset JSON (`presets/<name>.json`) directly into `timeline.json`."""
    root = Path(lib_dir).expanduser().resolve()
    cand = Path(preset_name).expanduser()
    preset_path = cand if cand.is_file() else (root / "presets" / (preset_name if preset_name.endswith(".json") else f"{preset_name}.json"))
    if not preset_path.is_file():
        raise FileNotFoundError(f"preset not found: {preset_path}")
    resolved = copy.deepcopy(json.loads(preset_path.read_text(encoding="utf-8")))
    if not isinstance(resolved, dict):
        raise ValueError(f"preset must be a JSON object: {preset_path}")
    if isinstance(resolved.get("sources"), dict):
        for sid, raw in list(resolved["sources"].items()):
            p = Path(str(raw)).expanduser()
            if not p.is_absolute() and (root / p).exists():
                resolved["sources"][sid] = str((root / p).resolve())
    sub = resolved.get("subtitle_style")
    if isinstance(sub, dict) and isinstance(sub.get("font_file"), str) and sub["font_file"]:
        fp = Path(sub["font_file"]).expanduser()
        if not fp.is_absolute() and (root / fp).exists():
            sub["font_file"] = str((root / fp).resolve())
    return _deep_merge_defaults(timeline, resolved)


def _resolve_tl_path(timeline: dict, ref: str, base_dir: Path | None) -> Path:
    p = Path(str((timeline.get("sources") or {}).get(ref, ref))).expanduser()
    if not p.is_absolute() and base_dir is not None:
        p = (base_dir / p).resolve()
    return p.resolve() if p.exists() else p


def audit_timeline(timeline: dict, *, library_dir: str | Path | None = None, base_dir: Path | None = None) -> dict:
    """Audit all referenced files, fonts, and voices directly from `timeline.json` v3."""
    entries: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def _add(role: str, path: Path | None, detail: dict | None = None):
        pstr = str(path) if path is not None else ""
        key = (role, pstr or json.dumps(detail or {}, sort_keys=True))
        if key not in seen:
            seen.add(key)
            entries.append({"role": role, "path": pstr or None, "exists": bool(path and path.is_file()),
                            "detail": detail or {}, "library": None})

    for c in timeline.get("video") or []:
        _add("video", _resolve_tl_path(timeline, c["source"], base_dir), {"source": c["source"]})
    audio = timeline.get("audio") or {}
    for role in ("bgm", "sfx"):
        for seg in audio.get(role) or []:
            ref = seg.get("source") or seg.get("file")
            if ref:
                _add(role, _resolve_tl_path(timeline, ref, base_dir), {"source": ref})
    for ovl in timeline.get("overlays") or []:
        ref = ovl.get("source") or ovl.get("file")
        if ref:
            _add("overlay", _resolve_tl_path(timeline, ref, base_dir), {"id": ovl.get("id")})
    font_file = (timeline.get("subtitle_style") or {}).get("font_file")
    if isinstance(font_file, str) and font_file:
        _add("font", _resolve_tl_path(timeline, font_file, base_dir))
    voice = (timeline.get("meta") or {}).get("voice")
    if isinstance(voice, dict) and voice:
        vref = voice.get("reference")
        _add("voice", _resolve_tl_path(timeline, vref, base_dir) if isinstance(vref, str) and vref else None, voice)

    lib_root: Path | None = None
    by_path: dict[str, dict] = {}
    voices_by_id: dict[str, dict] = {}
    if library_dir and Path(library_dir).expanduser().is_dir():
        lib_root = Path(library_dir).expanduser().resolve()
        for a in load_catalog(lib_root).get("assets") or []:
            if a.get("path"):
                ap = Path(a["path"])
                by_path[str((lib_root / ap).resolve() if not ap.is_absolute() else ap.resolve())] = a
            if a.get("kind") == "voice" and a.get("voice_id"):
                voices_by_id[str(a["voice_id"])] = a

    attention: list[dict] = []
    ok_consent = {"granted", "self", "licensed", "synthetic"}
    for e in entries:
        if e["path"] and not e["exists"]:
            attention.append({"code": "missing_file", "role": e["role"], "path": e["path"], "message": "referenced file not found"})
        matched = by_path.get(e["path"]) if e["path"] else None
        if matched is None and e["role"] == "voice":
            matched = voices_by_id.get(str((e["detail"] or {}).get("voice_id") or ""))
        if matched:
            lic, con = matched.get("license", "unknown"), matched.get("consent")
            e["library"] = {"id": matched["id"], "kind": matched["kind"], "license": lic, "consent": con}
            if lic in {"unknown", "restricted"}:
                attention.append({"code": f"license_{lic}", "role": e["role"], "asset_id": matched["id"], "message": f"{matched['id']} license is {lic}"})
            if e["role"] == "voice" and con not in ok_consent:
                attention.append({"code": f"consent_{con or 'unknown'}", "role": "voice", "asset_id": matched["id"], "message": f"{matched['id']} voice consent is {con or 'unknown'}"})
        elif e["role"] == "voice":
            cstatus = str((e["detail"] or {}).get("consent") or "unknown")
            if cstatus not in ok_consent:
                attention.append({"code": f"consent_{cstatus}", "role": "voice", "message": f"voice consent is {cstatus}"})
        elif lib_root is not None and e["role"] not in {"video", "overlay"}:
            attention.append({"code": "unregistered", "role": e["role"], "path": e["path"], "message": "asset not registered in library catalog.json"})

    return {"schema_version": 1, "generated_at": _now_iso(), "library": str(lib_root) if lib_root else None,
            "resources": entries, "attention": attention}


def cache_save(lib_dir: str | Path, source_video: str | Path, from_dir: str | Path) -> dict:
    """Save portable analysis artifacts into `<lib>/materials/<slug>-<bytes>/` with secret redaction."""
    root = Path(lib_dir).expanduser().resolve()
    init_library(root)
    src_vid, wdir = Path(source_video).expanduser().resolve(), Path(from_dir).expanduser().resolve()
    key = _material_key(src_vid)
    dest = root / "materials" / key
    dest.mkdir(parents=True, exist_ok=True)
    saved, seen = [], set()
    for pat in _CACHE_GLOBS:
        for fp in sorted(wdir.glob(pat)):
            if not fp.is_file() or fp in seen:
                continue
            seen.add(fp)
            target = dest / fp.name
            if fp.suffix.lower() == ".json":
                target.write_text(json.dumps(_redact_json(json.loads(fp.read_text(encoding="utf-8"))), ensure_ascii=False, indent=2), encoding="utf-8")
            elif fp.suffix.lower() in {".md", ".srt", ".txt"}:
                target.write_text(_redact_text(fp.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")
            else:
                shutil.copy2(fp, target)
            saved.append(fp.name)
    md = f"# Material: {src_vid.name}\n\n- material_id: `{key}`\n- source_bytes: {src_vid.stat().st_size if src_vid.is_file() else 0}\n- updated_at: {_now_iso()}\n- files: {', '.join(saved) or '(none)'}\n"
    (dest / "material.md").write_text(md, encoding="utf-8")
    return {"material_id": key, "dir": str(dest), "files": saved}


def cache_restore(lib_dir: str | Path, source_video: str | Path, to_dir: str | Path) -> dict:
    """Restore cached portable analysis artifacts matching `<slug>-<bytes>` into `to_dir`."""
    key = _material_key(source_video)
    src_dir = Path(lib_dir).expanduser().resolve() / "materials" / key
    if not src_dir.is_dir():
        return {"restored": False, "material_id": key, "files": []}
    dst = Path(to_dir).expanduser().resolve()
    dst.mkdir(parents=True, exist_ok=True)
    restored = []
    for fp in sorted(p for p in src_dir.iterdir() if p.is_file() and p.name != "material.md"):
        target = dst / fp.name
        if fp.name in {"PLAN.md", "notes.md"} and target.exists():
            continue
        shutil.copy2(fp, target)
        restored.append(fp.name)
    return {"restored": True, "material_id": key, "files": restored}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Asset catalog, portable material cache, presets & timeline audit.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_init = sub.add_parser("init")
    p_init.add_argument("lib_dir")
    p_init.add_argument("--name", default="")

    p_add = sub.add_parser("add")
    p_add.add_argument("lib_dir")
    p_add.add_argument("--id", required=True, dest="asset_id")
    p_add.add_argument("--kind", required=True, choices=_ASSET_KINDS)
    p_add.add_argument("--file", default=None)
    p_add.add_argument("--tags", default="")
    p_add.add_argument("--license", default="unknown", choices=_LICENSE_STATUSES)
    p_add.add_argument("--consent", default=None, choices=_CONSENT_STATUSES)
    p_add.add_argument("--meta", default=None)

    p_ls = sub.add_parser("list")
    p_ls.add_argument("lib_dir")
    p_ls.add_argument("--kind", default=None)
    p_ls.add_argument("--tag", default=None)

    p_find = sub.add_parser("find")
    p_find.add_argument("lib_dir")
    p_find.add_argument("query")

    p_pre = sub.add_parser("preset")
    p_pre.add_argument("lib_dir")
    p_pre.add_argument("preset_name")
    p_pre.add_argument("--timeline", required=True)
    p_pre.add_argument("--out", default=None)

    p_aud = sub.add_parser("audit")
    p_aud.add_argument("timeline")
    p_aud.add_argument("--library", default=os.environ.get("VIDEO_RECAP_LIBRARY_DIR"))
    p_aud.add_argument("--out", default=None)

    p_cs = sub.add_parser("cache-save")
    p_cs.add_argument("lib_dir")
    p_cs.add_argument("--source", required=True)
    p_cs.add_argument("--from-dir", required=True)

    p_cr = sub.add_parser("cache-restore")
    p_cr.add_argument("lib_dir")
    p_cr.add_argument("--source", required=True)
    p_cr.add_argument("--to-dir", required=True)

    args = ap.parse_args(argv)
    if args.cmd == "init":
        out = {"status": "initialized", "catalog": str(init_library(args.lib_dir, name=args.name))}
    elif args.cmd == "add":
        out = add_asset(args.lib_dir, asset_id=args.asset_id, kind=args.kind, file_path=args.file,
                        tags=[t.strip() for t in args.tags.split(",") if t.strip()],
                        license_status=args.license, consent=args.consent,
                        meta=json.loads(args.meta) if args.meta else None)
    elif args.cmd == "list":
        out = find_items(args.lib_dir, kind=args.kind, tag=args.tag)
    elif args.cmd == "find":
        out = find_items(args.lib_dir, args.query)
    elif args.cmd == "preset":
        tl_p = Path(args.timeline).expanduser().resolve()
        out_p = Path(args.out).expanduser().resolve() if args.out else tl_p
        out_p.write_text(json.dumps(apply_preset(args.lib_dir, args.preset_name, json.loads(tl_p.read_text(encoding="utf-8"))), ensure_ascii=False, indent=2), encoding="utf-8")
        out = {"status": "applied", "timeline": str(out_p)}
    elif args.cmd == "audit":
        tl_p = Path(args.timeline).expanduser().resolve()
        out = audit_timeline(json.loads(tl_p.read_text(encoding="utf-8")), library_dir=args.library, base_dir=tl_p.parent)
        if args.out:
            Path(args.out).expanduser().resolve().write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    elif args.cmd == "cache-save":
        out = cache_save(args.lib_dir, args.source, args.from_dir)
    else:
        out = cache_restore(args.lib_dir, args.source, args.to_dir)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
