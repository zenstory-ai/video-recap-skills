"""Unit tests for tts.py: SHA-256 caching, RMS norm, exact duration measurement (no speed-warping), and safety."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import tts
from conftest import write_sine_wav


def test_tts_clean_text_and_indextts_url_guard(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert tts.clean_tts_text("**Hello** [pause] *world*!") == "Hello world!"
    monkeypatch.setenv("INDEX_TTS_ENDPOINT", "http://user:pass@example.com/tts")
    monkeypatch.setenv("INDEX_TTS_VOICE", "v1")
    with pytest.raises(RuntimeError, match="Invalid INDEX_TTS_ENDPOINT"):
        tts.synthesize_segments([{"id": "n1", "text": "hi"}], tmp_path, engine="indextts")


def test_tts_local_engine_is_strictly_offline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(tts, "has_tool", lambda name: "/usr/bin/edge-tts" if name == "edge-tts" else None)
    with pytest.raises(RuntimeError, match="strictly offline local TTS CLI"):
        tts._synth_local("hello", tmp_path / "out.wav", voice=None, speed=1.0)


def test_tts_cache_measure_overflow_and_update_timeline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[str] = []

    def fake_synth(text: str, out_wav: Path, *, voice: str | None, speed: float) -> str:
        calls.append(text)
        dur = 1.2 if "long" in text else 0.6
        write_sine_wav(out_wav, duration_s=dur, amplitude=0.1)
        return "local:mock"

    monkeypatch.setattr(tts, "_synth_local", fake_synth)

    tl_path = tmp_path / "timeline.json"
    tl_path.write_text(json.dumps({
        "schema_version": 3,
        "canvas": {"width": 320, "height": 240, "fps": "25"},
        "sources": {"s1": "s1.mp4"},
        "video": [{"source": "s1", "in": 0.0, "out": 3.0}],
        "audio": {
            "narration": [
                {"id": "n1", "at": 0.2, "slot_s": 0.8, "text": "A long narration line"},
                {"id": "n2", "at": 1.6, "slot_s": 1.0, "text": "Short beat"},
            ]
        },
    }, ensure_ascii=False), encoding="utf-8")

    tts_dir = tmp_path / "tts"
    measure_p = tmp_path / "measure.json"
    rc = tts.main([
        "--timeline", str(tl_path), "--out-dir", str(tts_dir),
        "--engine", "local", "--update-timeline", "--out", str(measure_p),
    ])
    assert rc == 0
    assert len(calls) == 2

    m1 = json.loads(measure_p.read_text(encoding="utf-8"))
    s1, s2 = m1["segments"]
    # Never speed-warped: 1.2s clip stays 1.2s and reports exact 0.4s overflow against 0.8s slot.
    assert s1["actual_s"] == pytest.approx(1.2, abs=0.02)
    assert s1["overflow_s"] == pytest.approx(0.4, abs=0.02)
    assert s2["actual_s"] == pytest.approx(0.6, abs=0.02)
    assert s2["overflow_s"] == 0.0
    assert s1["rms_dbfs"] == pytest.approx(-20.0, abs=0.5)
    assert s1["cached"] is False

    # Timeline updated in place with relative file path and exact measured duration.
    updated_tl = json.loads(tl_path.read_text(encoding="utf-8"))
    assert updated_tl["audio"]["narration"][0]["file"] == "tts/n1.wav"
    assert updated_tl["audio"]["narration"][0]["duration"] == pytest.approx(1.2, abs=0.02)

    # Second run hits SHA-256 cache cleanly with zero synthesis calls.
    m2 = tts.synthesize_segments(updated_tl["audio"]["narration"], tts_dir, engine="local")
    assert len(calls) == 2
    assert all(seg["cached"] is True for seg in m2["segments"])


def test_tts_fish_audio_headers_env_fallback_and_sync_subtitles(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: list[tuple[str, dict[str, object], dict[str, str]]] = []
    wav_sample = write_sine_wav(tmp_path / "fish_sample.wav", duration_s=0.75, amplitude=0.2).read_bytes()

    def fake_post(url: str, payload: dict[str, object], headers: dict[str, str], label: str, **kwargs: object) -> bytes:
        captured.append((url, payload, headers))
        return wav_sample

    monkeypatch.delenv("FISH_API_KEY", raising=False)
    monkeypatch.setenv("FISH_AUDIO_API_KEY", "sk-testfishkey123456")
    monkeypatch.setenv("FISH_VOICE_ID", "ref_boyaa_01")
    monkeypatch.setenv("FISH_MODEL", "s1")
    monkeypatch.setattr(tts, "_post_json_wav", fake_post)

    tl_path = tmp_path / "timeline.json"
    tl_path.write_text(json.dumps({
        "schema_version": 3,
        "canvas": {"width": 320, "height": 240, "fps": "24"},
        "sources": {"s1": "s1.mp4"},
        "video": [{"source": "s1", "in": 0.0, "out": 3.0}],
        "audio": {"narration": [{"id": "n1", "at": 0.4, "text": "Fish voice line"}]},
        "subtitles": [
            {"id": "n1", "start": 0.0, "end": 0.5, "text": "Fish voice line", "style": "narration"},
            {"start": 1.8, "end": 2.4, "text": "Keep dialogue", "style": "dialogue"},
        ],
    }, ensure_ascii=False), encoding="utf-8")

    rc = tts.main([
        "--timeline", str(tl_path), "--out-dir", str(tmp_path / "vo"),
        "--engine", "auto", "--speed", "1.15", "--update-timeline", "--sync-subtitles",
    ])
    assert rc == 0
    assert len(captured) == 1
    _url, payload, headers = captured[0]
    assert headers["Authorization"] == "Bearer sk-testfishkey123456"
    assert headers["model"] == "s1"
    assert payload["reference_id"] == "ref_boyaa_01"
    assert payload["prosody"]["speed"] == pytest.approx(1.15)
    assert payload["prosody"]["normalize_loudness"] is True

    updated = json.loads(tl_path.read_text(encoding="utf-8"))
    subs = updated["subtitles"]
    assert len(subs) == 2
    assert subs[0]["start"] == pytest.approx(0.4)
    assert subs[0]["end"] == pytest.approx(1.15, abs=0.02)
    assert "highlights" not in subs[0]
    assert subs[1]["style"] == "dialogue"


def test_tts_fish_s21_default_cache_tags_and_spoken_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: list[tuple[dict[str, object], dict[str, str]]] = []
    wav_sample = write_sine_wav(tmp_path / "fish_s21.wav", duration_s=0.8, amplitude=0.2).read_bytes()

    def fake_post(url: str, payload: dict[str, object], headers: dict[str, str], label: str, **kwargs: object) -> bytes:
        captured.append((payload, headers))
        return wav_sample

    monkeypatch.setenv("FISH_API_KEY", "sk-test-s21-key")
    monkeypatch.delenv("FISH_MODEL", raising=False)
    monkeypatch.delenv("FISH_TTS_MODEL", raising=False)
    monkeypatch.delenv("FISH_VOICE_ID", raising=False)
    monkeypatch.setattr(tts, "_post_json_wav", fake_post)

    tl_path = tmp_path / "timeline.json"
    tl_path.write_text(json.dumps({
        "schema_version": 3,
        "canvas": {"width": 320, "height": 240, "fps": "24"},
        "sources": {"s1": "s1.mp4"},
        "video": [{"source": "s1", "in": 0.0, "out": 3.0}],
        "audio": {
            "narration": [{
                "id": "n1", "at": 0.2,
                "text": "2025赛季25投11中。",
                "spoken_text": "[chuckle] 二零二五赛季二十五投十一仲。",
            }]
        },
    }, ensure_ascii=False), encoding="utf-8")

    vo_dir = tmp_path / "vo"
    rc = tts.main([
        "--timeline", str(tl_path), "--out-dir", str(vo_dir),
        "--engine", "fish", "--update-timeline", "--sync-subtitles",
    ])
    assert rc == 0
    assert len(captured) == 1
    payload, headers = captured[0]
    assert headers["model"] == "s2.1-pro-free"
    assert payload["temperature"] == pytest.approx(0.9)
    assert payload["top_p"] == pytest.approx(0.9)
    assert payload["text"] == "[chuckle] 二零二五赛季二十五投十一仲。"

    updated = json.loads(tl_path.read_text(encoding="utf-8"))
    assert updated["subtitles"][0]["text"] == "2025赛季25投11中。"

    # Changing temperature or FISH_MODEL must invalidate the SHA-256 cache.
    segs = updated["audio"]["narration"]
    r_temp = tts.synthesize_segments(segs, vo_dir, engine="fish", temperature=0.5, top_p=0.6)
    assert len(captured) == 2 and r_temp["segments"][0]["cached"] is False
    monkeypatch.setenv("FISH_MODEL", "s2-pro")
    r_model = tts.synthesize_segments(segs, vo_dir, engine="fish", temperature=0.9, top_p=0.9)
    assert len(captured) == 3 and r_model["segments"][0]["cached"] is False
    assert captured[-1][1]["model"] == "s2-pro"

    # Local engine strips [chuckle] inline tags.
    local_seen: list[str] = []

    def fake_local(text: str, out_wav: Path, *, voice: str | None, speed: float) -> str:
        local_seen.append(text)
        write_sine_wav(out_wav, duration_s=0.5, amplitude=0.2)
        return "local:mock"

    monkeypatch.setattr(tts, "_synth_local", fake_local)
    tts.synthesize_segments([{"id": "n1", "text": "[chuckle] Hello [whisper] world"}], tmp_path / "loc", engine="local")
    assert local_seen == ["Hello world"]


def test_tts_whole_article_dp_split_and_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    if not tts.has_tool("ffmpeg"):
        pytest.skip("ffmpeg unavailable")
    import array
    import math
    import wave

    def make_article_wav(path: Path, blocks: list[tuple[bool, float]], sr: int = 48000) -> bytes:
        buf = array.array("h")
        for voiced, dur_s in blocks:
            n = int(round(sr * dur_s))
            if voiced:
                buf.extend(int(round(0.25 * 32767 * math.sin(2.0 * math.pi * 440.0 * i / sr))) for i in range(n))
            else:
                buf.extend([0] * n)
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(buf.tobytes())
        return path.read_bytes()

    master_bytes = make_article_wav(
        tmp_path / "master.wav",
        [(True, 1.0), (False, 0.35), (True, 1.0), (False, 0.35), (True, 0.8)],
    )
    solid_bytes = make_article_wav(tmp_path / "solid.wav", [(True, 2.5)])
    calls: list[str] = []

    def fake_post_split(url: str, payload: dict[str, object], headers: dict[str, str], label: str, **kwargs: object) -> bytes:
        calls.append(str(payload["text"]))
        return master_bytes

    monkeypatch.setenv("FISH_API_KEY", "sk-test-article")
    monkeypatch.delenv("FISH_MODEL", raising=False)
    monkeypatch.setattr(tts, "_post_json_wav", fake_post_split)

    segs = [
        {"id": "n1", "at": 0.0, "text": "第一段故事开篇十个字。"},
        {"id": "n2", "at": 1.5, "text": "第二段转折继续十个字。"},
        {"id": "n3", "at": 3.0, "text": "第三段收尾八个字。"},
    ]
    res = tts.synthesize_segments(segs, tmp_path / "wa", engine="fish", whole_article=True)
    assert res["synthesis_mode"] == "whole_article"
    assert len(calls) == 1 and "\n" in calls[0]
    assert [round(s["actual_s"], 1) for s in res["segments"]] == [1.0, 1.0, 0.8]

    # Fallback when no interior pauses exist in master synthesis.
    fb_calls: list[str] = []

    def fake_post_fb(url: str, payload: dict[str, object], headers: dict[str, str], label: str, **kwargs: object) -> bytes:
        fb_calls.append(str(payload["text"]))
        return solid_bytes

    monkeypatch.setattr(tts, "_post_json_wav", fake_post_fb)
    res_fb = tts.synthesize_segments(segs, tmp_path / "wa_fb", engine="fish", whole_article=True)
    assert res_fb["synthesis_mode"] == "per_segment_fallback"
    assert len(fb_calls) == 1 + len(segs)


def test_tts_streaming_wav_headers_accepted_and_transcoded(tmp_path: Path) -> None:
    import os

    base = bytearray(write_sine_wav(tmp_path / "base.wav", duration_s=0.4, amplitude=0.2).read_bytes())
    data_pos = base.find(b"data", 12)
    assert data_pos != -1

    # Streaming RIFF/data headers emitted by live Fish Audio s2.1-pro-free (0xFFFFFFFF or 0).
    for riff_sz, data_sz in ((0xFFFFFFFF, 0xFFFFFFFF), (0xFFFFFFFF, 0), (0, 0), (0x7FFFFFFF, 0x7FFFFFFF)):
        streamed = bytearray(base)
        streamed[4:8] = riff_sz.to_bytes(4, "little")
        streamed[data_pos + 4 : data_pos + 8] = data_sz.to_bytes(4, "little")
        tts._validate_wav_bytes(bytes(streamed), "Fish Audio")
        if tts.has_tool("ffmpeg"):
            out_wav = tmp_path / f"fixed_{riff_sz}_{data_sz}.wav"
            tts._write_raw_as_pcm16(bytes(streamed), out_wav)
            assert tts._wav_duration(out_wav) == pytest.approx(0.4, abs=0.02)
        elif os.environ.get("RECAP_REQUIRE_FFMPEG", "").strip().lower() not in {"", "0", "false", "no"}:
            pytest.fail("RECAP_REQUIRE_FFMPEG is set, but ffmpeg is unavailable")

    # Genuinely truncated non-streaming WAV must still be rejected.
    truncated = bytes(base[:-400])
    with pytest.raises(RuntimeError, match="invalid/truncated WAV frames"):
        tts._validate_wav_bytes(truncated, "Fish Audio")


def test_tts_measure_only_from_other_cwd_and_local_short_audio_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    work = tmp_path / "work"
    vo_dir = work / "media" / "vo"
    vo_dir.mkdir(parents=True)
    wav_p = write_sine_wav(vo_dir / "n1.wav", duration_s=0.6, amplitude=0.2)
    tl_path = work / "timeline.json"
    tl_path.write_text(
        json.dumps({"schema_version": 3, "audio": {"narration": [{"id": "n1", "at": 0.1, "text": "测试旁白", "file": "media/vo/n1.wav"}]}}),
        encoding="utf-8",
    )
    other_cwd = tmp_path / "elsewhere"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)
    monkeypatch.setattr(tts, "_synth_local", lambda *a, **kw: pytest.fail("must not synthesize when --measure-only"))

    rep_path = tmp_path / "measure.json"
    rc = tts.main(["--timeline", str(tl_path), "--out-dir", str(vo_dir), "--engine", "local", "--measure-only", "--out", str(rep_path)])
    assert rc == 0
    rep = json.loads(rep_path.read_text(encoding="utf-8"))
    assert rep["segments"][0]["cached"] is True
    assert rep["segments"][0]["actual_s"] == pytest.approx(0.6, abs=0.02)

    # Pre-recorded WAV with empty text also works in --measure-only mode.
    empty_txt_rep = tts.synthesize_segments([{"id": "n1", "at": 0.0, "file": "media/vo/n1.wav"}], vo_dir, engine="local", measure_only=True, base_dir=work)
    assert empty_txt_rep["segments"][0]["actual_s"] == pytest.approx(0.6, abs=0.02)

    # Missing WAV in --measure-only must raise FileNotFoundError instead of synthesizing.
    wav_p.unlink()
    with pytest.raises(FileNotFoundError, match="--measure-only WAV not found"):
        tts.synthesize_segments([{"id": "n1", "text": "测试旁白", "file": "media/vo/n1.wav"}], vo_dir, engine="local", measure_only=True, base_dir=work)

    # Local engine emitting < 0.15s audio for CJK text emits a non-blocking --voice warning.
    def fake_short_local(text: str, out_wav: Path, *, voice: str | None, speed: float) -> str:
        write_sine_wav(out_wav, duration_s=0.02, amplitude=0.1)
        return "local:say"

    monkeypatch.setattr(tts, "_synth_local", fake_short_local)
    short_rep = tts.synthesize_segments([{"id": "n2", "text": "半夜恶室友硬拉顾逸不放"}], tmp_path / "short_vo", engine="local")
    assert "--voice" in short_rep["segments"][0].get("warning", "")
