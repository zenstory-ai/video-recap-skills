import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[2] / "skills" / "video-voiceover" / "scripts"),
)

import fish_audio
import voiceover
from lib import CONFIG, DEFAULT_FISH_TTS_REFERENCE_ID


class _Response:
    def __init__(self, body, content_type="audio/wav"):
        self.body = body
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


def test_default_fish_voice_is_entertainment_commentary_voice():
    assert DEFAULT_FISH_TTS_REFERENCE_ID == "5653cea4ac83480aaf2bf45406556185"


def test_fish_transport_sends_free_model_and_writes_wav(monkeypatch, tmp_path):
    seen = {}
    wav = b"RIFF" + (b"\x00" * 4) + b"WAVE" + (b"x" * 64)

    def fake_urlopen(request, timeout):
        seen["request"] = request
        seen["timeout"] = timeout
        return _Response(wav)

    monkeypatch.setitem(CONFIG, "fish_api_key", "sk-fish-secret")
    monkeypatch.setitem(CONFIG, "fish_tts_model", "s2.1-pro-free")
    monkeypatch.setitem(CONFIG, "fish_tts_reference_id", "voice-model-id")
    monkeypatch.setitem(CONFIG, "tts_timeout", 12)
    monkeypatch.setattr(fish_audio.urllib.request, "urlopen", fake_urlopen)

    output = tmp_path / "voice.wav"
    fish_audio.synthesize_fish_audio("你好，世界。", output, rate="+8%")

    request = seen["request"]
    payload = json.loads(request.data.decode("utf-8"))
    assert output.read_bytes() == wav
    assert seen["timeout"] == 12
    assert request.get_header("Authorization") == "Bearer sk-fish-secret"
    assert request.get_header("Model") == "s2.1-pro-free"
    assert payload["format"] == "wav"
    assert payload["reference_id"] == "voice-model-id"
    assert payload["prosody"]["speed"] == pytest.approx(1.08)


def test_fish_transport_redacts_credentials_from_http_errors(monkeypatch, tmp_path):
    key = "opaque-fish-token-do-not-leak"
    error = urllib.error.HTTPError(
        "https://api.fish.audio/v1/tts",
        422,
        "invalid",
        {},
        io.BytesIO(f'{{"message":"bad token {key}"}}'.encode()),
    )
    monkeypatch.setitem(CONFIG, "fish_api_key", key)
    def raise_http_error(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(fish_audio.urllib.request, "urlopen", raise_http_error)

    with pytest.raises(RuntimeError) as caught:
        fish_audio.synthesize_fish_audio("测试", tmp_path / "out.wav")

    assert key not in str(caught.value)
    assert "<redacted-key>" in str(caught.value)


def test_fish_transport_rejects_non_wav_response(monkeypatch, tmp_path):
    monkeypatch.setitem(CONFIG, "fish_api_key", "sk-fish-test-key")
    monkeypatch.setattr(
        fish_audio.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(b"not-a-wave-file" * 8),
    )

    with pytest.raises(RuntimeError, match="不是有效 WAV"):
        fish_audio.synthesize_fish_audio("测试", tmp_path / "out.wav")

    assert not (tmp_path / "out.wav").exists()


def test_provider_selection_is_explicit_and_auto_is_backward_compatible(monkeypatch):
    monkeypatch.setitem(CONFIG, "tts_provider", "fish-audio")
    monkeypatch.setitem(CONFIG, "fish_api_key", "fish-key")
    monkeypatch.setitem(CONFIG, "mimo_tts_api_key", "mimo-key")
    assert voiceover.resolve_tts_engine() == "fish-audio"

    monkeypatch.setitem(CONFIG, "tts_provider", "auto")
    assert voiceover.resolve_tts_engine() == "mimo-tts"

    monkeypatch.setitem(CONFIG, "mimo_tts_api_key", "")
    assert voiceover.resolve_tts_engine() == "fish-audio"


def test_provider_selection_rejects_undocumented_aliases(monkeypatch):
    monkeypatch.setitem(CONFIG, "tts_provider", "fish")

    with pytest.raises(RuntimeError, match="fish-audio"):
        voiceover._configured_tts_engine_for_cache()


def test_explicit_provider_still_requires_its_credential(monkeypatch):
    monkeypatch.setitem(CONFIG, "tts_provider", "fish-audio")
    monkeypatch.setitem(CONFIG, "fish_api_key", "")

    with pytest.raises(RuntimeError, match="FISH_API_KEY"):
        voiceover.resolve_tts_engine()


def test_run_tts_engine_dispatches_to_fish(monkeypatch, tmp_path):
    output = tmp_path / "fish.wav"
    seen = []
    monkeypatch.setitem(CONFIG, "tts_retries", 1)
    monkeypatch.setattr(
        voiceover,
        "synthesize_fish_audio",
        lambda text, path, **kwargs: seen.append((text, path, kwargs)) or path.write_bytes(b"wav"),
    )
    monkeypatch.setattr(voiceover, "get_video_duration", lambda _path: 1.0)

    voiceover._run_tts_engine("fish-audio", "免费配音。", output, rate="+5%")

    assert seen[0][0] == "免费配音。"
    assert seen[0][2]["rate"] == "+5%"


def test_fish_settings_participate_in_cache_fingerprint(monkeypatch):
    monkeypatch.setitem(CONFIG, "tts_provider", "fish-audio")
    monkeypatch.setitem(CONFIG, "fish_api_key", "fish-key")
    monkeypatch.setitem(CONFIG, "fish_tts_model", "s2.1-pro-free")
    monkeypatch.setitem(CONFIG, "fish_tts_reference_id", "voice-a")
    first = voiceover.tts_settings_fingerprint("fish-audio")
    monkeypatch.setitem(CONFIG, "fish_tts_reference_id", "voice-b")
    second = voiceover.tts_settings_fingerprint("fish-audio")

    assert first["engine"] == "fish-audio"
    assert "mimo_tts_voice" not in first
    assert first != second


def test_fish_provider_rejects_local_voice_reference(monkeypatch, tmp_path):
    monkeypatch.setitem(CONFIG, "tts_provider", "fish-audio")
    monkeypatch.setitem(CONFIG, "fish_api_key", "fish-key")
    monkeypatch.setitem(CONFIG, "voice_ref", str(tmp_path / "local.wav"))

    with pytest.raises(RuntimeError, match="FISH_TTS_REFERENCE_ID"):
        voiceover.synthesize_tts(
            [{"start": 0.0, "end": 1.0, "narration": "测试。"}], tmp_path
        )
