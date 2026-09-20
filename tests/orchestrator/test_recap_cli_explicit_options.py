"""recap.py must not accept option abbreviations, and must know which options were explicit."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/video-recap/scripts"))

import recap_cli  # noqa: E402


def test_parse_args_records_explicit_options_from_its_own_argv(monkeypatch):
    """Explicitness follows what argparse consumed, not whatever sys.argv happens to say."""
    monkeypatch.setattr(sys, "argv", ["recap.py", "--voice-ref", "/unrelated.wav"])
    _parser, args = recap_cli.parse_args(
        ["video.mp4", "--edit-mode", "cut", "--no-review-narration"],
    )
    assert args.edit_mode == "cut"
    assert args.voice_ref is None
    assert args._explicit_options == frozenset({"--edit-mode", "--no-review-narration"})


def test_parse_args_without_options_reports_nothing_explicit():
    _parser, args = recap_cli.parse_args(["video.mp4"])
    assert args._explicit_options == frozenset()


@pytest.mark.parametrize("abbreviated", ["--voice", "--edit", "--require-narration"])
def test_parse_args_rejects_option_abbreviations(abbreviated, capsys):
    """A prefix must not silently resolve to a longer option and bypass explicit-flag guards."""
    with pytest.raises(SystemExit):
        recap_cli.parse_args(["video.mp4", abbreviated, "x"])
    assert "unrecognized arguments" in capsys.readouterr().err
