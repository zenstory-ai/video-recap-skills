#!/usr/bin/env python3
"""Optional JianYing / CapCut draft exporter — pure leaf CLI over timeline.json v3.

Reads canonical `timeline.json` (v3) and emits a portable JianYing desktop draft
(`draft_content.json`, `draft_info.json`, `draft_meta_info.json`, and bundled media
under `Resources/local/`). Uses stdlib + `ffprobe` only; never imported by core.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

__all__ = ["us", "build_draft", "export_timeline_to_jianying", "main"]

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "references" / "jianying"
_TEMPLATE_FILES = {
    "project": "empty_jy_project_info.json", "video": "empty_jy_material_video.json",
    "audio": "empty_yj_material_audio.json", "text": "empty_yj_material_text.json",
    "text_style": "empty_jy_text_styles.json", "segment": "empty_jy_segment.json",
    "meta": "empty_draft_meta_info.json", "meta_material": "empty_jy_meta_material_value.json",
}
_MATERIAL_KEYS = (
    "ai_translates", "audio_balances", "audio_effects", "audio_fades", "audio_track_indexes",
    "audios", "beats", "canvases", "chromas", "color_curves", "digital_humans", "drafts",
    "effects", "flowers", "green_screens", "handwrites", "hsl", "images", "log_color_wheels",
    "loudnesses", "manual_deformations", "masks", "common_mask", "material_animations",
    "material_colors", "multi_language_refs", "placeholders", "plugin_effects",
    "primary_color_wheels", "realtime_denoises", "shapes", "smart_crops", "smart_relights",
    "sound_channel_mappings", "speeds", "stickers", "tail_leaders", "text_templates", "texts",
    "time_marks", "transitions", "video_effects", "video_trackings", "videos", "vocal_beautifys",
    "vocal_separations",
)
_SEGMENT_RENDER_INDEX = 2
_TRACK_BANDS = {
    "audio": ("audio", 20_000), "video": ("video", 40_000), "image": ("video", 50_000),
    "subtitle": ("text", 90_000), "text": ("text", 100_000),
}
_DRAFT_PATH_PLACEHOLDER = "##_draftpath_placeholder_0E685133-18CE-45ED-8CB8-2904A212EC80_##"


def us(seconds: float | int) -> int:
    """Convert seconds to integer microseconds."""
    return int(round(float(seconds) * 1_000_000))


def _parse_fps(value) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    return float(Fraction(value.strip())) if isinstance(value, str) and value.strip() else 30.0


@lru_cache(maxsize=None)
def _read_template(name: str) -> dict:
    with open(_TEMPLATE_DIR / _TEMPLATE_FILES[name], encoding="utf-8") as fh:
        return json.load(fh)


def _template(name: str) -> dict:
    return copy.deepcopy(_read_template(name))


def _default_id() -> str:
    return str(uuid.uuid4()).upper()


def _probe_media(path: str) -> tuple[int, int, int]:
    """Return (duration_us, width, height) via ffprobe; images legitimately return 0 duration."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-of", "json",
         "-show_entries", "format=duration:stream=width,height,codec_type", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    if result.returncode:
        raise RuntimeError(f"ffprobe failed for JianYing media {path}: {result.stderr.strip()}")
    data = json.loads(result.stdout)
    duration = (data.get("format") or {}).get("duration")
    width = height = 0
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
            break
    return (us(float(duration)) if duration is not None else 0), width, height


def _resolve_ref(timeline: dict, ref: str, base_dir: Path | None) -> str:
    p = Path(str((timeline.get("sources") or {}).get(ref, ref))).expanduser()
    if not p.is_absolute() and base_dir is not None:
        p = (base_dir / p).resolve()
    return str(p)


def _envelope_keyframes(
    windows: list[tuple[float, float]], idle: float, duck: float, fade: float, total_s: float
) -> list[dict]:
    if not windows or abs(idle - duck) < 1e-4:
        return []
    pts: list[tuple[float, float]] = [(0.0, idle)]
    for s, e in windows:
        pts.extend([(max(0.0, s - fade), idle), (s, duck), (e, duck), (min(total_s, e + fade), idle)])
    pts.append((total_s, idle))
    dedup: list[dict] = []
    for t, g in pts:
        t_r, g_r = round(max(0.0, min(total_s, t)), 4), round(float(g), 4)
        if dedup and abs(dedup[-1]["t"] - t_r) < 1e-4:
            dedup[-1]["gain"] = g_r
        else:
            dedup.append({"t": t_r, "gain": g_r})
    return dedup


def _ducking_keyframes(timeline: dict, total_s: float) -> dict[str, list[dict]]:
    """Compute volume keyframes via `_audio.ducking_keyframes` when present, else locally."""
    try:
        import _audio  # type: ignore[import-not-found]
        if hasattr(_audio, "ducking_keyframes"):
            return _audio.ducking_keyframes(timeline)
    except Exception:  # noqa: BLE001
        pass
    narr = (timeline.get("audio") or {}).get("narration") or []
    duck_cfg = (timeline.get("mix") or {}).get("duck") or {}
    fade, bridge = float(duck_cfg.get("fade_s", 0.25)), float(duck_cfg.get("bridge_s", 0.5))
    orig_duck = float(duck_cfg.get("under_narration", 0.18))
    bgm_duck = float(duck_cfg.get("bgm_under_narration", 0.12))
    raw = sorted((float(s.get("at", 0.0)), float(s.get("at", 0.0)) + float(s.get("duration") or 0.0))
                 for s in narr if float(s.get("duration") or 0.0) > 0)
    wins: list[list[float]] = []
    for s, e in raw:
        if wins and s - wins[-1][1] <= bridge:
            wins[-1][1] = max(wins[-1][1], e)
        else:
            wins.append([s, e])
    merged = [(s, e) for s, e in wins]
    bgm_list = (timeline.get("audio") or {}).get("bgm") or []
    bgm_idle = float(bgm_list[0].get("gain", 0.25)) if bgm_list else 0.25
    return {
        "original": _envelope_keyframes(merged, 1.0, orig_duck, fade, total_s),
        "bgm": _envelope_keyframes(merged, bgm_idle, min(bgm_idle, bgm_duck), fade, total_s),
    }


def _volume_keyframes(keyframes: list[dict] | None, seg_start_s: float, new_id) -> list[dict]:
    if not keyframes:
        return []
    kfs = [{
        "curveType": "Line", "graphID": "",
        "left_control": {"x": 0.0, "y": 0.0}, "right_control": {"x": 0.0, "y": 0.0},
        "id": new_id(), "time_offset": max(0, us(float(kf["t"]) - seg_start_s)),
        "values": [round(float(kf["gain"]), 4)],
    } for kf in keyframes]
    return [{"id": new_id(), "keyframe_list": kfs, "material_id": "", "property_type": "KFTypeVolume"}]


def _windowed_keyframes(
    keyframes: list[dict] | None, start_s: float, end_s: float, default_gain: float, new_id
) -> list[dict]:
    if not keyframes or end_s <= start_s:
        return []
    ordered = sorted(
        ({"t": float(k["t"]), "gain": float(k["gain"])} for k in keyframes if "t" in k and "gain" in k),
        key=lambda k: k["t"],
    )
    if not ordered:
        return []
    start_gain = default_gain
    for kf in ordered:
        if kf["t"] <= start_s:
            start_gain = kf["gain"]
        else:
            break
    inner = [kf for kf in ordered if start_s <= kf["t"] <= end_s]
    if not inner and abs(start_gain - default_gain) < 1e-4:
        return []
    selected = [{"t": start_s, "gain": start_gain}]
    for kf in inner:
        if abs(kf["t"] - start_s) < 1e-4:
            selected[-1] = {"t": start_s, "gain": kf["gain"]}
        else:
            selected.append(kf)
    return [] if all(abs(kf["gain"] - default_gain) < 1e-4 for kf in selected) else _volume_keyframes(selected, start_s, new_id)


def _rect_to_jy_clip(rect: dict | None, cw: int, ch: int, iw: int, ih: int) -> dict:
    """Convert normalized top-left rect {x,y,w,h} in [0,1] to JianYing center-origin Y-up clip."""
    sx = sy = 1.0
    tx = ty = 0.0
    if isinstance(rect, dict) and cw > 0 and ch > 0:
        rx, ry = float(rect.get("x", 0.0)) * cw, float(rect.get("y", 0.0)) * ch
        rw, rh = float(rect.get("w", 1.0)) * cw, float(rect.get("h", 1.0)) * ch
        if iw > 0 and ih > 0:
            fit = min(cw / iw, ch / ih)
            sx, sy = round(rw / max(1e-6, iw * fit), 6), round(rh / max(1e-6, ih * fit), 6)
        tx = round((rx + rw / 2 - cw / 2) / (cw / 2), 6)
        ty = round((ch / 2 - ry - rh / 2) / (ch / 2), 6)
    return {"alpha": 1.0, "flip": {"horizontal": False, "vertical": False}, "rotation": 0.0,
            "scale": {"x": sx, "y": sy}, "transform": {"x": tx, "y": ty}}


def _text_payload(text: str) -> dict:
    style = _template("text_style")["styles"][0]
    style.update({
        "fill": {"alpha": 1.0, "content": {"render_type": "solid", "solid": {"alpha": 1.0, "color": [1.0, 1.0, 1.0]}}},
        "range": [0, len(text.encode("utf-16-le")) // 2],
        "size": 8.0, "bold": False, "italic": False, "underline": False, "strokes": [], "use_letter_color": True,
    })
    content = _template("text_style")
    content.update({"text": text, "styles": [style]})
    return content


@dataclass
class _TrackBucket:
    kind: str
    name: str
    track_type: str
    order: int
    segments: list[dict]


class _DraftBuilder:
    def __init__(self, width: int, height: int, fps: float, new_id, probe):
        self.width, self.height, self.fps = width, height, fps
        self.new_id, self.probe = new_id, probe
        self.materials: dict[str, list[dict]] = {"videos": [], "audios": [], "texts": []}
        self.notes: list[str] = []
        self.total_us = 0
        self._occupied: dict[tuple[str, str], list[tuple[int, int]]] = {}
        self._buckets: dict[tuple[str, str], _TrackBucket] = {}

    def base_seg(self, mat_id: str, start_us: int, dur_us: int, volume: float, kfs: list[dict]) -> dict:
        seg = _template("segment")
        seg.update({
            "id": self.new_id(), "material_id": mat_id,
            "target_timerange": {"start": int(start_us), "duration": int(dur_us)},
            "common_keyframes": kfs, "track_render_index": _SEGMENT_RENDER_INDEX,
            "render_index": _SEGMENT_RENDER_INDEX, "volume": round(float(volume), 4),
        })
        return seg

    def add_seg(self, kind: str, base_name: str, start_us: int, dur_us: int, seg: dict) -> None:
        track_type, band_order = _TRACK_BANDS[kind]
        end_us = int(start_us) + int(dur_us)
        suffix = 0
        while True:
            name = base_name if suffix == 0 else f"{base_name}-{suffix}"
            occ = self._occupied.setdefault((kind, name), [])
            if not any(start_us < oe and end_us > os_ for os_, oe in occ):
                occ.append((int(start_us), end_us))
                break
            suffix += 1
        bucket = self._buckets.setdefault((kind, name), _TrackBucket(kind, name, track_type, band_order + suffix, []))
        bucket.segments.append(seg)
        self.total_us = max(self.total_us, end_us)

    def add_audio_seg(self, track_name: str, mat_id: str, start_us: int, dur_us: int, src_start_us: int, vol: float, kfs: list[dict]) -> None:
        seg = self.base_seg(mat_id, start_us, dur_us, vol, kfs)
        seg["source_timerange"] = {"start": int(src_start_us), "duration": int(dur_us)}
        seg["extra_material_refs"] = []
        self.add_seg("audio", track_name, start_us, dur_us, seg)

    def finalize_tracks(self) -> list[dict]:
        return [
            {"attribute": 0, "flag": 0, "id": self.new_id(), "is_default_name": False,
             "name": b.name, "segments": b.segments, "type": b.track_type}
            for b in sorted(self._buckets.values(), key=lambda x: (x.order, x.name))
        ]


def _populate_tracks(ctx: _DraftBuilder, timeline: dict, base_dir: Path | None, kfs: dict[str, list[dict]]) -> None:
    cursor = 0.0
    for clip in timeline.get("video") or []:
        s_in, s_out = float(clip["in"]), float(clip["out"])
        dur = max(0.0, s_out - s_in)
        if dur <= 0:
            continue
        path = _resolve_ref(timeline, clip["source"], base_dir)
        src_dur_us, w, h = ctx.probe(path)
        if src_dur_us <= 0:
            raise ValueError(f"JianYing video source has no probed duration: {path}")
        mat_id = ctx.new_id()
        mat = _template("video")
        mat.update({"duration": int(src_dur_us), "height": h or ctx.height, "id": mat_id,
                    "material_name": os.path.basename(path), "path": path, "width": w or ctx.width})
        ctx.materials["videos"].append(mat)
        muted = str(clip.get("audio", "keep")).lower() == "mute"
        gain = 0.0 if muted else float(clip.get("audio_gain", 1.0))
        v_kfs = [] if muted else _windowed_keyframes(kfs.get("original"), cursor, cursor + dur, gain, ctx.new_id)
        seg = ctx.base_seg(mat_id, us(cursor), us(dur), 1.0 if v_kfs else gain, v_kfs)
        seg["source_timerange"] = {"start": us(s_in), "duration": us(dur)}
        seg["clip"] = _rect_to_jy_clip(None, ctx.width, ctx.height, w or ctx.width, h or ctx.height)
        ctx.add_seg("video", "video", us(cursor), us(dur), seg)
        cursor += dur

    audio = timeline.get("audio") or {}
    for role, default_gain in (("narration", 1.0), ("sfx", 0.7)):
        for seg in audio.get(role) or []:
            ref = seg.get("file") or seg.get("source")
            if not ref:
                continue
            path = _resolve_ref(timeline, ref, base_dir)
            mat_dur_us, _, _ = ctx.probe(path)
            if mat_dur_us <= 0:
                raise ValueError(f"JianYing {role} source has no probed duration: {path}")
            dur_us = min(us(float(seg["duration"])), mat_dur_us) if seg.get("duration") else mat_dur_us
            mat_id = ctx.new_id()
            mat = _template("audio")
            mat.update({"duration": int(mat_dur_us), "id": mat_id, "path": path})
            ctx.materials["audios"].append(mat)
            ctx.add_audio_seg(role, mat_id, us(float(seg.get("at", 0.0))), dur_us, 0, float(seg.get("gain", default_gain)), [])

    for seg in audio.get("bgm") or []:
        path = _resolve_ref(timeline, seg["source"], base_dir)
        mat_dur_us, _, _ = ctx.probe(path)
        if mat_dur_us <= 0:
            raise ValueError(f"JianYing BGM source has no probed duration: {path}")
        at, src_in_us = float(seg.get("at", 0.0)), us(float(seg.get("in", 0.0)))
        out_s = float(seg["out"]) if seg.get("out") is not None else cursor
        want_us = max(0, us(out_s - at))
        if want_us <= 0:
            continue
        gain, loop = float(seg.get("gain", 0.25)), bool(seg.get("loop", True))
        mat_id = ctx.new_id()
        mat = _template("audio")
        mat.update({"duration": int(mat_dur_us), "id": mat_id, "path": path})
        ctx.materials["audios"].append(mat)
        if loop and want_us > mat_dur_us:
            cur = 0
            while cur < want_us:
                avail = max(1, int(mat_dur_us) - (src_in_us if cur == 0 else 0))
                piece = min(avail, want_us - cur)
                b_kfs = _windowed_keyframes(kfs.get("bgm"), at + cur / 1e6, at + (cur + piece) / 1e6, gain, ctx.new_id)
                ctx.add_audio_seg("bgm", mat_id, us(at) + cur, piece, src_in_us if cur == 0 else 0, 1.0 if b_kfs else gain, b_kfs)
                cur += piece
        else:
            place_us = min(want_us, max(0, int(mat_dur_us) - src_in_us))
            if place_us <= 0:
                continue
            if want_us > place_us:
                ctx.notes.append(f"BGM 素材({mat_dur_us/1e6:.1f}s) 短于目标区间({want_us/1e6:.1f}s)，未循环铺满")
            b_kfs = _windowed_keyframes(kfs.get("bgm"), at, at + place_us / 1e6, gain, ctx.new_id)
            ctx.add_audio_seg("bgm", mat_id, us(at), place_us, src_in_us, 1.0 if b_kfs else gain, b_kfs)

    for ovl in timeline.get("overlays") or []:
        ts, te = float(ovl["start"]), float(ovl["end"])
        if te <= ts:
            continue
        path = _resolve_ref(timeline, ovl["source"], base_dir)
        src_dur_us, w, h = ctx.probe(path)
        is_video = Path(path).suffix.lower() in {".mov", ".webm", ".mp4", ".mkv"}
        dur_us = min(us(te - ts), int(src_dur_us)) if (is_video and src_dur_us > 0) else us(te - ts)
        mat_id, mat = ctx.new_id(), _template("video")
        mat.update({"duration": int(src_dur_us) if (is_video and src_dur_us > 0) else dur_us,
                    "height": h or ctx.height, "id": mat_id, "material_name": os.path.basename(path),
                    "path": path, "type": "video" if is_video else "photo", "width": w or ctx.width})
        ctx.materials["videos"].append(mat)
        seg = ctx.base_seg(mat_id, us(ts), dur_us, 1.0, [])
        seg["source_timerange"] = {"start": 0, "duration": dur_us}
        seg["clip"] = _rect_to_jy_clip(ovl.get("rect"), ctx.width, ctx.height, w or ctx.width, h or ctx.height)
        ctx.add_seg("image", "overlay", us(ts), dur_us, seg)

    safe_y = (timeline.get("subtitle_style") or {}).get("safe_y")
    sub_y = round(1.0 - (float(safe_y[0]) + float(safe_y[1])), 4) if isinstance(safe_y, list) and len(safe_y) == 2 else -0.72
    for cue in timeline.get("subtitles") or []:
        text, ts, te = str(cue.get("text") or "").strip(), float(cue["start"]), float(cue["end"])
        if not text or te <= ts:
            continue
        dur_us, mat_id = us(te - ts), ctx.new_id()
        mat = _template("text")
        mat.update({"id": mat_id, "content": json.dumps(_text_payload(text), ensure_ascii=False),
                    "type": "subtitle", "alignment": 1, "font_size": 8.0, "text_color": "#FFFFFF",
                    "line_spacing": 0.02, "letter_spacing": 0.0, "check_flag": 15})
        ctx.materials["texts"].append(mat)
        seg = ctx.base_seg(mat_id, us(ts), dur_us, 1.0, [])
        seg["source_timerange"] = {"start": 0, "duration": dur_us}
        seg["clip"] = _rect_to_jy_clip(None, ctx.width, ctx.height, ctx.width, ctx.height)
        seg["clip"]["transform"]["y"] = sub_y
        ctx.add_seg("subtitle", "subtitle", us(ts), dur_us, seg)


def build_draft(timeline: dict, new_id=None, probe=None, *, base_dir: Path | None = None) -> tuple[dict, dict, list[str]]:
    """Build JianYing `(draft_content, draft_meta_info, notes)` from canonical `timeline.json` v3."""
    if not isinstance(timeline, dict) or "canvas" not in timeline or "video" not in timeline:
        raise ValueError("timeline must be a JSON object with 'canvas' and 'video'")
    canvas = timeline["canvas"]
    width, height, fps = int(canvas["width"]), int(canvas["height"]), _parse_fps(canvas.get("fps", 30))
    new_id, probe = new_id or _default_id, probe or _probe_media
    est_total = sum(max(0.0, float(c["out"]) - float(c["in"])) for c in timeline.get("video") or [])
    ctx = _DraftBuilder(width, height, fps, new_id, probe)
    _populate_tracks(ctx, timeline, base_dir, _ducking_keyframes(timeline, est_total))

    draft_id = new_id()
    content = _template("project")
    for pkey in ("last_modified_platform", "platform"):
        for ikey in ("device_id", "hard_disk_id", "mac_address"):
            content[pkey][ikey] = ""
    mats = {k: [] for k in _MATERIAL_KEYS}
    mats.update(ctx.materials)
    content.update({
        "canvas_config": {"width": width, "height": height, "ratio": "original"},
        "duration": int(ctx.total_us), "fps": float(fps), "id": draft_id,
        "materials": mats, "tracks": ctx.finalize_tracks(),
    })
    meta = _template("meta")
    meta.update({"draft_id": draft_id, "draft_timeline_materials_size_": 0, "tm_duration": int(ctx.total_us)})
    return content, meta, ctx.notes


def _validate_draft_name(draft_name: str) -> None:
    if not isinstance(draft_name, str) or not draft_name.strip() or os.path.isabs(draft_name) or "/" in draft_name or "\\" in draft_name or draft_name in {".", ".."}:
        raise ValueError(f"draft_name must be a plain folder name: {draft_name!r}")


def _md5(path: str) -> str:
    d = hashlib.md5(usedforsecurity=False)
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            d.update(chunk)
    return d.hexdigest()


def _bundle_media(content: dict, meta: dict, draft_dir: str) -> list[str]:
    resources_root, copied, used = os.path.join(draft_dir, "Resources", "local"), {}, {k: set() for k in ("audio", "image", "video")}
    meta_values, notes, ts_ms = [], [], int(time.time() * 1000)
    for mkey in ("videos", "audios"):
        for mat in content["materials"][mkey]:
            src = mat["path"]
            kind, mtype = ("audio", "music") if mkey == "audios" else (("image", "photo") if mat.get("type") == "photo" else ("video", "video"))
            skey = (kind, os.path.realpath(src))
            if skey in copied:
                mat["path"] = copied[skey]
                continue
            if not os.path.isfile(src):
                if not str(src).startswith((_DRAFT_PATH_PLACEHOLDER, "Resources/", "./Resources/")):
                    notes.append(f"素材缺失，未打包: {src}")
                continue
            rdir = os.path.join(resources_root, kind)
            os.makedirs(rdir, exist_ok=True)
            base, idx = os.path.basename(src), 1
            stem, ext = os.path.splitext(base)
            name = base
            while name in used[kind] or os.path.exists(os.path.join(rdir, name)):
                name, idx = f"{stem}_{idx}{ext}", idx + 1
            used[kind].add(name)
            dst = os.path.join(rdir, name)
            shutil.copy2(src, dst)
            rel = f"Resources/local/{kind}/{name}"
            mat["path"] = copied[skey] = f"{_DRAFT_PATH_PLACEHOLDER}/{rel}"
            mv, dur = _template("meta_material"), int(mat.get("duration") or 0)
            mv.update({"duration": dur, "height": int(mat.get("height") or 0), "id": _default_id(), "md5": _md5(dst),
                       "metetype": mtype, "type": 0, "width": int(mat.get("width") or 0), "create_time": ts_ms // 1000,
                       "extra_info": name, "file_Path": f"./{rel}", "import_time": ts_ms // 1000, "import_time_ms": ts_ms,
                       "item_source": 1, "roughcut_time_range": {"duration": dur, "start": 0}, "sub_time_range": {"duration": -1, "start": -1}})
            meta_values.append(mv)
    next(g for g in meta["draft_materials"] if g["type"] == 0)["value"] = meta_values
    meta["draft_timeline_materials_size_"] = sum(os.path.getsize(os.path.join(draft_dir, v["file_Path"][2:])) for v in meta_values)
    return notes


def _has_content(path: str) -> bool:
    try:
        return os.path.exists(path) and any(os.scandir(path))
    except OSError:
        return True


def export_timeline_to_jianying(
    timeline: dict, out_dir: str | Path, draft_name: str = "recap",
    new_id=None, probe=None, bundle_media: bool = True, *, base_dir: Path | None = None,
) -> tuple[str, list[str]]:
    """Atomically write a collision-safe JianYing draft folder under `out_dir/draft_name`."""
    _validate_draft_name(draft_name)
    content, meta, notes = build_draft(timeline, new_id=new_id, probe=probe, base_dir=base_dir)
    out_abs = os.path.abspath(os.fspath(out_dir))
    os.makedirs(out_abs, exist_ok=True)
    actual_name, draft_dir, idx = draft_name, os.path.join(out_abs, draft_name), 2
    while _has_content(draft_dir):
        actual_name, idx = f"{draft_name}_{idx}", idx + 1
        draft_dir = os.path.join(out_abs, actual_name)
    if actual_name != draft_name:
        notes.append(f"草稿目录已存在，改写为 {actual_name} 以避免覆盖")

    tmp_parent = tempfile.mkdtemp(prefix=f".{actual_name}.", dir=out_abs)
    tmp_dir = os.path.join(tmp_parent, actual_name)
    try:
        os.makedirs(tmp_dir, exist_ok=False)
        if bundle_media:
            notes.extend(_bundle_media(content, meta, tmp_dir))
        ts_ms = int(time.time() * 1000)
        meta.update({"draft_name": actual_name, "draft_fold_path": draft_dir,
                     "tm_draft_create": ts_ms, "tm_draft_modified": ts_ms})
        content["name"] = actual_name
        for fname in ("draft_content.json", "draft_info.json"):
            with open(os.path.join(tmp_dir, fname), "w", encoding="utf-8") as fh:
                json.dump(content, fh, ensure_ascii=False, indent=2)
        with open(os.path.join(tmp_dir, "draft_meta_info.json"), "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2)
        if os.path.isdir(draft_dir) and not _has_content(draft_dir):
            os.rmdir(draft_dir)
        os.replace(tmp_dir, draft_dir)
    except Exception:
        shutil.rmtree(tmp_parent, ignore_errors=True)
        raise
    finally:
        if os.path.exists(tmp_parent):
            shutil.rmtree(tmp_parent, ignore_errors=True)
    return draft_dir, notes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Export canonical timeline.json v3 to a JianYing draft folder.")
    ap.add_argument("timeline", help="path to timeline.json")
    ap.add_argument("--out-dir", required=True, help="parent directory for the draft folder")
    ap.add_argument("--name", default="recap", help="draft folder name")
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument("--bundle-media", dest="bundle_media", action="store_true", help="bundle media (default)")
    grp.add_argument("--no-bundle-media", dest="bundle_media", action="store_false", help="keep external paths")
    ap.set_defaults(bundle_media=True)
    args = ap.parse_args(argv)
    tl_path = Path(args.timeline).expanduser().resolve()
    draft_dir, notes = export_timeline_to_jianying(
        json.loads(tl_path.read_text(encoding="utf-8")), args.out_dir, args.name,
        bundle_media=args.bundle_media, base_dir=tl_path.parent,
    )
    print(json.dumps({"status": "exported", "draft_dir": draft_dir, "notes": notes}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
