"""Backend-neutral timeline emission for the video-assemble skill."""

from pathlib import Path

from audio_mix import _seg_place_window
from lib import CONFIG, log
from media import _build_video_clips
from source_subtitles import _combined_subtitle_entries
from timeline import build_timeline, save_timeline

def _timeline_subtitle_segments(tts_segments, work_dir, duration_s):
    """Display-ready subtitle cues for timeline/export text tracks.

    The narration audio track keeps raw semantic text for editor reference; this
    payload mirrors SRT/ASS display policy, including terminal-punctuation cleanup
    and original-dialogue gap subtitles when configured.
    """
    return [
        {
            "text": entry["text"],
            "timeline_start": float(entry["start"]),
            "timeline_end": float(entry["end"]),
        }
        for entry in _combined_subtitle_entries(tts_segments, work_dir, duration_s)
    ]


def _emit_timeline(input_video, tts_segments, work_dir, duration_s, canvas, has_bgm, *,
                   audio_mode="narration", selected_audio_stream=0):
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
        if e <= s:
            continue
        narration_item = {
            # JianYing must consume the exact WAV written into narration.wav. In
            # particular, a tempo-adjusted beat cannot reference its longer pre-fit
            # source or the editor will trim its final words at timeline_end.
            "source_path": seg["placed_audio_path"],
            "timeline_start": s, "timeline_end": e,
            "text": seg["narration"],
            "overlaps_speech": seg.get("overlaps_speech", True),
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
    # carry ducking automation whenever ducking is on at all; even under sidechain
    # mode the draft gets editable volume keyframes (ffmpeg stays the canonical mix)
    ducking = None
    if audio_mode == "narration" and CONFIG["ducking_mode"] != "none":
        ducking = {"idle": CONFIG["idle_orig_volume"],
                   "speech": CONFIG["speech_ducking_volume"],
                   "quiet": CONFIG["zone_ducking_volume"],
                   "fade": fade,
                   "bridge": CONFIG["duck_bridge_seconds"]}
    subtitle_segments = _timeline_subtitle_segments(tts_segments, work_dir, duration_s)
    timeline = build_timeline(canvas, duration_s, video_clips,
                              narration_segments, bgm=bgm, ducking=ducking,
                              subtitle_segments=subtitle_segments)
    if audio_mode != "narration":
        source_gain = 1.0 if audio_mode == "adopted-packet-copy" else CONFIG["idle_orig_volume"]
        for clip in timeline["tracks"][0]["clips"]:
            clip["audio"].update({
                "base_gain": round(float(source_gain), 4),
                "selected_stream": selected_audio_stream,
                "mode": audio_mode,
            })
        timeline["audio_delivery"] = {
            "mode": audio_mode,
            "selected_stream": selected_audio_stream,
            "packet_frozen": audio_mode == "adopted-packet-copy",
            "reconstructable": (
                audio_mode == "adopted-packet-copy" and selected_audio_stream == 0
            ),
            "canonical_renderer": "stream_copy" if audio_mode == "adopted-packet-copy" else "ffmpeg_mix",
        }
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
