"""The printed resume command replays the original argv, from any working directory."""

import json
import shlex
from pathlib import Path

import pytest

import recap_cli
import recap_runtime
import recap_source
import recap_timeline

ENV_DEFAULTS = ("EDIT_MODE", "TARGET_DURATION", "TTS_PROVIDER")
PATH_DESTS = recap_cli._PATH_DESTS


@pytest.fixture
def no_env_defaults(monkeypatch):
    for key in ENV_DEFAULTS:
        monkeypatch.delenv(key, raising=False)


def _sample_argv(inline):
    """One spelling of every option the parser defines, each set away from its default."""
    parser, _ = recap_cli.parse_args(["in.mp4"])
    argv = ["in dir/in.mp4"]
    for action in parser._actions:
        if not action.option_strings or action.dest == "help":
            continue
        flag = action.option_strings[-1]
        if action.nargs == 0:
            argv.append(flag)
            continue
        if action.choices:
            value = next(choice for choice in action.choices if choice != action.default)
        elif action.type is int:
            value = "7"
        elif action.type is float:
            value = "0.5"
        else:
            value = f"rel dir/{action.dest}"
        argv += [f"{flag}={value}"] if inline else [flag, value]
    return argv


def _comparable(args, cwd):
    """Parsed settings with every path resolved against the cwd it was typed in."""
    values = {k: v for k, v in vars(args).items() if not k.startswith("_")}
    for dest in PATH_DESTS:
        if values[dest]:
            values[dest] = str((cwd / values[dest]).resolve())
    values["video"] = [str((cwd / v).resolve()) for v in values["video"]]
    return values


@pytest.mark.parametrize("inline", [False, True], ids=["spaced", "inline"])
def test_resume_command_round_trips_every_parser_option(
    tmp_path, monkeypatch, no_env_defaults, inline
):
    typed_in, resumed_in = tmp_path / "typed", tmp_path / "elsewhere"
    typed_in.mkdir()
    resumed_in.mkdir()
    argv = _sample_argv(inline)

    monkeypatch.chdir(typed_in)
    _, args = recap_cli.parse_args(argv)
    command = recap_timeline._continuation_command(typed_in / "unused", args)
    monkeypatch.chdir(resumed_in)
    _, resumed = recap_cli.parse_args(shlex.split(command)[2:])

    assert shlex.split(command)[1].endswith("recap.py")
    assert _comparable(resumed, resumed_in) == _comparable(args, typed_in)
    # The typed --work-dir is kept; the derived one is not appended a second time.
    assert shlex.split(command).count("--work-dir") + command.count("--work-dir=") == 1


def test_resume_command_keeps_the_user_spelling_and_pins_cwd_and_env(
    tmp_path, monkeypatch, no_env_defaults
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EDIT_MODE", "cut")
    monkeypatch.setenv("TARGET_DURATION", "90")
    _, args = recap_cli.parse_args(
        ["in.mp4", "--style=悬疑", "--output-dir", "out", "--no-consolidate"]
    )
    args.voice_ref = str(tmp_path / "voice.wav")  # main() folds VOICE_REF in like this
    work = tmp_path / "work dir"

    resume = shlex.split(recap_timeline._continuation_command(work, args))[2:]

    assert resume == [
        str(tmp_path / "in.mp4"), "--style=悬疑", "--output-dir", str(tmp_path / "out"),
        "--no-consolidate", "--work-dir", str(work), "--edit-mode", "cut",
        "--target-duration", "90", "--voice-ref", str(tmp_path / "voice.wav"),
    ]
    video = tmp_path / "in.mp4"
    video.write_bytes(b"v")
    work.mkdir()
    recap_runtime._write_run_manifest(work, video, args)
    manifest = json.loads((work / "recap_run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["argv"] == resume


def test_resume_command_does_not_promote_ambient_tts_in_source_modes(
    tmp_path, monkeypatch, no_env_defaults
):
    monkeypatch.setenv("TTS_PROVIDER", "fish-audio")
    parser, args = recap_cli.parse_args([str(tmp_path / "in.mp4"), "--audio-mode", "source-mix"])
    recap_source.validate_audio_routing(parser, args)

    command = recap_timeline._continuation_command(tmp_path, args)

    assert "--tts-provider" not in command
    assert Path(shlex.split(command)[2]) == tmp_path / "in.mp4"
