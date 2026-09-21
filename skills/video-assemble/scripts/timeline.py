"""Multi-track timeline model for the recap (backend-neutral, stdlib only).

A `Timeline` is a small, serializable representation of the finished recap as a
set of tracks — exactly like a cut-tool project:

  - one **video** track: the source clip(s), each carrying its own *original
    audio* with a per-clip volume automation (the ducking: a continuous low bed
    under narration, held across short inter-sentence gaps, back up only at the
    lead-in/out and genuine long gaps);
  - one **narration** audio track: the placed TTS beats;
  - an optional **bgm** audio track: a looped music bed with its own ducking;
  - one **subtitle** (text) track: the narration lines.
  - optional **image** tracks: local photo overlays with normalized center-origin,
    Y-up transforms for editable JianYing export.

The canonical ducking semantics live in `audio_automation.py`; ffmpeg
(`assemble.py`) and this timeline model both derive their automation from that
shared source. This model is emitted as `timeline.json` and consumed by the
*optional* 剪映 exporter. The model itself knows nothing about ffmpeg or 剪映 —
times are plain seconds and volumes are plain gains, so any backend can read it.
"""

import json
import math
from copy import deepcopy

from audio_automation import fixed_ducking_keyframes as ducking_keyframes
from audio_automation import release_ducking_keyframes

SCHEMA_VERSION = 2


def _ceil_time(value, digits=4):
    """Round an interval end outward so serialization can never shorten media."""
    scale = 10 ** digits
    return math.ceil((float(value) * scale) - 1e-9) / scale


def _floor_time(value, digits=4):
    """Round an interval start outward so serialization cannot clip source samples."""
    scale = 10 ** digits
    return math.floor((float(value) * scale) + 1e-9) / scale


def build_timeline(canvas, duration_s, video_clips, narration_segments,
                   bgm=None, ducking=None, subtitle_segments=None,
                   image_segments=(), resource_packages=None,
                   style_presets=None, extra_tracks=()):
    """Assemble a Timeline dict from resolved placement data.

    canvas: {"width", "height", "fps"}
    duration_s: total output length (seconds)
    video_clips: ordered [{"source_path", "source_start", "source_end",
                 "timeline_start", "timeline_end"}] (cut mode: one per clip;
                 full mode: a single clip spanning the whole video).
    narration_segments: placed beats [{"source_path", "timeline_start",
                 "timeline_end", "text", "overlaps_speech", "gain"}]; a zero-width
                 beat (an unplaced segment) is skipped.
    subtitle_segments: optional display-ready text cues [{"text", "timeline_start",
                 "timeline_end"}]. When present, this is authoritative for the
                 subtitle/text track; narration segment text remains raw editor metadata.
    bgm: optional {"source_path", "volume", "ducking_volume", "fade"}.
    ducking: {"idle", "speech", "quiet", "fade", "bridge"} for the original-audio
             automation; None disables original ducking (flat original). `bridge` holds
             the duck across inter-beat gaps shorter than it.
    image_segments: optional v2 local image overlays [{"source_path", "timeline_start",
                 "timeline_end", ...authoring extensions}], passed through as authored.
    """
    placed = [
        s for s in narration_segments
        if float(s["timeline_end"]) > float(s["timeline_start"])
    ]
    windows = [(float(s["timeline_start"]), float(s["timeline_end"])) for s in placed]
    duck_windows = []
    if ducking is not None:
        for s in placed:
            end = float(s["timeline_end"])
            hold_end = max(end, float(s.get("source_duck_end", end)))
            restore_at = max(
                hold_end, float(s.get("source_restore_at", hold_end + float(ducking["fade"])))
            )
            level = float(ducking["speech" if s["overlaps_speech"] else "quiet"])
            duck_windows.append((float(s["timeline_start"]), hold_end, level, restore_at))

    # --- video track: each clip carries its original audio + ducking automation
    video_clip_objs = []
    for c in video_clips:
        ts, te = float(c["timeline_start"]), float(c["timeline_end"])
        audio = {"role": "original", "volume_keyframes": []}
        if ducking is not None:
            audio["volume_keyframes"] = release_ducking_keyframes(
                duck_windows, ducking["idle"], ducking["fade"], ts, te,
                bridge=ducking["bridge"])
            audio["base_gain"] = round(float(ducking["idle"]), 4)
        else:
            audio["base_gain"] = 1.0
        video_clip = {
            "source_path": c["source_path"],
            "source_start": round(float(c["source_start"]), 4),
            "source_end": round(float(c["source_end"]), 4),
            "timeline_start": round(ts, 4),
            "timeline_end": round(te, 4),
            "audio": audio,
        }
        for key in (
            "chroma", "compound", "flip", "green_background", "lut", "mask",
            "opacity", "position", "reverse", "reverse_path", "rotation_degrees",
            "scale", "speed", "transition",
        ):
            if key in c:
                video_clip[key] = deepcopy(c[key])
        video_clip_objs.append(video_clip)

    tracks = [{"kind": "video", "name": "video", "clips": video_clip_objs}]

    # --- narration track
    narr_segs = []
    for s in placed:
        narration = {
            "source_path": s["source_path"],
            "timeline_start": _floor_time(s["timeline_start"], 4),
            "timeline_end": _ceil_time(s["timeline_end"], 4),
            "gain": round(float(s["gain"]), 4),
            "text": s["text"],
            "overlaps_speech": bool(s["overlaps_speech"]),
        }
        for key in ("source_duck_end", "source_restore_at", "source_handoff_status", "source_entry_status"):
            if key in s:
                narration[key] = deepcopy(s[key])
        if "speed" in s:
            narration["speed"] = float(s["speed"])
        narr_segs.append(narration)
    if narr_segs:
        tracks.append({"kind": "audio", "name": "narration", "role": "narration",
                       "segments": narr_segs})

    # --- bgm track (optional, looped, ducked under narration)
    if bgm:
        base = float(bgm["volume"])
        duck = float(bgm["ducking_volume"])
        fade = float(bgm["fade"])
        kfs = ducking_keyframes(windows, base, duck, fade, 0.0, duration_s,
                                bridge=ducking["bridge"] if ducking else None)
        tracks.append({
            "kind": "audio", "name": "bgm", "role": "bgm", "loop": True,
            "segments": [{
                "source_path": bgm["source_path"],
                "timeline_start": 0.0,
                "timeline_end": round(float(duration_s), 4),
                "gain": round(base, 4),
                "volume_keyframes": kfs,
            }],
        })

    # --- subtitle (text) track: empty cues carry nothing to display
    text_source = subtitle_segments if subtitle_segments is not None else narration_segments
    text_segs = []
    for s in text_source:
        if not s.get("text"):
            continue
        ts, te = float(s["timeline_start"]), float(s["timeline_end"])
        if te <= ts:
            continue
        text_segment = {
            "text": s["text"],
            "timeline_start": round(ts, 4),
            "timeline_end": round(te, 4),
        }
        for key in ("flip", "opacity", "position", "rotation_degrees", "scale", "style", "style_id", "words"):
            if key in s:
                text_segment[key] = deepcopy(s[key])
        text_segs.append(text_segment)
    if text_segs:
        tracks.append({"kind": "text", "name": "subtitle", "segments": text_segs})

    # --- local image overlays (optional, timeline schema v2). Transform fields are
    # optional authoring extensions; the JianYing exporter validates and defaults them.
    images = []
    for segment in image_segments:
        image = {
            "source_path": segment["source_path"],
            "timeline_start": round(float(segment["timeline_start"]), 4),
            "timeline_end": round(float(segment["timeline_end"]), 4),
        }
        for key in ("flip", "lut", "mask", "opacity", "position", "rotation_degrees",
                    "scale", "speed", "transition"):
            if key in segment:
                image[key] = deepcopy(segment[key])
        images.append(image)
    if images:
        tracks.append({"kind": "image", "name": "image", "segments": images})

    tracks.extend(deepcopy(track) for track in extra_tracks)

    timeline = {
        "schema_version": SCHEMA_VERSION,
        "canvas": {"width": int(canvas["width"]), "height": int(canvas["height"]),
                   "fps": float(canvas["fps"])},
        "duration": round(float(duration_s), 4),
        "tracks": tracks,
    }
    if resource_packages:
        timeline["resource_packages"] = deepcopy(resource_packages)
    if style_presets:
        timeline["style_presets"] = deepcopy(style_presets)
    return timeline


def save_timeline(timeline, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(timeline, f, ensure_ascii=False, indent=2)
    return path


def load_timeline(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)
