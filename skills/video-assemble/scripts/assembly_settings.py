"""Render-affecting settings payload recorded in assembly_manifest.json for audit."""

from pathlib import Path

from artifacts import _artifact_identity
from assemble_constants import VISUAL_OVERLAYS
from loudness import _loudness_mode, final_loudnorm_filter
from lib import CONFIG
from source_subtitles import _has_user_subtitles, _source_subtitle_mask
from subtitles.core import _subtitle_style_config
from packaging import packaging_settings


def assembly_settings_payload(work_dir=None, *, audio_mode="narration"):
    """Settings that affect the rendered video, as a plain nested dict. assembly_manifest.json
    records it for audit; nothing compares it to decide a rebuild. When work_dir is given, a
    user_subtitles presence flag and the ``{size, mtime_ns}`` identity of the overlay input
    are included."""
    burn_subtitles = CONFIG["burn_subtitles"]
    mask_source_subtitles = _source_subtitle_mask()["active"]
    overlay_identity = (
        _artifact_identity(Path(work_dir) / VISUAL_OVERLAYS) if work_dir is not None else None
    )
    settings = {
        "user_subtitles": _has_user_subtitles(work_dir),
        "burn_subtitles": burn_subtitles,
        "subtitle_burn_degraded": CONFIG["burn_subtitles_degraded"],
        "force_video_reencode": CONFIG["force_video_reencode"],
        "encode": {
            "output_crf": CONFIG["output_crf"],
            "output_preset": CONFIG["output_preset"],
            "output_max_height": CONFIG["output_max_height"],
        },
        "video_filters": {
            "mask_source_subtitles": mask_source_subtitles,
            "source_subtitle_mask_ratio": (
                CONFIG["source_subtitle_mask_ratio"] if mask_source_subtitles else None
            ),
            "source_subtitle_mask_timing": (
                CONFIG["source_subtitle_mask_timing"] if mask_source_subtitles else None
            ),
            "subtitle_mask_opacity": (
                CONFIG["subtitle_mask_opacity"] if mask_source_subtitles else None
            ),
            "subtitle_mask_padding": (
                CONFIG["subtitle_mask_padding"] if mask_source_subtitles else None
            ),
            "subtitle_y_top": CONFIG["subtitle_y_top"],
            "subtitle_y_bot": CONFIG["subtitle_y_bot"],
            "visual_overlays": {
                "artifact": VISUAL_OVERLAYS,
                "present": overlay_identity is not None,
                "identity": overlay_identity,
            },
            "packaging_layers": packaging_settings(work_dir),
        },
        "audio": {
            "mode": audio_mode,
            "selected_stream_index": 0,
        },
    }
    if audio_mode == "narration":
        settings["narration_timing"] = {
            "fade_ms": CONFIG["fade_ms"],
            "narration_speed": CONFIG["narration_speed"],
            "tempo_source": "configuration",
            "narration_cumulative_tempo_max": CONFIG["narration_cumulative_tempo_max"],
            "tts_segment_tempo_max": CONFIG["tts_segment_tempo_max"],
        }
    settings["audio_mix"] = {
        "duck_fade_seconds": CONFIG["duck_fade_seconds"],
        "duck_bridge_seconds": CONFIG["duck_bridge_seconds"],
        "ducking_narr_weight": CONFIG["ducking_narr_weight"],
        "idle_orig_volume": CONFIG["idle_orig_volume"],
        "speech_ducking_volume": CONFIG["speech_ducking_volume"],
        "zone_ducking_volume": CONFIG["zone_ducking_volume"],
        "final_loudnorm": final_loudnorm_filter(),
        "loudness_mode": _loudness_mode(),
        "loudness_limiter_max_db": CONFIG["loudness_limiter_max_db"],
        "bgm_path": CONFIG["bgm_path"],
        "bgm_volume": CONFIG["bgm_volume"],
        "bgm_ducking_volume": CONFIG["bgm_ducking_volume"],
    }
    if burn_subtitles:
        settings["subtitle_renderer"] = "ass"
        settings["subtitle_style"] = _subtitle_style_config()
    return settings
