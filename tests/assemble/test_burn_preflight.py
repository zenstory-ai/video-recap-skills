import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'skills' / 'video-assemble' / 'scripts'))
"""Burn preflight: when ffmpeg lacks the libass `subtitles` filter, an explicit burn request
must raise BEFORE the render (not die at the final -vf) and the default burn must degrade to
the .srt sidecar with the reason recorded."""
import shutil  # noqa: E402
import subprocess  # noqa: E402

import pytest  # noqa: E402

import render_preflight as preflight  # noqa: E402


def test_preflight_raises_when_explicit_burn_and_filter_missing(monkeypatch):
    monkeypatch.setitem(preflight.CONFIG, "burn_subtitles", True)
    monkeypatch.setitem(preflight.CONFIG, "burn_subtitles_explicit", True)
    monkeypatch.setitem(preflight.CONFIG, "burn_subtitles_degraded", None)
    monkeypatch.setattr(shutil, "which", lambda _n: "/usr/bin/ffmpeg")
    monkeypatch.setattr(preflight, "_ffmpeg_filters", lambda: {"scale", "atempo"})
    with pytest.raises(SystemExit, match="subtitles/libass"):
        preflight._preflight_burn_subtitles()
    assert preflight.CONFIG["burn_subtitles_degraded"] is None


def test_preflight_default_burn_degrades_to_sidecar(monkeypatch, capsys):
    monkeypatch.setitem(preflight.CONFIG, "burn_subtitles", True)
    monkeypatch.setitem(preflight.CONFIG, "burn_subtitles_explicit", False)
    monkeypatch.setitem(preflight.CONFIG, "burn_subtitles_degraded", None)
    monkeypatch.setattr(shutil, "which", lambda _n: "/usr/bin/ffmpeg")
    monkeypatch.setattr(preflight, "_ffmpeg_filters", lambda: {"scale", "atempo"})
    preflight._preflight_burn_subtitles()  # must not raise
    assert preflight.CONFIG["burn_subtitles"] is False
    assert preflight.CONFIG["burn_subtitles_degraded"] == "ffmpeg_missing_libass"
    assert ".srt" in capsys.readouterr().out


def test_preflight_ok_when_filter_present(monkeypatch):
    monkeypatch.setitem(preflight.CONFIG, "burn_subtitles", True)
    monkeypatch.setattr(shutil, "which", lambda _n: "/usr/bin/ffmpeg")
    monkeypatch.setattr(preflight, "_ffmpeg_filters", lambda: {"subtitles", "scale"})
    preflight._preflight_burn_subtitles()  # must not raise


def test_preflight_noop_when_ffmpeg_absent(monkeypatch):
    # ffmpeg absent entirely → guard stays out of it (render fails later regardless), so the
    # mocked, ffmpeg-less test/CI environment is never blocked.
    monkeypatch.setitem(preflight.CONFIG, "burn_subtitles", True)
    monkeypatch.setattr(shutil, "which", lambda _n: None)

    def _boom():
        raise AssertionError("must not probe filters when ffmpeg is absent")

    monkeypatch.setattr(preflight, "_ffmpeg_filters", _boom)
    preflight._preflight_burn_subtitles()  # must not raise, must not probe


def test_preflight_skipped_when_burn_off(monkeypatch):
    monkeypatch.setitem(preflight.CONFIG, "burn_subtitles", False)

    def _boom():
        raise AssertionError("must not probe ffmpeg when burn is off")

    monkeypatch.setattr(preflight, "_ffmpeg_filters", _boom)
    preflight._preflight_burn_subtitles()  # must not raise, must not probe


def test_ffmpeg_filters_parse_matches_doctor(monkeypatch):
    """The parser must mirror doctor.py: take column-2 names from filter rows whose first
    token starts with a flag char. A mis-parse returning an empty set would block every
    capable ffmpeg, so this pins the parse contract."""
    sample = (
        "Filters:\n"
        "  T.. atempo            A->A       Adjust audio tempo.\n"
        " ..C subtitles          V->V       Render text subtitles.\n"
        "garbage line with no flag token\n"
    )

    class _Result:
        returncode = 0
        stdout = sample

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Result())
    filters = preflight._ffmpeg_filters()
    assert "subtitles" in filters and "atempo" in filters


def test_ffmpeg_filters_empty_on_nonzero_returncode(monkeypatch):
    class _Result:
        returncode = 1
        stdout = "should be ignored"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Result())
    assert preflight._ffmpeg_filters() == set()
