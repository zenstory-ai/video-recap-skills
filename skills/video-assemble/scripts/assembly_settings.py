"""Render-affecting settings payload recorded in the manifest and compared by resume logic."""

from pathlib import Path

from artifacts import _artifact_identity
from assemble_constants import VISUAL_OVERLAYS
from audio_mix import _loudness_mode, final_loudnorm_filter
from lib import CONFIG
from source_subtitles import _has_user_subtitles, _source_subtitle_mask_policy
from subtitles.core import _subtitle_style_config
from adoption.narration_binding import binding_record
from adoption.audio_mix_binding import binding_record as audio_mix_binding_record


def assembly_settings_payload(work_dir=None, *, audio_mode="narration", audio_stream_index=0):
    """Settings that affect the rendered video, as a plain nested dict compared with ``==`` by
    pipeline resume logic. When work_dir is given, a user_subtitles presence flag and the
    ``{size, mtime_ns}`` identity of the overlay/subtitle-track inputs are included so dropping
    in or rewriting one of those files rebuilds the cached subtitles."""
    burn_subtitles = CONFIG["burn_subtitles"]
    mask_policy = _source_subtitle_mask_policy(work_dir)
    mask_source_subtitles = mask_policy["active"]
    overlay_identity = (
        _artifact_identity(Path(work_dir) / VISUAL_OVERLAYS) if work_dir is not None else None
    )
    subtitle_track_identity = (
        _artifact_identity(Path(work_dir) / "subtitle_track.json")
        if work_dir is not None else None
    )
    settings = {
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
                "present": overlay_identity is not None,
                "identity": overlay_identity,
            },
        },
        "audio": {
            "mode": audio_mode,
            "selected_stream_index": audio_stream_index,
        },
    }
    explicit_mix = (
        audio_mix_binding_record(work_dir)
        if work_dir is not None and audio_mode == "narration" else None
    )
    if explicit_mix:
        settings["audio"]["path"] = "explicit_adopted_full_sound"
        settings["audio_mix_binding"] = explicit_mix
    if audio_mode == "narration":
        narration_binding = binding_record(work_dir) if work_dir else None
        settings["narration_input_binding"] = narration_binding
        adopted_tempo = (
            narration_binding.get("tempo_policy") if narration_binding else None
        )
        settings["narration_timing"] = {
            "delay_seconds": 0.0 if explicit_mix else CONFIG["narration_delay_seconds"],
            "tail_pad_seconds": 0.0 if explicit_mix else CONFIG["narration_tail_pad_seconds"],
            "fade_ms": 0 if explicit_mix else CONFIG["fade_ms"],
            "narration_speed": (
                1.0 if explicit_mix else
                adopted_tempo["global_atempo"] if adopted_tempo else CONFIG["narration_speed"]
            ),
            "tempo_source": (
                "explicit_audio_mix" if explicit_mix else
                "adoption" if adopted_tempo else "configuration"
            ),
            "narration_cumulative_tempo_max": CONFIG["narration_cumulative_tempo_max"],
            "tts_segment_tempo_max": (
                adopted_tempo["segment_tempo_max"]
                if explicit_mix and adopted_tempo else CONFIG["tts_segment_tempo_max"]
            ),
        }
        if explicit_mix and adopted_tempo:
            settings["narration_timing"]["narration_cumulative_tempo_max"] = \
                adopted_tempo["cumulative_tempo_max"]
            settings["narration_timing"]["narration_cumulative_tempo_hard_max"] = \
                adopted_tempo["cumulative_tempo_hard_max"]
    if audio_mode in {"narration", "source-mix"} and not explicit_mix:
        # adopted-packet-copy never decodes or mixes, so mix settings cannot change it.
        settings["audio_mix"] = {
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
        settings["subtitle_renderer"] = "ass"
        settings["subtitle_style"] = _subtitle_style_config()
    if subtitle_track_identity is not None:
        settings["subtitle_track"] = {
            "artifact": "subtitle_track.json",
            "identity": subtitle_track_identity,
        }
    return settings
