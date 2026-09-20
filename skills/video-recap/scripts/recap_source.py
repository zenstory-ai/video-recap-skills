"""Validate recap audio ownership without importing another skill."""

import hashlib
import json
from pathlib import Path

import materials


AUDIO_MODES = ("narration", "source-mix", "adopted-packet-copy")

_TTS_OPTIONS = frozenset({
    "--tts-provider",
    "--mimo-tts-voice",
    "--voice-ref",
    "--allow-partial-tts",
    "--preserve-approved-text",
    "--review-narration",
    "--no-review-narration",
    "--require-narration-review",
})

_LOCAL_ADOPTION_OPTIONS = (
    ("tts_meta", "--tts-meta"),
    ("narration_adoption", "--narration-adoption"),
    ("audio_mix_adoption", "--audio-mix-adoption"),
)

_LOCAL_ADOPTION_CONFLICTS = _TTS_OPTIONS | frozenset({
    "--mimo-qc",
    "--mimo-qc-refresh",
    "--export-jianying",
    "--jianying-bundle-media",
    "--jianying-no-bundle-media",
})


def uses_narration(args):
    return getattr(args, "audio_mode", "narration") == "narration"


def uses_local_adoption(args):
    return all(getattr(args, field, None) is not None for field, _ in _LOCAL_ADOPTION_OPTIONS)


def needs_voiceover(args):
    return uses_narration(args) and not uses_local_adoption(args)


def audio_binding(args):
    binding = {
        "mode": getattr(args, "audio_mode", "narration"),
        "selected_stream_index": getattr(args, "audio_stream_index", 0),
    }
    if uses_local_adoption(args):
        binding["local_adoption"] = {
            field: {
                "path": str(Path(getattr(args, field)).resolve()),
                "sha256": materials.file_fingerprint(getattr(args, field)),
            }
            for field, _ in _LOCAL_ADOPTION_OPTIONS
        }
    return binding


def _resolve_local_file(parser, value, option):
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        parser.error(f"{option} does not exist or is not a file: {path}")
    return str(path)


def validate_local_adoption(parser, args):
    """Validate and resolve the assembly-only local adoption boundary."""
    supplied = [getattr(args, field, None) is not None for field, _ in _LOCAL_ADOPTION_OPTIONS]
    if any(supplied) and not all(supplied):
        parser.error("--tts-meta, --narration-adoption and --audio-mix-adoption are all-or-none")
    if not all(supplied):
        return
    if args.edit_mode != "full" or args.audio_mode != "narration":
        parser.error("local audio adoption requires --edit-mode full and --audio-mode narration")
    if args.audio_stream_index != 0:
        parser.error("local audio adoption requires --audio-stream-index 0")
    if len(args.video) != 1:
        parser.error("local audio adoption requires exactly one input video")
    if args.work_dir is None:
        parser.error("local audio adoption requires explicit --work-dir")
    explicit = set(getattr(args, "_explicit_options", ()))
    conflicts = sorted(explicit.intersection(_LOCAL_ADOPTION_CONFLICTS))
    if conflicts:
        parser.error("local audio adoption cannot use: " + ", ".join(conflicts))
    for field, option in _LOCAL_ADOPTION_OPTIONS:
        setattr(args, field, _resolve_local_file(parser, getattr(args, field), option))

    work_dir = Path(args.work_dir).expanduser().resolve()
    if work_dir.exists():
        parser.error("local audio adoption requires a new --work-dir")
    output_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir is not None else work_dir.parent
    )
    # Pre-run guard only: mirrors the assembler's `recap_<stem>.mp4` naming so an existing
    # delivery is refused before work starts. The authoritative published path is read back
    # from the child's assembly_manifest.json after the run.
    delivery = output_dir / f"recap_{Path(args.video[0]).stem}.mp4"
    if delivery.exists():
        parser.error(f"local audio adoption will not overwrite delivery: {delivery}")
    args.work_dir = str(work_dir)
    args.output_dir = str(output_dir)

    # Ambient authoring configuration is irrelevant because this route never synthesizes
    # or reviews narration. Explicit forms were rejected above.
    args.tts_provider = "auto"
    args.mimo_tts_voice = None
    args.voice_ref = None
    args.mimo_qc = "off"


def load_local_assembly_evidence(work_dir):
    """Load the assembler's minimal parent-verifiable identity references."""
    work_dir = Path(work_dir)
    try:
        return {
            name: json.loads((work_dir / filename).read_text(encoding="utf-8"))
            for name, filename in (
                ("assembly", "assembly_manifest.json"),
                ("narration", "narration_input_binding.json"),
                ("mix", "audio_mix_binding.json"),
            )
        }
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit("assembler did not publish readable local adoption evidence") from exc


def verify_local_assembly_evidence(evidence, manifest):
    """Cross-check only the adoption and picture identities owned by recap."""
    try:
        local = manifest["audio"]["local_adoption"]
        narration = evidence["narration"]
        mix = evidence["mix"]
        matches = (
            narration["adoption"]["sha256"] == local["narration_adoption"]["sha256"]
            and narration["adoption"]["tts_meta"]["sha256"]
            == local["tts_meta"]["sha256"]
            and mix["adoption"]["sha256"] == local["audio_mix_adoption"]["sha256"]
            and mix["picture"]["sha256"] == manifest["source_video_fingerprint"]
        )
    except (KeyError, TypeError):
        matches = False
    if not matches:
        raise SystemExit("assembler bindings do not match the sealed local adoption manifest")


def owned_local_delivery(evidence):
    """Return a stable ownership token only when all child reports bind the published file."""
    try:
        expected = Path(evidence["assembly"]["final_output"]).resolve()
    except (KeyError, TypeError) as exc:
        raise SystemExit(
            "assembly manifest does not declare final_output; cannot own the delivery"
        ) from exc
    try:
        declared = [
            Path(evidence["narration"]["final_output"]["path"]).resolve(),
            Path(evidence["mix"]["final_output"]["path"]).resolve(),
        ]
        hashes = {
            evidence["narration"]["final_output"]["sha256"],
            evidence["mix"]["final_output"]["sha256"],
        }
    except (KeyError, TypeError):
        return None
    if declared != [expected, expected] or len(hashes) != 1 or not expected.is_file():
        return None
    with expected.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if hashes != {digest}:
        return None
    stat = expected.stat()
    return {"path": expected, "sha256": digest, "device": stat.st_dev, "inode": stat.st_ino}


def remove_owned_local_delivery(token):
    if token is None:
        return
    path = token["path"]
    if not path.is_file():
        return
    stat = path.stat()
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if (stat.st_dev, stat.st_ino) == (token["device"], token["inode"]) \
            and digest == token["sha256"]:
        path.unlink()


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
    validate_local_adoption(parser, args)
    mode = getattr(args, "audio_mode", "narration")
    stream = getattr(args, "audio_stream_index", 0)
    if isinstance(stream, bool) or not isinstance(stream, int) or stream < 0:
        parser.error("--audio-stream-index must be a non-negative integer")
    if args.edit_mode == "dub" and mode != "narration":
        parser.error("--edit-mode dub cannot be combined with a non-narration --audio-mode")
    if args.tts_provider == "index-tts":
        if args.mimo_tts_voice or args.voice_ref:
            parser.error("--tts-provider index-tts cannot use MiMo voice or --voice-ref")
        if args.edit_mode == "dub":
            parser.error("--edit-mode dub does not support --tts-provider index-tts")
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


def reject_unsupported_subtitle_track(work_dir, args):
    if (
        getattr(args, "audio_mode", "narration") == "source-mix"
        and (Path(work_dir) / "subtitle_track.json").exists()
    ):
        raise SystemExit(
            "source-mix 当前不能绑定显式 subtitle_track.json；请使用新的 work_dir，"
            "或选择 adopted-packet-copy"
        )


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


def begin_local_adoption_qc(work_dir, write_stage):
    """Record that authoring was intentionally skipped for adopted local assets."""
    return write_stage(
        work_dir,
        "pre_assemble",
        metadata={
            "audio_mode": "narration",
            "selected_audio_stream_index": 0,
            "tts": "adopted_local_not_generated",
            "narration_validation": "not_run",
            "narration_review": "not_run",
            "semantic_validation": "video-assemble",
            "visual_overlays": "not_authored_not_present",
        },
    )
