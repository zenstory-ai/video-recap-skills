"""End-to-end real-ffmpeg tests for render.py and check.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import check
import render


def test_validate_timeline_fails_fast_on_keep_window_collision(synthetic_media: dict[str, Path]) -> None:
    tl = {
        "schema_version": 3,
        "canvas": {"width": 320, "height": 240, "fps": "25"},
        "sources": {"v1": str(synthetic_media["v1"]), "n1": str(synthetic_media["narr"])},
        "video": [{"source": "v1", "in": 0.0, "out": 2.0, "keep_windows": [[0.5, 1.2]]}],
        "audio": {"narration": [{"id": "n1", "at": 0.3, "file": "n1", "duration": 0.9}]},
    }
    pre = render.validate_timeline_physical(tl)
    assert any("collides with keep_window" in w for w in pre["warnings"])
    with pytest.raises(ValueError, match="collides with keep_window"):
        render.validate_timeline_physical(tl, strict=True)
    bad = {**tl, "video": [{"source": "v1", "in": 0.0, "out": 99.0}]}
    with pytest.raises(ValueError, match="exceeds source duration"):
        render.validate_timeline_physical(bad)


def test_render_multi_source_vertical_with_overlays_sfx_subtitles_and_check(
    synthetic_media: dict[str, Path], tmp_path: Path
) -> None:
    tl_path = tmp_path / "timeline.json"
    tl = {
        "schema_version": 3,
        "canvas": {
            "width": 360, "height": 640, "fps": "25",
            "backdrop": {"kind": "blur", "blur_sigma": 8},
            "viewport": {"x": 0.0, "y": 0.2, "w": 1.0, "h": 0.5},
        },
        "sources": {
            "v1": str(synthetic_media["v1"]),
            "v2": str(synthetic_media["v2"]),
            "narr": str(synthetic_media["narr"]),
            "bgm": str(synthetic_media["bgm"]),
            "sfx": str(synthetic_media["sfx"]),
            "plate": str(synthetic_media["overlay"]),
        },
        "video": [
            {"id": "c1", "source": "v1", "in": 0.0, "out": 1.5, "audio": "keep", "audio_gain": 0.8},
            {"id": "c2", "source": "v2", "in": 0.0, "out": 1.2, "audio": "mute"},
        ],
        "audio": {
            "narration": [{"id": "n1", "at": 0.2, "file": "narr", "text": "Hello recap", "gain": 1.0, "duration": 0.9}],
            "bgm": [{"id": "b1", "source": "bgm", "in": 0.0, "at": 0.0, "out": 2.7, "loop": True, "gain": 0.25, "fade_out": 0.3}],
            "sfx": [{"id": "x1", "source": "sfx", "at": 1.5, "gain": 0.6}],
        },
        "overlays": [
            {"id": "o1", "source": "plate", "start": 0.0, "end": 1.2, "rect": {"x": 0.1, "y": 0.05, "w": 0.8, "h": 0.1}, "fade_in": 0.1, "motion": "slide_up"}
        ],
        "subtitles": [
            {"start": 0.2, "end": 1.1, "text": "Hello recap", "style": "narration"},
            {"start": 1.5, "end": 2.5, "text": "Second shot", "style": "dialogue"},
        ],
        "subtitle_style": {"font_size": 24, "safe_y": [0.74, 0.88]},
        "mix": {"target_lufs": -14.0, "true_peak_dbtp": -1.5, "duck": {"under_narration": 0.2, "fade_s": 0.2}},
    }
    tl_path.write_text(json.dumps(tl, ensure_ascii=False, indent=2), encoding="utf-8")

    out_mp4 = tmp_path / "final.mp4"
    out_srt = tmp_path / "final.srt"
    rc = render.main([str(tl_path), "--out", str(out_mp4), "--out-srt", str(out_srt)])
    assert rc == 0
    assert out_mp4.is_file() and out_mp4.stat().st_size > 5000
    assert out_srt.is_file()

    chk_path = tmp_path / "check.json"
    rc_chk = check.main([str(out_mp4), "--timeline", str(tl_path), "--out", str(chk_path)])
    assert rc_chk == 0
    report = json.loads(chk_path.read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert set(report["checks"]) == {
        "container_valid", "decode_integrity", "geometry_and_cfr",
        "av_sync", "audio_health", "visual_health", "subtitle_and_timing",
    }
    assert all(row["status"] in {"pass", "warn"} for row in report["checks"].values())
    assert isinstance(report["review_hints"], list)
    assert report["telemetry"]["clip_count"] == 2
    assert report["limitations"]


def test_render_preview_and_audio_only(synthetic_media: dict[str, Path], tmp_path: Path) -> None:
    tl = {
        "schema_version": 3,
        "canvas": {"width": 1080, "height": 1920, "fps": "25"},
        "sources": {"v1": str(synthetic_media["v1"])},
        "video": [{"id": "short_sliver", "source": "v1", "in": 0.0, "out": 0.08}, {"id": "main", "source": "v1", "in": 0.2, "out": 1.2}],
    }
    preview_mp4 = tmp_path / "preview.mp4"
    res = render.render_timeline(tl, preview_mp4, preview=True)
    assert preview_mp4.is_file()
    assert max(res["canvas"]["width"], res["canvas"]["height"]) <= 640

    # Subjective/short sliver is telemetry warning only — never blocks check.py ok!
    chk = check.check_media(preview_mp4, timeline=tl)
    assert chk["checks"]["container_valid"]["status"] == "pass"
    assert len(chk["telemetry"]["flash_sliver_warnings"]) == 1

    wav_out = tmp_path / "mix.wav"
    res_a = render.render_timeline(tl, wav_out, audio_only=True, preview=True)
    assert res_a["audio_only"] is True and wav_out.is_file() and wav_out.stat().st_size > 1000


def test_check_fails_cleanly_on_missing_media(tmp_path: Path) -> None:
    rc = check.main([str(tmp_path / "does_not_exist.mp4"), "--out", str(tmp_path / "bad.json")])
    assert rc == 1
    data = json.loads((tmp_path / "bad.json").read_text(encoding="utf-8"))
    assert data["ok"] is False
    assert data["checks"]["container_valid"]["status"] == "fail"


def test_render_viewport_cover_and_prores4444_alpha_video_overlay(
    synthetic_media: dict[str, Path], tmp_path: Path
) -> None:
    import subprocess

    alpha_mov = tmp_path / "remotion_overlay.mov"
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=0xFFD24C@0.5:size=360x640:rate=24:duration=1.2,format=yuva444p10le",
        "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", str(alpha_mov),
    ], check=True)

    tl = {
        "schema_version": 3,
        "canvas": {
            "width": 360, "height": 640, "fps": "24",
            "backdrop": {"kind": "color", "color": "#0A0A0D"},
            "viewport": {"x": 0.0, "y": 0.1, "w": 1.0, "h": 0.8, "fit": "cover"},
        },
        "sources": {"v2": str(synthetic_media["v2"]), "anim": str(alpha_mov)},
        "video": [{"id": "c1", "source": "v2", "in": 0.0, "out": 1.2, "focus": [0.5, 0.3], "audio": "mute"}],
        "overlays": [{"id": "remotion_packaging", "source": "anim", "start": 0.0, "end": 1.2, "rect": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}}],
    }
    out_mp4 = tmp_path / "cover_alpha.mp4"
    res = render.render_timeline(tl, out_mp4)
    assert res["canvas"]["fps"] == "24/1"
    chk = check.check_media(out_mp4, timeline=tl)
    assert chk["ok"] is True
    assert chk["checks"]["geometry_and_cfr"]["evidence"]["fps"] == 24.0


def test_check_edge_audio_warnings_detect_leak_and_truncation(synthetic_media: dict[str, Path], tmp_path: Path) -> None:
    tl = {
        "schema_version": 3,
        "canvas": {"width": 320, "height": 240, "fps": "25"},
        "sources": {"v1": str(synthetic_media["v1"]), "narr": str(synthetic_media["narr"])},
        "video": [
            {"id": "bed", "source": "v1", "in": 0.0, "out": 1.6, "audio": "keep", "audio_gain": 1.0},
            {"id": "cut_keep", "source": "v1", "in": 0.4, "out": 1.4, "audio": "keep", "audio_gain": 1.0},
        ],
        "audio": {"narration": [{"id": "n1", "at": 0.1, "file": "narr", "duration": 0.8}]},
    }
    out_mp4 = tmp_path / "edge_warn.mp4"
    render.render_timeline(tl, out_mp4)
    chk = check.check_media(out_mp4, timeline=tl)
    assert chk["ok"] is True
    assert chk["checks"]["audio_health"]["status"] == "warn"
    warns = chk["telemetry"]["edge_audio_warnings"]
    codes = {w["code"] for w in warns}
    assert "narration_bed_audio_leak" in codes
    assert "edge_speech_truncation" in codes
    hint_codes = {h["code"] for h in chk["review_hints"]}
    assert {"narration_bed_audio_leak", "edge_speech_truncation"}.issubset(hint_codes)
    assert "edge audio warning" in chk["checks"]["audio_health"]["detail"]


def test_check_soft_warn_review_hints_vs_hard_fail(tmp_path: Path) -> None:
    import subprocess

    black_mp4 = tmp_path / "black_card.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=black:size=320x240:rate=25:duration=2.0",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(black_mp4),
    ], check=True)

    soft_tl = {
        "schema_version": 3,
        "canvas": {"width": 320, "height": 240, "fps": "25"},
        "sources": {"b": str(black_mp4)},
        "video": [{"id": "b1", "source": "b", "in": 0.0, "out": 2.0, "audio": "mute"}],
        "subtitles": [
            {"start": 0.2, "end": 1.2, "text": "Line A"},
            {"start": 0.5, "end": 1.5, "text": "Dual subtitle"},
        ],
    }
    soft = check.check_media(black_mp4, timeline=soft_tl)
    assert soft["ok"] is True
    assert soft["checks"]["visual_health"]["status"] == "warn"
    assert soft["checks"]["subtitle_and_timing"]["status"] == "warn"
    soft_codes = {h["code"] for h in soft["review_hints"]}
    assert {"black_span", "subtitle_overlap"}.issubset(soft_codes)

    hard_tl = {**soft_tl, "subtitles": [{"start": 1.2, "end": 0.5, "text": "Broken span"}]}
    hard = check.check_media(black_mp4, timeline=hard_tl)
    assert hard["ok"] is False
    assert hard["checks"]["subtitle_and_timing"]["status"] == "fail"


def test_render_all_mute_video_with_narration_bgm_sfx(synthetic_media: dict[str, Path], tmp_path: Path) -> None:
    tl = {
        "schema_version": 3,
        "canvas": {"width": 320, "height": 240, "fps": "25"},
        "sources": {
            "v1": str(synthetic_media["v1"]),
            "v2": str(synthetic_media["v2"]),
            "narr": str(synthetic_media["narr"]),
            "bgm": str(synthetic_media["bgm"]),
            "sfx": str(synthetic_media["sfx"]),
        },
        "video": [
            {"id": "m1", "source": "v1", "in": 0.0, "out": 1.0, "audio": "mute"},
            {"id": "m2", "source": "v2", "in": 0.0, "out": 1.0, "audio": "mute"},
        ],
        "audio": {
            "narration": [{"id": "n1", "at": 0.1, "file": "narr", "duration": 0.9}],
            "bgm": [{"id": "b1", "source": "bgm", "in": 0.0, "at": 0.0, "out": 2.0, "loop": True, "gain": 0.2}],
            "sfx": [{"id": "x1", "source": "sfx", "at": 1.0, "gain": 0.5}],
        },
    }
    out_mp4 = tmp_path / "all_mute_vo.mp4"
    res = render.render_timeline(tl, out_mp4)
    assert res["warnings"] == []
    assert out_mp4.is_file() and out_mp4.stat().st_size > 2000
    chk = check.check_media(out_mp4, timeline=tl)
    assert chk["ok"] is True
    assert chk["checks"]["audio_health"]["status"] == "pass"
