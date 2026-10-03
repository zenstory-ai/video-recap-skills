"""Brief labels the reader acts on: 1-based scene numbers and no source-clock text inside
cut pass-2 OUTPUT sections."""

import json
import sys
from pathlib import Path


sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[2] / "skills" / "video-understanding" / "scripts"),
)

from lib import CONFIG  # noqa: E402
from briefing.builder import build_agent_brief  # noqa: E402
from briefing.timeline import (  # noqa: E402
    _remap_asr_to_output_for_brief,
    _remap_prose_times_for_brief,
)


def _write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _brief(monkeypatch, work_dir, scenes, asr, silence, duration, edit_mode="full"):
    monkeypatch.setitem(CONFIG, "edit_mode", edit_mode)
    monkeypatch.setitem(CONFIG, "target_duration", "")
    monkeypatch.setitem(CONFIG, "context_info", "")
    return build_agent_brief(scenes, asr, silence, duration, work_dir).read_text(
        encoding="utf-8"
    )


def test_brief_numbers_every_scene_from_one(monkeypatch, tmp_path):
    """Fusion, ASR chunk and the timing guide name the same scene the same way; the JSON
    sidecars keep the 0-based scene_id."""
    scenes = [
        {"scene_id": 0, "start": 0.0, "end": 10.0, "description": "门口对峙"},
        {"scene_id": 1, "start": 10.0, "end": 20.0, "description": "屋内争吵"},
    ]
    asr = [{"start": 12.0, "end": 18.0, "text": "你到底想怎么样。"}]
    text = _brief(monkeypatch, tmp_path, scenes, asr, [], 20.0)

    assert "### Fusion scene 1: 0.0-10.0s" in text
    assert "### Fusion scene 2: 10.0-20.0s" in text
    assert "### Fusion scene 0" not in text
    assert "| scenes 2 |" in text
    assert "### Scene 1: 0.0-10.0s" in text and "### Scene 2: 10.0-20.0s" in text
    fusion = json.loads((tmp_path / "timeline_fusion.json").read_text(encoding="utf-8"))
    chunks = json.loads((tmp_path / "asr_writing_chunks.json").read_text(encoding="utf-8"))
    assert [item["scene_id"] for item in fusion] == [0, 1]
    assert chunks[0]["scene_ids"] == [1]


def _cut_pass2(tmp_path, clips):
    (tmp_path / "edited_source.mp4").write_bytes(b"edited")
    _write_json(tmp_path / "clip_plan_validated.json", {"clips": clips})


def test_cut_pass2_output_sections_quote_no_source_time(monkeypatch, tmp_path):
    """VLM prose times move to the OUTPUT clock or are marked cut away, a coarse ASR window
    the cut keeps only part of shows no text, and split scenes number from one."""
    _cut_pass2(
        tmp_path,
        [
            {"source_start": 10.0, "source_end": 20.0, "output_start": 0.0, "output_end": 10.0},
            {"source_start": 40.0, "source_end": 50.0, "output_start": 10.0, "output_end": 20.0},
        ],
    )
    scenes = [
        {
            "scene_id": 0,
            "start": 10.0,
            "end": 50.0,
            "description": "两人对峙",
            "depth_analysis": "12.0s 他抬头试探，00:30 她转身离开；他停顿了3秒才开口。",
        }
    ]
    asr = [
        {"start": 11.0, "end": 13.0, "text": "保留的对白。"},
        {"start": 15.0, "end": 30.0, "text": "前半句在片段里。后半句已经剪掉。"},
    ]
    text = _brief(monkeypatch, tmp_path, scenes, asr, [], 60.0, edit_mode="cut")

    part1 = text.split("source scene 1 part 1)")[1].split("### OUTPUT")[0]
    assert "- Deeper analysis: 2.0s 他抬头试探，[cut-away moment] 她转身离开；他停顿了3秒才开口。" in part1
    assert "12.0s 他抬头" not in text and "00:30" not in text
    assert "### Fusion scene 1 part 1: 0.0-10.0s" in text
    assert "### Fusion scene 1 part 2: 10.0-20.0s" in text
    assert "[1.0-3.0] 保留的对白。" in part1
    assert "[5.0-10.0] [partial ASR window: only part of it is in the cut, text withheld]" in part1
    chunks = json.loads((tmp_path / "asr_writing_chunks.json").read_text(encoding="utf-8"))
    assert "[partial ASR window: only part of it is in the cut, text withheld]" in [
        piece["text"] for chunk in chunks for piece in chunk["segments"]
    ]
    for name in ("asr_writing_chunks.json", "timeline_fusion.json"):
        written = (tmp_path / name).read_text(encoding="utf-8")
        assert "已经剪掉" not in written and "前半句" not in written
    assert "已经剪掉" not in text and "前半句" not in text


def test_partial_window_rule_keeps_text_covered_across_a_seam():
    """A window the kept clips cover completely keeps its words even when a seam splits it."""
    spans = [
        {"source_start": 10.0, "source_end": 20.0, "output_start": 0.0, "output_end": 10.0},
        {"source_start": 20.0, "source_end": 30.0, "output_start": 10.0, "output_end": 20.0},
    ]
    rows = _remap_asr_to_output_for_brief(
        [{"start": 15.0, "end": 25.0, "text": "整句都在。"}, {"start": 28.0, "end": 31.0, "text": ""}],
        spans,
    )
    assert [(row["start"], row["end"], row["text"]) for row in rows] == [
        (5.0, 10.0, "整句都在。"),
        (10.0, 15.0, "整句都在。"),
        (18.0, 20.0, ""),
    ]


def test_prose_times_outside_the_scene_are_left_alone():
    scene = {"start": 100.0, "end": 110.0}
    overlap = {"source_start": 100.0, "source_end": 105.0, "output_start": 0.0, "output_end": 5.0}
    assert (
        _remap_prose_times_for_brief("1:42 他回头，107.5秒 再出手，墙上时钟 12:30，等了5s", scene, overlap)
        == "2.0s 他回头，[cut-away moment] 再出手，墙上时钟 12:30，等了5s"
    )
