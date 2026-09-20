"""Validate recap audio ownership without importing another skill."""

from pathlib import Path


AUDIO_MODES = ("narration", "source-mix", "adopted-packet-copy")

_TTS_OPTIONS = frozenset({
    "--tts-provider",
    "--mimo-tts-voice",
    "--voice-ref",
    "--allow-partial-tts",
    "--review-narration",
    "--no-review-narration",
    "--require-narration-review",
})

def uses_narration(args):
    return getattr(args, "audio_mode", "narration") == "narration"


def needs_voiceover(args):
    return uses_narration(args)


def audio_binding(args):
    return {
        "mode": getattr(args, "audio_mode", "narration"),
        "selected_stream_index": getattr(args, "audio_stream_index", 0),
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
    mode = getattr(args, "audio_mode", "narration")
    stream = getattr(args, "audio_stream_index", 0)
    if isinstance(stream, bool) or not isinstance(stream, int) or stream < 0:
        parser.error("--audio-stream-index must be a non-negative integer")
    if args.edit_mode == "dub" and mode != "narration":
        parser.error("--edit-mode dub cannot be combined with a non-narration --audio-mode")
    if mode == "narration":
        if stream != 0:
            parser.error("narration currently requires --audio-stream-index 0")
        return

    explicit = set(getattr(args, "_explicit_options", ()))
    conflicts = sorted(explicit.intersection(_TTS_OPTIONS))
    if conflicts:
        detail = ", ".join(conflicts)
        parser.error(f"--audio-mode {mode} cannot use TTS/strict narration options: {detail}")
    if args.mimo_qc != "off":
        parser.error(f"--audio-mode {mode} currently requires --mimo-qc off")
    if args.edit_mode == "cut" and stream != 0:
        parser.error("cut audio modes currently require --audio-stream-index 0")
    if args.export_jianying and stream != 0:
        parser.error("JianYing export currently requires --audio-stream-index 0")
    # Environment defaults are irrelevant once source audio owns the run. Normalizing
    # prevents an invalid ambient provider from leaking into later state or continuation.
    args.tts_provider = "auto"


def extend_assemble_args(cli_args, args):
    mode = getattr(args, "audio_mode", "narration")
    stream = getattr(args, "audio_stream_index", 0)
    if mode != "narration":
        cli_args += ["--audio-mode", mode]
    if stream != 0:
        cli_args += ["--audio-stream-index", str(stream)]
    return cli_args


def begin_non_narration_qc(work_dir, args, write_stage):
    """Start a clean run-local QC ledger without reading or deleting old TTS."""
    (Path(work_dir) / "preflight_qc.json").unlink(missing_ok=True)
    return write_stage(
        work_dir,
        "pre_assemble",
        metadata={
            "audio_mode": args.audio_mode,
            "selected_audio_stream_index": args.audio_stream_index,
            "tts": "not_applicable",
            "narration_validation": "not_applicable",
            "narration_review": "not_applicable",
            "visual_overlays": "preserved_not_authored_by_this_run",
        },
    )
