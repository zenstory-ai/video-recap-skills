"""Server-side views of one recap work_dir for the read-only dashboard.

Each stage view parses its own artifact (clip_plan_validated.json, narration.json,
timeline.json, QC reports, resource_lock.json) into plain rows the UI only lays out.
A missing artifact is "that stage has not run"; an unreadable one becomes
``{"status": "unparseable", "error", "raw"}`` for that view alone. Run state itself comes
from ``recap_inspect.cmd_state``. Media is only what the run declares: the
``assembly_manifest.json`` ``final_output`` and the cut stage's ``edited_source.mp4``.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath

import recap_inspect
from dashboard_io import (RUN_FILE, dir_of, media_entry, nearest, raw_text,
                          read_json, read_object, unreadable_reason)

# Files cmd_state parses; each must stay under the JSON cap before it is handed over.
QC_FILES = (
    ("final_qc.json", "成片 QC"),
    ("golden_eval.json", "黄金评估"),
    ("assembly_qc.json", "合成 QC"),
    ("mimo_qc.json", "MiMo 复核"),
)
STAGES = (("home", "概况"), ("understanding", "理解"), ("cut", "剪辑"), ("narration", "旁白"),
          ("film", "成片"), ("qc", "QC"), ("resources", "资源"))
MAX_FINDINGS = 20


def _fallback(path: Path, error: str) -> dict:
    return {"status": "unparseable", "error": error, "raw": raw_text(path)}


def _num(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"不是数字: {value!r}")
    return float(value)


def run_state(rdir: Path):
    # recap_inspect reads these without limits; vet every JSON it might open first.
    for path in sorted(rdir.glob("*.json")):
        reason = unreadable_reason(path)
        if reason:
            return None, f"{path.name} {reason}"
    try:
        return recap_inspect.cmd_state(rdir), None
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return None, f"无法解析运行状态: {exc}"


def _audio_mode(rdir: Path) -> str:
    manifest, _ = read_object(rdir / RUN_FILE)
    audio = (manifest or {}).get("audio")
    mode = audio.get("mode") if isinstance(audio, dict) else None
    return mode if isinstance(mode, str) else "narration"


# --- stage views -------------------------------------------------------------------------
def cut_view(rdir: Path) -> dict:
    path = rdir / "clip_plan_validated.json"
    if not path.is_file():
        pending = (rdir / "clip_plan.json").is_file()
        return {"status": "pending" if pending else "missing"}
    data, error = read_object(path)
    if error:
        return _fallback(path, error)
    try:
        clips = []
        for index, clip in enumerate(data["clips"]):
            reason = str(clip.get("reason") or "")
            clips.append({
                "index": index, "id": clip.get("clip_id", index), "source_id": clip.get("source_id"),
                "source_start": _num(clip["source_start"]), "source_end": _num(clip["source_end"]),
                "output_start": _num(clip["output_start"]), "output_end": _num(clip["output_end"]),
                "reason": reason, "beat": reason.split("|")[0].strip(),
            })
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        return _fallback(path, f"剪辑片段字段不完整: {exc}")
    qc = data.get("qc") if isinstance(data.get("qc"), dict) else {}
    blocking = [{"code": str(b.get("code", "")), "message": str(b.get("message", ""))}
                for b in qc.get("blocking", []) if isinstance(b, dict)] if isinstance(qc.get("blocking"), list) else []
    total = data.get("total_duration")
    return {"status": "ok", "clips": clips, "blocking": blocking,
            "total": total if isinstance(total, (int, float)) else (clips[-1]["output_end"] if clips else 0),
            "target": data.get("target_duration") if isinstance(data.get("target_duration"), (int, float)) else None,
            "sources": sorted({c["source_id"] for c in clips if c["source_id"]})}


def narration_view(rdir: Path) -> dict:
    path = rdir / "narration.json"
    if not path.is_file():
        return {"status": "missing"}
    data, error = read_json(path)
    if error:
        return _fallback(path, error)
    segments = data.get("segments") if isinstance(data, dict) else data
    try:
        rows = [{"index": i, "start": _num(s["start"]), "end": _num(s["end"]),
                 "text": str(s.get("narration") or s.get("text") or ""),
                 "overlaps_speech": s.get("overlaps_speech") is True}
                for i, s in enumerate(segments)]
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        return _fallback(path, f"旁白段落字段不完整: {exc}")
    tts = None
    meta, meta_error = read_object(rdir / "tts_meta.json") if (rdir / "tts_meta.json").is_file() else (None, None)
    if meta is not None:
        failures = meta.get("failures") if isinstance(meta.get("failures"), list) else []
        tts = {"engine": meta.get("engine"), "partial": meta.get("partial") is True, "failures": len(failures)}
    elif meta_error:
        tts = {"error": meta_error}
    return {"status": "ok", "segments": rows, "chars": sum(len(r["text"]) for r in rows), "tts": tts}


def _track_rows(track: dict, key: str) -> list:
    rows = []
    for item in track.get(key) or []:
        if not isinstance(item, dict):
            continue
        start, end = _num(item["timeline_start"]), _num(item["timeline_end"])
        label = str(item.get("text") or Path(str(item.get("source_path") or "")).name)
        rows.append({"start": start, "end": end, "label": label})
    return rows


def timeline_view(rdir: Path) -> dict:
    path = rdir / "timeline.json"
    if not path.is_file():
        return {"status": "missing"}
    data, error = read_object(path)
    if error:
        return _fallback(path, error)
    lanes = {"video": [], "narration": [], "bgm": [], "subtitles": []}
    try:
        for track in data["tracks"]:
            kind, role = track.get("kind"), track.get("role") or track.get("name")
            if kind == "video":
                lanes["video"] += _track_rows(track, "clips")
            elif kind == "audio" and role in ("narration", "bgm"):
                lanes[role] += _track_rows(track, "segments")
            elif kind == "text":
                lanes["subtitles"] += _track_rows(track, "segments")
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        return _fallback(path, f"时间线字段不完整: {exc}")
    ends = [row["end"] for rows in lanes.values() for row in rows]
    duration = data.get("duration") if isinstance(data.get("duration"), (int, float)) else max(ends, default=0)
    canvas = data.get("canvas") if isinstance(data.get("canvas"), dict) else None
    return {"status": "ok", "duration": duration, "canvas": canvas, "lanes": lanes}


def final_video(root: Path, rdir: Path) -> dict | None:
    """The video ``assembly_manifest.json`` binds as ``final_output``; never guessed by name."""
    path = rdir / "assembly_manifest.json"
    if not path.is_file():
        return None
    manifest, error = read_object(path)
    if error:
        return {"path": None, "name": "assembly_manifest.json", "note": error}
    output = manifest.get("final_output")
    if not isinstance(output, str) or not output:
        return None
    declared = Path(output) if Path(output).is_absolute() else rdir / output
    return media_entry(root, declared) or {
        "path": None, "name": declared.name, "note": "成片不在 --root 内或不存在，不提供预览"}


def _findings(data: dict) -> list:
    rows = []
    for item in data.get("findings") or []:
        if isinstance(item, dict):
            rows.append({"code": str(item.get("code", "")), "message": str(item.get("message", "")),
                         "blocking": item.get("blocking") is True})
    rows.sort(key=lambda r: not r["blocking"])
    return rows[:MAX_FINDINGS]


def _qc_card(rdir: Path, name: str, label: str) -> dict | None:
    path = rdir / name
    if not path.is_file():
        return None
    card = {"file": name, "label": label, "findings": []}
    data, error = read_object(path)
    if error:
        return {**card, "level": "unparseable", "blockers": 0, "text": error}
    if name == "assembly_qc.json":
        verdict, codes = data.get("verdict"), data.get("blocking_codes") or []
        if verdict == "PASS":
            return {**card, "level": "ok", "blockers": 0, "text": "通过"}
        if verdict == "FAIL":
            detail = f"：{', '.join(map(str, codes))}" if codes else ""
            return {**card, "level": "error", "blockers": max(len(codes), 1), "text": f"未通过{detail}"}
        return {**card, "level": "warn", "blockers": 0, "text": f"结论未知（{verdict}）"}
    if name == "mimo_qc.json":
        meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        texts = {"completed": ("ok", f"已完成，{data.get('finding_count', 0)} 条建议（不阻断）"),
                 "dry_run": ("ok", "演练模式，未请求模型"),
                 "failed": ("warn", "请求失败，只记录不阻断")}
        level, text = texts.get(meta.get("status"), ("warn", f"状态：{meta.get('status')}"))
        return {**card, "level": level, "blockers": 0, "text": text, "findings": _findings(data)}
    ok, count = data.get("ok"), data.get("blocker_count")
    card["findings"] = _findings(data)
    if ok is True and count == 0:
        return {**card, "level": "ok", "blockers": 0, "text": "通过，无阻断项"}
    if isinstance(count, int) and not isinstance(count, bool) and count > 0:
        return {**card, "level": "error", "blockers": count, "text": f"{count} 个阻断项"}
    return {**card, "level": "warn", "blockers": 0, "text": f"摘要不完整（ok={ok}, blocker_count={count}）"}


def qc_cards(rdir: Path) -> list:
    return [c for c in (_qc_card(rdir, name, label) for name, label in QC_FILES) if c]


def lock_view(rdir: Path) -> dict:
    path = rdir / "resource_lock.json"
    if not path.is_file():
        return {"status": "missing"}
    data, error = read_object(path)
    if error:
        return _fallback(path, error)

    def rows(key):
        value = data.get(key)
        return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []

    library = data.get("library")
    resources = [{**entry, **_lock_file(entry), "registry": _registry(entry, library)} for entry in rows("resources")]
    return {"status": "ok", "generated_at": data.get("generated_at"), "library": library,
            "project": data.get("project") if isinstance(data.get("project"), dict) else None,
            "templates": rows("templates"), "resources": resources, "attention": rows("attention")}


def _lock_file(entry: dict) -> dict:
    """File name and directory of a lock entry's path, split for display."""
    path = entry.get("path")
    if not isinstance(path, str) or not path:
        return {"name": None, "dir": None}
    pure = PureWindowsPath(path) if "\\" in path else PurePosixPath(path)
    return {"name": pure.name, "dir": str(pure.parent)}


def _registry(entry: dict, library) -> str:
    """Where a resource is accounted for: the resource library, the material library (source
    videos are never registered as resources, by design), unregistered, or not applicable."""
    if entry.get("role") == "source_video":
        return "material"
    if isinstance(entry.get("library"), dict):
        return "library"
    if library and (entry.get("path") or entry.get("role") == "voice"):
        return "unregistered"
    return "none"


# --- stage bar ---------------------------------------------------------------------------
def _stage_bar(mode, audio_mode, state, views, qc) -> list:
    """Each stage's dot: ok, danger (must fix: QC or cut blockers), warn (advisory or unreadable),
    todo (waiting for the agent) or "" (not reached)."""
    pause = ((state or {}).get("next_pause") or {}).get("artifact")
    understanding = ((state or {}).get("artifacts") or {}).get("understanding") or {"present": [], "missing": []}
    total = len(understanding["present"]) + len(understanding["missing"])
    cut, narration, film, lock = views["cut"], views["narration"], views["film"], views["resources"]
    blockers = sum(c["blockers"] for c in qc)
    cells = {
        "home": ("", ""),
        "understanding": ("ok" if {"understanding_index.json", "agent_narration_brief.md"} & set(understanding["present"])
                          else "", f"{len(understanding['present'])}/{total}" if total else ""),
        "cut": {"ok": ("danger" if cut.get("blocking") else "ok", f"{len(cut.get('clips', []))} 段"),
                "pending": ("todo", "待校验"), "unparseable": ("warn", "无法解析"),
                }.get(cut["status"], ("todo", "等剪辑计划") if pause == "clip_plan.json" else ("", "未剪")),
        "narration": {"ok": ("warn" if (narration.get("tts") or {}).get("partial") else "ok",
                             f"{len(narration.get('segments', []))} 段"),
                      "unparseable": ("warn", "无法解析"),
                      }.get(narration["status"], ("todo", "等解说") if pause == "narration.json" else ("", "未写")),
        "film": (("ok", _clock(film["timeline"].get("duration")) if film["timeline"]["status"] == "ok" else "已合成")
                 if (film["video"] or {}).get("path")
                 else ("warn", "无法预览") if film["video"] else ("", "未合成")),
        "qc": (("danger", f"{blockers} 阻断") if blockers
               else ("warn", "无法解析") if any(c["level"] == "unparseable" for c in qc)
               else ("ok", "通过") if qc else ("", "未运行")),
        "resources": {"ok": ("warn", f"{len(lock.get('attention', []))} 注意") if lock.get("attention")
                      else ("ok", f"{len(lock.get('resources', []))} 项"),
                      "unparseable": ("warn", "无法解析")}.get(lock["status"], ("", "无记录")),
    }
    shown = [key for key, _ in STAGES
             if not (key == "cut" and mode != "cut") and not (key == "narration" and audio_mode != "narration")]
    labels = dict(STAGES)
    return [{"key": key, "label": labels[key], "state": cells[key][0], "meta": cells[key][1]} for key in shown]


def _clock(seconds) -> str:
    if not isinstance(seconds, (int, float)) or seconds <= 0:
        return "已合成"
    whole = int(round(seconds))
    return f"{whole // 60}:{whole % 60:02d}"


# --- runs --------------------------------------------------------------------------------
def run_views(root: Path, rel: str, runs: set) -> dict:
    """Everything the dashboard shows about one run; each view degrades on its own."""
    rdir = dir_of(root, rel)
    state, state_error = run_state(rdir)
    mode = (state or {}).get("mode") or ("cut" if (rdir / "clip_plan.json").is_file() else "full")
    audio_mode = _audio_mode(rdir)
    qc = qc_cards(rdir)
    edited = rdir / "edited_source.mp4"
    views = {
        "cut": {**cut_view(rdir), "video": media_entry(root, edited) if mode == "cut" else None},
        "narration": narration_view(rdir),
        "film": {"video": final_video(root, rdir), "timeline": timeline_view(rdir)},
        "resources": lock_view(rdir),
    }
    unparseable = [c["file"] for c in qc if c["level"] == "unparseable"]
    unparseable += [name for name, view in (("clip_plan_validated.json", views["cut"]),
                                            ("narration.json", views["narration"]),
                                            ("timeline.json", views["film"]["timeline"]),
                                            ("resource_lock.json", views["resources"]))
                    if view["status"] == "unparseable"]
    if state_error:
        unparseable.insert(0, RUN_FILE)
    return {
        "path": rel, "name": rdir.name or root.name, "work_dir": str(rdir),
        "parent": nearest(rel, runs), "mode": mode, "audio_mode": audio_mode,
        "state": state, "state_error": state_error,
        "next_pause": (state or {}).get("next_pause"),
        "qc": qc, "blockers": sum(c["blockers"] for c in qc), "unparseable": unparseable,
        "lock_attention": len(views["resources"].get("attention", [])),
        "has_video": bool((views["film"]["video"] or {}).get("path")),
        "stages": _stage_bar(mode, audio_mode, state, views, qc),
        "views": views,
    }


def run_brief(root: Path, rel: str, runs: set) -> dict:
    """The run without its per-stage views, for lists."""
    full = run_views(root, rel, runs)
    return {k: v for k, v in full.items() if k not in {"views", "state", "qc"}}
