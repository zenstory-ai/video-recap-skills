"""Backend-neutral timeline emission for assemble.py."""

from pathlib import Path

from audio_mix import _seg_place_window
from lib import CONFIG, log
from media import _build_video_clips
from source_subtitles import _combined_subtitle_entries
from subtitles.core import _effective_subtitle_style
from timeline import build_timeline, save_timeline
import packaging

def _timeline_subtitle_segments(tts_segments, work_dir, duration_s, canvas=None):
    """Display-ready subtitle cues for timeline/export text tracks.

    The narration audio track keeps raw semantic text for editor reference; this
    payload mirrors SRT/ASS display policy, including terminal-punctuation cleanup
    and original-dialogue gap subtitles when configured.
    """
    style = _effective_subtitle_style(canvas, work_dir) if canvas is not None else None
    if (
        style is None
        or (
            int(style["max_chars"]) == int(CONFIG["subtitle_max_chars"])
            and float(style.get("bridge_gap_seconds", 0.0)) <= 0.0
            and not packaging.has_packaging_plan(work_dir)
        )
    ):
        entries = _combined_subtitle_entries(tts_segments, work_dir, duration_s)
    else:
        entries = _combined_subtitle_entries(
            tts_segments, work_dir, duration_s,
            max_chars=int(style["max_chars"]),
            bridge_gap_seconds=float(style.get("bridge_gap_seconds", 0.0)),
        )
    return [
        {
            "text": entry["text"],
            "timeline_start": float(entry["start"]),
            "timeline_end": float(entry["end"]),
        }
        for entry in entries
    ]


def _emit_timeline(input_video, tts_segments, work_dir, duration_s, canvas, has_bgm, *,
                   audio_mode="narration", studio_packaging=None):
    """Build and persist the backend-neutral multi-track timeline.json."""
    if audio_mode == "narration":
        video_clips = _build_video_clips(input_video, work_dir, duration_s)
    else:
        # Non-narration sound comes from the actual current picture input as one
        # complete interval. Re-expanding an old cut plan would substitute different
        # source sound and make the optional editor project misrepresent the render.
        video_clips = [{
            "source_path": str(Path(input_video)),
            "source_start": 0.0,
            "source_end": float(duration_s),
            "timeline_start": 0.0,
            "timeline_end": float(duration_s),
        }]
    narration_segments = []
    for seg in tts_segments:
        s, e = _seg_place_window(seg)
        narration_item = {
            # JianYing must consume the exact WAV written into narration.wav. In
            # particular, a tempo-adjusted beat cannot reference its longer pre-fit
            # source or the editor will trim its final words at timeline_end.
            "source_path": seg["placed_audio_path"],
            "timeline_start": s, "timeline_end": e,
            "text": seg["narration"],
            "overlaps_speech": seg["overlaps_speech"],
            "gain": 1.0,
        }
        for key in ("source_duck_end", "source_restore_at", "source_handoff_status", "source_entry_status"):
            if key in seg:
                narration_item[key] = seg[key]
        narration_segments.append(narration_item)
    fade = CONFIG["duck_fade_seconds"]
    bgm = None
    if has_bgm:
        bgm = {"source_path": CONFIG["bgm_path"],
               "volume": CONFIG["bgm_volume"],
               "ducking_volume": CONFIG["bgm_ducking_volume"],
               "fade": fade}
    # narration mode always ducks; the draft carries the same envelope as editable
    # volume keyframes (ffmpeg stays the canonical mix)
    ducking = None
    if audio_mode == "narration":
        ducking = {"idle": CONFIG["idle_orig_volume"],
                   "speech": CONFIG["speech_ducking_volume"],
                   "quiet": CONFIG["zone_ducking_volume"],
                   "fade": fade,
                   "bridge": CONFIG["duck_bridge_seconds"]}
    subtitle_segments = _timeline_subtitle_segments(tts_segments, work_dir, duration_s, canvas)
    image_segments = (
        packaging.studio_timeline_segments(studio_packaging, duration_s)
        + packaging.timeline_image_segments(
            packaging.load_packaging_layers(work_dir, canvas), canvas, duration_s
        )
    )
    timeline = build_timeline(canvas, duration_s, video_clips,
                              narration_segments, bgm=bgm, ducking=ducking,
                              subtitle_segments=subtitle_segments,
                              image_segments=image_segments)
    if audio_mode != "narration":
        for clip in timeline["tracks"][0]["clips"]:
            clip["audio"]["base_gain"] = round(float(CONFIG["idle_orig_volume"]), 4)
    degraded = [
        {"source_path": clip["source_path"], "reason": clip["provenance_reason"]}
        for clip in video_clips
        if clip.get("provenance_degraded")
    ]
    if degraded:
        timeline["provenance"] = {"degraded": True, "degraded_clips": degraded}
        log(f"  ⚠️ 时间线 provenance 降级: {degraded[0]['reason']} ({len(degraded)} clip)")
    else:
        timeline["provenance"] = {"degraded": False}
    out = Path(work_dir) / "timeline.json"
    save_timeline(timeline, out)
    log(f"时间线模型: {out} ({len(timeline['tracks'])} 轨)")
    return timeline
