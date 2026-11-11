"""Agent-calibrated original-dialogue subtitles (`original_subtitles.json`) in narration gaps."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "video-recap" / "scripts"))

import assembly_settings  # noqa: E402
from lib import CONFIG  # noqa: E402
import source_subtitles  # noqa: E402
import subtitles.core as subtitle_core  # noqa: E402
import subtitles.render as subtitle_render  # noqa: E402


def _burn_on(monkeypatch):
    monkeypatch.setitem(CONFIG, "burn_subtitles", True)
    monkeypatch.setitem(CONFIG, "mask_source_subtitles", True)
    monkeypatch.setitem(CONFIG, "source_subtitle_mask_timing", "all")
    monkeypatch.setitem(CONFIG, "subtitle_mask_opacity", 1.0)
    monkeypatch.setitem(CONFIG, "subtitle_original_in_gaps", True)


def test_narration_gap_windows_complement():
    segs = [
        {"actual_place_start": 2.0, "actual_place_end": 5.0},
        {"actual_place_start": 8.0, "actual_place_end": 10.0},
    ]
    gaps = source_subtitles._narration_gap_windows(segs, 14.0, min_gap=0.8)
    assert (0.0, 2.0) in gaps and (5.0, 8.0) in gaps and (10.0, 14.0) in gaps


def test_original_gap_entries_gated_off(monkeypatch, tmp_path):
    (tmp_path / "original_subtitles.json").write_text(
        json.dumps([{"start": 1.0, "end": 4.0, "text": "原声台词"}]), encoding="utf-8"
    )
    # burn off (and not degraded)
    monkeypatch.setitem(CONFIG, "burn_subtitles", False)
    monkeypatch.setitem(CONFIG, "burn_subtitles_degraded", None)
    monkeypatch.setitem(CONFIG, "mask_source_subtitles", True)
    monkeypatch.setitem(CONFIG, "subtitle_original_in_gaps", True)
    assert source_subtitles._original_gap_subtitle_entries([], tmp_path, 10.0) == []
    # explicit override off
    monkeypatch.setitem(CONFIG, "burn_subtitles", True)
    monkeypatch.setitem(CONFIG, "subtitle_original_in_gaps", False)
    assert source_subtitles._original_gap_subtitle_entries([], tmp_path, 10.0) == []


def test_original_gap_entries_work_without_mask_on_clean_source(monkeypatch, tmp_path):
    """Agent-calibrated original_subtitles.json populates gaps even when mask_source_subtitles=False
    (clean/unsubtitled source). Without original_subtitles.json, nothing is emitted."""
    monkeypatch.setitem(CONFIG, "burn_subtitles", True)
    monkeypatch.setitem(CONFIG, "mask_source_subtitles", False)
    monkeypatch.setitem(CONFIG, "subtitle_original_in_gaps", True)
    segs = [{"actual_place_start": 5.0, "actual_place_end": 8.0, "narration": "解说"}]

    assert source_subtitles._original_gap_subtitle_entries(segs, tmp_path, 10.0) == []

    (tmp_path / "original_subtitles.json").write_text(
        json.dumps([{"start": 1.0, "end": 4.0, "text": "原声台词"}]), encoding="utf-8"
    )
    entries = source_subtitles._original_gap_subtitle_entries(segs, tmp_path, 10.0)
    assert entries and all(e["text"] for e in entries)
    assert all(1.0 <= e["start"] and e["end"] <= 4.0 + 1e-6 for e in entries)


def test_coarse_asr_result_is_not_guessed_into_gap_subtitles(monkeypatch, tmp_path):
    """Coarse asr_result.json must never be guessed or sliced into gap subtitles without
    agent-verified original_subtitles.json."""
    _burn_on(monkeypatch)
    (tmp_path / "asr_result.json").write_text(
        json.dumps([{"start": 1.0, "end": 4.0, "text": "未经校验的粗粒度转写"}]), encoding="utf-8"
    )
    segs = [{"actual_place_start": 5.0, "actual_place_end": 8.0, "narration": "解说"}]
    assert source_subtitles._original_gap_subtitle_entries(segs, tmp_path, 10.0) == []


def test_original_gap_entries_suppressed_when_mask_only_follows_narration(monkeypatch, tmp_path):
    _burn_on(monkeypatch)
    monkeypatch.setitem(CONFIG, "source_subtitle_mask_timing", "narration")
    (tmp_path / "original_subtitles.json").write_text(
        json.dumps([{"start": 1.0, "end": 4.0, "text": "原声已自带硬字幕"}]), encoding="utf-8"
    )
    segs = [{"actual_place_start": 5.0, "actual_place_end": 8.0, "narration": "解说"}]
    assert source_subtitles._original_gap_subtitle_entries(segs, tmp_path, 10.0) == []


@pytest.mark.parametrize("opacity", [0.0, 0.6], ids=("transparent", "translucent"))
def test_original_gap_entries_suppressed_when_source_subtitles_show_through_mask(
    monkeypatch, tmp_path, opacity
):
    _burn_on(monkeypatch)
    monkeypatch.setitem(CONFIG, "subtitle_mask_opacity", opacity)
    (tmp_path / "original_subtitles.json").write_text(
        json.dumps([{"start": 1.0, "end": 4.0, "text": "原声仍透过遮罩可见"}]),
        encoding="utf-8",
    )
    assert source_subtitles._original_gap_subtitle_entries([], tmp_path, 10.0) == []


def test_combined_entries_sorted_no_overlap_with_narration(monkeypatch, tmp_path):
    _burn_on(monkeypatch)
    (tmp_path / "original_subtitles.json").write_text(
        json.dumps([{"start": 1.0, "end": 4.0, "text": "原声"}]), encoding="utf-8"
    )
    segs = [
        {
            "actual_place_start": 5.0,
            "actual_place_end": 8.0,
            "narration": "解说词内容",
            "spoken_text": "解说词内容",
            "start": 5.0,
            "end": 8.0,
        }
    ]
    combined = source_subtitles._combined_subtitle_entries(segs, tmp_path, 10.0)
    assert combined[0]["start"] < 5.0
    for e in combined:
        if e["start"] < 5.0:
            assert e["end"] <= 5.0 + 1e-6


def test_generate_ass_includes_original_gap_subtitles(monkeypatch, tmp_path):
    _burn_on(monkeypatch)
    (tmp_path / "original_subtitles.json").write_text(
        json.dumps([{"start": 1.0, "end": 4.0, "text": "原声台词"}]), encoding="utf-8"
    )
    segs = [
        {
            "actual_place_start": 5.0,
            "actual_place_end": 8.0,
            "narration": "解说",
            "spoken_text": "解说",
            "start": 5.0,
            "end": 8.0,
        }
    ]
    subtitle_render._generate_ass(segs, tmp_path, 10.0, {"width": 1280, "height": 720})
    ass = (tmp_path / "subtitles.ass").read_text(encoding="utf-8")
    assert "原声台词" in ass and "解说" in ass


def test_original_gap_entries_strip_terminal_punctuation_inside_brackets(monkeypatch, tmp_path):
    _burn_on(monkeypatch)
    (tmp_path / "original_subtitles.json").write_text(
        json.dumps([{"start": 1.0, "end": 4.0, "text": "原声台词。"}]), encoding="utf-8"
    )
    segs = [{"actual_place_start": 5.0, "actual_place_end": 8.0, "narration": "解说"}]
    entries = source_subtitles._original_gap_subtitle_entries(segs, tmp_path, 10.0)
    joined = "".join(e["text"] for e in entries)
    assert joined == "「原声台词」"
    assert "原声台词。" not in joined


def test_original_gap_entries_keep_closing_quote_with_stripped_terminal_punctuation(
    monkeypatch, tmp_path
):
    _burn_on(monkeypatch)
    (tmp_path / "original_subtitles.json").write_text(
        json.dumps([{"start": 1.0, "end": 4.0, "text": "他说：「你好。」"}]), encoding="utf-8"
    )
    segs = [{"actual_place_start": 5.0, "actual_place_end": 8.0, "narration": "解说"}]
    entries = source_subtitles._original_gap_subtitle_entries(segs, tmp_path, 10.0)
    texts = [e["text"] for e in entries]
    assert "」" not in texts
    assert "".join(texts) == "「他说：「你好」」"


def test_calibrated_line_not_subject_to_density_guard(monkeypatch, tmp_path):
    _burn_on(monkeypatch)
    dense = "我既然回来了京都就是最安全的小姐遇害你和你的黑骑为什么不在京都"
    (tmp_path / "original_subtitles.json").write_text(
        json.dumps([{"start": 1.0, "end": 3.0, "text": dense}]), encoding="utf-8"
    )
    segs = [{"actual_place_start": 6.0, "actual_place_end": 9.0, "narration": "解说"}]
    assert source_subtitles._original_gap_subtitle_entries(segs, tmp_path, 10.0)


def test_straddling_calibrated_line_stays_whole_in_best_gap_without_mid_sentence_slicing(
    monkeypatch, tmp_path
):
    _burn_on(monkeypatch)
    (tmp_path / "original_subtitles.json").write_text(
        json.dumps([{"start": 4.5, "end": 10.0, "text": "完整保留的一句原声台词"}]),
        encoding="utf-8",
    )
    segs = [{"actual_place_start": 5.0, "actual_place_end": 7.0, "narration": "解说"}]
    entries = source_subtitles._original_gap_subtitle_entries(segs, tmp_path, 12.0)
    assert entries
    assert all(e["start"] >= 7.0 - 1e-6 and e["end"] <= 10.0 + 1e-6 for e in entries)
    assert "".join(e["text"] for e in entries) == "「完整保留的一句原声台词」"


def test_malformed_original_subtitles_raises(monkeypatch, tmp_path):
    _burn_on(monkeypatch)
    (tmp_path / "original_subtitles.json").write_text(
        json.dumps([{"start": 4.0, "end": 2.0, "text": "反了"}]), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="original_subtitles.json"):
        source_subtitles._original_gap_subtitle_entries([], tmp_path, 10.0)

    (tmp_path / "original_subtitles.json").write_text("[]", encoding="utf-8")
    assert source_subtitles._load_agent_original_subtitles(tmp_path) is None
    assert source_subtitles._load_agent_original_subtitles(None) is None


def test_settings_payload_original_subtitles_flag(tmp_path):
    assert assembly_settings.assembly_settings_payload(tmp_path)["original_subtitles"] is False
    (tmp_path / "original_subtitles.json").write_text("[]", encoding="utf-8")
    assert assembly_settings.assembly_settings_payload(tmp_path)["original_subtitles"] is True
    assert assembly_settings.assembly_settings_payload()["original_subtitles"] is False


def test_normalize_subtitle_text_collapses_em_dashes():
    assert subtitle_core._normalize_subtitle_text("他来了——然后走了") == "他来了，然后走了"
    assert subtitle_core._normalize_subtitle_text("等等—别走") == "等等，别走"
    assert subtitle_core._normalize_subtitle_text("一———二") == "一，二"
    assert subtitle_core._normalize_subtitle_text("已经，——好") == "已经，好"
    assert subtitle_core._normalize_subtitle_text("") == ""


def test_generated_srt_and_ass_normalize_em_dashes(monkeypatch, tmp_path):
    segs = [
        {
            "actual_place_start": 1.0,
            "actual_place_end": 4.0,
            "narration": "我回来了——这一次",
            "spoken_text": "我回来了——这一次",
            "start": 1.0,
            "end": 4.0,
        }
    ]
    subtitle_render._generate_srt(segs, tmp_path, 4.0)
    subtitle_render._generate_ass(segs, tmp_path, 4.0, {"width": 1280, "height": 720})
    srt = (tmp_path / "subtitles.srt").read_text(encoding="utf-8")
    ass = (tmp_path / "subtitles.ass").read_text(encoding="utf-8")
    assert "——" not in srt and "—" not in srt
    assert "——" not in ass and "—" not in ass
    assert "我回来了，这一次" in srt


def test_original_gap_text_normalizes_em_dashes(monkeypatch, tmp_path):
    _burn_on(monkeypatch)
    (tmp_path / "original_subtitles.json").write_text(
        json.dumps([{"start": 1.0, "end": 4.0, "text": "活着——让我看看"}]), encoding="utf-8"
    )
    segs = [
        {
            "actual_place_start": 5.0,
            "actual_place_end": 8.0,
            "narration": "解说",
            "spoken_text": "解说",
            "start": 5.0,
            "end": 8.0,
        }
    ]
    subtitle_render._generate_ass(segs, tmp_path, 10.0, {"width": 1280, "height": 720})
    ass = (tmp_path / "subtitles.ass").read_text(encoding="utf-8")
    assert "——" not in ass and "—" not in ass
    assert "活着，让我看看" in ass


def test_no_narration_and_no_dialogue_yields_no_subtitle_entries(tmp_path):
    assert source_subtitles._combined_subtitle_entries([], tmp_path, 6) == []
