"""Validate recap audio ownership and removed-route guards."""

import json
from pathlib import Path

from lib import read_json_object


AUDIO_MODES = ("narration", "source-mix")

# The delivery family (frozen sound, strict adoption, explicit subtitle track) was removed;
# v0.6.2 is the fallback. These names stay only so an old work_dir fails instead of silently
# resuming through another route.
REMOVED_AUDIO_MODE = "adopted-packet-copy"
_REMOVED_DELIVERY_ARTIFACTS = (
    "subtitle_track.json",
    "subtitle_track_validation.json",
    "audio_mix_binding.json",
    ".narration_input_snapshots",
    ".explicit_audio_mix",
)

_TTS_OPTIONS = frozenset({
    "--tts-provider",
    "--mimo-tts-voice",
    "--voice-ref",
    "--allow-partial-tts",
})

def uses_narration(args):
    return args.audio_mode == "narration"


def audio_binding(args):
    return {
        "mode": args.audio_mode,
        # Constant: resume compares this record against the manifest of older runs.
        "selected_stream_index": 0,
    }


def reject_removed_dub_workdir(work_dir):
    """A work_dir left by the removed dub mode must not silently resume as another mode."""
    work_dir = Path(work_dir)
    try:
        manifest = json.loads((work_dir / "recap_run_manifest.json").read_text(encoding="utf-8"))
        edit_mode = manifest["settings"]["edit_mode"]
    except (OSError, ValueError, KeyError, TypeError):
        edit_mode = None
    if edit_mode == "dub" or any(
        (work_dir / name).exists()
        for name in ("dub_transcript.json", "dub_script.json", "dub_manifest.json")
    ):
        raise SystemExit(
            f"{work_dir} 是已移除的 dub 模式留下的工作目录，不能续跑；"
            "请使用新的 --work-dir，仍需英译中克隆配音请使用 v0.6.2"
        )


def removed_route_message(what):
    return (
        f"{what} 属于已移除的声音定稿交付路线（adopted-packet-copy、narration/audio-mix adoption、"
        "subtitle_track）；请使用新的 --work-dir 走当前路线，仍需这些路线请使用 v0.6.2"
    )


def reject_removed_delivery_workdir(work_dir):
    """A work_dir left by a removed delivery route must not silently resume as another route.

    A malformed file reads as absent: it is not evidence of a removed route."""
    work_dir = Path(work_dir)
    for name in _REMOVED_DELIVERY_ARTIFACTS:
        if (work_dir / name).exists():
            raise SystemExit(removed_route_message(str(work_dir / name)))
    binding_path = work_dir / "narration_input_binding.json"
    if (read_json_object(binding_path) or {}).get("identity_status") == "BOUND_TO_ADOPTION":
        raise SystemExit(removed_route_message(str(binding_path)))
    for name in ("assembly_manifest.json", "assembly_qc.json"):
        if (read_json_object(work_dir / name) or {}).get("audio_mode") == REMOVED_AUDIO_MODE:
            raise SystemExit(removed_route_message(str(work_dir / name)))
    manifest = read_json_object(work_dir / "recap_run_manifest.json") or {}
    audio = manifest.get("audio")
    if "local_adoption_run" in manifest or (
        isinstance(audio, dict)
        and (audio.get("mode") == REMOVED_AUDIO_MODE or "local_adoption" in audio)
    ):
        raise SystemExit(removed_route_message(str(work_dir / "recap_run_manifest.json")))


def reject_unbound_narration_workdir(work_dir, args):
    """Do not reinterpret an unbound narrated work directory as source-owned audio."""
    work_dir = Path(work_dir)
    if (
        not uses_narration(args)
        and not (work_dir / "recap_run_manifest.json").exists()
        and (work_dir / "narration.json").exists()
    ):
        raise SystemExit(
            "当前 work_dir 含 narration.json 但缺少可验证的音频运行策略；"
            "非解说模式请使用新的 --work-dir"
        )


def validate_audio_routing(parser, args):
    """Reject combinations the current pipeline cannot execute truthfully."""
    mode = args.audio_mode
    # index-tts + MiMo voice/--voice-ref is rejected once, in recap_runner.main, after the
    # ambient VOICE_REF/MIMO_TTS_VOICE have been folded in.
    if mode == "narration":
        return

    conflicts = sorted(set(args._explicit_options).intersection(_TTS_OPTIONS))
    if conflicts:
        detail = ", ".join(conflicts)
        parser.error(f"--audio-mode {mode} cannot use TTS/narration options: {detail}")
    # Environment defaults are irrelevant once source audio owns the run. Normalizing
    # prevents an invalid ambient provider from leaking into later state or continuation.
    args.tts_provider = "auto"
