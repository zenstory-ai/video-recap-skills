import hashlib
import http.client
import io
import json
import socket
import sys
import urllib.error
import wave
from pathlib import Path

import pytest


sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[2] / "skills" / "video-voiceover" / "scripts"),
)

import index_tts
import voiceover
from lib import CONFIG


def _wav_bytes(frames=160, rate=16000):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x00" * frames)
    return output.getvalue()


class _Response:
    def __init__(self, body, content_type="audio/wav", content_length=None):
        self.body = body
        self.headers = {"Content-Type": content_type}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        return self.body if size < 0 else self.body[:size]


def test_index_transport_posts_exact_contract_and_records_request_receipt(monkeypatch, tmp_path):
    audio = _wav_bytes()
    seen = {}

    def fake_open(request, timeout):
        seen["request"] = request
        seen["timeout"] = timeout
        return _Response(audio)

    monkeypatch.setattr(index_tts, "_open_without_redirects", fake_open)
    output = tmp_path / "index.wav"

    receipt = index_tts.synthesize_index_tts(
        "批准全文。", output, endpoint="http://127.0.0.1:9880/tts", voice="private-voice", timeout=7
    )

    request = seen["request"]
    assert json.loads(request.data.decode("utf-8")) == {
        "voice": "private-voice",
        "text": "批准全文。",
    }
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("Accept") == "audio/wav"
    assert seen["timeout"] == 7
    assert output.read_bytes() == audio
    assert receipt == {
        "receipt_schema": "index-tts-request-receipt",
        "receipt_version": 1,
        "provider": "index-tts",
        "requested_voice": "private-voice",
        "returned_wav_sha256": hashlib.sha256(audio).hexdigest(),
        "speed_policy": "provider-default-no-rate-control",
    }


@pytest.mark.parametrize(
    "endpoint",
    [
        "ftp://private.invalid/tts",
        "http://user:secret@private.invalid/tts",
        "http://private.invalid/tts?token=secret",
        "http://private.invalid/tts#secret",
        "http:///missing-host",
    ],
)
def test_index_endpoint_rejects_unsafe_or_credential_bearing_urls(endpoint):
    with pytest.raises(ValueError, match="INDEX_TTS_ENDPOINT"):
        index_tts.validate_index_tts_config(endpoint, "voice")


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://@private.invalid/tts",
        "http://private.invalid:bad/tts",
        "http://private.invalid:99999/tts",
        "http://private.invalid/tts\nInjected: value",
        "http://[broken/tts",
    ],
)
def test_index_endpoint_rejects_malformed_values_without_echo(endpoint):
    with pytest.raises(ValueError) as raised:
        index_tts.validate_index_tts_config(endpoint, "voice")

    assert "INDEX_TTS_ENDPOINT" in str(raised.value)
    assert "private.invalid" not in str(raised.value)
    assert "Injected" not in str(raised.value)


def test_index_transport_refuses_redirect_without_forwarding_body(monkeypatch, tmp_path):
    error = urllib.error.HTTPError(
        "http://private.invalid/tts", 307, "redirect", {"Location": "http://other/tts"}, None
    )
    monkeypatch.setattr(
        index_tts, "_open_without_redirects", lambda *_args, **_kwargs: (_ for _ in ()).throw(error)
    )

    with pytest.raises(RuntimeError, match="重定向"):
        index_tts.synthesize_index_tts(
            "不得转发。", tmp_path / "out.wav", endpoint="http://private.invalid/tts",
            voice="voice", timeout=3,
        )

    assert not (tmp_path / "out.wav").exists()


def test_index_http_error_reads_only_bounded_bytes_and_does_not_echo_body(monkeypatch, tmp_path):
    class _BoundedBody(io.BytesIO):
        read_size = None

        def read(self, size=-1):
            self.read_size = size
            return super().read(size)

    private = b"http://user:secret@private.invalid/tts?token=secret"
    body = _BoundedBody(private * 1000)
    error = urllib.error.HTTPError("http://host/tts", 500, "error", {}, body)
    monkeypatch.setattr(
        index_tts, "_open_without_redirects", lambda *_args, **_kwargs: (_ for _ in ()).throw(error)
    )

    with pytest.raises(RuntimeError) as raised:
        index_tts.synthesize_index_tts(
            "批准文本。", tmp_path / "out.wav", endpoint="http://host/tts",
            voice="voice", timeout=3,
        )

    assert body.read_size == index_tts.MAX_ERROR_BYTES
    assert "HTTP 500" in str(raised.value)
    assert "private.invalid" not in str(raised.value)
    assert "secret" not in str(raised.value)


def test_index_http_error_body_read_failure_is_closed_and_private_reason_hidden(monkeypatch, tmp_path):
    private = "private.internal:9880 token=secret"

    class _FailingBody:
        closed = False

        def read(self, size=-1):
            assert size == index_tts.MAX_ERROR_BYTES
            raise OSError(private)

        def close(self):
            self.closed = True

    body = _FailingBody()
    error = urllib.error.HTTPError("http://host/tts", 503, "error", {}, body)
    monkeypatch.setattr(
        index_tts, "_open_without_redirects", lambda *_args, **_kwargs: (_ for _ in ()).throw(error)
    )

    with pytest.raises(RuntimeError) as raised:
        index_tts.synthesize_index_tts(
            "批准文本。", tmp_path / "out.wav", endpoint="http://host/tts",
            voice="voice", timeout=3,
        )

    assert body.closed is True
    assert private not in str(raised.value)
    assert raised.value.__cause__ is None


@pytest.mark.parametrize("failure_point", ["read", "close"])
def test_index_success_response_io_failures_hide_private_reason(
    monkeypatch, tmp_path, failure_point
):
    private = "private.internal:9880?credential=secret"

    class _FailingResponse(_Response):
        def read(self, size=-1):
            if failure_point == "read":
                raise OSError(private)
            return super().read(size)

        def __exit__(self, *_args):
            if failure_point == "close":
                raise http.client.RemoteDisconnected(private)
            return False

    monkeypatch.setattr(
        index_tts,
        "_open_without_redirects",
        lambda *_args, **_kwargs: _FailingResponse(_wav_bytes()),
    )

    with pytest.raises(RuntimeError) as raised:
        index_tts.synthesize_index_tts(
            "测试。", tmp_path / "out.wav", endpoint="http://host/tts",
            voice="voice", timeout=3,
        )

    assert private not in str(raised.value)
    assert raised.value.__cause__ is None
    assert not (tmp_path / "out.wav").exists()


def test_index_json_and_url_errors_do_not_echo_private_service_values(monkeypatch, tmp_path):
    private = "http://user:secret@private.invalid/tts?token=secret"
    monkeypatch.setattr(
        index_tts,
        "_open_without_redirects",
        lambda *_args, **_kwargs: _Response(
            json.dumps({"error": private}).encode(), "application/json"
        ),
    )
    with pytest.raises(RuntimeError) as json_error:
        index_tts.synthesize_index_tts(
            "测试。", tmp_path / "json.wav", endpoint="http://host/tts",
            voice="voice", timeout=3,
        )
    assert private not in str(json_error.value)

    monkeypatch.setattr(
        index_tts,
        "_open_without_redirects",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(urllib.error.URLError(private)),
    )
    with pytest.raises(RuntimeError) as url_error:
        index_tts.synthesize_index_tts(
            "测试。", tmp_path / "url.wav", endpoint="http://host/tts",
            voice="voice", timeout=3,
        )
    assert private not in str(url_error.value)


def test_index_redirect_handler_never_creates_forwarded_request():
    handler = index_tts._NoRedirectHandler()
    assert handler.redirect_request(None, None, 307, "redirect", {}, "http://other/tts") is None


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        (b'{"error":"not wav"}', "application/json"),
        (b"RIFFbad-WAVE", "audio/wav"),
        (_wav_bytes(frames=0), "audio/wav"),
    ],
)
def test_index_transport_rejects_non_authentic_wav(monkeypatch, tmp_path, body, content_type):
    monkeypatch.setattr(
        index_tts, "_open_without_redirects", lambda *_args, **_kwargs: _Response(body, content_type)
    )

    with pytest.raises(RuntimeError, match="WAV|音频"):
        index_tts.synthesize_index_tts(
            "测试。", tmp_path / "out.wav", endpoint="https://private.invalid/tts",
            voice="voice", timeout=3,
        )

    assert not (tmp_path / "out.wav").exists()


def test_index_transport_rejects_riff_declared_longer_than_response(monkeypatch, tmp_path):
    audio = bytearray(_wav_bytes())
    audio[4:8] = (len(audio) + 1024).to_bytes(4, "little")
    monkeypatch.setattr(
        index_tts,
        "_open_without_redirects",
        lambda *_args, **_kwargs: _Response(bytes(audio)),
    )

    with pytest.raises(RuntimeError, match="截断|长度"):
        index_tts.synthesize_index_tts(
            "测试。", tmp_path / "out.wav", endpoint="http://host/tts",
            voice="voice", timeout=3,
        )


def test_index_transport_enforces_response_size_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(index_tts, "MAX_WAV_BYTES", 64)
    monkeypatch.setattr(
        index_tts,
        "_open_without_redirects",
        lambda *_args, **_kwargs: _Response(_wav_bytes(), content_length=999),
    )

    with pytest.raises(RuntimeError, match="过大"):
        index_tts.synthesize_index_tts(
            "测试。", tmp_path / "out.wav", endpoint="https://private.invalid/tts",
            voice="voice", timeout=3,
        )


def test_index_transport_surfaces_timeout_without_output(monkeypatch, tmp_path):
    monkeypatch.setattr(
        index_tts,
        "_open_without_redirects",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(socket.timeout("timed out")),
    )

    with pytest.raises(RuntimeError, match="超时"):
        index_tts.synthesize_index_tts(
            "测试。", tmp_path / "out.wav", endpoint="https://private.invalid/tts",
            voice="voice", timeout=1,
        )

    assert not (tmp_path / "out.wav").exists()


@pytest.mark.parametrize("timeout", [True, 0, float("inf"), float("nan")])
def test_index_transport_rejects_unbounded_timeout(timeout, tmp_path):
    with pytest.raises(ValueError, match="有限正数"):
        index_tts.synthesize_index_tts(
            "测试。", tmp_path / "out.wav", endpoint="https://private.invalid/tts",
            voice="voice", timeout=timeout,
        )


def test_index_provider_is_explicit_and_auto_never_selects_it(monkeypatch):
    monkeypatch.setitem(CONFIG, "index_tts_endpoint", "http://private.invalid/tts")
    monkeypatch.setitem(CONFIG, "index_tts_voice", "voice")
    monkeypatch.setitem(CONFIG, "mimo_tts_api_key", "")
    monkeypatch.setitem(CONFIG, "fish_api_key", "")
    monkeypatch.setitem(CONFIG, "tts_provider", "auto")
    assert voiceover._configured_tts_engine_for_cache() == "mimo-tts"

    monkeypatch.setitem(CONFIG, "tts_provider", "index-tts")
    assert voiceover.resolve_tts_engine() == "index-tts"


def test_index_cache_identity_tracks_voice_and_endpoint_without_exposing_endpoint(monkeypatch):
    monkeypatch.setitem(CONFIG, "index_tts_endpoint", "http://private-a.invalid/tts")
    monkeypatch.setitem(CONFIG, "index_tts_voice", "voice-a")
    first = voiceover.tts_settings_fingerprint("index-tts")
    monkeypatch.setitem(CONFIG, "index_tts_voice", "voice-b")
    second = voiceover.tts_settings_fingerprint("index-tts")
    monkeypatch.setitem(CONFIG, "index_tts_endpoint", "http://private-b.invalid/tts")
    third = voiceover.tts_settings_fingerprint("index-tts")

    assert first != second != third
    assert "private-a.invalid" not in json.dumps(first)
    assert first["index_tts_voice"] == "voice-a"


def test_index_cache_revision_is_optional_but_invalidates_when_bumped(monkeypatch):
    monkeypatch.setitem(CONFIG, "index_tts_endpoint", "http://private.invalid/tts")
    monkeypatch.setitem(CONFIG, "index_tts_voice", "voice")
    monkeypatch.delitem(CONFIG, "index_tts_cache_revision", raising=False)
    unspecified = voiceover.tts_settings_fingerprint("index-tts")
    monkeypatch.setitem(CONFIG, "index_tts_cache_revision", "deployment-2")
    revised = voiceover.tts_settings_fingerprint("index-tts")

    assert unspecified["index_tts_cache_revision"] == ""
    assert revised["index_tts_cache_revision"] == "deployment-2"
    assert unspecified != revised


def test_index_cached_receipt_requires_complete_consistent_schema():
    processed = "a" * 64
    base = {
        "audio_fingerprint": processed,
        "processed_wav_sha256": processed,
        "provider_receipt": {
            "receipt_schema": "index-tts-request-receipt",
            "receipt_version": 1,
            "provider": "index-tts",
            "requested_voice": "voice",
            "returned_wav_sha256": processed,
            "processed_wav_sha256": processed,
            "speed_policy": "provider-default-no-rate-control",
            "raw_wav_retained_as_processed": True,
            "raw_wav_reconstructable_from_receipt": False,
        },
    }
    config = {"index_tts_endpoint": "http://host/tts", "index_tts_voice": "voice"}
    assert index_tts.valid_cached_receipt(base, config) is True

    mutations = [
        lambda data: data["provider_receipt"].pop("receipt_schema"),
        lambda data: data["provider_receipt"].update(returned_wav_sha256="Z" * 64),
        lambda data: data["provider_receipt"].pop("raw_wav_retained_as_processed"),
        lambda data: data.update(processed_wav_sha256="b" * 64),
        lambda data: data["provider_receipt"].update(
            returned_wav_sha256="b" * 64, raw_wav_retained_as_processed=True
        ),
    ]
    for mutate in mutations:
        candidate = json.loads(json.dumps(base))
        mutate(candidate)
        assert index_tts.valid_cached_receipt(candidate, config) is False


def test_index_provider_rejects_clone_reference_before_cache(monkeypatch, tmp_path):
    monkeypatch.setitem(CONFIG, "tts_provider", "index-tts")
    monkeypatch.setitem(CONFIG, "index_tts_endpoint", "http://host/tts")
    monkeypatch.setitem(CONFIG, "index_tts_voice", "voice")
    monkeypatch.setitem(CONFIG, "voice_ref", str(tmp_path / "clone.wav"))

    with pytest.raises(RuntimeError, match="不支持.*VOICE_REF|克隆"):
        voiceover.synthesize_tts(
            [{"start": 0.0, "end": 1.0, "narration": "测试。"}], tmp_path
        )


@pytest.mark.parametrize(("endpoint", "voice"), [("", "voice"), ("http://host/tts", "")])
def test_explicit_index_provider_requires_endpoint_and_voice_before_cache(monkeypatch, endpoint, voice):
    monkeypatch.setitem(CONFIG, "tts_provider", "index-tts")
    monkeypatch.setitem(CONFIG, "index_tts_endpoint", endpoint)
    monkeypatch.setitem(CONFIG, "index_tts_voice", voice)

    with pytest.raises((RuntimeError, ValueError), match="INDEX_TTS_(ENDPOINT|VOICE)"):
        voiceover._configured_tts_engine_for_cache()


def test_index_preparation_uses_provider_default_speed_and_rejects_emotion(monkeypatch, tmp_path):
    monkeypatch.setitem(CONFIG, "tts_dynamic_params", True)
    monkeypatch.setitem(CONFIG, "index_tts_endpoint", "http://host/tts")
    monkeypatch.setitem(CONFIG, "index_tts_voice", "voice")
    segment = {"start": 0.0, "end": 2.0, "narration": "很长很长的感叹句！"}

    prepared = voiceover._prepare_tts_segment(0, segment, [segment], tmp_path, "index-tts")

    assert prepared[2:4] == ("+0%", "+0Hz")
    with pytest.raises(RuntimeError, match="emotion|情绪"):
        voiceover._prepare_tts_segment(
            0, {**segment, "emotion": "紧张"}, [segment], tmp_path, "index-tts"
        )


@pytest.mark.parametrize(
    "control",
    [{"style": "dramatic"}, {"rate": "+5%"}, {"pitch": "+3Hz"}],
)
def test_index_preparation_rejects_unsupported_segment_controls(monkeypatch, tmp_path, control):
    segment = {"start": 0.0, "end": 2.0, "narration": "测试。", **control}

    with pytest.raises(RuntimeError, match="端点不接受段级控制字段"):
        voiceover._prepare_tts_segment(0, segment, [segment], tmp_path, "index-tts")


def test_index_dispatch_rejects_unsupported_controls(monkeypatch, tmp_path):
    monkeypatch.setitem(CONFIG, "tts_retries", 1)
    monkeypatch.setitem(CONFIG, "index_tts_endpoint", "http://host/tts")
    monkeypatch.setitem(CONFIG, "index_tts_voice", "voice")

    with pytest.raises(RuntimeError, match="端点不接受 rate"):
        voiceover._run_tts_engine("index-tts", "测试。", tmp_path / "out.wav", rate="+5%")


def test_index_receipt_and_processed_hash_survive_sidecar_cache_hit(monkeypatch, tmp_path):
    monkeypatch.setitem(CONFIG, "tts_provider", "index-tts")
    monkeypatch.setitem(CONFIG, "index_tts_endpoint", "http://host/tts")
    monkeypatch.setitem(CONFIG, "index_tts_voice", "voice-a")
    monkeypatch.setitem(CONFIG, "tts_dynamic_params", True)
    monkeypatch.setitem(CONFIG, "tts_segment_normalize", False)
    monkeypatch.setitem(CONFIG, "preserve_approved_text", True)
    monkeypatch.setitem(CONFIG, "allow_partial_tts", False)
    audio = _wav_bytes()
    calls = []

    def fake_synthesize(text, output_path, config):
        calls.append((text, config))
        output_path.write_bytes(audio)
        return {
            "receipt_schema": "index-tts-request-receipt",
            "receipt_version": 1,
            "provider": "index-tts",
            "requested_voice": config["index_tts_voice"],
            "returned_wav_sha256": hashlib.sha256(audio).hexdigest(),
            "speed_policy": "provider-default-no-rate-control",
        }

    monkeypatch.setattr(index_tts, "synthesize_configured", fake_synthesize)
    monkeypatch.setattr(voiceover, "get_video_duration", lambda _path: 0.01)
    narration = [{"start": 0.0, "end": 2.0, "narration": "批准全文。"}]

    first, _, _ = voiceover.synthesize_tts(narration, tmp_path)
    second, _, _ = voiceover.synthesize_tts(narration, tmp_path)

    assert len(calls) == 1
    assert first[0]["authored_text"] == second[0]["authored_text"] == "批准全文。"
    assert first[0]["spoken_text"] == second[0]["spoken_text"] == "批准全文。"
    receipt = second[0]["provider_receipt"]
    assert receipt["requested_voice"] == "voice-a"
    assert receipt["returned_wav_sha256"] == hashlib.sha256(audio).hexdigest()
    assert receipt["processed_wav_sha256"] == hashlib.sha256(audio).hexdigest()
    sidecar = json.loads((tmp_path / "tts_segments/narr_000.wav.cache.json").read_text())
    assert sidecar["provider_receipt"] == receipt

    sidecar.pop("provider_receipt")
    (tmp_path / "tts_segments/narr_000.wav.cache.json").write_text(json.dumps(sidecar))
    voiceover.synthesize_tts(narration, tmp_path)
    assert len(calls) == 2
