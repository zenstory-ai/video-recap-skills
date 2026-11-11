"""Validate recap audio ownership and routing."""

from pathlib import Path


AUDIO_MODES = ("narration", "source-mix")

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
