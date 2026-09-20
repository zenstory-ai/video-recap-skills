"""Offline-only Index TTS doctor coverage."""

import json
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "video-recap" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import doctor  # noqa: E402


def _base_config(monkeypatch):
    monkeypatch.setattr(doctor, "_ffmpeg_filters", lambda: {"subtitles", "ass"})
    monkeypatch.setattr(
        doctor,
        "_command_path",
        lambda name: f"/usr/bin/{name}" if name in {"ffmpeg", "ffprobe"} else None,
    )
    for key in ("api_key", "mimo_asr_api_key", "mimo_video_api_key"):
        monkeypatch.setitem(doctor.CONFIG, key, "configured")


def test_doctor_accepts_valid_index_configuration_without_exposing_values(
    monkeypatch, capsys
):
    _base_config(monkeypatch)
    endpoint = "https://private.example.test/index/tts"
    voice = "private-voice-name"
    monkeypatch.setenv("INDEX_TTS_ENDPOINT", endpoint)
    monkeypatch.setenv("INDEX_TTS_VOICE", voice)

    report = doctor.build_report(tts_provider="index-tts")
    doctor._print_human(report)

    assert report["ok"] is True
    assert report["checks"]["tts"]["provider"] == "index-tts"
    assert report["checks"]["tts"]["index_tts_configured"] is True
    assert report["checks"]["tts"]["index_tts_endpoint_format_valid"] is True
    assert report["checks"]["tts"]["connectivity_checked"] is False
    assert "index_tts_configuration" in {
        item["name"] for item in report["capability_menu"]["ready"]
    }
    serialized = json.dumps(report, ensure_ascii=False) + capsys.readouterr().out
    assert endpoint not in serialized
    assert voice not in serialized
    assert "offline configuration only" in serialized


def test_doctor_rejects_missing_index_voice_without_contacting_endpoint(monkeypatch):
    _base_config(monkeypatch)
    monkeypatch.setenv("INDEX_TTS_ENDPOINT", "https://private.example.test/tts")
    monkeypatch.delenv("INDEX_TTS_VOICE", raising=False)

    report = doctor.build_report(tts_provider="index-tts")

    assert report["ok"] is False
    assert any("INDEX_TTS_VOICE" in failure for failure in report["failures"])
    assert report["checks"]["tts"]["connectivity_checked"] is False


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://user:secret@private.example.test/tts?token=hidden",
        "http://@private.example.test/tts",
        "http://private.example.test:70000/tts",
        "http://private.example.test/tt\ns",
        "http://[broken-ipv6",
        "file:///tmp/index.sock",
    ],
)
def test_doctor_rejects_invalid_index_endpoint_format(monkeypatch, endpoint):
    _base_config(monkeypatch)
    monkeypatch.setenv("INDEX_TTS_ENDPOINT", endpoint)
    monkeypatch.setenv("INDEX_TTS_VOICE", "configured")

    report = doctor.build_report(tts_provider="index-tts")

    assert report["ok"] is False
    assert any("INDEX_TTS_ENDPOINT" in failure for failure in report["failures"])
    assert report["checks"]["tts"]["index_tts_endpoint_format_valid"] is False


def test_doctor_auto_selection_ignores_index_configuration(monkeypatch):
    _base_config(monkeypatch)
    monkeypatch.setitem(doctor.CONFIG, "mimo_tts_api_key", "configured")
    monkeypatch.setenv("INDEX_TTS_ENDPOINT", "https://private.example.test/tts")
    monkeypatch.setenv("INDEX_TTS_VOICE", "configured")

    report = doctor.build_report(tts_provider="auto")

    assert report["checks"]["tts"]["provider"] == "mimo-tts"
