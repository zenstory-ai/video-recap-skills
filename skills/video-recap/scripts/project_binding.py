"""Resolve a ``recap_project.json`` binding into the settings stage skills already accept.

Only video-recap reads the library. A bound subtitle style becomes ``SUBTITLE_*`` env for
video-assemble, a bound voice becomes the voiceover provider / voice arguments, a bound
BGM becomes ``BGM_PATH``. Stage skills keep seeing concrete paths and values; they never
learn about templates. Anything the caller already set explicitly must agree with the
binding — a silent override would make the delivered file disagree with the project.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import library as library_lib

PROJECT_FILE = "recap_project.json"
PROJECT_SCHEMA = "video-recap.project.v1"
PROJECT_KEYS = {"schema", "name", "library", "bindings", "notes"}
BINDING_KINDS = ("subtitle_style", "packaging", "voice", "bgm")


class BindingError(SystemExit):
    """A project binding that cannot be applied; exits before any stage runs."""

    def __init__(self, message):
        super().__init__(f"项目绑定无法使用: {message}")


def project_path(value) -> Path:
    path = Path(value).expanduser().resolve()
    if path.is_dir():
        path = path / PROJECT_FILE
    if not path.is_file():
        raise BindingError(f"找不到 {path}")
    return path


def _value(params, key):
    node = params.get(key)
    return node.get("value") if isinstance(node, dict) else None


def _record(entry):
    return json.loads(Path(entry["record"]).read_text(encoding="utf-8"))


def _subtitle_env(template, record, resources):
    params = record["params"]
    canvas = record["canvas"]
    env = {
        "SUBTITLE_PLAY_RES_X": canvas["width"],
        "SUBTITLE_PLAY_RES_Y": canvas["height"],
        "SUBTITLE_FONT_SIZE": _value(params, "size_px"),
        "SUBTITLE_OUTLINE": _value(params, "outline_px"),
        "SUBTITLE_SHADOW": _value(params, "shadow_px"),
        "SUBTITLE_PRIMARY_COLOR": _value(params, "primary_color"),
        "SUBTITLE_OUTLINE_COLOR": _value(params, "outline_color"),
        "SUBTITLE_MAX_CHARS": _value(params, "max_chars"),
        "SUBTITLE_MAX_LINES": _value(params, "max_lines"),
    }
    font = params["font"]
    if font.get("resource"):
        resource = resources[font["resource"]]
        env["SUBTITLE_FONT_NAME"] = (_record(resource).get("font") or {}).get("family")
        env["SUBTITLE_FONT_FILE"] = resource["files"][0]["path"]
    else:
        env["SUBTITLE_FONT_NAME"] = font["family"]
    band = _value(params, "band")
    if band:
        env["SUBTITLE_ALIGNMENT"] = 2
        env["SUBTITLE_MARGIN_V"] = canvas["height"] - band["y_bot"]
    return {k: str(v) for k, v in env.items() if v is not None}


def _voice_updates(resource, args):
    record = _record(resource)
    voice = record.get("voice") or {}
    provider = voice.get("provider")
    consent = (record.get("consent") or {}).get("status")
    if resource["files"] and consent == "denied":
        raise BindingError(f"音色 {resource['id']} 的参考音频声音授权为 denied")
    if args.tts_provider not in ("auto", provider):
        raise BindingError(f"音色 {resource['id']} 属于 {provider}，与 --tts-provider {args.tts_provider} 冲突")
    updates, env = {"tts_provider": provider}, {}
    if provider == "mimo-tts":
        if resource["files"]:
            updates["voice_ref"] = resource["files"][0]["path"]
        else:
            updates["mimo_tts_voice"] = voice.get("voice_id")
    elif provider == "fish-audio":
        env["FISH_TTS_REFERENCE_ID"] = voice.get("voice_id")
    else:
        env["INDEX_TTS_VOICE"] = voice.get("voice_id")
    return updates, env


def _same_setting(key, current, value):
    if key.endswith(("_PATH", "_FILE", "VOICE_REF")):
        return Path(current).expanduser().resolve() == Path(value).expanduser().resolve()
    try:
        return float(current) == float(value)
    except ValueError:
        return current == value


def _check_env_conflicts(env, environ):
    for key, value in env.items():
        current = environ.get(key, "").strip()
        if current and not _same_setting(key, current, value):
            raise BindingError(f"{key}={current} 与项目绑定的 {value} 冲突；删掉其中一处")


def _check_arg_conflicts(updates, args):
    for key, value in updates.items():
        current = getattr(args, key, None)
        if key == "tts_provider" or current in (None, ""):
            continue
        if str(current) != str(value):
            raise BindingError(f"--{key.replace('_', '-')} {current} 与项目绑定的 {value} 冲突")


def resolve_project(value, args, environ=None, *, include_voice=True) -> dict:
    """Load and validate a project; return what to apply plus the lock's project block."""
    environ = os.environ if environ is None else environ
    path = project_path(value)
    try:
        project = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise BindingError(f"{path} 无法解析: {exc}") from None
    if not isinstance(project, dict) or project.get("schema") != PROJECT_SCHEMA:
        raise BindingError(f"{path} 的 schema 必须是 {PROJECT_SCHEMA}")
    unknown = sorted(set(project) - PROJECT_KEYS)
    bindings = project.get("bindings") or {}
    unknown += sorted(f"bindings.{k}" for k in set(bindings) - set(BINDING_KINDS))
    if unknown:
        raise BindingError(f"{path} 有未知字段: {', '.join(unknown)}")
    library_root = (path.parent / str(project.get("library", ""))).resolve()
    if not (library_root / "library.json").is_file():
        raise BindingError(f"库目录无效（缺少 library.json）: {library_root}")
    index, report = library_lib.scan_library(library_root)
    resources = {r["id"]: r for r in index["resources"]}
    templates = {f"{t['id']}@v{t['version']}": t for t in index["templates"]}
    errors = {}
    for issue in report.errors:
        errors.setdefault(issue["path"], issue["message"])

    def require_valid(entry, label):
        rel = Path(entry["record"]).relative_to(library_root).as_posix()
        if rel in errors:
            raise BindingError(f"{label}: {errors[rel]}（先运行 library.py check）")

    def template(kind):
        ref = bindings.get(kind)
        if not ref:
            return None
        entry = templates.get(ref)
        if entry is None or entry["kind"] != kind:
            raise BindingError(f"{kind} 模板 {ref} 不存在或无效（先运行 library.py check）")
        if entry["status"] != "adopted":
            raise BindingError(f"{kind} 模板 {ref} 的状态是 {entry['status']}，只有 adopted 能绑定")
        require_valid(entry, ref)
        for _, rid in entry["refs"]:
            if rid not in resources:
                raise BindingError(f"{ref} 引用的资源 {rid} 不存在或无效（先运行 library.py check）")
            require_valid(resources[rid], f"{ref} 引用的资源 {rid}")
        return entry

    def resource(kind):
        rid = bindings.get(kind)
        if not rid:
            return None
        entry = resources.get(rid)
        if entry is None or entry["kind"] != kind:
            raise BindingError(f"{kind} 资源 {rid} 不存在或无效（先运行 library.py check）")
        require_valid(entry, f"{kind} 资源 {rid}")
        return entry

    env, updates, used_templates = {}, {}, []
    subtitle = template("subtitle_style")
    if subtitle:
        env.update(_subtitle_env(subtitle, _record(subtitle), resources))
        used_templates.append({"role": "subtitle_style", "id": subtitle["id"],
                               "version": subtitle["version"], "status": subtitle["status"],
                               "canvas": subtitle["canvas"]})
    packaging = template("packaging")
    if packaging:
        used_templates.append({"role": "packaging", "id": packaging["id"],
                               "version": packaging["version"], "status": packaging["status"],
                               "canvas": packaging["canvas"]})
    voice = resource("voice") if include_voice else None
    if voice:
        voice_updates, voice_env = _voice_updates(voice, args)
        updates.update(voice_updates)
        env.update(voice_env)
    bgm = resource("bgm")
    if bgm and getattr(args, "audio_mode", "narration") == "adopted-packet-copy":
        raise BindingError("adopted-packet-copy 冻结原音轨，不能再绑定 BGM；去掉 bindings.bgm 或换声音模式")
    if bgm:
        env["BGM_PATH"] = bgm["files"][0]["path"]
    material_env = environ.get("VIDEO_RECAP_MATERIAL_LIBRARY_DIR", "").strip()
    if (material_env and not getattr(args, "material_library_dir", None)
            and Path(material_env).expanduser().resolve() != library_root):
        raise BindingError(
            f"VIDEO_RECAP_MATERIAL_LIBRARY_DIR={material_env} 与项目的库 {library_root} 不是同一个目录；"
            "素材库与资源库共用根目录，删掉其中一处"
        )
    _check_env_conflicts(env, environ)
    _check_env_conflicts(
        {name: updates[key] for key, name in (("mimo_tts_voice", "MIMO_TTS_VOICE"), ("voice_ref", "VOICE_REF"))
         if key in updates},
        environ,
    )
    _check_arg_conflicts(updates, args)
    return {
        "path": str(path),
        "name": project.get("name", ""),
        "library": str(library_root),
        "env": env,
        "arg_updates": updates,
        "templates": used_templates,
        "resources": [],
        "packaging": packaging and {"record": _record(packaging), "resources": resources},
    }


def apply_project(resolved, args, environ=None) -> None:
    """Write the resolved settings where the stage skills will read them."""
    environ = os.environ if environ is None else environ
    for key, value in resolved["env"].items():
        environ[key] = value
    bound = set()
    for key, value in resolved["arg_updates"].items():
        if getattr(args, key, None) != value:
            bound.add(key)
        setattr(args, key, value)
    if not getattr(args, "material_library_dir", None):
        args.material_library_dir = resolved["library"]
        bound.add("material_library_dir")
    args._bound_from_project = frozenset(bound)
    args.resolved_project = resolved


def check_canvas(resolved, width, height) -> None:
    """Templates only apply to the canvas they were calibrated on."""
    for template in resolved["templates"]:
        canvas = template["canvas"]
        if (canvas["width"], canvas["height"]) != (width, height):
            raise BindingError(
                f"{template['role']} 模板 {template['id']}@v{template['version']} 按 "
                f"{canvas['width']}x{canvas['height']} 校准，成片画布是 {width}x{height}；换一个同画幅的模板"
            )


PACKAGING_LAYERS = "packaging_layers.json"
_WRITTEN_BY = "video-recap --project"


def sync_packaging_layers(work_dir, resolved) -> None:
    """Write the bound packaging template's layers for video-assemble, or retire our old copy.

    A caller-authored ``packaging_layers.json`` (no ``written_by`` marker) is left alone.
    """
    path = Path(work_dir) / PACKAGING_LAYERS
    packaging = (resolved or {}).get("packaging")
    if not packaging:
        if path.exists():
            try:
                plan = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                plan = None
            ours = isinstance(plan, dict) and plan.get("written_by") == _WRITTEN_BY
            if ours:
                path.unlink()
        return
    record, resources = packaging["record"], packaging["resources"]
    layers = [{"name": layer["name"],
               "path": resources[layer["image"]["resource"]]["files"][0]["path"],
               "rect": layer["rect"]}
              for layer in record["params"]["layers"]]
    path.write_text(json.dumps({
        "written_by": _WRITTEN_BY,
        "template": {"id": record["id"], "version": record["version"]},
        "canvas": record["canvas"],
        "layers": layers,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
