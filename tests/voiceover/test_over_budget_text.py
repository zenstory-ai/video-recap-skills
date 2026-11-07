import json
import sys
from pathlib import Path

import pytest


sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[2] / "skills" / "video-voiceover" / "scripts"),
)

import tts_cache
import voiceover
from lib import CONFIG


def _configure_offline_tts(monkeypatch):
    monkeypatch.setitem(CONFIG, "tts_segment_normalize", False)
    monkeypatch.setitem(CONFIG, "narration_speed", 1.0)
    monkeypatch.setitem(CONFIG, "narration_cumulative_tempo_max", 1.2)
    monkeypatch.setitem(CONFIG, "tts_segment_tempo_max", 1.2)
    monkeypatch.setitem(CONFIG, "mimo_tts_model", "mimo-v2.5-tts")
    monkeypatch.setitem(CONFIG, "mimo_tts_voice", "冰糖")


def test_over_budget_text_is_kept_with_one_tts_call(monkeypatch, tmp_path):
    _configure_offline_tts(monkeypatch)
    calls = []

    def fake_run(_engine, text, output_wav, **_kwargs):
        calls.append(text)
        output_wav.write_text(text, encoding="utf-8")

    monkeypatch.setattr(voiceover, "_run_tts_engine", fake_run)
    monkeypatch.setattr(
        voiceover,
        "get_video_duration",
        lambda path: len(Path(path).read_text(encoding="utf-8")) * 0.4,
    )
    tts_dir = tmp_path / "tts_segments"
    tts_dir.mkdir()
    text = "第一句需要保留。第二句会超出窗口。第三句也很长。"
    seg = {"start": 0.0, "end": 4.0, "pause_after_ms": 200, "narration": text}

    result = voiceover._synthesize_segment(0, seg, [seg], tts_dir, "mimo-tts")

    # Over budget (raw 9.6s > 3.8s slot): the authored text is kept; assemble fits it with
    # bounded tempo or blocks it as no_safe_fit before the encode.
    assert calls == [text]
    assert result["spoken_text"] == text
    assert result["narration"] == text
    assert result["truncated"] is False
    assert result["truncate_reason"] == "none"
    assert "authored_text" not in result and "authored_text_policy" not in result


def test_complete_cached_block_is_reused_offline(monkeypatch, tmp_path):
    _configure_offline_tts(monkeypatch)
    monkeypatch.setitem(CONFIG, "mimo_tts_api_key", "")
    narration = [{"start": 0.0, "end": 3.0, "narration": "完整缓存。"}]
    tts_dir = tmp_path / "tts_segments"
    tts_dir.mkdir()
    wav = tts_dir / "narr_000.wav"
    wav.write_bytes(b"complete-audio")
    text, _output, _rate, _pitch, key = voiceover._prepare_tts_segment(
        0, narration[0], narration, tts_dir, "mimo-tts"
    )
    voiceover._write_tts_segment_cache(wav, key, text, 1.0)
    monkeypatch.setattr(
        voiceover,
        "_run_tts_engine",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("the cached block must be reused")
        ),
    )

    segments, engine, failures = voiceover.synthesize_tts(narration, tmp_path)

    assert engine == "mimo-tts"
    assert failures == []
    assert segments[0]["spoken_text"] == "完整缓存。"
    assert segments[0]["truncated"] is False


def test_block_empty_after_cleanup_is_skipped(monkeypatch, tmp_path):
    _configure_offline_tts(monkeypatch)
    monkeypatch.setitem(CONFIG, "mimo_tts_api_key", "offline-test-key")
    calls = []

    def fake_run(_engine, text, output_wav, **_kwargs):
        calls.append(text)
        output_wav.write_text(text, encoding="utf-8")

    monkeypatch.setattr(voiceover, "_run_tts_engine", fake_run)
    monkeypatch.setattr(voiceover, "get_video_duration", lambda _path: 1.0)
    narration = [
        {"start": 0.0, "end": 2.0, "narration": "正常句。"},
        {"start": 2.0, "end": 3.0, "narration": "[停顿]"},
    ]

    segments, _engine, failures = voiceover.synthesize_tts(narration, tmp_path)

    assert calls == ["正常句。"]
    assert [segment["index"] for segment in segments] == [0]
    assert failures == []


def test_removed_flag_fails_at_the_parser(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        sys, "argv", ["voiceover.py", "--work-dir", str(tmp_path), "--preserve-approved-text"]
    )
    with pytest.raises(SystemExit) as raised:
        voiceover.main()
    assert raised.value.code == 2
    assert "--preserve-approved-text" in capsys.readouterr().err


def test_successful_meta_write_is_atomic_and_leaves_no_temporary_file(tmp_path):
    meta_path = tmp_path / "tts_meta.json"
    meta_path.write_text('{"stale": true}', encoding="utf-8")
    payload = {"segments": [], "partial": False, "failures": []}

    tts_cache.write_json_atomically(meta_path, payload)

    assert json.loads(meta_path.read_text(encoding="utf-8")) == payload
    assert list(tmp_path.glob(".tts_meta.*.tmp")) == []
