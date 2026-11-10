"""Which transcript and which rows decide source-speech ownership in video-script."""

import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "skills" / "video-recap" / "scripts")
)
import json

from speech_ownership import _dialogue_speech_spans, load_source_sentence_evidence


def _write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


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
