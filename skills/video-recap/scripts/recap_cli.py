"""Define the video-recap command-line contract."""

import argparse
import os
import sys
from pathlib import Path

from lib import TTS_PROVIDERS
from recap_source import AUDIO_MODES


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


# Options whose value names a file or directory; the resume command makes them absolute.
_PATH_DESTS = frozenset({
    "work_dir", "output_dir", "voice_ref", "material_library_dir", "project",
    "tts_meta", "narration_adoption", "audio_mix_adoption",
})


def _absolute(value):
    return str(Path(value).expanduser().resolve()) if value else value


def _replayable_argv(parser, argv):
    """The argv as typed, with videos and path values made absolute so it replays from any cwd.

    Runs only after a successful parse, so every option token is an exact option string
    (allow_abbrev=False) and every value-taking option is followed by its value.
    """
    options = {name: action for action in parser._actions for name in action.option_strings}
    out, tokens, positional_only = [], iter(argv), False
    for token in tokens:
        if positional_only or not token.startswith("-"):
            out.append(_absolute(token))
            continue
        if token == "--":
            positional_only = True
            out.append(token)
            continue
        name, inline, value = token.partition("=")
        action = options[name]
        if action.nargs == 0:
            out.append(token)
            continue
        fix = _absolute if action.dest in _PATH_DESTS else str
        if inline:
            out.append(f"{name}={fix(value)}")
        else:
            out += [token, fix(next(tokens))]
    return out


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Full video recap orchestrator (video-* skill bundle).",
        allow_abbrev=False,
    )
    parser.add_argument("video", nargs="*")

    core = parser.add_argument_group("核心流程")
    core.add_argument("--work-dir", default=None)
    core.add_argument("--context", default="")
    core.add_argument("--scene-threshold", type=float, default=None)
    core.add_argument("--style", default="纪录片")
    core.add_argument(
        "--edit-mode",
        default=os.environ.get("EDIT_MODE", "full"),
        choices=["full", "cut", "dub"],
    )
    core.add_argument(
        "--confirm-voice-rights",
        action="store_true",
        help="dub mode only, required there: the user confirms they may use this video's audio "
        "and clone its speaker's voice (sent to MiMo ASR and MiMo voiceclone)",
    )
    core.add_argument(
        "--target-duration", default=os.environ.get("TARGET_DURATION") or None
    )
    core.add_argument(
        "--allow-duration-drift",
        action="store_true",
        help="cut mode: accept clip duration drift from --target-duration (primary override)",
    )
    core.add_argument("--output-dir", default=None)
    core.add_argument("--skip-asr", action="store_true")
    core.add_argument("--mimo-video-overview", action="store_true")
    core.add_argument(
        "--consolidate",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="build the understanding story index (Pass B); default ON, --no-consolidate to skip",
    )
    core.add_argument(
        "--consolidate-asr", action="store_true", help="also clean ASR (Pass A)"
    )

    voice = parser.add_argument_group("声音策略与配音")
    voice.add_argument("--audio-mode", choices=AUDIO_MODES, default="narration")
    voice.add_argument("--audio-stream-index", type=int, default=0)
    voice.add_argument(
        "--tts-provider",
        default=os.environ.get("TTS_PROVIDER", "auto"),
        choices=TTS_PROVIDERS,
        help="voiceover provider; auto prefers configured MiMo, then Fish Audio; Index is explicit",
    )
    voice.add_argument("--mimo-tts-voice", default=None, help="MiMo TTS voice")
    voice.add_argument(
        "--voice-ref",
        default=None,
        help="reference audio for cloned narration voice (mimo-v2.5-tts-voiceclone)",
    )
    voice.add_argument(
        "--preserve-approved-text",
        action="store_true",
        help="forward strict approved-text preservation to narration voiceover",
    )
    voice.add_argument(
        "--allow-partial-tts",
        action="store_true",
        help="allow video-voiceover to continue when some narration segments fail TTS",
    )
    voice.add_argument(
        "--burn-subtitles",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="burn narration subtitles into the video (default on; without libass the default "
             "delivers a .srt sidecar instead, while an explicit --burn-subtitles fails; "
             "--no-burn-subtitles to disable)",
    )
    voice.add_argument(
        "--subtitle-y-top",
        type=int,
        default=None,
        help="inclusive auto-rotated display-frame Y at the top of the measured subtitle band",
    )
    voice.add_argument(
        "--subtitle-y-bot",
        type=int,
        default=None,
        help="exclusive auto-rotated display-frame Y at the bottom of the measured subtitle band",
    )

    adoption = parser.add_argument_group("本地采用三件套（assembly-only）")
    adoption.add_argument(
        "--tts-meta", default=None,
        help="local adopted tts_meta.json; requires both adoption flags",
    )
    adoption.add_argument(
        "--narration-adoption", default=None,
        help="local narration_adoption v1; requires --tts-meta and --audio-mix-adoption",
    )
    adoption.add_argument(
        "--audio-mix-adoption", default=None,
        help="local audio_mix_adoption v1 for assembly-only full-sound rendering",
    )

    review = parser.add_argument_group("评审、QC 与导出")
    review.add_argument(
        "--review-narration",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="run advisory narration quality review before TTS (default on; fail-open)",
    )
    review.add_argument(
        "--require-narration-review",
        action="store_true",
        help="make narration review a strict pre-TTS gate (also REQUIRE_NARRATION_REVIEW=1)",
    )
    review.add_argument(
        "--require-final-qc",
        action="store_true",
        help="full/cut: require a literal passing final_qc summary",
    )
    review.add_argument(
        "--export-jianying",
        action="store_true",
        help="also export an OPTIONAL 剪映/JianYing draft (decoupled; never required)",
    )
    review.add_argument(
        "--jianying-bundle-media",
        action="store_true",
        help="copy media into the 剪映 draft (default on; portable to another machine)",
    )
    review.add_argument(
        "--jianying-no-bundle-media",
        action="store_true",
        help="reference media in place instead of copying it into the draft",
    )

    materials = parser.add_argument_group("素材库")
    materials.add_argument(
        "--material-library-dir",
        default=None,
        help="filesystem material library dir (or VIDEO_RECAP_MATERIAL_LIBRARY_DIR)",
    )
    materials.add_argument(
        "--use-materials",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="restore compatible analyzed artifacts from the material library",
    )
    materials.add_argument(
        "--save-materials",
        action="store_true",
        help="save analyzed JSON/MD artifacts into the material library",
    )

    materials.add_argument(
        "--project",
        default=None,
        help="recap_project.json（或其所在目录）：绑定资源库里已采用的字幕样式、包装模板、音色与 BGM",
    )

    selfcheck = parser.add_argument_group("自检")
    selfcheck.add_argument("--doctor", action="store_true")

    _record_explicit_options(parser)
    argv = sys.argv[1:] if argv is None else [str(token) for token in argv]
    args = parser.parse_args(argv)
    args._explicit_options = frozenset(getattr(args, "_explicit_options", ()))
    args._argv = _replayable_argv(parser, argv)
    return parser, args
