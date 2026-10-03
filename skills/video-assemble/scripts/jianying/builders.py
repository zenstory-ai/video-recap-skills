"""Production material and segment builders for the JianYing exporter."""

import json
import os

from jianying.schema import us
from jianying.templates import template
from jianying.tracks import SEGMENT_RENDER_INDEX


def timerange(start_us, dur_us):
    return {"start": int(start_us), "duration": int(dur_us)}


def volume_keyframes(keyframes, seg_start_s, new_id):
    """Build one KFTypeVolume keyframe list from timeline-absolute points."""
    if not keyframes:
        return []
    kfs = []
    for kf in keyframes:
        kfs.append({
            "curveType": "Line",
            "graphID": "",
            "left_control": {"x": 0.0, "y": 0.0},
            "right_control": {"x": 0.0, "y": 0.0},
            "id": new_id(),
            "time_offset": max(0, us(kf["t"] - seg_start_s)),
            "values": [round(float(kf["gain"]), 4)],
        })
    return [{
        "id": new_id(),
        "keyframe_list": kfs,
        "material_id": "",
        "property_type": "KFTypeVolume",
    }]


def windowed_volume_keyframes(keyframes, seg_start_s, seg_end_s, default_gain, new_id):
    """Window timeline-absolute keyframes for one split/looped segment."""
    if not keyframes:
        return []
    start = float(seg_start_s)
    end = float(seg_end_s)
    default_gain = float(default_gain)
    ordered = sorted((
        {"t": float(kf["t"]), "gain": float(kf["gain"])}
        for kf in keyframes
        if "t" in kf and "gain" in kf
    ), key=lambda kf: kf["t"])
    if not ordered or end <= start:
        return []

    start_gain = default_gain
    for kf in ordered:
        if kf["t"] <= start:
            start_gain = kf["gain"]
        else:
            break
    inner = [kf for kf in ordered if start <= kf["t"] <= end]
    if not inner and abs(start_gain - default_gain) < 1e-4:
        return []

    selected = [{"t": start, "gain": start_gain}]
    for kf in inner:
        if abs(kf["t"] - start) < 1e-4:
            selected[-1] = {"t": start, "gain": kf["gain"]}
        else:
            selected.append(kf)
    if all(abs(kf["gain"] - default_gain) < 1e-4 for kf in selected):
        return []
    return volume_keyframes(selected, start, new_id)


def clip_from_segment(segment):
    """Map the optional scale/position transform (validated as objects by the contract).

    Positions use JianYing's normalized, canvas-center, Y-up coordinates.
    """
    scale = segment.get("scale", {})
    position = segment.get("position", {})
    return {
        "alpha": 1.0,
        "flip": {"horizontal": False, "vertical": False},
        "rotation": 0.0,
        "scale": {
            "x": float(scale.get("x", 1.0)),
            "y": float(scale.get("y", 1.0)),
        },
        "transform": {
            "x": float(position.get("x", 0.0)),
            "y": float(position.get("y", 0.0)),
        },
    }


def base_segment(material_id, target_start_us, target_dur_us, volume, keyframes, new_id):
    segment = template("segment")
    segment.update({
        "id": new_id(),
        "material_id": material_id,
        "target_timerange": timerange(target_start_us, target_dur_us),
        "common_keyframes": keyframes,
        "track_render_index": SEGMENT_RENDER_INDEX,
        "render_index": SEGMENT_RENDER_INDEX,
        "volume": round(float(volume), 4),
    })
    return segment


def audio_segment_piece(material_id, target_start_us, target_dur_us, source_start_us,
                        source_dur_us, volume, keyframes, new_id):
    seg = base_segment(material_id, target_start_us, target_dur_us, volume, keyframes, new_id)
    seg["source_timerange"] = timerange(source_start_us, source_dur_us)
    seg["extra_material_refs"] = []
    return seg


def text_content(text):
    """duo-video's rich-text payload with one default style over the whole string.

    Ranges are UTF-16 code units, matching JianYing rather than Python code points.
    """
    style = template("text_style")["styles"][0]
    style.update({
        "fill": {
            "alpha": 1.0,
            "content": {"render_type": "solid", "solid": {"alpha": 1.0, "color": [1.0, 1.0, 1.0]}},
        },
        "range": [0, len(text.encode("utf-16-le")) // 2],
        "size": 8.0,
        "bold": False,
        "italic": False,
        "underline": False,
        "strokes": [],
        "use_letter_color": True,
    })
    content = template("text_style")
    content["text"] = text
    content["styles"] = [style]
    return content


def build_video_track(ctx, timeline_track):
    track_name = timeline_track.get("name", "video")
    for clip in timeline_track["clips"]:
        ts, te = float(clip["timeline_start"]), float(clip["timeline_end"])
        ss, se = float(clip["source_start"]), float(clip["source_end"])
        path = clip["source_path"]
        src_dur_us, width, height = ctx.probe(path)
        if src_dur_us <= 0:
            raise ValueError(f"JianYing video source has no probed duration: {path}")
        mat_id = ctx.new_id()
        material = template("video")
        material.update({
            "duration": int(src_dur_us),
            "height": height or ctx.height,
            "id": mat_id,
            "material_name": os.path.basename(path),
            "path": path,
            "width": width or ctx.width,
        })
        ctx.materials["videos"].append(material)
        audio = clip.get("audio", {})
        keyframes = volume_keyframes(audio.get("volume_keyframes"), ts, ctx.new_id)
        volume = audio.get("base_gain", 1.0) if not keyframes else 1.0
        seg = base_segment(mat_id, us(ts), us(te - ts), volume, keyframes, ctx.new_id)
        seg["source_timerange"] = timerange(us(ss), us(se - ss))
        seg["clip"] = clip_from_segment(clip)
        ctx.add_segment("video", track_name, us(ts), us(te - ts), seg)


def build_audio_track(ctx, timeline_track):
    role = timeline_track.get("role", timeline_track.get("name", "audio"))
    track_name = timeline_track.get("name", role)
    looped_bgm = role == "bgm" and timeline_track.get("loop")
    for segment in timeline_track["segments"]:
        ts, te = float(segment["timeline_start"]), float(segment["timeline_end"])
        path = segment["source_path"]
        mat_dur_us, _width, _height = ctx.probe(path)
        if mat_dur_us <= 0:
            raise ValueError(f"JianYing audio source has no probed duration: {path}")
        want_us = us(te - ts)
        place_us = want_us
        if want_us > mat_dur_us and not looped_bgm:
            place_us = int(mat_dur_us)
            if role == "bgm":
                ctx.note(
                    f"BGM 素材({mat_dur_us/1e6:.1f}s) 短于时间线({(te - ts):.1f}s)，"
                    "剪映中未循环铺满（可在剪映里手动复制延长）"
                )
        mat_id = ctx.new_id()
        material = template("audio")
        material.update({
            "duration": int(mat_dur_us),
            "id": mat_id,
            "path": path,
        })
        ctx.materials["audios"].append(material)
        keyframes = volume_keyframes(segment.get("volume_keyframes"), ts, ctx.new_id)
        volume = segment.get("gain", 1.0) if not keyframes else 1.0
        if looped_bgm and want_us > mat_dur_us:
            cursor = 0
            while cursor < want_us:
                piece = min(int(mat_dur_us), want_us - cursor)
                piece_start_s = ts + (cursor / 1_000_000)
                piece_end_s = ts + ((cursor + piece) / 1_000_000)
                piece_kfs = windowed_volume_keyframes(
                    segment.get("volume_keyframes"), piece_start_s, piece_end_s,
                    segment.get("gain", 1.0), ctx.new_id)
                piece_volume = segment.get("gain", 1.0) if not piece_kfs else 1.0
                piece_seg = audio_segment_piece(
                    mat_id, us(ts) + cursor, piece, 0, piece,
                    piece_volume, piece_kfs, ctx.new_id)
                ctx.add_segment("audio", track_name, us(ts) + cursor, piece, piece_seg)
                cursor += piece
        else:
            audio_seg = audio_segment_piece(
                mat_id, us(ts), place_us, 0, place_us, volume, keyframes, ctx.new_id)
            ctx.add_segment("audio", track_name, us(ts), place_us, audio_seg)


def build_text_track(ctx, timeline_track):
    track_name = timeline_track.get("name", "text")
    track_kind = "subtitle" if track_name == "subtitle" else "text"
    for segment in timeline_track["segments"]:
        ts, te = float(segment["timeline_start"]), float(segment["timeline_end"])
        mat_id = ctx.new_id()
        material = template("text")
        material.update({
            "id": mat_id,
            "content": json.dumps(text_content(segment["text"]), ensure_ascii=False),
            "type": track_kind,
            "alignment": 1,
            "font_size": 8.0,
            "text_color": "#FFFFFF",
            "line_spacing": 0.02,
            "letter_spacing": 0.0,
            "check_flag": 15,
        })
        ctx.materials["texts"].append(material)
        duration_us = us(te - ts)
        seg = base_segment(mat_id, us(ts), duration_us, 1.0, [], ctx.new_id)
        seg["source_timerange"] = timerange(0, duration_us)
        seg["clip"] = clip_from_segment(segment)
        if track_kind == "subtitle" and "position" not in segment:
            seg["clip"]["transform"]["y"] = -0.72
        ctx.add_segment(track_kind, track_name, us(ts), duration_us, seg)


def build_image_track(ctx, timeline_track):
    """Build local image overlays as JianYing photo materials on video tracks."""
    track_name = timeline_track.get("name", "image")
    for segment in timeline_track["segments"]:
        ts, te = float(segment["timeline_start"]), float(segment["timeline_end"])
        duration_us = us(te - ts)
        path = segment["source_path"]
        _still_image_duration, width, height = ctx.probe(path)
        mat_id = ctx.new_id()
        material = template("video")
        material.update({
            "duration": duration_us,
            "height": height or ctx.height,
            "id": mat_id,
            "material_name": os.path.basename(path),
            "path": path,
            "type": "photo",
            "width": width or ctx.width,
        })
        ctx.materials["videos"].append(material)
        seg = base_segment(mat_id, us(ts), duration_us, 1.0, [], ctx.new_id)
        seg["source_timerange"] = timerange(0, duration_us)
        seg["clip"] = clip_from_segment(segment)
        ctx.add_segment("image", track_name, us(ts), duration_us, seg)


def build_timeline_track(ctx, timeline_track):
    kind = timeline_track["kind"]
    if kind in ("audio", "text") and not timeline_track["segments"]:
        return
    if kind == "video":
        build_video_track(ctx, timeline_track)
    elif kind == "audio":
        build_audio_track(ctx, timeline_track)
    elif kind == "text":
        build_text_track(ctx, timeline_track)
    else:  # normalize_timeline admits only video/audio/text/image track kinds
        build_image_track(ctx, timeline_track)
