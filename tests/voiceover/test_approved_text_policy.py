import json
import sys
from pathlib import Path

import pytest


sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[2] / "skills" / "video-voiceover" / "scripts"),
)

import voiceover
from lib import CONFIG


def _configure_offline_tts(monkeypatch):
    monkeypatch.setitem(CONFIG, "tts_dynamic_params", False)
    monkeypatch.setitem(CONFIG, "tts_segment_normalize", False)
    monkeypatch.setitem(CONFIG, "narration_speed", 1.0)
    monkeypatch.setitem(CONFIG, "narration_cumulative_tempo_max", 1.2)
    monkeypatch.setitem(CONFIG, "tts_segment_tempo_max", 1.2)
    monkeypatch.setitem(CONFIG, "mimo_tts_model", "mimo-v2.5-tts")
    monkeypatch.setitem(CONFIG, "mimo_tts_voice", "冰糖")


def test_preserve_approved_text_fails_closed_without_resynthesis(monkeypatch, tmp_path):
    _configure_offline_tts(monkeypatch)
    monkeypatch.setitem(CONFIG, "preserve_approved_text", True)
    calls = []

    def fake_run(_engine, text, output_wav, **_kwargs):
        calls.append(text)
        output_wav.write_text(text, encoding="utf-8")

    monkeypatch.setattr(voiceover, "_run_tts_engine", fake_run)
    monkeypatch.setattr(voiceover, "get_video_duration", lambda _path: 5.0)
    tts_dir = tmp_path / "tts_segments"
    tts_dir.mkdir()
    seg = {
        "start": 10.0,
        "end": 12.0,
        "pause_after_ms": 200,
        "narration": "[情绪提示]这是已经批准、不得自动删减的完整旁白。",
    }

    with pytest.raises(voiceover.ApprovedTextDurationError) as raised:
        voiceover._synthesize_segment(3, seg, [seg], tts_dir, "mimo-tts")

    evidence = raised.value.evidence
    assert calls == ["这是已经批准、不得自动删减的完整旁白。"]
    assert evidence == {
        "failure_kind": "approved_text_duration_conflict",
        "policy": "preserve-approved-text-v1",
        "index": 3,
        "segment": 4,
        "authored_text": "[情绪提示]这是已经批准、不得自动删减的完整旁白。",
        "spoken_text": "这是已经批准、不得自动删减的完整旁白。",
        "audio_duration": 5.0,
        "window_start": 10.0,
        "window_end": 12.0,
        "window_duration": 2.0,
        "pause_after_ms": 200,
        "available_duration": 1.8,
        "max_raw_duration": 2.16,
    }
    assert not (tts_dir / "narr_003.wav").exists()
    assert not (tts_dir / "narr_003.wav.cache.json").exists()


def test_legacy_default_still_truncates_and_resynthesizes(monkeypatch, tmp_path):
    _configure_offline_tts(monkeypatch)
    monkeypatch.setitem(CONFIG, "preserve_approved_text", False)
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

    assert len(calls) == 2
    assert calls[0] == text
    assert calls[1] != text
    assert result["narration"] == text
    assert result["truncated"] is True


def test_approved_text_policy_is_part_of_segment_cache_key(monkeypatch, tmp_path):
    _configure_offline_tts(monkeypatch)
    seg = {"start": 0.0, "end": 2.0, "narration": "缓存必须隔离。"}
    tts_dir = tmp_path / "tts_segments"
    tts_dir.mkdir()

    monkeypatch.setitem(CONFIG, "preserve_approved_text", False)
    legacy_key = voiceover._prepare_tts_segment(0, seg, [seg], tts_dir, "mimo-tts")[4]
    monkeypatch.setitem(CONFIG, "preserve_approved_text", True)
    approved_key = voiceover._prepare_tts_segment(0, seg, [seg], tts_dir, "mimo-tts")[4]

    assert legacy_key != approved_key


def test_legacy_cache_payload_shape_remains_compatible(monkeypatch, tmp_path):
    _configure_offline_tts(monkeypatch)
    monkeypatch.setitem(CONFIG, "preserve_approved_text", False)
    monkeypatch.setattr(voiceover, "stable_hash", lambda payload: payload)
    seg = {"start": 0.0, "end": 2.0, "narration": "旧缓存继续可用。"}

    payload = voiceover._tts_segment_cache_key(
        "mimo-tts", 0, seg, "旧缓存继续可用。", "+0%", "+0Hz"
    )

    assert "authored_text_policy" not in payload
    assert "authored_text" not in payload
    assert "provider_text_cleanup" not in payload


def test_strict_cache_fingerprints_raw_authored_text_after_cleanup(monkeypatch):
    _configure_offline_tts(monkeypatch)
    monkeypatch.setitem(CONFIG, "preserve_approved_text", True)
    base = {"start": 0.0, "end": 2.0, "narration": "批准文本。"}
    marked = {**base, "narration": "[提示]批准文本。"}

    base_key = voiceover._tts_segment_cache_key(
        "mimo-tts", 0, base, "批准文本。", "+0%", "+0Hz"
    )
    marked_key = voiceover._tts_segment_cache_key(
        "mimo-tts", 0, marked, "批准文本。", "+0%", "+0Hz"
    )

    assert base_key != marked_key


def test_strict_duration_failure_cannot_be_downgraded_to_partial(monkeypatch, tmp_path):
    _configure_offline_tts(monkeypatch)
    monkeypatch.setitem(CONFIG, "preserve_approved_text", True)
    monkeypatch.setitem(CONFIG, "allow_partial_tts", True)
    monkeypatch.setitem(CONFIG, "mimo_tts_api_key", "offline-test-key")
    narration = [
        {"start": 0.0, "end": 3.0, "narration": "可放置。"},
        {"start": 3.0, "end": 4.0, "narration": "这个严格段无法放进批准的窗口。"},
    ]

    def fake_run(_engine, text, output_wav, **_kwargs):
        output_wav.write_text(text, encoding="utf-8")

    monkeypatch.setattr(voiceover, "_run_tts_engine", fake_run)
    monkeypatch.setattr(
        voiceover,
        "get_video_duration",
        lambda path: 0.5 if Path(path).name == "narr_000.wav" else 5.0,
    )

    with pytest.raises(voiceover.ApprovedTextDurationError, match="approved_text_duration_conflict"):
        voiceover.synthesize_tts(narration, tmp_path)

    assert not (tmp_path / "tts_meta.json").exists()


def test_strict_provider_failure_cannot_be_downgraded_to_partial(monkeypatch, tmp_path):
    _configure_offline_tts(monkeypatch)
    monkeypatch.setitem(CONFIG, "preserve_approved_text", True)
    monkeypatch.setitem(CONFIG, "allow_partial_tts", True)
    monkeypatch.setitem(CONFIG, "mimo_tts_api_key", "offline-test-key")
    narration = [
        {"start": 0.0, "end": 3.0, "narration": "可生成。"},
        {"start": 3.0, "end": 6.0, "narration": "供应商失败段。"},
    ]

    def fake_segment(index, seg, *_args):
        if index == 1:
            raise RuntimeError("offline provider failed")
        return {"index": index, "narration": seg["narration"], "audio_duration": 0.5}

    monkeypatch.setattr(voiceover, "_synthesize_segment", fake_segment)

    with pytest.raises(RuntimeError, match="必需 TTS 段失败.*不能按部分成功交付") as raised:
        voiceover.synthesize_tts(narration, tmp_path)

    assert '"required": true' in str(raised.value)
    assert '"policy": "preserve-approved-text-v1"' in str(raised.value)


def test_matching_strict_complete_text_cache_can_be_reused_offline(monkeypatch, tmp_path):
    _configure_offline_tts(monkeypatch)
    monkeypatch.setitem(CONFIG, "preserve_approved_text", True)
    monkeypatch.setitem(CONFIG, "mimo_tts_api_key", "")
    narration = [{"start": 0.0, "end": 3.0, "narration": "批准全文缓存。"}]
    tts_dir = tmp_path / "tts_segments"
    tts_dir.mkdir()
    wav = tts_dir / "narr_000.wav"
    wav.write_bytes(b"complete-approved-audio")
    text, _output, rate, _pitch, key = voiceover._prepare_tts_segment(
        0, narration[0], narration, tts_dir, "mimo-tts"
    )
    voiceover._write_tts_segment_cache(
        wav, key, text, 1.0, voiceover._parse_rate_offset(rate)
    )
    monkeypatch.setattr(
        voiceover,
        "_run_tts_engine",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("compatible strict cache must be reused")
        ),
    )

    segments, engine, failures = voiceover.synthesize_tts(narration, tmp_path)

    assert engine == "mimo-tts"
    assert failures == []
    assert segments[0]["spoken_text"] == "批准全文缓存。"
    assert segments[0]["truncated"] is False


def test_strict_mixed_clean_empty_segment_is_required_failure(monkeypatch, tmp_path):
    _configure_offline_tts(monkeypatch)
    monkeypatch.setitem(CONFIG, "preserve_approved_text", True)
    monkeypatch.setitem(CONFIG, "allow_partial_tts", True)
    narration = [
        {"start": 0.0, "end": 2.0, "narration": "正常句。"},
        {"start": 2.0, "end": 3.0, "narration": "[停顿]"},
    ]
    monkeypatch.setattr(
        voiceover,
        "_run_tts_engine",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("strict authored-text validation must run before synthesis")
        ),
    )

    with pytest.raises(RuntimeError, match="approved_text_empty_after_cleanup") as raised:
        voiceover.synthesize_tts(narration, tmp_path)

    assert '"index": 1' in str(raised.value)
    assert '"segment": 2' in str(raised.value)
    assert '"authored_text": "[停顿]"' in str(raised.value)
    assert '"spoken_text": ""' in str(raised.value)
    assert '"required": true' in str(raised.value)


def test_strict_cli_archives_stale_success_meta_before_failure(monkeypatch, tmp_path):
    monkeypatch.setitem(CONFIG, "preserve_approved_text", False)
    narration_path = tmp_path / "narration.json"
    narration_path.write_text(
        json.dumps([{"start": 0.0, "end": 1.0, "narration": "超窗。"}], ensure_ascii=False),
        encoding="utf-8",
    )
    old_meta = {
        "segments": [{"index": 0, "spoken_text": "旧成功。"}],
        "engine": "mimo-tts",
        "narration": "narration.json",
        "partial": False,
        "failures": [],
    }
    meta_path = tmp_path / "tts_meta.json"
    old_bytes = json.dumps(old_meta, ensure_ascii=False, indent=2).encode("utf-8")
    meta_path.write_bytes(old_bytes)
    error = voiceover.ApprovedTextDurationError({
        "failure_kind": "approved_text_duration_conflict",
        "index": 0,
    })
    monkeypatch.setattr(
        voiceover, "synthesize_tts", lambda *_args, **_kwargs: (_ for _ in ()).throw(error)
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["voiceover.py", "--work-dir", str(tmp_path), "--preserve-approved-text"],
    )

    with pytest.raises(voiceover.ApprovedTextDurationError):
        voiceover.main()

    assert not meta_path.exists()
    archived = list((tmp_path / "tts_meta.history").glob("*.json"))
    assert len(archived) == 1
    assert archived[0].read_bytes() == old_bytes


def test_successful_meta_write_is_atomic_and_leaves_no_temporary_file(tmp_path):
    meta_path = tmp_path / "tts_meta.json"
    meta_path.write_text('{"stale": true}', encoding="utf-8")
    payload = {"segments": [], "partial": False, "failures": []}

    voiceover._write_tts_meta_atomically(meta_path, payload)

    assert json.loads(meta_path.read_text(encoding="utf-8")) == payload
    assert list(tmp_path.glob(".tts_meta.*.tmp")) == []
