"""Read-only JSON for the dashboard: overview, libraries, projects, runs and search.

Libraries come from ``library.scan_library``; runs from ``dashboard_runs``; every path in
and out is root-relative and passes ``dashboard_io.resolve_under``. A broken file becomes a
Chinese message on the card or view that owns it and never aborts the rest of the page.
"""
from __future__ import annotations

import re
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import library
import project_binding
from dashboard_io import (LIBRARY_FILE, PROJECT_FILE, RUN_FILE, Refused, dir_of, discover,
                          media_entry, nearest, read_object, rel_of, resolve_under)
from dashboard_runs import run_views
from dashboard_templates import param_rows, template_preview

ATTENTION_CODES = {"license_unknown", "license_restricted", "consent_unknown", "consent_denied",
                   "changed_since_adoption", "sample_offline"}
LICENCE_CODES = {"license_unknown", "license_restricted", "consent_unknown", "consent_denied"}
# What a binding problem means for the delivered file: a binding that cannot apply is danger.
SEVERITY = {"blocked": "danger", "library_error": "danger", "binding": "danger",
            "waiting": "todo", "licence": "warn", "library_warn": "warn", "unparseable": "warn"}
REF_RE = re.compile(r"^(?P<id>.+)@v?(?P<version>\d+)$")
MAX_NEXT = 3
MAX_HITS = 60


def _strings(value) -> list:
    """Hand-written list fields: keep only strings so one odd record cannot break a view."""
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def _canvas(value):
    """A record's canvas as positive integers, or None; never pass author data through raw."""
    if not isinstance(value, dict):
        return None
    width, height = value.get("width"), value.get("height")
    ok = all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in (width, height))
    return {"width": width, "height": height} if ok else None


def _href(kind: str, rel: str, view: str = "", **query) -> str:
    tail = f"/{view}" if view else ""
    qs = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in query.items() if v is not None)
    return f"#/{kind}/{quote(rel, safe='')}{tail}{'?' + qs if qs else ''}"


# --- library -----------------------------------------------------------------------------
def _scan(lib_dir: Path, cache: dict):
    key = str(lib_dir)
    if key not in cache:
        cache[key] = library.scan_library(lib_dir)
    return cache[key]


def _record_target(record_rel: str):
    """``(tab, id)`` of the library entry a record path belongs to."""
    parts = PurePosixPath(record_rel).parts
    if len(parts) >= 3 and parts[0] == "resources":
        return "resources", parts[2]
    if len(parts) >= 4 and parts[0] == "templates":
        return "templates", f"{parts[2]}@{parts[3]}"
    if len(parts) >= 2 and parts[0] == "samples":
        return "samples", parts[1]
    return "resources", None


def _library_summary(root: Path, rel: str, cache: dict) -> dict:
    lib_dir = dir_of(root, rel).resolve()
    meta, meta_error = read_object(lib_dir / LIBRARY_FILE)
    index, report = _scan(lib_dir, cache)
    return {
        "path": rel, "name": (meta or {}).get("name") or lib_dir.name, "error": meta_error,
        "counts": {k: len(v) for k, v in index.items()},
        "error_count": len(report.errors), "warning_count": len(report.warnings),
    }


def library_detail(root: Path, rel: str) -> dict:
    lib_dir = resolve_under(root, rel)
    if not (lib_dir / LIBRARY_FILE).is_file():
        raise Refused(404, "该目录不是资源库（缺少 library.json）")
    rel = rel_of(root, lib_dir)
    cache: dict = {}
    summary = _library_summary(root, rel, cache)
    index, report = _scan(lib_dir, cache)
    issues: dict = {}
    for level, items in (("error", report.errors), ("warn", report.warnings)):
        for item in items:
            issues.setdefault(item["path"], []).append({"level": level, **item})
    bound_by = _bindings_into(root, lib_dir, cache)

    def record(entry):
        record_rel = Path(entry["record"]).relative_to(lib_dir).as_posix()
        data, _ = read_object(Path(entry["record"]))
        return record_rel, data or {}

    samples = {}
    for sample in index["samples"]:
        record_rel, data = record(sample)
        samples[sample["id"]] = {
            "id": sample["id"], "title": sample["title"], "record": record_rel,
            "demonstrates": _strings(data.get("demonstrates")), "not_reusable": _text(data.get("not_reusable")),
            "templates": sample["templates"], "canvas": _canvas(data.get("canvas")),
            "file": media_entry(root, sample["file"]) if sample["file"] else None,
            "offline": sample["file"] is None, "issues": issues.pop(record_rel, []),
        }
    resources = []
    for res in index["resources"]:
        record_rel, data = record(res)
        resources.append({
            "id": res["id"], "kind": res["kind"], "title": res["title"], "record": record_rel,
            "license": data.get("license") if isinstance(data.get("license"), dict) else {},
            "consent": data.get("consent"), "voice": data.get("voice"), "origin": data.get("origin"),
            "tags": _strings(data.get("tags")), "notes": _text(data.get("notes")),
            "files": [{"role": f["role"], "name": Path(f["path"]).name, "size": f["size"],
                       "media": media_entry(root, f["path"])} for f in res["files"]],
            "bound_by": bound_by.get(("resource", res["id"]), []), "issues": issues.pop(record_rel, []),
        })
    by_id = {r["id"]: r for r in index["resources"]}
    templates = []
    for tpl in index["templates"]:
        record_rel, data = record(tpl)
        ref = f"{tpl['id']}@v{tpl['version']}"
        params = data.get("params", {})
        templates.append({
            "rows": param_rows(tpl["kind"], params, by_id),
            "preview": template_preview(root, tpl["kind"], tpl["canvas"], params, by_id),
            "id": tpl["id"], "version": tpl["version"], "ref": ref, "kind": tpl["kind"],
            "title": tpl["title"], "status": tpl["status"], "canvas": tpl["canvas"],
            "adoption": tpl["adoption"] if isinstance(tpl["adoption"], dict) else None,
            "params": params, "notes": data.get("notes", ""), "record": record_rel,
            "samples": [samples.get(s, {"id": s, "missing": True}) for s in tpl["samples"]],
            "bound_by": bound_by.get(("template", ref), []), "issues": issues.pop(record_rel, []),
        })
    leftover = [item for items in issues.values() for item in items]
    return {**summary, "resources": resources, "templates": templates,
            "samples": list(samples.values()), "issues": leftover}


# --- projects & bindings -----------------------------------------------------------------
def _resolve_ref(role: str, ref, index) -> dict:
    row = _check_ref(role, ref, index)
    row["severity"] = "ok" if row["status"] == "ok" else "danger"
    return row


def _check_ref(role: str, ref, index) -> dict:
    row = {"role": role, "ref": ref, "target": None}
    if not isinstance(ref, str) or not ref.strip():
        return {**row, "status": "invalid", "message": "绑定值必须是非空字符串"}
    if index is None:
        return {**row, "status": "no_library", "message": "未找到项目引用的资源库"}
    match = REF_RE.match(ref)
    if match:
        tpl = next((t for t in index["templates"]
                    if t["id"] == match["id"] and t["version"] == int(match["version"])), None)
        if tpl is None:
            return {**row, "status": "missing", "message": "库里没有这个模板版本，绑定不会生效"}
        row["target"] = {"type": "template", "ref": f"{tpl['id']}@v{tpl['version']}", "title": tpl["title"],
                         "kind": tpl["kind"], "status": tpl["status"], "canvas": tpl["canvas"]}
        if role in library.TEMPLATE_KINDS and tpl["kind"] != role:
            return {**row, "status": "kind_mismatch", "message": f"需要 {role} 模板，实际是 {tpl['kind']}"}
        if tpl["status"] != "adopted":
            return {**row, "status": "not_adopted", "message": f"模板状态为 {tpl['status']}，只有 adopted 能绑定，不会生效"}
        return {**row, "status": "ok", "message": "已解析"}
    res = next((r for r in index["resources"] if r["id"] == ref), None)
    if res is None:
        if any(t["id"] == ref for t in index["templates"]):
            return {**row, "status": "version_required", "message": "模板需要写成 id@vN"}
        return {**row, "status": "missing", "message": "库里没有这个资源，绑定不会生效"}
    row["target"] = {"type": "resource", "ref": res["id"], "title": res["title"], "kind": res["kind"],
                     "license": res["license"]}
    if role in library.RESOURCE_EXTS and res["kind"] != role:
        return {**row, "status": "kind_mismatch", "message": f"需要 {role} 资源，实际是 {res['kind']}"}
    return {**row, "status": "ok", "message": "已解析"}


def _application(pdir: Path) -> dict:
    """What ``recap.py --project`` would apply, resolved with no ambient env or CLI flags."""
    args = Namespace(tts_provider="auto", mimo_tts_voice=None, voice_ref=None, material_library_dir=None)
    try:
        resolved = project_binding.resolve_project(pdir, args, environ={})
    except project_binding.BindingError as exc:
        return {"ok": False, "message": str(exc.code)}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
        return {"ok": False, "message": f"项目绑定无法解析: {exc}"}
    return {"ok": True, "message": "按这份绑定运行时会下发以下设置", "env": resolved["env"],
            "arg_updates": resolved["arg_updates"]}


def _project(root: Path, rel: str, cache: dict) -> dict:
    pdir = dir_of(root, rel)
    data, error = read_object(pdir / PROJECT_FILE)
    base = {"path": rel, "name": pdir.name or root.name, "error": error, "schema": None,
            "library": None, "bindings": [], "lib_dir": None}
    if error:
        return base
    raw = data.get("library")
    lib_dir = (pdir / raw).resolve() if isinstance(raw, str) and raw.strip() else None
    found = bool(lib_dir) and (lib_dir / LIBRARY_FILE).is_file()
    index = _scan(lib_dir, cache)[0] if found else None
    bindings = data.get("bindings")
    rows = []
    for role, value in (bindings.items() if isinstance(bindings, dict) else []):
        for ref in (value if isinstance(value, list) else [value]):
            rows.append(_resolve_ref(role, ref, index))
    return {**base, "name": data.get("name") or base["name"], "schema": data.get("schema"),
            "error": "bindings 必须是 object" if bindings is not None and not isinstance(bindings, dict) else None,
            "library": {"raw": raw, "path": str(lib_dir) if lib_dir else None, "found": found,
                        "rel": rel_of(root, lib_dir) if found else None},
            "bindings": rows, "lib_dir": str(lib_dir) if found else None}


def _bindings_into(root: Path, lib_dir: Path, cache: dict) -> dict:
    """``{(type, ref): [{path, name, role}]}`` for every discovered project bound to lib_dir."""
    used: dict = {}
    for rel in discover(root)["projects"]:
        project = _project(root, rel, cache)
        if project["lib_dir"] != str(lib_dir):
            continue
        for row in project["bindings"]:
            if row["target"]:
                used.setdefault((row["target"]["type"], row["target"]["ref"]), []).append(
                    {"path": rel, "name": project["name"], "role": row["role"]})
    return used


def _run_list(root: Path, found: dict) -> list:
    runs_set, projects_set = set(found["runs"]), set(found["projects"])
    runs = []
    for rel in found["runs"]:
        views = run_views(root, rel, runs_set)
        brief = {k: v for k, v in views.items() if k not in {"views", "state", "qc"}}
        brief["project"] = nearest(rel, projects_set, include_self=True)
        runs.append(brief)
    return runs


def project_detail(root: Path, rel: str) -> dict:
    pdir = resolve_under(root, rel)
    if not (pdir / PROJECT_FILE).is_file():
        raise Refused(404, "该目录没有 recap_project.json")
    rel = rel_of(root, pdir)
    project = _project(root, rel, {})
    project.pop("lib_dir")
    runs = [r for r in _run_list(root, discover(root)) if r["project"] == rel]
    return {**project, "application": _application(pdir), "runs": runs}


def run_detail(root: Path, rel: str) -> dict:
    rdir = resolve_under(root, rel)
    if not (rdir / RUN_FILE).is_file():
        raise Refused(404, "该目录没有 recap_run_manifest.json")
    rel = rel_of(root, rdir)
    found = discover(root)
    runs = set(found["runs"])
    detail = run_views(root, rel, runs)
    children = [r for r in _run_list(root, found) if r["parent"] == rel]
    return {**detail, "project": nearest(rel, set(found["projects"]), include_self=True),
            "children": children}


# --- overview ----------------------------------------------------------------------------
def _attention(libraries, reports, projects, runs) -> list:
    items = []
    for lib in libraries:
        if lib["error"]:
            items.append({"kind": "library_error", "title": lib["name"],
                          "message": f"library.json {lib['error']}", "href": _href("library", lib["path"]),
                          "ask": f"请修复资源库「{lib['name']}」的 library.json：{lib['error']}"})
        report = reports[lib["path"]]
        for level, issues in (("error", report.errors), ("warn", report.warnings)):
            for issue in issues:
                if level == "warn" and issue["code"] not in ATTENTION_CODES:
                    continue
                tab, entry = _record_target(issue["path"])
                kind = ("library_error" if level == "error"
                        else "licence" if issue["code"] in LICENCE_CODES else "library_warn")
                items.append({
                    "kind": kind, "code": issue["code"],
                    "title": f"{lib['name']} · {entry or issue['path']}", "message": issue["message"],
                    "href": _href("library", lib["path"], tab, id=entry),
                    "ask": f"请处理资源库「{lib['name']}」里 {issue['path']} 的问题：{issue['message']}"})
    for project in projects:
        if project["error"]:
            items.append({"kind": "binding", "title": project["name"],
                          "message": project["error"], "href": _href("project", project["path"]),
                          "ask": f"请修复项目「{project['name']}」的 recap_project.json：{project['error']}"})
        for row in project["bindings"]:
            if row["status"] != "ok":
                items.append({"kind": "binding", "code": row["status"],
                              "title": f"{project['name']} · {row['role']}",
                              "message": f"{row['ref']}：{row['message']}", "href": _href("project", project["path"]),
                              "ask": f"请修复项目「{project['name']}」的 {row['role']} 绑定 {row['ref']}：{row['message']}"})
    for run in runs:
        if run["blockers"]:
            items.append({"kind": "blocked", "title": run["path"],
                          "message": f"QC 共 {run['blockers']} 个阻断项", "href": _href("run", run["path"], "qc"),
                          "ask": f"请查看 {run['work_dir']} 的 QC 阻断项，修复后重新合成。"})
        if run["next_pause"]:
            artifact = run["next_pause"]["artifact"]
            view = "cut" if artifact == "clip_plan.json" else "narration"
            items.append({"kind": "waiting", "title": run["path"],
                          "message": f"等你：{run['next_pause']['hint']}",
                          "href": _href("run", run["path"], view),
                          "ask": f"请继续 {run['work_dir']} 这次运行：写 {artifact}（{run['next_pause']['hint']}）。"})
        for name in run["unparseable"]:
            items.append({"kind": "unparseable", "title": run["path"], "message": f"{name} 无法解析",
                          "href": _href("run", run["path"]),
                          "ask": f"{run['work_dir']} 里的 {name} 无法解析，请检查。"})
        if run["lock_attention"]:
            items.append({"kind": "licence", "title": run["path"],
                          "message": f"resource_lock 有 {run['lock_attention']} 条需要注意",
                          "href": _href("run", run["path"], "resources"),
                          "ask": f"请核对 {run['work_dir']} 的 resource_lock.json 里需要注意的资源。"})
    for item in items:
        item["severity"] = SEVERITY[item["kind"]]
    return items


NEXT_ORDER = ("blocked", "waiting", "licence", "library_error", "binding", "unparseable", "library_warn")


def _worst(items) -> str:
    """The colour the next-steps card takes: danger only when something must be fixed."""
    severities = {item["severity"] for item in items}
    return next((s for s in ("danger", "todo", "warn") if s in severities), "ok")


def overview(root: Path) -> dict:
    found = discover(root)
    cache: dict = {}
    libraries = [_library_summary(root, rel, cache) for rel in found["libraries"]]
    reports = {lib["path"]: _scan(dir_of(root, lib["path"]).resolve(), cache)[1] for lib in libraries}
    runs = _run_list(root, found)
    projects = []
    for rel in found["projects"]:
        project = _project(root, rel, cache)
        project.pop("lib_dir")
        project["runs"] = [r["path"] for r in runs if r["project"] == rel and r["parent"] is None]
        project["unresolved"] = sum(1 for row in project["bindings"] if row["status"] != "ok")
        projects.append(project)
    attention = _attention(libraries, reports, projects, runs)
    ranked = sorted(attention, key=lambda item: NEXT_ORDER.index(item["kind"]))
    counts = {kind: sum(1 for item in attention if item["kind"] == kind) for kind in NEXT_ORDER}
    totals = {k: sum(lib["counts"][k] for lib in libraries) for k in ("resources", "templates", "samples")}
    return {
        "root": str(root), "name": root.name,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "counts": {"libraries": len(libraries), "projects": len(projects), "runs": len(runs), **totals},
        "next": ranked[:MAX_NEXT], "next_counts": counts,
        "next_severity": _worst(ranked[:MAX_NEXT]),
        "libraries": libraries, "projects": projects, "runs": runs,
        "attention": ranked, "warnings": found["warnings"],
    }


# --- search ------------------------------------------------------------------------------
def search(root: Path, q) -> dict:
    """Case-insensitive substring search over narration, clip reasons and library entries."""
    query = (q or "").strip()
    hits: list = []
    if not query:
        return {"q": query, "hits": hits}
    needle = query.casefold()

    def add(group, title, text, href):
        pos = text.casefold().find(needle)
        if pos < 0 or len(hits) >= MAX_HITS:
            return
        start = max(0, pos - 24)
        prefix = "…" if start else ""
        hits.append({"group": group, "title": title, "href": href,
                     "snippet": prefix + text[start:pos + len(query) + 60],
                     "at": len(prefix) + pos - start, "len": len(query)})

    found = discover(root)
    runs = set(found["runs"])
    for rel in found["runs"]:
        views = run_views(root, rel, runs)["views"]
        for seg in views["narration"].get("segments", []):
            add("旁白", f"{rel} · {seg['start']:.1f}s", seg["text"], _href("run", rel, "narration", i=seg["index"]))
        for clip in views["cut"].get("clips", []):
            add("剪辑", f"{rel} · 片段 {clip['id']}", clip["reason"], _href("run", rel, "cut", clip=clip["index"]))
    cache: dict = {}
    for rel in found["libraries"]:
        index, _ = _scan(dir_of(root, rel).resolve(), cache)
        for res in index["resources"]:
            add("资源", f"{res['kind']} · {res['id']}", f"{res['id']} {res['title']}",
                _href("library", rel, "resources", id=res["id"]))
        for tpl in index["templates"]:
            ref = f"{tpl['id']}@v{tpl['version']}"
            add("模板", f"{tpl['kind']} · {ref}", f"{ref} {tpl['title']}", _href("library", rel, "templates", id=ref))
        for sample in index["samples"]:
            add("样片", sample["id"], f"{sample['id']} {sample['title']}", _href("library", rel, "samples", id=sample["id"]))
    return {"q": query, "hits": hits}
