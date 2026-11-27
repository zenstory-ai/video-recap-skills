"""End-to-end real-ffmpeg tests for render.py and check.py."""

from __future__ import annotations

import json
import subprocess
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
    if not check.has_tool("ffmpeg") or not check.has_tool("ffprobe"):
        pytest.skip("ffmpeg/ffprobe unavailable")
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


def test_render_range_exact_duration_and_single_quote_ass_and_fonts_dir(synthetic_media: dict[str, Path], tmp_path: Path) -> None:
    import _common

    work_dir = tmp_path / "Alice's render dir"
    tl = {
        "schema_version": 3,
        "canvas": {"width": 320, "height": 240, "fps": "25"},
        "sources": {"v1": str(synthetic_media["v1"])},
        "video": [
            {"id": "c1", "source": "v1", "in": 0.0, "out": 1.5},
            {"id": "c2", "source": "v1", "in": 0.0, "out": 1.5},
        ],
        "subtitles": [{"start": 0.5, "end": 1.4, "text": "Range slice subtitle"}],
        "subtitle_style": {"font_file": str(tmp_path / "Bob's fonts" / "Custom.ttf")},  # 落到 ass 的 fontsdir
    }
    (tmp_path / "Bob's fonts").mkdir()
    out_mp4 = tmp_path / "ranged.mp4"
    render.render_timeline(tl, out_mp4, preview=True, time_range=(0.5, 1.5), work_dir=work_dir)
    assert out_mp4.is_file()
    assert _common.probe_media(out_mp4)["duration_s"] == pytest.approx(1.0, abs=0.2)


def test_check_catches_odd_dimensions_and_handles_null_audio(synthetic_media: dict[str, Path], tmp_path: Path) -> None:
    import subprocess

    _ = synthetic_media
    odd_mp4 = tmp_path / "odd.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=size=321x240:rate=25:duration=1.0",
        "-c:v", "libx264", "-pix_fmt", "yuv444p", str(odd_mp4),
    ], check=True)
    tl = {"schema_version": 3, "audio": None, "subtitles": [{"start": 0.1, "end": 0.8, "text": "Null audio ok"}]}
    report = check.check_media(odd_mp4, timeline=tl)
    assert report["ok"] is False
    assert "odd dimensions 321x240" in report["checks"]["geometry_and_cfr"]["detail"]
    assert report["checks"]["subtitle_and_timing"]["status"] == "pass"


def test_filter_script_args_uses_the_option_file_syntax_ffmpeg_accepts(tmp_path: Path, monkeypatch) -> None:
    import _common

    long_expr = "anullsrc," * 600 + "anull"
    monkeypatch.setattr(_common, "_OPTION_FILES", [True])
    assert _common.filter_script_args("filter_complex", long_expr, tmp_path)[0][0] == "-/filter_complex"
    monkeypatch.setattr(_common, "_OPTION_FILES", [False])
    assert _common.filter_script_args("filter_complex", long_expr, tmp_path)[0][0] == "-filter_complex_script"
    assert _common.filter_script_args("filter_complex", "anull", tmp_path)[0] == ["-filter_complex", "anull"]


@pytest.mark.parametrize("chunk", [48, 8])
def test_render_many_short_clips_stays_frame_exact(synthetic_media: dict[str, Path], tmp_path: Path, monkeypatch, chunk: int) -> None:
    """40 clips: one graph over the inline limit (file option path), or seeked batches of 8 (long-timeline path); neither drifts."""
    monkeypatch.setattr(render, "_CHUNK_CLIPS", chunk)
    clips = [{"source": "v1", "in": round(0.05 * (i % 20), 3), "out": round(0.05 * (i % 20) + 0.13 + 0.01 * (i % 3), 3)} for i in range(40)]
    tl = {"schema_version": 3, "canvas": {"width": 320, "height": 240, "fps": "25"}, "sources": {"v1": str(synthetic_media["v1"])}, "video": clips}
    (tmp_path / "timeline.json").write_text(json.dumps(tl), encoding="utf-8")
    out = tmp_path / "many.mp4"
    assert render.main([str(tmp_path / "timeline.json"), "--out", str(out)]) == 0
    spans, total = render.timeline_video_spans(tl)
    assert abs(total * 25 - round(total * 25)) < 1e-6 and abs(spans[-1]["timeline_end"] - total) < 1e-9
    info = render.probe_media(out)
    assert abs(float(info["duration_s"]) - total) <= 0.05


def test_render_does_not_inherit_source_title_comment_or_chapters(synthetic_media: dict[str, Path], tmp_path: Path) -> None:
    import subprocess

    meta = tmp_path / "chapters.txt"
    meta.write_text(";FFMETADATA1\ntitle=Scraped Title\ncomment=https://example.invalid/watch\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=1000\ntitle=Intro\n", encoding="utf-8")
    tagged = tmp_path / "tagged.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(synthetic_media["v1"]), "-i", str(meta), "-map", "0", "-map_metadata", "1",
                    "-map_chapters", "1", "-c", "copy", str(tagged)], check=True)
    tl = {
        "schema_version": 3,
        "canvas": {"width": 320, "height": 240, "fps": "25"},
        "sources": {"v1": str(tagged)},
        "video": [{"id": "c1", "source": "v1", "in": 0.0, "out": 1.2, "audio": "keep"}],
    }
    out = tmp_path / "out.mp4"
    render.render_timeline(tl, out)
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags", "-show_chapters", "-of", "json", str(out)],
                                      capture_output=True, text=True, encoding="utf-8", errors="replace", check=True).stdout)
    tags = {k.lower(): v for k, v in (probe.get("format", {}).get("tags") or {}).items()}
    assert "title" not in tags and "comment" not in tags
    assert probe.get("chapters") == []


def test_render_chunked_special_work_dir_and_all_mute_parity(
    synthetic_media: dict[str, Path], tmp_path: Path, monkeypatch
) -> None:
    import os

    monkeypatch.setattr(render, "_CHUNK_CLIPS", 2)
    monkeypatch.setattr(
        render, "measure_loudness",
        lambda **_kw: (_ for _ in ()).throw(AssertionError("all-muted chunked render must not run loudnorm")),
    )
    work_dir = tmp_path / ("Alice's:chunk" if os.name != "nt" else "Alice's chunk")
    tl = {
        "schema_version": 3,
        "canvas": {"width": 320, "height": 240, "fps": "25"},
        "sources": {"v1": str(synthetic_media["v1"])},
        "video": [{"id": f"m{i}", "source": "v1", "in": 0.1 * i, "out": 0.1 * i + 0.24, "audio": "mute"} for i in range(5)],
    }
    out = tmp_path / "chunked_muted.mp4"
    res = render.render_timeline(tl, out, work_dir=work_dir)
    assert out.is_file() and res["aac_true_peak"] == {}
    chunks_txt = (work_dir / "chunks.txt").read_text(encoding="utf-8")
    assert chunks_txt.splitlines() == ["file 'chunk_0000.mkv'", "file 'chunk_0001.mkv'", "file 'chunk_0002.mkv'"]
    assert render.probe_media(out)["duration_s"] == pytest.approx(res["duration_s"], abs=0.05)


def test_render_chunked_continuous_join_fade_clamp_and_warnings_parity(
    synthetic_media: dict[str, Path], tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(render, "_CHUNK_CLIPS", 2)
    orig_fsa = render.filter_script_args
    captured_fc: list[str] = []

    def spy_fsa(flag: str, expr: str, work_dir: Path, threshold: int = 4096):
        captured_fc.append(expr)
        return orig_fsa(flag, expr, work_dir, threshold)

    monkeypatch.setattr(render, "filter_script_args", spy_fsa)
    tl = {
        "schema_version": 3,
        "canvas": {"width": 320, "height": 240, "fps": "60"},
        "sources": {"v1": str(synthetic_media["v1"]), "narr": str(synthetic_media["narr"])},
        "video": [
            {"id": "short", "source": "v1", "in": 0.0, "out": 0.015},
            {"id": "join_a", "source": "v1", "in": 0.2, "out": 0.5, "keep_windows": [[0.25, 0.45]]},
            {"id": "join_b", "source": "v1", "in": 0.5, "out": 0.8},
        ],
        "audio": {"narration": [{"id": "n1", "at": 0.05, "file": "narr", "duration": 0.3}]},
    }
    out = tmp_path / "chunked_parity.mp4"
    res = render.render_timeline(tl, out, preview=True)
    assert out.is_file()
    assert any("collides with keep_window" in w for w in res["warnings"])

    chunk0_audio = {part.rsplit("[", 1)[1].rstrip("]"): part for part in captured_fc[0].split(";") if "[a" in part and "concat=" not in part}
    chunk1_audio = {part.rsplit("[", 1)[1].rstrip("]"): part for part in captured_fc[1].split(";") if "[a" in part and "concat=" not in part}
    assert "afade=t=in:st=0:d=0.0083" in chunk0_audio["a0"] and "afade=t=out:st=0.0083:d=0.0083" in chunk0_audio["a0"]
    assert "afade=t=in:" in chunk0_audio["a1"] and "afade=t=out:" not in chunk0_audio["a1"]
    assert "afade=t=in:" not in chunk1_audio["a0"] and "afade=t=out:" in chunk1_audio["a0"]


def test_render_omitted_canvas_fps_and_color_backdrop_quantized_duration(
    synthetic_media: dict[str, Path], tmp_path: Path
) -> None:
    tl = {
        "schema_version": 3,
        "canvas": {
            "width": 320, "height": 240,
            "backdrop": {"kind": "color", "color": "#112233"},
            "viewport": {"x": 0.0, "y": 0.1, "w": 1.0, "h": 0.8},
        },
        "sources": {"v1": str(synthetic_media["v1"])},
        "video": [{"id": f"c{i}", "source": "v1", "in": 0.1 * i, "out": 0.1 * i + 0.13} for i in range(10)],
    }
    pre = render.validate_timeline_physical(tl)
    assert pre["duration_s"] == pytest.approx(1.2, abs=1e-6)

    out = tmp_path / "no_canvas_fps.mp4"
    res = render.render_timeline(tl, out, preview=True)
    assert res["canvas"]["fps"] == "25/1"
    assert res["duration_s"] == pytest.approx(1.2, abs=1e-6)
    info = render.probe_media(out)
    assert info["duration_s"] == pytest.approx(1.2, abs=0.05)
    assert abs(info["duration_s"] - info["audio_duration_s"]) <= 0.06

    # Color backdrop uses quantized duration n_frames / fps (0.08s), not raw span['duration'] (0.06s).
    vf = render._clip_vfilter(
        "[0:v:0]", "[v0]", {"in": 0.0, "out": 0.06, "duration": 0.06, "clip": {}},
        {"fps_rational": "25/1"}, 320, 240, "25/1", 2,
        {"kind": "color", "color": "#112233"}, {"x": 0.0, "y": 0.1, "w": 1.0, "h": 0.8},
    )
    assert ":d=0.080000," in vf and ":d=0.060000," not in vf


@pytest.mark.parametrize("chunk", [48, 1])
def test_render_slowed_clip_keeps_its_timeline_length(synthetic_media: dict[str, Path], tmp_path: Path, monkeypatch, chunk: int) -> None:
    """A muted clip with speed 0.75 fills (out-in)/0.75 of the timeline in the single-graph and the batched paths."""
    monkeypatch.setattr(render, "_CHUNK_CLIPS", chunk)
    tl = {"schema_version": 3, "canvas": {"width": 320, "height": 240, "fps": "25"}, "sources": {"v1": str(synthetic_media["v1"])},
          "video": [{"id": "a", "source": "v1", "in": 0.0, "out": 0.6, "audio": "mute", "speed": 0.75},
                    {"id": "b", "source": "v1", "in": 1.0, "out": 1.4, "audio": "mute"}]}
    spans, total = render.timeline_video_spans(tl)
    assert abs(spans[0]["timeline_end"] - 0.8) < 1e-6 and abs(total - 1.2) < 1e-6
    out = tmp_path / "slow.mp4"
    render.render_timeline(tl, out)
    assert abs(float(render.probe_media(out)["duration_s"]) - total) <= 0.05
    loud = {**tl, "video": [{**tl["video"][0], "audio": "keep"}]}
    with pytest.raises(ValueError, match="only muted clips can change speed"):
        render.render_timeline(loud, tmp_path / "loud.mp4")
