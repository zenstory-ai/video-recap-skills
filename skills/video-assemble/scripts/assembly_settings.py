"""Render-affecting settings fingerprint for cache/resume decisions."""

from pathlib import Path

from artifacts import _artifact_fingerprint
from assemble_constants import (
    SUBTITLE_RENDER_VERSION,
    SUBTITLE_TEXT_NORMALIZE_VERSION,
    VISUAL_OVERLAYS,
)
from audio_mix import _loudness_mode, final_loudnorm_filter
from lib import CONFIG
from source_subtitles import _has_user_subtitles, _source_subtitle_mask_policy
from subtitle_core import _subtitle_style_config

AUDIO_MODES = ("narration", "source-mix", "adopted-packet-copy")


def assembly_settings_fingerprint(work_dir=None, *, audio_mode="narration", audio_stream_index=0):
    """Settings that affect the rendered video, used by pipeline resume cache. When work_dir is
    given, a user_subtitles presence flag is included so dropping in a user-subtitle file rebuilds
    the cached subtitles."""
    burn_subtitles = CONFIG["burn_subtitles"]
    mask_policy = _source_subtitle_mask_policy(work_dir)
    mask_source_subtitles = mask_policy["active"]
    overlay_fingerprint = (
        _artifact_fingerprint(Path(work_dir) / VISUAL_OVERLAYS) if work_dir is not None else None
    )
    if audio_mode not in AUDIO_MODES:
        raise ValueError(f"unsupported audio mode: {audio_mode}")
    fingerprint = {
        "version": SUBTITLE_RENDER_VERSION,
        "subtitle_text_normalize": SUBTITLE_TEXT_NORMALIZE_VERSION,
        "user_subtitles": _has_user_subtitles(work_dir),
        "burn_subtitles": burn_subtitles,
        "force_video_reencode": CONFIG["force_video_reencode"],
        "encode": {
            "output_crf": CONFIG["output_crf"],
            "output_preset": CONFIG["output_preset"],
            "output_max_height": CONFIG["output_max_height"],
        },
        "video_filters": {
            "mask_source_subtitles": mask_source_subtitles,
            "source_subtitle_mask_policy": mask_policy["policy"],
            "source_subtitle_mask_policy_declared": mask_policy["declared"],
            "source_subtitle_mask_policy_trigger": mask_policy["trigger"],
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
                "present": overlay_fingerprint is not None,
                "fingerprint": overlay_fingerprint,
            },
        },
        "audio": {
            "mode": audio_mode,
            "selected_stream_index": audio_stream_index,
        },
    }
    if audio_mode == "narration":
        fingerprint["narration_timing"] = {
            "delay_seconds": CONFIG["narration_delay_seconds"],
            "tail_pad_seconds": CONFIG["narration_tail_pad_seconds"],
            "fade_ms": CONFIG["fade_ms"],
            "narration_speed": CONFIG["narration_speed"],
            "narration_cumulative_tempo_max": CONFIG["narration_cumulative_tempo_max"],
            "tts_segment_tempo_max": CONFIG["tts_segment_tempo_max"],
        }
    if audio_mode in {"narration", "source-mix"}:
        # adopted-packet-copy never decodes or mixes, so mix settings cannot change it.
        fingerprint["audio_mix"] = {
            "ducking_mode": CONFIG["ducking_mode"],
            "duck_fade_seconds": CONFIG["duck_fade_seconds"],
            "duck_bridge_seconds": CONFIG["duck_bridge_seconds"],
            "ducking_narr_weight": CONFIG["ducking_narr_weight"],
            "ducking_orig_volume": CONFIG["ducking_orig_volume"],
            "idle_orig_volume": CONFIG["idle_orig_volume"],
            "speech_ducking_volume": CONFIG["speech_ducking_volume"],
            "zone_ducking_volume": CONFIG["zone_ducking_volume"],
            "ducking_threshold": CONFIG["ducking_threshold"],
            "ducking_ratio": CONFIG["ducking_ratio"],
            "ducking_attack": CONFIG["ducking_attack"],
            "ducking_release": CONFIG["ducking_release"],
            "ducking_level_sc": CONFIG["ducking_level_sc"],
            "ducking_makeup": CONFIG["ducking_makeup"],
            "final_loudnorm": final_loudnorm_filter(),
            "loudness_mode": _loudness_mode(),
            "bgm_path": CONFIG["bgm_path"],
            "bgm_volume": CONFIG["bgm_volume"],
            "bgm_ducking_volume": CONFIG["bgm_ducking_volume"],
        }
    if burn_subtitles:
        fingerprint["subtitle_renderer"] = "ass"
        fingerprint["subtitle_style"] = _subtitle_style_config()
    return fingerprint
