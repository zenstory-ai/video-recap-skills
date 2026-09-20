"""Define the video-recap command-line contract."""

import argparse
import os

from lib import env_bool
from recap_source import AUDIO_MODES

TTS_PROVIDERS = ("auto", "mimo-tts", "fish-audio", "index-tts")


class _RecordExplicit:
    """Record the option argparse actually consumed, not the raw argv spelling."""

    def __call__(self, parser, namespace, values, option_string=None):
        if option_string is not None:
            recorded = getattr(namespace, "_explicit_options", None)
            if recorded is None:
                recorded = set()
                setattr(namespace, "_explicit_options", recorded)
            recorded.add(option_string)
        super().__call__(parser, namespace, values, option_string)


def _record_explicit_options(parser):
    """Make every optional action report itself, so guards never re-parse sys.argv."""
    tracked = {}
    for action in parser._actions:
        if not action.option_strings:
            continue
        base = type(action)
        if not issubclass(base, _RecordExplicit):
            action.__class__ = tracked.setdefault(
                base, type(base.__name__, (_RecordExplicit, base), {})
            )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Full video recap orchestrator (video-* skill bundle).",
        allow_abbrev=False,
    )
    parser.add_argument("video", nargs="*")
    parser.add_argument("--work-dir", default=None)
    parser.add_argument("--context", default="")
    parser.add_argument("--scene-threshold", type=float, default=None)
    parser.add_argument("--style", default="纪录片")
    parser.add_argument(
        "--edit-mode",
        default=os.environ.get("EDIT_MODE", "full"),
        choices=["full", "cut", "dub"],
    )
    parser.add_argument("--audio-mode", choices=AUDIO_MODES, default="narration")
    parser.add_argument("--audio-stream-index", type=int, default=0)
    parser.add_argument(
        "--tts-meta", default=None,
        help="local adopted tts_meta.json; requires both adoption flags",
    )
    parser.add_argument(
        "--narration-adoption", default=None,
        help="local narration_adoption v1; requires --tts-meta and --audio-mix-adoption",
    )
    parser.add_argument(
        "--audio-mix-adoption", default=None,
        help="local audio_mix_adoption v1 for assembly-only full-sound rendering",
    )
    parser.add_argument(
        "--target-duration", default=os.environ.get("TARGET_DURATION") or None
    )
    parser.add_argument(
        "--allow-duration-drift",
        action="store_true",
        help="cut mode: accept clip duration drift from --target-duration (primary override)",
    )
    parser.add_argument(
        "--allow-sparse-cut",
        action="store_true",
        help="compatibility: accept sparse cut mapping and legacy duration drift override",
    )
    parser.add_argument("--skip-asr", action="store_true")
    parser.add_argument("--mimo-video-overview", action="store_true")
    parser.add_argument(
        "--mimo-qc",
        default=os.environ.get("MIMO_QC", "off"),
        choices=["off", "pre-assemble", "post-render", "both"],
        help="optional advisory MiMo QC stage(s); never blocks the pipeline",
    )
    parser.add_argument(
        "--mimo-qc-refresh",
        action="store_true",
        default=env_bool("MIMO_QC_REFRESH", False),
        help="ignore a matching MiMo QC stage cache",
    )
    parser.add_argument(
        "--consolidate",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="build the understanding story index (Pass B); default ON, --no-consolidate to skip",
    )
    parser.add_argument(
        "--consolidate-asr", action="store_true", help="also clean ASR (Pass A)"
    )
    parser.add_argument("--mimo-tts-voice", default=None, help="MiMo TTS voice")
    parser.add_argument(
        "--tts-provider",
        default=os.environ.get("TTS_PROVIDER", "auto"),
        choices=TTS_PROVIDERS,
        help="voiceover provider; auto prefers configured MiMo, then Fish Audio; Index is explicit",
    )
    parser.add_argument(
        "--voice-ref",
        default=None,
        help="reference audio for cloned narration voice (mimo-v2.5-tts-voiceclone)",
    )
    parser.add_argument(
        "--allow-partial-tts",
        action="store_true",
        help="allow video-voiceover to continue when some narration segments fail TTS",
    )
    parser.add_argument(
        "--preserve-approved-text",
        action="store_true",
        help="forward strict approved-text preservation to narration voiceover",
    )
    parser.add_argument(
        "--burn-subtitles",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="burn narration subtitles into the video (default on; --no-burn-subtitles to disable)",
    )
    parser.add_argument(
        "--subtitle-y-top",
        type=int,
        default=None,
        help="inclusive auto-rotated display-frame Y at the top of the measured subtitle band",
    )
    parser.add_argument(
        "--subtitle-y-bot",
        type=int,
        default=None,
        help="exclusive auto-rotated display-frame Y at the bottom of the measured subtitle band",
    )
    parser.add_argument(
        "--review-narration",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="run advisory narration quality review before TTS (default on; fail-open)",
    )
    parser.add_argument(
        "--require-narration-review",
        action="store_true",
        help="make narration review a strict pre-TTS gate (also REQUIRE_NARRATION_REVIEW=1)",
    )
    parser.add_argument(
        "--require-final-qc",
        action="store_true",
        help="full/cut: require literal passing final_qc and golden_eval summaries",
    )
    parser.add_argument("--output-dir", default=None)
    parser.add_argument(
        "--export-jianying",
        action="store_true",
        help="also export an OPTIONAL 剪映/JianYing draft (decoupled; never required)",
    )
    parser.add_argument(
        "--jianying-bundle-media",
        action="store_true",
        help="copy media into the 剪映 draft (default on; portable to another machine)",
    )
    parser.add_argument(
        "--jianying-no-bundle-media",
        action="store_true",
        help="reference media in place instead of copying it into the draft",
    )
    parser.add_argument(
        "--material-library-dir",
        default=None,
        help="filesystem material library dir (or VIDEO_RECAP_MATERIAL_LIBRARY_DIR)",
    )
    parser.add_argument(
        "--use-materials",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="restore compatible analyzed artifacts from the material library",
    )
    parser.add_argument(
        "--save-materials",
        action="store_true",
        help="save analyzed JSON/MD artifacts into the material library",
    )
    parser.add_argument("--doctor", action="store_true")
    _record_explicit_options(parser)
    args = parser.parse_args(argv)
    args._explicit_options = frozenset(getattr(args, "_explicit_options", ()))
    return parser, args
