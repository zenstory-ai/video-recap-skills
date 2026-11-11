import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'skills' / 'video-recap' / 'scripts'))
import pytest
import voiceover as tts
from voiceover import _run_tts_engine, _tts_mimo


@pytest.mark.parametrize(
    ("response", "match"),
    [
        ({"choices": [{"message": {}}]}, "缺少 audio.data"),
        ({"choices": [{"message": {"audio": {"data": "!!!not-base64!!!"}}}]}, "base64"),
    ],
)
def test_mimo_tts_unusable_response_raises_instead_of_writing_wav(monkeypatch, tmp_path, response, match):
    """A MiMo reply without decodable audio.data must raise, not write a silent wav."""
    monkeypatch.setattr("voiceover.mimo_tts_api_call", lambda payload: response)
    with pytest.raises(RuntimeError, match=match):
        _tts_mimo("测试文本", tmp_path / "out.wav")


def test_run_tts_engine_retries_then_raises_and_cleans_partial(monkeypatch, tmp_path):
    """Repeated MiMo failures: retry up to tts_retries, remove partial output, surface the error."""
    output = tmp_path / "narr_000.wav"
    output.write_bytes(b"stale-partial")
    calls = []

    def boom(text, path, rate="+0%", pitch="+0Hz", emotion=None, voice_ref_b64=None):
        calls.append(1)
        raise RuntimeError("mimo down")

    monkeypatch.setitem(tts.CONFIG, "tts_retries", 2)
    monkeypatch.setattr("voiceover._tts_mimo", boom)
    monkeypatch.setattr("voiceover.time.sleep", lambda s: None)

    with pytest.raises(RuntimeError, match="mimo down|合成失败"):
        _run_tts_engine("mimo-tts", "测试", output)

    assert len(calls) == 2          # retried tts_retries times
    assert not output.exists()      # partial output cleaned up


def test_mimo_tts_request_uses_the_configured_timeout(monkeypatch):
    import io
    import lib

    seen = {}

    def fake_urlopen(request, timeout):
        seen["timeout"] = timeout
        return io.BytesIO(b"{}")

    monkeypatch.setitem(lib.CONFIG, "tts_timeout", 7)
    monkeypatch.setattr(lib.urllib.request, "urlopen", fake_urlopen)

    assert lib.mimo_tts_api_call({"model": "m", "messages": []}) == {}
    assert seen["timeout"] == 7
