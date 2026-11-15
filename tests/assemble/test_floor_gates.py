"""Deterministic floor-quality gates in audio_mix and assembly_contract."""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "video-recap" / "scripts"))

import assembly_contract  # noqa: E402
import audio_mix  # noqa: E402
from lib import CONFIG  # noqa: E402
from tts_fixtures import tts_segment  # noqa: E402


def test_audio_mix_tail_fade_applies_ending_afade_to_orig_and_bgm(monkeypatch):
    monkeypatch.setitem(CONFIG, "ending_audio_fade_ms", 350.0)
    seg = {
        "index": 0,
        "actual_place_start": 0.5,
        "actual_place_end": 4.0,
        "overlaps_speech": False,
    }
    graph = audio_mix._build_audio_filter_complex([seg], has_bgm=True, video_duration=10.0)
    assert "afade=t=out:st=9.650:d=0.350[orig]" in graph
    assert "afade=t=out:st=9.650:d=0.350[bgm]" in graph

    monkeypatch.setitem(CONFIG, "ending_audio_fade_ms", 0.0)
    no_fade = audio_mix._build_audio_filter_complex([seg], has_bgm=True, video_duration=10.0)
    assert "afade=t=out" not in no_fade


def test_bounded_release_does_not_block_while_unsafe_entry_propagates_to_assembly_qc(
    monkeypatch, tmp_path
):
    (tmp_path / "speech_boundary_anchors.json").write_text(
        json.dumps(
            {
                "sentence_anchors": [
                    {"time": 1.0, "pause_start": 0.8, "confidence": "high"},
                    {"time": 12.0, "pause_start": 11.8, "confidence": "high"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "asr_result.json").write_text(
        json.dumps([{"start": 1.0, "end": 12.0, "text": "你以为把证据烧了就能全身而退吗"}], ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setitem(CONFIG, "duck_fade_seconds", 0.3)

    # Releasing at 4.0s (> 3.0s before 12.0s anchor) uses smooth bounded_release without blocking
    release_seg = tts_segment(
        index=0,
        start=1.0,
        end=4.0,
        actual_place_start=1.0,
        actual_place_end=4.0,
        placed_audio_duration=3.0,
        overlaps_speech=True,
    )
    safe_handoffs = audio_mix._apply_source_sentence_handoffs([release_seg], tmp_path, 15.0)
    assert safe_handoffs[0]["status"] == "bounded_release"
    assert release_seg.get("source_handoff_blocking") is not True

    # Entering mid-sentence at 4.0s (away from 1.0s and 12.0s anchors) blocks assembly QC!
    seg = tts_segment(
        index=0,
        start=4.0,
        end=7.0,
        actual_place_start=4.0,
        actual_place_end=7.0,
        placed_audio_duration=0.0,
        overlaps_speech=True,
    )
    handoffs = audio_mix._apply_source_sentence_handoffs([seg], tmp_path, 15.0)
    assert seg["source_entry_status"] == "unsafe_entry"
    assert seg["source_handoff_blocking"] is True

    qc = assembly_contract._build_assembly_qc(
        [seg],
        15.0,
        audio_operations={"source_sentence_handoffs": handoffs},
        render_delivery={
            "video_encode_passes": 1,
            "reencode_reason": ["burn_subtitles"],
            "audio_sample_rate": 48000,
            "final_compat_notes": ["faststart"],
        },
    )
    assert qc["blocking"] is True
    assert "unsafe_source_handoff" in qc["blocking_codes"]


def test_source_handoff_does_not_swallow_fresh_short_line_after_measured_pause(
    monkeypatch, tmp_path
):
    # Narration ends at 93.29s inside an acoustic pause [90.73, 93.69], right before a short
    # line ("救我！", 93.69–94.76s) whose anchor is at 95.06s (<= 3.0s away).
    (tmp_path / "speech_boundary_anchors.json").write_text(
        json.dumps(
            {
                "sentence_anchors": [
                    {
                        "time": 90.86,
                        "pause_start": 90.73,
                        "confidence": "low",
                        "boundary_use": "unverified",
                    },
                    {
                        "time": 95.06,
                        "pause_start": 94.76,
                        "confidence": "low",
                        "boundary_use": "unverified",
                    },
                ],
                "acoustic_pauses": [
                    {"start": 90.73, "end": 93.69},
                    {"start": 94.76, "end": 95.06},
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "asr_result.json").write_text(
        json.dumps([{"start": 85.0, "end": 100.0, "text": "唤醒了魔导士。救我！"}], ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setitem(CONFIG, "duck_fade_seconds", 0.3)

    seg = tts_segment(
        index=0,
        start=87.55,
        end=93.29,
        actual_place_start=87.55,
        actual_place_end=93.29,
        placed_audio_duration=0.0,
        overlaps_speech=True,
    )
    handoffs = audio_mix._apply_source_sentence_handoffs([seg], tmp_path, 110.0)
    assert handoffs[0]["status"] == "bounded_release"
    assert seg["source_duck_end"] == 93.29
    assert seg["source_restore_at"] == 93.59

