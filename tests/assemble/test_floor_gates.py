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


def test_unsafe_mid_sentence_release_propagates_to_blocking_assembly_qc(monkeypatch, tmp_path):
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
    monkeypatch.setitem(CONFIG, "duck_fade_seconds", 0.3)

    # Without active dialogue at 4.0s, release is safe (bounded_release)
    quiet_seg = tts_segment(
        index=0,
        start=1.0,
        end=4.0,
        actual_place_start=1.0,
        actual_place_end=4.0,
        placed_audio_duration=3.0,
        overlaps_speech=True,
    )
    safe_handoffs = audio_mix._apply_source_sentence_handoffs([quiet_seg], tmp_path, 15.0)
    assert safe_handoffs[0]["status"] == "bounded_release"
    assert quiet_seg.get("source_handoff_blocking") is not True

    # With active source dialogue spanning [1.0, 12.0], releasing at 4.0s cuts into mid-sentence!
    (tmp_path / "asr_result.json").write_text(
        json.dumps([{"start": 1.0, "end": 12.0, "text": "你以为把证据烧了就能全身而退吗"}], ensure_ascii=False),
        encoding="utf-8",
    )
    seg = tts_segment(
        index=0,
        start=1.0,
        end=4.0,
        actual_place_start=1.0,
        actual_place_end=4.0,
        placed_audio_duration=0.0,
        overlaps_speech=True,
    )
    handoffs = audio_mix._apply_source_sentence_handoffs([seg], tmp_path, 15.0)
    assert handoffs[0]["status"] == "unsafe_mid_sentence_release"
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
