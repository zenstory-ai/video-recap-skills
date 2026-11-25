"""Unit tests for _common.py, _audio.py, and _subtitles.py."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest

import _audio
import _common
import _subtitles
from conftest import write_sine_wav


def test_common_fps_frames_and_atomic_json(tmp_path: Path) -> None:
    assert _common.parse_fps("30000/1001") == Fraction(30000, 1001)
    assert _common.parse_fps(29.97) == Fraction(30000, 1001)
    assert _common.parse_fps("24") == Fraction(24, 1)
    assert _common.parse_fps(24) == Fraction(24, 1)
    assert _common.parse_fps(24.0) == Fraction(24, 1)
    assert _common.parse_fps(23.976) == Fraction(24000, 1001)
    assert _common.parse_fps(25) == Fraction(25, 1)
    assert _common.frame_count(2.0, Fraction(25, 1)) == 50
    assert _common.frame_count(0.0, Fraction(25, 1)) == 0

    target = tmp_path / "nested" / "data.json"
    _common.save_json(target, {"hello": "世界", "n": 3})
    assert _common.load_json(target) == {"hello": "世界", "n": 3}


def test_common_sanitize_api_error_redacts_secrets() -> None:
    key = "tp-" + ("secret12" * 4)
    raw = f"err data:audio/wav;base64,QUJDREVGR0hJSktMTU5P key={key} custom=MY_TOKEN_VAL"
    safe = _common.sanitize_api_error(raw, extra_secrets=("MY_TOKEN_VAL",))
    assert "QUJDREV" not in safe
    assert key not in safe
    assert "MY_TOKEN_VAL" not in safe
    assert "<redacted-data-url>" in safe
    assert "<redacted-key>" in safe


def test_common_color_tags_and_timeline_spans(tmp_path: Path) -> None:
    rgb_tags = _common.output_color_tags({"pix_fmt": "rgb24", "color_space": "gbr"})
    assert rgb_tags["colorspace"] == "bt709"
    assert rgb_tags["from_rgb"] is True
    assert "-colorspace" in _common.color_tag_args(rgb_tags)

    tl = {
        "sources": {"a": "a.mp4"},
        "video": [{"id": "c1", "source": "a", "in": 1.0, "out": 3.5}, {"id": "c2", "source": "a", "in": 5.0, "out": 6.0}],
    }
    spans, total = _common.timeline_video_spans(tl)
    assert total == 3.5
    assert spans[1]["timeline_start"] == 2.5
    assert _common.resolve_source(tl, "a", tmp_path) == (tmp_path / "a.mp4").resolve()


def test_audio_ducking_keyframes_and_rms_norm(tmp_path: Path) -> None:
    merged = _audio.coalesce_duck_windows([(1.0, 2.0, 0.2), (2.2, 3.0, 0.15)], bridge_s=0.5)
    assert merged == [[1.0, 3.0, 0.15]]
    expr = _audio.ducking_expression([(1.0, 2.0, 0.2)], idle_gain=1.0, fade_s=0.25)
    assert "between" not in expr and "min(1,max(0" in expr

    tl = {
        "sources": {"s1": "s1.mp4"},
        "video": [{"source": "s1", "in": 0.0, "out": 6.0}],
        "audio": {"narration": [{"id": "n1", "at": 1.0, "duration": 2.0}], "bgm": [{"source": "b1", "gain": 0.3}]},
        "mix": {"duck": {"under_narration": 0.2, "bgm_under_narration": 0.1, "fade_s": 0.25}},
    }
    kfs = _audio.ducking_keyframes(tl)
    assert kfs["original"][0] == {"t": 0.0, "gain": 1.0}
    assert any(p["t"] == 1.0 and p["gain"] == 0.2 for p in kfs["original"])
    assert any(p["t"] == 1.0 and p["gain"] == 0.1 for p in kfs["bgm"])

    src_wav = write_sine_wav(tmp_path / "quiet.wav", duration_s=0.5, amplitude=0.05)
    dst_wav = tmp_path / "norm.wav"
    meta = _audio.normalize_wav_rms(src_wav, dst_wav, target_rms_dbfs=-20.0, peak_limit=0.98)
    assert meta["rms_dbfs_after"] == pytest.approx(-20.0, abs=0.4)
    assert meta["peak_after"] <= 0.98


def test_audio_loudnorm_filter_plan() -> None:
    measured = {"integrated": -22.0, "true_peak": -4.0, "lra": 6.0, "threshold": -32.0, "target_offset": 0.2}
    filt, plan = _audio.build_loudnorm_filter(measured, target_lufs=-14.0, true_peak=-1.5)
    assert "linear=true" in filt
    assert plan["mode"] == "two_pass_linear_limited"
    assert "alimiter" in filt


def test_subtitles_srt_and_ass_generation(tmp_path: Path) -> None:
    cues = [
        {"start": 0.25, "end": 2.5, "text": "这是一句测试旁白，用来验证自动换行。", "style": "narration"},
        {"start": 2.7, "end": 4.0, "text": "Hello world dialogue.", "style": "dialogue"},
    ]
    srt_p = _subtitles.write_srt(cues, tmp_path / "out.srt", strip_trailing_punct=True)
    parsed = _subtitles.parse_srt(srt_p)
    assert len(parsed) == 2
    assert not parsed[0]["text"].endswith("。")

    ass_p = _subtitles.write_ass(
        cues, {"width": 1080, "height": 1920}, tmp_path / "out.ass",
        style={"primary_color": "#FFFFFF", "glow": 1.5, "max_chars": 12},
        safe_y=[0.74, 0.88],
    )
    ass_txt = ass_p.read_text(encoding="utf-8")
    assert "PlayResX: 1080" in ass_txt and "PlayResY: 1920" in ass_txt
    assert "Style: Glow," in ass_txt
    assert "\\c&H" not in ass_txt
    assert "\\fad(90,70)" in ass_txt
    assert "自动换行。" not in ass_txt  # strip_trailing_punct defaults to True

    # Semantic balanced 2-line wrap prefers clause punctuation & keeps numbers intact.
    wrapped_clause = _subtitles.wrap_subtitle_text("第四节还剩两分钟，他连续命中关键三分。", max_chars=12)
    assert wrapped_clause == "第四节还剩两分钟\n他连续命中关键三分"
    wrapped_num = _subtitles.wrap_subtitle_text("在2025年拿到38.5%命中率，完成逆转。", max_chars=12)
    assert "38.5%" in wrapped_num
    assert not wrapped_num.endswith("。")
    kept_punct = _subtitles.wrap_subtitle_text("他犯满离场。", max_chars=12, strip_trailing_punct=False)
    assert kept_punct == "他犯满离场。"

    vtt_p = tmp_path / "short.vtt"
    vtt_p.write_text("WEBVTT\n\n00:01.250 --> 00:03.500\nHour-omitted VTT cue\n", encoding="utf-8")
    vtt_cues = _subtitles.parse_srt(vtt_p)
    assert len(vtt_cues) == 1
    assert vtt_cues[0] == {"start": 1.25, "end": 3.5, "text": "Hour-omitted VTT cue"}


def test_ducking_filters_split_long_timelines_and_keep_the_same_curve(has_ffmpeg: bool) -> None:
    import subprocess

    windows = [(10.0 * i, 10.0 * i + 4.0, 0.18) for i in range(120)]
    filters = _audio.ducking_filters(windows, 1.0, 0.25, 0.5)
    assert len(filters) == 3 and len(_audio.ducking_filters(windows[:10], 1.0, 0.25, 0.5)) == 1
    bgm = _audio.ducking_filters([(s, e, 0.12) for s, e, _ in windows], 0.3, 0.25, 0.5)
    assert len(bgm) == 3 and "0.3000" in bgm[0] and "0.3000" not in bgm[1]
    if has_ffmpeg:
        res = subprocess.run(["ffmpeg", "-hide_banner", "-f", "lavfi", "-i", "anullsrc=d=2", "-af", ",".join(filters), "-f", "null", "-"],
                             capture_output=True, text=True)
        assert res.returncode == 0, res.stderr[-300:]
