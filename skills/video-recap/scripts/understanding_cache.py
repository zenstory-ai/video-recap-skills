"""Own understanding-stage cache keys, provenance, and statuses."""

import json

from pathlib import Path

from asr_timing_evidence import (
    EVIDENCE_FILENAME,
    _identity as _artifact_identity,
    validate_asr_timing_evidence,
)
from extract import FRAME_TIME_CONVENTION_VERSION
from lib import (
    CONFIG,
    file_identity,
    offline_ignored_settings,
)


from vlm import vlm_prompt_payload


def _artifact_meta_path(artifact_path):
    artifact_path = Path(artifact_path)
    return artifact_path.with_name(f"{artifact_path.name}.meta.json")


def _load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _video_input(video_path):
    """The source video as a cache input: resolved path plus {size, mtime_ns}, so two
    different files written in the same timestamp tick never share a stage output."""
    return {"path": str(Path(video_path).resolve()), **file_identity(video_path)}


def _without_settings(meta, keys):
    settings = meta.get("settings")
    if not keys or not isinstance(settings, dict):
        return meta
    return {**meta, "settings": {k: v for k, v in settings.items() if k not in keys}}


def _stage_cache_valid(artifact_path, expected_meta, *, ignore_settings=()):
    """A stage output is reusable when it exists, is non-empty, its sidecar equals the
    expected inputs/settings dict, and the output itself is the one the sidecar recorded.

    `ignore_settings` names `settings` keys left out of the comparison on both sides (an
    endpoint URL that only reflects which credential is set, or a retired legacy key)."""
    artifact_path = Path(artifact_path)
    if not artifact_path.exists() or artifact_path.stat().st_size == 0:
        return False
    meta_path = _artifact_meta_path(artifact_path)
    if not meta_path.exists():
        return False
    try:
        meta = _load_json(meta_path)
    except (OSError, ValueError, TypeError):
        return False
    if not isinstance(meta, dict) or meta.get("artifact") != file_identity(artifact_path):
        return False
    expected = dict(expected_meta)
    expected["artifact"] = meta["artifact"]
    return _without_settings(meta, ignore_settings) == _without_settings(
        expected, ignore_settings
    )


def _write_stage_meta(artifact_path, meta):
    meta_path = _artifact_meta_path(artifact_path)
    payload = dict(meta)
    payload["artifact"] = _artifact_identity(artifact_path)
    meta_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _remove_stage_meta(artifact_path):
    _artifact_meta_path(artifact_path).unlink(missing_ok=True)


def _scene_cache_payload(video_path):
    return {
        "schema_version": 1,
        "stage": "scenes",
        "inputs": {"video": _video_input(video_path)},
        "settings": {
            "scene_threshold": CONFIG.get("scene_threshold"),
            "scene_junk_filter": CONFIG.get("scene_junk_filter"),
            "scene_junk_dark_luma": CONFIG.get("scene_junk_dark_luma"),
            "scene_junk_bright_luma": CONFIG.get("scene_junk_bright_luma"),
            "scene_junk_pixel_ratio": CONFIG.get("scene_junk_pixel_ratio"),
        },
    }


def _asr_cache_payload(video_path, *, skip_asr=False):
    return {
        "schema_version": 1,
        "stage": "asr",
        "inputs": {"video": _video_input(video_path)},
        "settings": {
            "skip_asr": bool(skip_asr),
            "mimo_asr_api_url": CONFIG.get("mimo_asr_api_url"),
            "mimo_asr_model": CONFIG.get("mimo_asr_model"),
            "mimo_asr_language": CONFIG.get("mimo_asr_language"),
            "mimo_asr_base64_max_mb": CONFIG.get("mimo_asr_base64_max_mb"),
            "asr_segment_seconds": CONFIG.get("asr_segment_seconds"),
        },
    }


def _asr_cache_state(artifact_path, expected_meta, video_path):
    """Classify an ASR cache without upgrading stale evidence into new authority."""
    artifact_path = Path(artifact_path)
    ignored = offline_ignored_settings(CONFIG.get("mimo_asr_api_key"), "mimo_asr_api_url")
    if not _stage_cache_valid(artifact_path, expected_meta, ignore_settings=ignored):
        return "MISS"
    evidence_path = artifact_path.parent / EVIDENCE_FILENAME
    if validate_asr_timing_evidence(evidence_path, video_path, artifact_path):
        evidence = _load_json(evidence_path)
        # An all-empty transcription is an unexplained outcome, not proven silence; treating it
        # as fresh would make one bad run a permanent cache hit. A key-less placeholder is
        # retried too, so setting the key later transcribes for real.
        if evidence.get("status") in {
            "UNAVAILABLE_NO_DURATION", "EMPTY_UNKNOWN", "UNAVAILABLE_NO_KEY"
        }:
            return "MISS"
        return "FRESH"
    return "MISS"


def _silence_cache_payload(video_path, asr_json):
    return {
        "schema_version": 1,
        "stage": "silence",
        "inputs": {
            "video": _video_input(video_path),
            "asr_result": _artifact_identity(asr_json),
        },
        "asr_meta": _load_json(_artifact_meta_path(asr_json))
        if _artifact_meta_path(asr_json).exists()
        else None,
        "settings": {
            "silence_noise_threshold": CONFIG.get("silence_noise_threshold"),
            "silence_min_duration": CONFIG.get("silence_min_duration"),
            "quiet_window_min": CONFIG.get("quiet_window_min"),
            "silence_merge_gap": CONFIG.get("silence_merge_gap"),
            "source_boundary_noise_threshold": CONFIG.get(
                "source_boundary_noise_threshold"
            ),
            "source_boundary_min_pause": CONFIG.get("source_boundary_min_pause"),
            "source_boundary_max_alignment_error": CONFIG.get(
                "source_boundary_max_alignment_error"
            ),
        },
    }


def _vlm_cache_payload(video_path, work_dir, scenes_json, frames):
    return {
        "schema_version": 1,
        "stage": "vlm",
        "inputs": {
            "video": _video_input(video_path),
            "scenes": _artifact_identity(scenes_json),
            "background_research": _artifact_identity(
                Path(work_dir) / "background_research.json"
            ),
        },
        "frames": _frame_cache_payload(video_path, CONFIG.get("fps"), frames),
        "prompt": vlm_prompt_payload(),
        "settings": {
            "fps": CONFIG.get("fps"),
            "vlm_model": CONFIG.get("vlm_model"),
            "api_url": CONFIG.get("api_url"),
            "mimo_disable_thinking": CONFIG.get("mimo_disable_thinking", True),
            "mimo_media_resolution": CONFIG.get("mimo_media_resolution"),
        },
    }


def _frames_manifest_path(work_dir):
    return Path(work_dir) / "frames" / "frames_manifest.json"


def _frame_cache_payload(video_path, fps, frames):
    frame_names = [Path(frame).name for frame in frames]
    return {
        # v2: frame number→time is (n-1)/fps, not n/fps. Bumping invalidates frames/VLM
        # artifacts produced under the old off-by-one so they are recomputed, not reused.
        "schema_version": FRAME_TIME_CONVENTION_VERSION,
        "inputs": {"video": _video_input(video_path)},
        "fps": float(fps),
        "frame_count": len(frames),
        "frames": frame_names,
    }


def _write_frames_manifest(work_dir, video_path, fps, frames):
    manifest_path = _frames_manifest_path(work_dir)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            _frame_cache_payload(video_path, fps, frames), ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )


def _frames_cache_valid(video_path, work_dir, fps):
    frames_dir = Path(work_dir) / "frames"
    frames = sorted(frames_dir.glob("frame_*.jpg"))
    if not frames:
        return False
    manifest_path = _frames_manifest_path(work_dir)
    if not manifest_path.exists():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected = _frame_cache_payload(video_path, fps, frames)
    except (OSError, ValueError, TypeError):
        return False
    return manifest == expected
