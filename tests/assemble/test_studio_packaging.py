import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "skills" / "video-recap" / "scripts")
)

import packaging  # noqa: E402
import sfx_mix  # noqa: E402
import subtitles.render as subtitle_render  # noqa: E402
import visual_render  # noqa: E402
from assemble import assemble_video  # noqa: E402
from lib import CONFIG  # noqa: E402
from tts_fixtures import tts_segment  # noqa: E402


def _write_plan(work_dir: Path, payload: dict) -> Path:
    path = work_dir / "packaging_plan.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def test_portrait_cinema_compiles_plates_glow_highlights_and_bridging(tmp_path):
    _write_plan(
        tmp_path,
        {
            "schema": "video-recap.packaging_plan.v1",
            "preset": "portrait_cinema",
            "source_crop_bottom_ratio": 0.15,
            "header": {
                "kicker": "《尚公主》精编",
                "title": "替她挡毒昏迷，背他夜行松林",
                "highlights": ["挡毒昏迷", "夜行松林"],
            },
            "subtitle_highlights": ["剧毒", "松林"],
            "callouts": [
                {"start": 0.5, "end": 3.5, "text": "身中剧毒硬撑。", "style": "calligraphy"},
                {"start": 4.2, "end": 7.0, "text": "借认树拽神智", "style": "coral"},
            ],
        },
    )
    input_canvas = {
        "width": 720, "height": 960, "fps": 25.0,
        "storage_width": 720, "storage_height": 960,
        "rotation": 0, "sample_aspect_ratio": "1:1", "display_aspect_ratio": "3:4",
    }
    segs = [
        {
            "index": 0,
            "narration": "他身中剧毒硬撑。",
            "spoken_text": "他身中剧毒硬撑。",
            "actual_place_start": 0.4,
            "actual_place_end": 2.4,
        },
        {
            "index": 1,
            "narration": "一步步走进松林。",
            "spoken_text": "一步步走进松林。",
            "actual_place_start": 2.65,
            "actual_place_end": 4.8,
        },
    ]

    out_canvas = packaging.resolve_output_canvas(tmp_path, input_canvas)
    assert (out_canvas["width"], out_canvas["height"]) == (1080, 1920)

    studio = packaging.prepare_studio_packaging(tmp_path, input_canvas, 8.0, segs)
    assert studio is not None
    assert studio["qc"]["blocking_codes"] == []
    assert len(studio["static_plates"]) == 1
    assert len(studio["timed_plates"]) == 2
    assert Path(studio["static_plates"][0]["path"]).is_file()

    ass_path = subtitle_render._generate_ass(segs, tmp_path, 8.0, out_canvas)
    ass_text = ass_path.read_text(encoding="utf-8")
    assert "PlayResX: 1080" in ass_text
    assert "PlayResY: 1920" in ass_text
    assert "Style: Glow," in ass_text
    assert "\\blur3.2" in ass_text
    assert "{\\c&H4CD2FF&}剧毒" in ass_text
    # 0.25s micro-gap (2.40 -> 2.65) bridged cleanly so cues do not blink off
    assert "0:00:00.40,0:00:02.65,Default" in ass_text

    vqc = visual_render._build_visual_qc(
        segs, tmp_path, 8.0, out_canvas, packaging_qc=studio["qc"]
    )
    assert vqc["verdict"] == "PASS"
    assert vqc["geometry"]["packaging"]["preset"] == "portrait_cinema"


def test_landscape_editorial_paper_coalesces_narrator_strip_and_preserves_dialogue_gap(tmp_path):
    _write_plan(
        tmp_path,
        {
            "schema": "video-recap.packaging_plan.v1",
            "preset": "landscape_cinema",
            "subtitle_card_mode": "editorial_paper",
            "title_mark": {
                "title": "服务区烧烤店",
                "badge": "第一集",
                "start": 0.2,
                "end": 4.0,
                "reprise_tail_seconds": 3.0,
            },
            "callouts": [
                {"start": 4.5, "end": 7.5, "text": "七万网贷接盘", "style": "gold"},
            ],
        },
    )
    (tmp_path / "original_subtitles.json").write_text(
        json.dumps([{"start": 5.5, "end": 8.0, "text": "这批酒今天必须结清！"}], ensure_ascii=False),
        encoding="utf-8",
    )
    canvas = {
        "width": 1920, "height": 1080, "fps": 30.0,
        "storage_width": 1920, "storage_height": 1080,
        "rotation": 0, "sample_aspect_ratio": "1:1", "display_aspect_ratio": "16:9",
    }
    segs = [
        {
            "index": 0, "narration": "第一句解说。", "spoken_text": "第一句解说。",
            "actual_place_start": 0.5, "actual_place_end": 2.2,
        },
        {
            "index": 1, "narration": "紧接着第二句。", "spoken_text": "紧接着第二句。",
            "actual_place_start": 2.5, "actual_place_end": 4.6,
        },
        {
            "index": 2, "narration": "原声对白后再进解说。", "spoken_text": "原声对白后再进解说。",
            "actual_place_start": 8.6, "actual_place_end": 10.8,
        },
    ]

    studio = packaging.prepare_studio_packaging(tmp_path, canvas, 12.0, segs)
    strips = [p for p in studio["timed_plates"] if p["name"].startswith("editorial_strip_")]
    # Seg 0 & Seg 1 (0.3s gap) coalesce into 1 steady strip; gap [4.6..8.6] stays open; Seg 2 is strip #2
    assert len(strips) == 2
    assert strips[0]["end"] < 5.0
    assert strips[1]["start"] > 8.2

    ass_text = subtitle_render._generate_ass(segs, tmp_path, 12.0, canvas).read_text(encoding="utf-8")
    assert "Style: Default," in ass_text and "&H0016191E" in ass_text
    assert "Dialogue,," in ass_text and "「这批酒今天必须结清」" in ass_text


def test_explicit_slot_collision_is_blocked_by_visual_qc(tmp_path):
    _write_plan(
        tmp_path,
        {
            "schema": "video-recap.packaging_plan.v1",
            "preset": "portrait_stage",
            "callouts": [
                {"start": 1.0, "end": 4.0, "text": "第一条冲突花字", "slot": "stage_lower"},
                {"start": 2.0, "end": 5.0, "text": "第二条同槽花字", "slot": "stage_lower"},
            ],
        },
    )
    canvas = {
        "width": 1080, "height": 1920, "fps": 25.0,
        "storage_width": 1080, "storage_height": 1920,
        "rotation": 0, "sample_aspect_ratio": "1:1", "display_aspect_ratio": "9:16",
    }
    segs = [{"index": 0, "narration": "测试。", "spoken_text": "测试。", "actual_place_start": 0.5, "actual_place_end": 2.0}]
    studio = packaging.prepare_studio_packaging(tmp_path, canvas, 6.0, segs)
    assert "packaging_overlay_collision" in studio["qc"]["blocking_codes"]
    vqc = visual_render._build_visual_qc(segs, tmp_path, 6.0, canvas, packaging_qc=studio["qc"])
    assert vqc["blocking"] is True
    assert "packaging_overlay_collision" in vqc["blocking_codes"]


@pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg/ffprobe not available",
)
def test_end_to_end_portrait_stage_render_with_cards_progress_and_sfx(tmp_path, monkeypatch):
    monkeypatch.setitem(CONFIG, "burn_subtitles", False)
    work = tmp_path / "work"
    work.mkdir()
    src = tmp_path / "src_16x9.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=#2255AA:s=640x360:d=3.0:r=25",
            "-f", "lavfi", "-i", "sine=frequency=220:duration=3.0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(src),
        ],
        check=True, capture_output=True,
    )
    wav = work / "narr_000.wav"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1.1",
            "-ar", "44100", "-ac", "1", str(wav),
        ],
        check=True, capture_output=True,
    )
    (work / "original_subtitles.json").write_text(
        json.dumps([{"start": 1.8, "end": 2.8, "text": "保留原声对白"}], ensure_ascii=False),
        encoding="utf-8",
    )
    _write_plan(
        work,
        {
            "schema": "video-recap.packaging_plan.v1",
            "preset": "portrait_stage",
            "canvas": {"width": 360, "height": 640},
            "backdrop_mode": "blurred_frame",
            "progress_bar": True,
            "header": {
                "kicker": "精编连剪",
                "title": "接盘七万烂摊子\n靠系统逆风翻盘",
                "highlights": ["七万烂摊子", "逆风翻盘"],
            },
            "cards": [
                {"kind": "name_tag", "start": 0.2, "end": 1.6, "name": "李阳", "role": "烧烤店主"},
                {"kind": "data_card", "start": 0.2, "end": 1.6, "value": "70,000", "label": "网贷囤啤"},
            ],
            "callouts": [
                {"start": 0.3, "end": 1.6, "text": "开局天崩接盘", "style": "gold"},
            ],
            "sfx": [
                {"preset": "whoosh_soft", "start": 0.05, "gain": 0.25},
                # Overlaps kept dialogue [1.8..2.8] by >0.25s -> automatically skipped by guard!
                {"preset": "impact_warm", "start": 2.10, "gain": 0.30},
            ],
        },
    )
    out = work / "output.mp4"
    seg = tts_segment(
        index=0, start=0.2, end=1.4, narration="测试解说。",
        audio_path=str(wav), audio_duration=1.1, pause_after_ms=150, overlaps_speech=False,
    )
    assemble_video(src, [seg], work, out)

    probe = json.loads(
        subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", str(out)],
            check=True, capture_output=True, text=True,
        ).stdout
    )["streams"][0]
    assert (probe["width"], probe["height"]) == (360, 640)
    assert (work / "sfx_track.wav").is_file()
    sfx_meta = sfx_mix.sfx_settings(work)
    assert sfx_meta["present"] is True
    timeline = json.loads((work / "timeline.json").read_text(encoding="utf-8"))
    assert timeline["canvas"]["width"] == 360 and timeline["canvas"]["height"] == 640
    img_track = next(t for t in timeline["tracks"] if t["kind"] == "image")
    assert len(img_track["segments"]) >= 4


def test_cjk_compound_wrap_multi_chunk_dialogue_style_and_sfx_peak_offset(tmp_path):
    from subtitles.core import _hard_wrap_clause

    wrapped = _hard_wrap_clause("把烧烤店开在高速服务区还主打卖啤酒", 16)
    assert wrapped == ["把烧烤店开在高速", "服务区还主打卖啤酒"]

    _write_plan(
        tmp_path,
        {
            "schema": "video-recap.packaging_plan.v1",
            "preset": "portrait_stage",
            "sfx": [
                {"preset": "whoosh_soft", "start": 1.00, "gain": 0.25, "peak_offset_ms": 150},
            ],
        },
    )
    (tmp_path / "original_subtitles.json").write_text(
        json.dumps(
            [{"start": 2.5, "end": 5.5, "text": "你今天必须把话说清楚。行，分手吧！"}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    canvas = {
        "width": 1080, "height": 1920, "fps": 25.0,
        "storage_width": 1080, "storage_height": 1920,
        "rotation": 0, "sample_aspect_ratio": "1:1", "display_aspect_ratio": "9:16",
    }
    segs = [
        {"index": 0, "narration": "开场解说。", "spoken_text": "开场解说。", "actual_place_start": 0.3, "actual_place_end": 1.8}
    ]
    ass_text = subtitle_render._generate_ass(segs, tmp_path, 6.0, canvas).read_text(encoding="utf-8")
    dlg_lines = [ln for ln in ass_text.splitlines() if ",Dialogue,," in ln]
    # Both chunks of the multi-sentence original dialogue must use Style: Dialogue (never narrator Default)
    assert len(dlg_lines) >= 2
    assert any("「你今天必须把话说清楚" in ln for ln in dlg_lines)
    assert any("分手吧」" in ln for ln in dlg_lines)
    assert not any("Default,," in ln and "分手吧」" in ln for ln in ass_text.splitlines())

    sfx_wav, applied = sfx_mix.build_sfx_track(tmp_path, 6.0, segs)
    assert sfx_wav is not None and len(applied) == 1
    # Placed 150ms early so the whoosh transient peak lands right on 1.00s
    assert applied[0]["start"] == pytest.approx(0.85, abs=0.01)
    assert applied[0]["peak_offset_ms"] == 150.0
