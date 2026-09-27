#!/usr/bin/env python3
"""Read-only resource / template / sample library for video-recap.

The library shares its root with the material library (``--material-library-dir``):

    <library>/library.json
    <library>/resources/<kind>/<id>/resource.json
    <library>/templates/<kind>/<id>/v<version>/template.json
    <library>/samples/<id>/sample.json

Nothing here writes to the library. ``check`` reports errors (the entry cannot be used)
and warnings (usable, but a person should look: unknown licence, missing consent, a
resource that changed since a template was adopted). The record format lives in
``references/resource-library.md``.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from materials import file_identity

LIBRARY_ENV = "VIDEO_RECAP_MATERIAL_LIBRARY_DIR"
LIBRARY_SCHEMA = "video-recap.library.v1"
RESOURCE_SCHEMA = "video-recap.resource.v1"
TEMPLATE_SCHEMA = "video-recap.template.v1"
SAMPLE_SCHEMA = "video-recap.sample.v1"

AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}
RESOURCE_EXTS = {
    "bgm": AUDIO_EXTS,
    "sfx": AUDIO_EXTS,
    "voice": AUDIO_EXTS,
    "font": {".ttf", ".otf", ".ttc"},
    "image": {".png", ".jpg", ".jpeg", ".webp"},
}
TEMPLATE_KINDS = ("subtitle_style", "packaging")
LICENSE_STATUSES = ("unknown", "owned", "licensed", "restricted")
CONSENT_STATUSES = ("unknown", "granted", "denied")
TEMPLATE_STATUSES = ("draft", "adopted", "retired")
PROVENANCES = ("measured", "fitted", "specified", "unknown")
VOICE_PROVIDERS = ("mimo-tts", "fish-audio", "index-tts")
SAMPLE_EXTS = {".mp4", ".mov", ".mkv", ".webm"}

RESOURCE_KEYS = {
    "schema", "id", "kind", "title", "files", "origin", "license", "tags", "notes",
    "voice", "consent", "font",
}
TEMPLATE_KEYS = {
    "schema", "id", "version", "kind", "title", "canvas", "params", "samples", "status",
    "adoption", "notes",
}
SAMPLE_KEYS = {
    "schema", "id", "title", "file", "canvas", "demonstrates", "not_reusable", "templates",
    "notes",
}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class Report:
    def __init__(self, root: Path):
        self.root = root
        self.errors: list[dict] = []
        self.warnings: list[dict] = []

    def _rel(self, path) -> str:
        try:
            return Path(path).relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    def error(self, path, code, message):
        self.errors.append({"path": self._rel(path), "code": code, "message": message})

    def warn(self, path, code, message):
        self.warnings.append({"path": self._rel(path), "code": code, "message": message})


def _load(path: Path, schema: str, keys: set, report: Report):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        report.error(path, "unreadable", f"无法解析: {exc}")
        return None
    if not isinstance(data, dict):
        report.error(path, "not_object", "顶层必须是 JSON object")
        return None
    if data.get("schema") != schema:
        report.error(path, "schema", f"schema 必须是 {schema}")
        return None
    unknown = sorted(set(data) - keys)
    if unknown:
        report.error(path, "unknown_keys", f"未知字段: {', '.join(unknown)}")
    return data


def _nonempty_str(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def resolve_inside(root: Path, base: Path, rel) -> Path | None:
    """Resolve a record-relative path; None when it is absolute or escapes the library."""
    if not _nonempty_str(rel) or Path(rel).is_absolute():
        return None
    resolved = (base / rel).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        return None
    return resolved


def _check_resource(path: Path, data: dict, report: Report) -> dict | None:
    rdir = path.parent
    kind = rdir.parent.name
    if data.get("id") != rdir.name or not ID_RE.match(str(data.get("id", ""))):
        report.error(path, "id", f"id 必须与目录名 {rdir.name} 一致，且只用小写字母、数字、. _ -")
        return None
    if data.get("kind") != kind or kind not in RESOURCE_EXTS:
        report.error(path, "kind", f"kind 必须是所在目录 {kind}，且属于 {sorted(RESOURCE_EXTS)}")
        return None
    if not _nonempty_str(data.get("title")):
        report.error(path, "title", "缺少 title")
    files = data.get("files", [])
    if not isinstance(files, list):
        report.error(path, "files", "files 必须是数组")
        files = []
    resolved = []
    for index, item in enumerate(files):
        rel = item.get("path") if isinstance(item, dict) else None
        target = resolve_inside(report.root, rdir, rel)
        if target is None:
            report.error(path, "file_path", f"files[{index}].path 必须是库内相对路径")
            continue
        if not _nonempty_str(item.get("role")):
            report.error(path, "file_role", f"files[{index}] 缺少 role")
        if target.suffix.lower() not in RESOURCE_EXTS[kind]:
            report.error(path, "file_ext", f"files[{index}] 扩展名不适用于 {kind}: {target.name}")
        if not target.is_file():
            report.error(path, "file_missing", f"文件不存在: {rel}")
            continue
        resolved.append({"role": item.get("role"), "path": str(target), **file_identity(target)})
    if kind != "voice" and not files:
        report.error(path, "files", f"{kind} 资源至少需要一个文件")
    licence = data.get("license")
    status = licence.get("status") if isinstance(licence, dict) else None
    if status not in LICENSE_STATUSES:
        report.error(path, "license", f"license.status 必须是 {LICENSE_STATUSES} 之一")
    elif status in {"unknown", "restricted"}:
        report.warn(path, f"license_{status}", f"授权状态为 {status}，交付前需要人工确认")
    if kind == "voice":
        voice = data.get("voice")
        if not isinstance(voice, dict) or voice.get("provider") not in VOICE_PROVIDERS:
            report.error(path, "voice", f"voice.provider 必须是 {VOICE_PROVIDERS} 之一")
        elif not (_nonempty_str(voice.get("voice_id")) or files):
            report.error(path, "voice", "voice 资源需要 voice.voice_id 或一个参考音频文件")
        if files:
            consent = data.get("consent")
            consent_status = consent.get("status") if isinstance(consent, dict) else None
            if consent_status not in CONSENT_STATUSES:
                report.error(path, "consent", f"参考音频需要 consent.status（{CONSENT_STATUSES}）")
            elif consent_status != "granted":
                report.warn(path, f"consent_{consent_status}", "参考音频的声音授权未确认")
    return {"id": data["id"], "kind": kind, "title": data.get("title", ""),
            "license": status, "files": resolved, "record": str(path)}


def _param_values(node, where="params"):
    """Yield (where, dict) for every nested dict in a template's params."""
    if isinstance(node, dict):
        yield where, node
        for key, value in node.items():
            yield from _param_values(value, f"{where}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _param_values(value, f"{where}[{index}]")


def _check_rect(path, where, rect, canvas, report):
    fields = ("x", "y", "width", "height")
    if not isinstance(rect, dict) or not all(isinstance(rect.get(f), int) for f in fields):
        report.error(path, "rect", f"{where} 需要整数 x / y / width / height")
        return
    if (rect["x"] < 0 or rect["y"] < 0 or rect["width"] <= 0 or rect["height"] <= 0
            or rect["x"] + rect["width"] > canvas["width"]
            or rect["y"] + rect["height"] > canvas["height"]):
        report.error(path, "rect_outside_canvas", f"{where} 超出画布 {canvas['width']}x{canvas['height']}")


def _check_template(path: Path, data: dict, report: Report) -> dict | None:
    vdir = path.parent
    tid, kind = vdir.parent.name, vdir.parent.parent.name
    version = data.get("version")
    if data.get("id") != tid or not ID_RE.match(str(tid)):
        report.error(path, "id", f"id 必须与目录名 {tid} 一致")
        return None
    if not isinstance(version, int) or isinstance(version, bool) or version < 1 or vdir.name != f"v{version}":
        report.error(path, "version", f"version 必须是正整数，且目录名为 v<version>（当前 {vdir.name}）")
        return None
    if data.get("kind") != kind or kind not in TEMPLATE_KINDS:
        report.error(path, "kind", f"kind 必须是所在目录 {kind}，且属于 {TEMPLATE_KINDS}")
        return None
    canvas = data.get("canvas")
    if not (isinstance(canvas, dict) and all(isinstance(canvas.get(k), int) and canvas[k] > 0
                                             for k in ("width", "height"))):
        report.error(path, "canvas", "canvas 需要正整数 width / height")
        canvas = None
    params = data.get("params")
    if not isinstance(params, dict):
        report.error(path, "params", "params 必须是 object")
        params = {}
    refs = []
    for where, node in _param_values(params):
        if "provenance" in node:
            if node["provenance"] not in PROVENANCES or "value" not in node:
                report.error(path, "provenance", f"{where} 需要 value 与 provenance（{PROVENANCES}）")
        if "resource" in node:
            refs.append((where, node["resource"]))
    if kind == "subtitle_style":
        font = params.get("font")
        if not (isinstance(font, dict) and (_nonempty_str(font.get("resource")) or _nonempty_str(font.get("family")))):
            report.error(path, "font", "subtitle_style 需要 params.font.resource 或 params.font.family")
        if not isinstance(params.get("size_px"), dict):
            report.error(path, "size_px", "subtitle_style 需要 params.size_px")
        band = params.get("band", {}).get("value") if isinstance(params.get("band"), dict) else None
        if band is not None and canvas and not (
            isinstance(band, dict) and 0 <= band.get("y_top", -1) < band.get("y_bot", -1) <= canvas["height"]
        ):
            report.error(path, "band", "params.band.value 需要 0 <= y_top < y_bot <= 画布高度")
    if kind == "packaging":
        layers = params.get("layers")
        if not isinstance(layers, list) or not layers:
            report.error(path, "layers", "packaging 需要非空 params.layers")
            layers = []
        names = [layer.get("name") for layer in layers if isinstance(layer, dict)]
        if len(names) != len(set(names)) or not all(_nonempty_str(n) for n in names):
            report.error(path, "layers", "每个图层需要唯一的 name")
        for index, layer in enumerate(layers):
            if canvas and isinstance(layer, dict):
                _check_rect(path, f"params.layers[{index}].rect", layer.get("rect"), canvas, report)
        if canvas and "safe_rect" in params:
            _check_rect(path, "params.safe_rect", params["safe_rect"], canvas, report)
    status = data.get("status")
    if status not in TEMPLATE_STATUSES:
        report.error(path, "status", f"status 必须是 {TEMPLATE_STATUSES} 之一")
    adoption = data.get("adoption")
    if status == "adopted":
        if not (isinstance(adoption, dict) and DATE_RE.match(str(adoption.get("date", "")))
                and all(_nonempty_str(adoption.get(k)) for k in ("by", "statement", "scope"))):
            report.error(path, "adoption", "adopted 模板需要 adoption.date(YYYY-MM-DD) / by / statement / scope")
    return {"id": tid, "version": version, "kind": kind, "title": data.get("title", ""),
            "status": status, "canvas": canvas, "refs": refs,
            "samples": data.get("samples", []), "adoption": adoption, "record": str(path)}


def _check_sample(path: Path, data: dict, report: Report) -> dict | None:
    sdir = path.parent
    if data.get("id") != sdir.name or not ID_RE.match(sdir.name):
        report.error(path, "id", f"id 必须与目录名 {sdir.name} 一致")
        return None
    for key in ("title", "not_reusable"):
        if not _nonempty_str(data.get(key)):
            report.error(path, key, f"缺少 {key}")
    if not (isinstance(data.get("demonstrates"), list) and data["demonstrates"]
            and all(_nonempty_str(x) for x in data["demonstrates"])):
        report.error(path, "demonstrates", "demonstrates 需要非空字符串数组")
    rel = data.get("file", {}).get("path") if isinstance(data.get("file"), dict) else None
    target = None
    if _nonempty_str(rel) and Path(rel).is_absolute():
        target = Path(rel)
        if not target.is_file():
            report.warn(path, "sample_offline", f"样片不在本机: {rel}")
            target = None
    else:
        target = resolve_inside(report.root, sdir, rel)
        if target is None:
            report.error(path, "file_path", "file.path 必须是库内相对路径或绝对路径")
        elif not target.is_file():
            report.error(path, "file_missing", f"样片不存在: {rel}")
            target = None
    if target is not None and target.suffix.lower() not in SAMPLE_EXTS:
        report.error(path, "file_ext", f"样片扩展名不支持: {target.name}")
    return {"id": data["id"], "title": data.get("title", ""), "file": str(target) if target else None,
            "templates": data.get("templates", []), "record": str(path)}


def _check_links(index: dict, report: Report):
    resources = {r["id"]: r for r in index["resources"]}
    templates = {f"{t['id']}@v{t['version']}": t for t in index["templates"]}
    samples = {s["id"] for s in index["samples"]}
    expected_kind = {"params.font": "font"}
    for template in index["templates"]:
        path = Path(template["record"])
        for where, ref in template["refs"]:
            resource = resources.get(ref)
            want = expected_kind.get(where) or ("image" if where.endswith(".image") else None)
            if resource is None:
                report.error(path, "resource_missing", f"{where} 引用的资源不存在: {ref}")
            elif want and resource["kind"] != want:
                report.error(path, "resource_kind", f"{where} 需要 {want} 资源，{ref} 是 {resource['kind']}")
        for sample_id in template["samples"]:
            if sample_id not in samples:
                report.error(path, "sample_missing", f"引用的样片不存在: {sample_id}")
        snapshot = (template["adoption"] or {}).get("resources") if isinstance(template["adoption"], dict) else None
        for res_id, files in (snapshot or {}).items():
            current = {Path(f["path"]).relative_to(report.root.resolve()).as_posix(): f
                       for f in resources.get(res_id, {}).get("files", [])}
            for recorded in files if isinstance(files, list) else []:
                now = current.get(recorded.get("path"))
                if now is None or (now["size"], now["mtime_ns"]) != (recorded.get("size"), recorded.get("mtime_ns")):
                    report.warn(path, "changed_since_adoption",
                                f"采用后资源已变化: {res_id} {recorded.get('path')}，需重新采用或出新版本")
    for sample in index["samples"]:
        for ref in sample["templates"]:
            if ref not in templates:
                report.error(Path(sample["record"]), "template_missing", f"引用的模板不存在: {ref}")


def scan_library(root) -> tuple[dict, Report]:
    """Load and validate every record under ``root``; never writes."""
    root = Path(root).resolve()
    report = Report(root)
    index = {"resources": [], "templates": [], "samples": []}
    meta = root / "library.json"
    if meta.is_file():
        _load(meta, LIBRARY_SCHEMA, {"schema", "name", "notes"}, report)
    else:
        report.warn(meta, "no_library_json", "缺少 library.json（仍会扫描资源、模板与样片）")
    for path in sorted(root.glob("resources/*/*/resource.json")):
        data = _load(path, RESOURCE_SCHEMA, RESOURCE_KEYS, report)
        entry = data and _check_resource(path, data, report)
        if entry:
            index["resources"].append(entry)
    for path in sorted(root.glob("templates/*/*/v*/template.json")):
        data = _load(path, TEMPLATE_SCHEMA, TEMPLATE_KEYS, report)
        entry = data and _check_template(path, data, report)
        if entry:
            index["templates"].append(entry)
    for path in sorted(root.glob("samples/*/sample.json")):
        data = _load(path, SAMPLE_SCHEMA, SAMPLE_KEYS, report)
        entry = data and _check_sample(path, data, report)
        if entry:
            index["samples"].append(entry)
    _check_links(index, report)
    return index, report


def _library_root(value):
    root = value or os.environ.get(LIBRARY_ENV)
    if not root:
        raise SystemExit(f"需要 --library-dir 或环境变量 {LIBRARY_ENV}")
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"库目录不存在: {root}")
    return root


def _print_issues(report: Report):
    for level, items in (("ERROR", report.errors), ("WARN", report.warnings)):
        for item in items:
            print(f"{level} {item['path']} [{item['code']}] {item['message']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="video-recap 资源库：只读列出、查看与校验。")
    ap.add_argument("--library-dir", help=f"库根目录（默认读取 {LIBRARY_ENV}）")
    sub = ap.add_subparsers(dest="command", required=True)
    list_cmd = sub.add_parser("list", help="列出资源、模板与样片")
    list_cmd.add_argument("--kind", help="只看某一类，如 bgm / font / subtitle_style")
    list_cmd.add_argument("--json", action="store_true")
    show_cmd = sub.add_parser("show", help="查看一个条目（模板可写 id 或 id@vN）")
    show_cmd.add_argument("id")
    check_cmd = sub.add_parser("check", help="校验整个库；有错误时退出码为 1")
    check_cmd.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    index, report = scan_library(_library_root(args.library_dir))

    if args.command == "check":
        if args.json:
            print(json.dumps({"ok": not report.errors, "errors": report.errors,
                              "warnings": report.warnings,
                              "counts": {k: len(v) for k, v in index.items()}},
                             ensure_ascii=False, indent=2))
        else:
            _print_issues(report)
            print(f"{len(report.errors)} error(s), {len(report.warnings)} warning(s); "
                  + ", ".join(f"{k} {len(v)}" for k, v in index.items()))
        return 1 if report.errors else 0

    rows = [{"type": "resource", "kind": r["kind"], "id": r["id"], "title": r["title"],
             "status": r["license"]} for r in index["resources"]]
    rows += [{"type": "template", "kind": t["kind"], "id": f"{t['id']}@v{t['version']}",
              "title": t["title"], "status": t["status"]} for t in index["templates"]]
    rows += [{"type": "sample", "kind": "sample", "id": s["id"], "title": s["title"],
              "status": "online" if s["file"] else "offline"} for s in index["samples"]]
    if args.command == "list":
        rows = [r for r in rows if not args.kind or r["kind"] == args.kind]
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            for r in rows:
                print(f"{r['type']:<9} {r['kind']:<15} {r['id']:<36} {r['status']:<10} {r['title']}")
        return 0

    matches = [e for group in index.values() for e in group
               if args.id in {e["id"], f"{e['id']}@v{e.get('version')}"}]
    if not matches:
        print(f"未找到: {args.id}", file=sys.stderr)
        return 1
    for entry in matches:
        record = json.loads(Path(entry["record"]).read_text(encoding="utf-8"))
        print(json.dumps({"record": entry["record"], **record,
                          **({"resolved_files": entry["files"]} if "files" in entry else {})},
                         ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
