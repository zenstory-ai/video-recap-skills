"""Which transcript and which rows decide source-speech ownership in video-script."""

import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "skills" / "video-script" / "scripts")
)
import json

from narration_lint import lint_narration
from speech_ownership import _dialogue_speech_spans, load_source_sentence_evidence

_UNVERIFIED_ANCHOR = {
    "time": 12.0, "pause_start": 11.8, "confidence": "low",
    "boundary_use": "unverified", "timing_bound_seconds": 9.0,
}


def _write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _entry_codes(work_dir, start):
    _write_json(
        work_dir / "speech_boundary_anchors.json",
        {"schema_version": 2, "sentence_anchors": [_UNVERIFIED_ANCHOR]},
    )
    report = lint_narration(
        [{"start": start, "end": start + 3.0, "narration": "入口测试。", "overlaps_speech": True}],
        mode="full",
        work_dir=work_dir,
    )
    return [item["code"] for item in report["errors"] if "source_sentence" in item["code"]]


def test_full_mode_lint_reads_asr_clean_like_cut_and_assemble(tmp_path):
    # Raw ASR heard only "啊！" in 15-30s; consolidation recovered a real line there.
    # video-cut and video-assemble read asr_clean.json, so the lint must reach the same
    # verdict instead of passing an entry assemble later blocks as unsafe_entry.
    _write_json(
        tmp_path / "asr_result.json",
        [{"start": 0.0, "end": 15.0, "text": "真实对白。"}, {"start": 15.0, "end": 30.0, "text": "啊！"}],
    )
    assert _entry_codes(tmp_path, 20.0) == []

    _write_json(
        tmp_path / "asr_clean.json",
        {"segments": [
            {"start": 0.0, "end": 15.0, "text": "真实对白。"},
            {"start": 15.0, "end": 30.0, "text": "救我！"},
        ]},
    )
    assert _entry_codes(tmp_path, 20.0) == ["interrupts_source_sentence"]


def test_full_mode_evidence_drops_blank_text_rows(tmp_path):
    _write_json(
        tmp_path / "asr_result.json",
        [
            {"start": 0.0, "end": 5.0, "text": "真实对白。"},
            {"start": 5.0, "end": 10.0, "text": "   "},
            {"start": 10.0, "end": 12.0, "text": ""},
        ],
    )
    evidence = load_source_sentence_evidence(tmp_path, mode="full")
    assert [(row["start"], row["end"]) for row in evidence["speech_spans"]] == [(0.0, 5.0)]
    assert evidence["dialogue_spans"] == [{"start": 0.0, "end": 5.0}]


def test_blank_text_rows_are_timing_only_and_guard_nothing():
    rows = [
        {"start": 0.0, "end": 15.0, "text": "真实对白。"},
        {"start": 15.0, "end": 30.0, "text": " "},
        {"start": 30.0, "end": 45.0, "text": ""},
    ]
    assert _dialogue_speech_spans(rows) == [{"start": 0.0, "end": 15.0}]
    # Filtering blank rows first (video-cut's caller) gives the same spans.
    assert _dialogue_speech_spans(rows[:1]) == _dialogue_speech_spans(rows)


def test_rows_without_text_field_stay_dialogue():
    # Measured timing with unknown words is not evidence that nobody is talking.
    assert _dialogue_speech_spans([{"start": 2.0, "end": 4.0}]) == [{"start": 2.0, "end": 4.0}]


def test_interjection_guard_still_applies_next_to_dialogue_after_blank_rows_are_skipped():
    rows = [
        {"start": 0.0, "end": 15.0, "text": "真实对白。"},
        {"start": 15.0, "end": 30.0, "text": "啊！"},
        {"start": 30.0, "end": 31.0, "text": ""},
    ]
    assert _dialogue_speech_spans(rows) == [{"start": 0.0, "end": 16.0}]
