"""Generate and cache source and edited storyboard sheets."""

import json
from pathlib import Path

from lib import CONFIG, log


from storyboard import (
    _frame_index,
    build_edited_storyboard,
    build_source_storyboard,
    work_dir_relative_pages,
)


from understanding_cache import (
    _artifact_identity,
    _video_input,
    _frames_manifest_path,
    _load_json,
    _stage_cache_valid,
    _write_stage_meta,
)


def _storyboard_sample_policy():
    return {
        "max_tiles": CONFIG["storyboard_max_tiles"],
        "columns": CONFIG["storyboard_columns"],
    }


def _relocated(payload):
    """A cached storyboard whose stored paths still name the work_dir it was written in.

    Sidecars written before 0.6.1 stored absolute page paths; a copied work_dir hits the
    cache (identities are size + mtime) and would otherwise point the brief at the original
    directory. Paths are re-derived from file names, so the copy names its own pages."""
    if not isinstance(payload, dict):
        return payload
    payload = dict(payload)
    payload["page_images"] = work_dir_relative_pages(payload.get("page_images"))
    if payload.get("edited_video_path"):
        payload["edited_video_path"] = "edited_source.mp4"  # the only edited output there is
    return payload


def _reuse_cached_storyboard(json_path, meta, label):
    """The cached storyboard with work_dir-relative paths, or None when it is unreadable.

    A legacy sidecar with absolute paths is rewritten (and re-stamped) once, so the JSON on
    disk names this work_dir's pages too. Advisory: never raises."""
    try:
        cached = _load_json(json_path)
    except (OSError, ValueError):
        log(f"storyboard {label} 缓存命中但文件损坏，重建")
        return None
    relocated = _relocated(cached)
    if relocated != cached:
        try:
            json_path.write_text(
                json.dumps(relocated, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            _write_stage_meta(json_path, meta)
        except OSError as exc:
            log(f"storyboard {label} 路径改写失败（忽略）: {exc}")
    log(f"storyboard 跳过 {label}（缓存匹配）")
    return relocated


def _edited_storyboard_meta(clip_plan_validated_json, frames_manifest_path):
    """Cache key for the edited storyboard: clip plan + fps + frame-set all in the key, so an
    fps change OR a re-validated plan invalidates it. font availability is deliberately NOT in
    the key (the JSON `labels_burned` flag surfaces it instead)."""
    return {
        "schema_version": 1,
        "stage": "edited_storyboard",
        "inputs": {
            "clip_plan_validated": _artifact_identity(clip_plan_validated_json),
            "frames_manifest": _artifact_identity(frames_manifest_path),
        },
        "fps": float(CONFIG.get("fps") or 0),
        "sample_policy": _storyboard_sample_policy(),
    }


def _multi_source_edited_storyboard_meta(clip_plan_validated_json, frame_sets):
    """Cache key for a multi-source edited storyboard: the plan plus every used source's
    frames-manifest identity and fps."""
    return {
        "schema_version": 1,
        "stage": "edited_storyboard",
        "inputs": {
            "clip_plan_validated": _artifact_identity(clip_plan_validated_json),
            "frames_manifests": {
                sid: _artifact_identity(entry["manifest"]) for sid, entry in frame_sets.items()
            },
        },
        "fps": {sid: entry["fps"] for sid, entry in frame_sets.items()},
        "sample_policy": _storyboard_sample_policy(),
    }


def _generate_source_storyboard(
    work_dir, video_path, scenes, scenes_json, *, force=False
):
    """Generate (or reuse cached) the source storyboard. Advisory: returns dict|None, never raises.

    Cached via _write_stage_meta/_stage_cache_valid on storyboard/source_storyboard.json; the
    meta includes fps + the frames-manifest identity so an fps-change resume rebuilds (Principle 5).
    If frames/ is absent (cache hit skipped extraction / cleaned) → skip + log; pipeline continues.
    """
    if not CONFIG["storyboard"]:
        return None
    frames_dir = Path(work_dir) / "frames"
    if not frames_dir.is_dir() or not any(frames_dir.glob("frame_*.jpg")):
        log("storyboard 跳过 source：frames/ 缺失（缓存命中跳过了帧提取？）")
        return None
    json_path = Path(work_dir) / "storyboard" / "source_storyboard.json"
    meta = {
        "schema_version": 1,
        "stage": "source_storyboard",
        "inputs": {
            "video": _video_input(video_path),
            "scenes": _artifact_identity(scenes_json),
            "frames_manifest": _artifact_identity(_frames_manifest_path(work_dir)),
        },
        "fps": float(CONFIG.get("fps") or 0),
        "sample_policy": _storyboard_sample_policy(),
    }
    if not force and _stage_cache_valid(json_path, meta):
        cached = _reuse_cached_storyboard(json_path, meta, "source")
        if cached is not None:
            return cached
    result = build_source_storyboard(work_dir, video_path, scenes, CONFIG["fps"])
    if result is not None and json_path.exists():
        _write_stage_meta(json_path, meta)
    return result


def _multi_source_frame_sets(work_dir, plan_sources, clips=None):
    """{source_id: {paths, numbers, fps, source_path}} for a multi-source validated plan.

    Each source's frames live in its own work_dir (`sources.<id>.source_work_dir`, relative to
    the project work_dir) at the fps recorded in that source's frames manifest — sources of
    different lengths are extracted at different fps. A source without frames is left out, and
    so is one no clip in `clips` uses (when given): it would not appear on the sheet, yet it
    would enter the cache key.
    """
    used = None if clips is None else {
        clip.get("source_id") for clip in clips if isinstance(clip, dict)
    }
    frame_sets = {}
    for source_id, source in plan_sources.items():
        if used is not None and source_id not in used:
            continue
        if not isinstance(source, dict):
            log(f"storyboard edited：来源 {source_id} 不是对象，跳过")
            continue
        rel = source.get("source_work_dir")
        if not rel:
            log(f"storyboard edited：来源 {source_id} 未登记 source_work_dir，跳过")
            continue
        source_dir = Path(work_dir) / rel
        manifest_path = _frames_manifest_path(source_dir)
        try:
            fps = float(_load_json(manifest_path)["fps"])
        except (OSError, ValueError, TypeError, KeyError):
            log(f"storyboard edited：来源 {source_id} 缺少可读的 frames_manifest.json，跳过")
            continue
        paths, numbers = _frame_index(source_dir)
        if not paths or fps <= 0:
            log(f"storyboard edited：来源 {source_id} 的 frames/ 缺失，跳过")
            continue
        frame_sets[source_id] = {
            "paths": paths,
            "numbers": numbers,
            "fps": fps,
            "source_path": source.get("source_path"),
            "manifest": manifest_path,
        }
    return frame_sets


def _generate_edited_storyboard(work_dir, source_video_path, *, force=False):
    """Generate (or reuse cached) the edited storyboard, GATED on clip_plan_validated.json
    file-presence (NOT on edit_mode — recap.py forwards --edit-mode cut in BOTH passes, so the
    validated plan presence is the only reliable pass2 signal). Advisory: returns dict|None.

    A multi-source plan (one with `sources`) reads every source's frames from its own work_dir
    under the project work_dir; the single-source plan reads work_dir/frames.
    """
    if not CONFIG["storyboard"]:
        return None
    clip_plan_validated_json = Path(work_dir) / "clip_plan_validated.json"
    if not clip_plan_validated_json.exists():
        return None  # pass1 (no validated plan yet) → no edited storyboard
    try:
        clip_plan_validated = _load_json(clip_plan_validated_json)
    except (OSError, ValueError):
        log("storyboard 跳过 edited：clip_plan_validated.json 无法解析")
        return None
    if not isinstance(clip_plan_validated, dict):
        log("storyboard 跳过 edited：clip_plan_validated.json 不是对象")
        return None
    plan_sources = clip_plan_validated.get("sources")
    if isinstance(plan_sources, dict) and plan_sources:
        clips = clip_plan_validated.get("clips")
        if not isinstance(clips, list) or not clips:
            log("storyboard 跳过 edited：clip_plan_validated 无 clips")
            return None
        frame_sets = _multi_source_frame_sets(work_dir, plan_sources, clips)
        if not frame_sets:
            log("storyboard 跳过 edited：剪辑用到的来源都没有 frames/")
            return None
        meta = _multi_source_edited_storyboard_meta(clip_plan_validated_json, frame_sets)
    else:
        frame_sets = None
        frames_dir = Path(work_dir) / "frames"
        if not frames_dir.is_dir() or not any(frames_dir.glob("frame_*.jpg")):
            log("storyboard 跳过 edited：frames/ 缺失（缓存命中跳过了帧提取？）")
            return None
        meta = _edited_storyboard_meta(
            clip_plan_validated_json, _frames_manifest_path(work_dir)
        )
    json_path = Path(work_dir) / "storyboard" / "edited_storyboard.json"
    if not force and _stage_cache_valid(json_path, meta):
        cached = _reuse_cached_storyboard(json_path, meta, "edited")
        if cached is not None:
            return cached
    result = build_edited_storyboard(
        work_dir, source_video_path, clip_plan_validated, CONFIG["fps"],
        source_frames=frame_sets,
    )
    if result is not None and json_path.exists():
        _write_stage_meta(json_path, meta)
    return result


_STORYBOARD_HEADING = "## Storyboard（先看 storyboard 再写；路径相对 work_dir）"
# Also matches the pre-0.6.1 heading (no "路径相对 work_dir"), so a re-run replaces it too.
_STORYBOARD_HEADING_PREFIX = "## Storyboard（先看 storyboard 再写"


def _write_edited_storyboard_only(video, work_dir):
    """--edited-storyboard-only: build the cut-output storyboard and point the brief at it.

    For callers that write the pass-2 brief themselves (a multi-source orchestrator): no
    extraction, ASR, VLM or API; the existing work_dir/agent_narration_brief.md, if any, gets
    the same storyboard header a --brief-only rebuild would carry. Advisory: a storyboard
    failure only logs.
    """
    work_dir = Path(work_dir)
    edited = _generate_edited_storyboard(work_dir, video)
    brief_path = work_dir / "agent_narration_brief.md"
    if edited and brief_path.exists():
        _prepend_storyboard_brief_header(brief_path, None, edited, cut_mode=True)
    print(
        json.dumps(
            {
                "status": "edited_storyboard" if edited else "edited_storyboard_skipped",
                "work_dir": str(work_dir),
                "page_images": (edited or {}).get("page_images", []),
            },
            ensure_ascii=False,
        )
    )


def _prepend_storyboard_brief_header(
    brief_path, source_storyboard, edited_storyboard, *, cut_mode
):
    """Post-process the RETURNED brief markdown FILE (C1): prepend a short storyboard header.

    Branches the edited-storyboard line on clip_plan_validated presence (edited_storyboard truthy)
    so pass1 never prints a not-yet-existing path. If labels_burned:false, point to inspect clip-map.
    """
    if not source_storyboard and not edited_storyboard:
        return
    try:
        brief_path = Path(brief_path)
        lines = [_STORYBOARD_HEADING, ""]
        any_labels_missing = False
        if source_storyboard:
            pages = source_storyboard.get("page_images") or []
            lines.append(
                f"- 源时间线 storyboard: {', '.join(pages)}（tiles 时间戳=原片时间）"
            )
            if not source_storyboard.get("labels_burned", False):
                any_labels_missing = True
        if cut_mode and edited_storyboard:
            pages = edited_storyboard.get("page_images") or []
            lines.append(
                f"- 成片(output)时间线 storyboard: {', '.join(pages)}"
                "（每块双标 out 时间 / src 原片时间；注意区分两条时间线）"
            )
            if not edited_storyboard.get("labels_burned", False):
                any_labels_missing = True
            sources = edited_storyboard.get("sources") or []
            if sources:
                lines.append(
                    "- 来源标签: "
                    + "，".join(f"{s['label']}={s['source_id']}" for s in sources)
                )
        if any_labels_missing:
            lines.append(
                "- 时间戳未烧入 → 用 `inspect clip-map` 查时间（JSON sidecar 仍为权威时间源）"
            )
        lines.append("")
        header = "\n".join(lines) + "\n"
        existing = brief_path.read_text(encoding="utf-8") if brief_path.exists() else ""
        if existing.startswith(_STORYBOARD_HEADING_PREFIX):
            # Re-running on the same brief replaces the earlier header instead of stacking.
            end = existing.find("\n\n", existing.find("\n") + 2)
            existing = existing[end + 2:] if end >= 0 else ""
        brief_path.write_text(header + existing, encoding="utf-8")
    except OSError as exc:
        log(f"storyboard brief 头部写入失败（忽略）: {exc}")
