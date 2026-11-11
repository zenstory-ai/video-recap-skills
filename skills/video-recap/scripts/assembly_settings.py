"""Render-affecting settings payload recorded in assembly_manifest.json for audit."""

from pathlib import Path

from artifacts import _artifact_identity
from loudness import _loudness_mode, final_loudnorm_filter
from lib import CONFIG
from source_subtitles import _source_subtitle_mask
from subtitles.core import _subtitle_style_config
from packaging import packaging_settings, studio_packaging_settings
from sfx_mix import sfx_settings


def assembly_settings_payload(work_dir=None, *, audio_mode="narration"):
    """Settings that affect the rendered video, recorded in assembly_manifest.json for audit."""
    burn_subtitles = CONFIG["burn_subtitles"]
    mask_source_subtitles = _source_subtitle_mask()["active"]
    original_subs_identity = (
        _artifact_identity(Path(work_dir) / "original_subtitles.json")
        if work_dir is not None
        else None
    )
    settings = {
        "original_subtitles": original_subs_identity is not None,
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
        "ending_audio_fade_ms": CONFIG["ending_audio_fade_ms"],
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
    studio_meta = studio_packaging_settings(work_dir)
    if studio_meta is not None:
        settings["video_filters"]["packaging_plan"] = studio_meta
    sfx_meta = sfx_settings(work_dir)
    if sfx_meta["present"]:
        settings["audio_mix"]["sfx"] = sfx_meta
    return settings
