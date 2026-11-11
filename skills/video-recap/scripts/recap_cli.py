"""Define the video-recap command-line contract."""

import argparse
import os
import sys
from pathlib import Path

from lib import TTS_PROVIDERS, env_bool
from recap_source import AUDIO_MODES


# Options whose value names a file or directory; the resume command makes them absolute.
_PATH_DESTS = frozenset({
    "work_dir", "output_dir", "voice_ref", "material_library_dir", "project",
})


def _absolute(value):
    return str(Path(value).expanduser().resolve()) if value else value


def _replayable_argv(parser, argv):
    """The argv as typed, with videos and path values made absolute so it replays from any cwd,
    plus the option names it used, so guards never re-parse sys.argv.

    Runs only after a successful parse, so every option token is an exact option string
    (allow_abbrev=False) and every value-taking option is followed by its value.
    """
    options = {name: action for action in parser._actions for name in action.option_strings}
    out, explicit, tokens, positional_only = [], set(), iter(argv), False
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
        explicit.add(name)
        if action.nargs == 0:
            out.append(token)
            continue
        fix = _absolute if action.dest in _PATH_DESTS else str
        if inline:
            out.append(f"{name}={fix(value)}")
        else:
            out += [token, fix(next(tokens))]
    return out, frozenset(explicit)


class _DoctorMoved(argparse.Action):
    """Answer the old offline check instead of argparse's "video is required"."""

    def __call__(self, parser, namespace, values, option_string=None):
        parser.error("recap.py --doctor 已移除，请运行 scripts/doctor.py（--tts-provider、--json 照旧）")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Full video recap orchestrator (video-recap).",
        allow_abbrev=False,
    )
    parser.add_argument("video", nargs="+")

    core = parser.add_argument_group("核心流程")
    core.add_argument("--work-dir", default=None)
    core.add_argument("--context", default="")
    core.add_argument("--scene-threshold", type=float, default=None)
    core.add_argument("--style", default="纪录片")
    core.add_argument(
        "--edit-mode",
        default="full",
        choices=["full", "cut"],
    )
    core.add_argument("--target-duration", default=None)
    core.add_argument("--output-dir", default=None)
    core.add_argument("--skip-asr", action="store_true")

    voice = parser.add_argument_group("声音策略与配音")
    voice.add_argument("--audio-mode", choices=AUDIO_MODES, default="narration")
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
        "--allow-partial-tts",
        action="store_true",
        help="allow voiceover to continue when some narration segments fail TTS",
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

    parser.add_argument("--doctor", nargs=0, action=_DoctorMoved,
                        default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    qc = parser.add_argument_group("QC 与导出")
    qc.add_argument(
        "--require-final-qc",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="full/cut: require a literal passing final_qc summary (default on; --no-require-final-qc for advisory preview)",
    )
    qc.add_argument(
        "--export-jianying",
        action="store_true",
        help="also export an OPTIONAL 剪映/JianYing draft (decoupled; never required)",
    )
    qc.add_argument(
        "--jianying-bundle-media",
        action="store_true",
        help="copy media into the 剪映 draft (default on; portable to another machine)",
    )
    qc.add_argument(
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

    argv = sys.argv[1:] if argv is None else [str(token) for token in argv]
    args = parser.parse_args(argv)
    # Removed after v0.6.2. Refused for one release: an unread EDIT_MODE=cut would silently run full.
    for name, flag in (("EDIT_MODE", "--edit-mode"), ("TARGET_DURATION", "--target-duration")):
        if os.environ.get(name):
            parser.error(f"环境变量 {name} 已移除：请从环境里删掉它，改传 {flag}")
    # Also refused for one release: an unread =1 would silently drop the gate it used to set.
    if env_bool("REQUIRE_NARRATION_REVIEW", False):
        parser.error(
            "环境变量 REQUIRE_NARRATION_REVIEW 已移除：TTS 前的解说评审门禁已不存在，"
            "旁白靠 SKILL.md 成稿自检；请从环境里删掉它"
        )
    args._argv, args._explicit_options = _replayable_argv(parser, argv)
    return parser, args
