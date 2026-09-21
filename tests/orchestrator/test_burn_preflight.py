"""Orchestrator fail-fast: recap.py must reject a burn-on run on an ffmpeg without the
libass `subtitles` filter BEFORE any understand/VLM/ASR/TTS spend, and must surface the
advisory narration review to the user at delivery."""

import json
import shutil
from argparse import Namespace

import pytest

import recap_runtime as recap
import recap_timeline


def _args(burn_subtitles=None):
    return Namespace(burn_subtitles=burn_subtitles)


@pytest.mark.parametrize(
    "env, cli, expected",
    [
        pytest.param(None, None, True, id="default-on"),
        pytest.param(None, False, False, id="cli-off"),
        pytest.param("0", True, True, id="cli-beats-env"),
        *[
            pytest.param(raw, None, expected, id=f"env-{raw.strip() or 'blank'}")
            for raw, expected in [
                ("on", True),
                ("yes", True),
                ("TRUE", True),
                ("1", True),
                (" 1 ", True),
                ("0", False),
                ("no", False),
                ("off", False),
                ("false", False),
                ("garbage", False),
            ]
        ],
    ],
)
def test_burn_intended_resolves_cli_over_env_token(monkeypatch, env, cli, expected):
    monkeypatch.delenv("BURN_SUBTITLES", raising=False)
    if env is not None:
        monkeypatch.setenv("BURN_SUBTITLES", env)
    assert recap._burn_subtitles_intended(_args(burn_subtitles=cli)) is expected


def test_preflight_raises_when_present_but_cannot_burn(monkeypatch):
    monkeypatch.delenv("BURN_SUBTITLES", raising=False)
    monkeypatch.setattr(recap, "_ffmpeg_present_but_cannot_burn", lambda: True)
    with pytest.raises(SystemExit, match="subtitles/libass"):
        recap._preflight_burn_subtitles(_args())


def test_preflight_ok_when_can_burn(monkeypatch):
    monkeypatch.delenv("BURN_SUBTITLES", raising=False)
    monkeypatch.setattr(recap, "_ffmpeg_present_but_cannot_burn", lambda: False)
    recap._preflight_burn_subtitles(_args())  # must not raise


def test_preflight_does_not_probe_when_burn_off(monkeypatch):
    def _boom():
        raise AssertionError("must not probe ffmpeg when burn is off")

    monkeypatch.setattr(recap, "_ffmpeg_present_but_cannot_burn", _boom)
    recap._preflight_burn_subtitles(_args(burn_subtitles=False))  # must not raise


@pytest.mark.parametrize(
    "ffmpeg_path, has_filter, expected",
    [
        # ffmpeg absent entirely -> this guard stays out of it (reported by doctor)
        pytest.param(None, True, False, id="ffmpeg-absent"),
        pytest.param("/usr/bin/ffmpeg", False, True, id="present-without-filter"),
        pytest.param("/usr/bin/ffmpeg", True, False, id="present-with-filter"),
    ],
)
def test_cannot_burn_only_when_ffmpeg_is_present_without_the_filter(
    monkeypatch, ffmpeg_path, has_filter, expected
):
    monkeypatch.setattr(shutil, "which", lambda _n: ffmpeg_path)
    monkeypatch.setattr(recap, "ffmpeg_has_subtitles_filter", lambda: has_filter)
    assert recap._ffmpeg_present_but_cannot_burn() is expected


def test_review_pointer_prints_verdict(tmp_path, capsys):
    (tmp_path / "narration_review.md").write_text("# review", encoding="utf-8")
    (tmp_path / "narration_review.json").write_text(
        json.dumps(
            {
                "verdict": "REVISE",
                "findings": [{"severity": "error"}, {"severity": "warning"}],
            }
        ),
        encoding="utf-8",
    )
    recap_timeline._print_narration_review_pointer(tmp_path)
    out = capsys.readouterr().out
    assert "REVISE" in out
    assert "narration_review.md" in out
    assert "error 1" in out


def test_review_pointer_silent_when_review_did_not_run(tmp_path, capsys):
    """review_ran=False (disabled/failed) prints nothing, even with stale artifacts around."""
    (tmp_path / "narration_review.md").write_text("# stale", encoding="utf-8")
    recap_timeline._print_narration_review_pointer(tmp_path, review_ran=False)
    assert capsys.readouterr().out == ""


def test_review_pointer_reads_the_review_by_contract_when_it_ran(tmp_path):
    """review_ran=True means video-script wrote narration_review.json; a corrupt one raises."""
    (tmp_path / "narration_review.md").write_text("# review", encoding="utf-8")
    (tmp_path / "narration_review.json").write_text("{ not json", encoding="utf-8")
    with pytest.raises(ValueError):
        recap_timeline._print_narration_review_pointer(tmp_path)
