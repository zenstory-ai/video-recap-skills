"""A TTS reply far longer than its text (the text plus invented speech) is a failed synthesis.

Real case: MiMo returned 18.08s for a 32-character sentence that reads in 8.2-8.64s; the
extra audio was invented speech. It used to be logged as over budget, cached, and reused on
every rerun, so assemble blocked with no_safe_fit forever.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "video-voiceover" / "scripts"))
from lib import CONFIG  # noqa: E402
import voiceover  # noqa: E402
from tts_audio import implausible_tts_duration, max_plausible_tts_seconds  # noqa: E402

SENTENCE = "范闲替丫鬟出了这口气，一巴掌把管家扇倒在地，脸上还落下一片古怪的红斑。"
SEGMENT = {"start": 0.0, "end": 30.0, "narration": SENTENCE, "pause_after_ms": 200}


def _fake_provider(monkeypatch, durations):
    """MiMo stub writing one wav per call; each call's probed duration is the next in `durations`."""
    calls = []

    def fake_mimo(text, output_path, **_kwargs):
        calls.append(text)
        output_path.write_bytes(f"audio-{len(calls)}".encode("utf-8"))

    def probe(path):
        if not Path(path).exists():
            return 0.0
        return durations[min(len(calls), len(durations)) - 1]

    monkeypatch.setitem(CONFIG, "tts_segment_normalize", False)
    monkeypatch.setitem(CONFIG, "mimo_tts_api_key", "tp-test")
    monkeypatch.setitem(CONFIG, "tts_provider", "mimo-tts")
    monkeypatch.setitem(CONFIG, "voice_ref", "")
    monkeypatch.setitem(CONFIG, "tts_retries", 3)
    monkeypatch.setattr(voiceover, "_tts_mimo", fake_mimo)
    monkeypatch.setattr(voiceover, "get_video_duration", probe)
    monkeypatch.setattr(voiceover.time, "sleep", lambda _s: None)
    return calls


def test_bound_separates_the_real_hallucination_from_real_readings():
    limit = max_plausible_tts_seconds(SENTENCE)

    assert 8.64 < limit < 18.08
    assert implausible_tts_duration(SENTENCE, 8.64) is None
    assert "幻读" in implausible_tts_duration(SENTENCE, 18.08)
    # A slow, emotional reading (3 chars/s plus pauses) is still a reading.
    assert implausible_tts_duration(SENTENCE, 32 / 3 + 1.5) is None


def test_bound_allows_short_lines_pauses_and_english():
    assert implausible_tts_duration("好。", 1.8) is None
    assert implausible_tts_duration("他回来了……可是没有人认出他。", 7.0) is None
    assert implausible_tts_duration("He finally came home, but nobody knew him.", 6.0) is None
    assert implausible_tts_duration("He finally came home, but nobody knew him.", 12.0) is not None


def test_zero_min_speech_rate_disables_the_bound(monkeypatch):
    monkeypatch.setitem(CONFIG, "tts_min_speech_rate", 0.0)

    assert max_plausible_tts_seconds(SENTENCE) is None
    assert implausible_tts_duration(SENTENCE, 60.0) is None


def test_hallucinated_reply_is_retried_and_the_faithful_one_kept(monkeypatch, tmp_path):
    calls = _fake_provider(monkeypatch, [18.08, 8.2])
    tts_dir = tmp_path / "tts_segments"
    tts_dir.mkdir()

    result = voiceover._synthesize_segment(0, SEGMENT, [SEGMENT], tts_dir, "mimo-tts")

    assert calls == [SENTENCE, SENTENCE]
    assert result["audio_duration"] == 8.2
    assert (tts_dir / "narr_000.wav").read_bytes() == b"audio-2"


def test_persistent_hallucination_fails_the_segment_and_caches_nothing(monkeypatch, tmp_path):
    calls = _fake_provider(monkeypatch, [18.08])

    with pytest.raises(RuntimeError, match="幻读") as excinfo:
        voiceover.synthesize_tts([SEGMENT], tmp_path)

    assert len(calls) == 3
    assert "18.1s" in str(excinfo.value)
    assert list((tmp_path / "tts_segments").iterdir()) == []


def test_rerun_resynthesizes_a_hallucinated_wav_cached_by_an_earlier_version(monkeypatch, tmp_path):
    tts_dir = tmp_path / "tts_segments"
    tts_dir.mkdir()
    calls = _fake_provider(monkeypatch, [8.2])
    # What the earlier version left behind: the bad wav plus a matching sidecar.
    text, wav, rate, _pitch, cache_inputs = voiceover._prepare_tts_segment(0, SEGMENT, [SEGMENT], tts_dir, "mimo-tts")
    wav.write_bytes(b"hallucinated")
    voiceover._write_tts_segment_cache(wav, cache_inputs, text, 18.08, voiceover._parse_rate_offset(rate))

    segments, _engine, failures = voiceover.synthesize_tts([SEGMENT], tmp_path)

    assert calls == [SENTENCE]
    assert failures == []
    assert segments[0]["audio_duration"] == 8.2
    assert wav.read_bytes() == b"audio-1"
    # The fresh sidecar is a normal cache hit on the next rerun.
    again, _engine, _failures = voiceover.synthesize_tts([SEGMENT], tmp_path)
    assert calls == [SENTENCE]
    assert again[0]["audio_duration"] == 8.2
