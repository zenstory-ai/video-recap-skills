"""Regression tests for asr.py name-glossary correction (Q8).

ASR mishears character names as homophones (e.g. 叶青眉 → 叶轻眉). After transcription
we correct any single-character substitution of a known name from background_research.json.
The rule is tight (exactly one differing char at one position vs. a length-matched name)
to avoid over-correcting unrelated words.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'skills' / 'video-understanding' / 'scripts'))

import asr  # noqa: E402
from asr import (  # noqa: E402
    _apply_glossary_corrections,
    _correct_text_with_glossary,
    _load_name_glossary,
)


def _write_research(work_dir, characters=None, character_details=None):
    payload = {}
    if characters is not None:
        payload["characters"] = characters
    if character_details is not None:
        payload["character_details"] = character_details
    (Path(work_dir) / "background_research.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def test_strip_reasoning_residue_removes_think_leakage():
    """Full-mode ASR must strip leaked MiMo <think> reasoning residue too (parity with
    video-voiceover/scripts/dub.py); clean transcript text stays untouched."""
    assert asr._strip_reasoning_residue("think>\n 真相比逃跑更狼。").strip() == "真相比逃跑更狼。"
    assert asr._strip_reasoning_residue("<think>x\n</think>\n开门的却是个陌生男人").strip() == "开门的却是个陌生男人"
    assert asr._strip_reasoning_residue("<think>未闭合 后续").strip() == ""
    assert asr._strip_reasoning_residue("普通转写文本。") == "普通转写文本。"


def test_corrects_homophone_substitution_of_known_name(tmp_path):
    _write_research(tmp_path, characters={"叶轻眉": "主角之母"})
    segments = [{"start": 0.0, "end": 3.0, "text": "她叫叶青眉"}]
    _apply_glossary_corrections(segments, tmp_path)
    assert segments[0]["text"] == "她叫叶轻眉"


def test_glossary_includes_character_details_keys_and_aliases(tmp_path):
    _write_research(
        tmp_path,
        character_details={"范闲": {"aliases": ["小范闲"]}},
    )
    names = _load_name_glossary(tmp_path)
    assert "范闲" in names and "小范闲" in names


@pytest.mark.parametrize(
    "text, expected",
    [
        ("这是范闹", "这是范闲"),  # single-char substitution -> corrected
        ("他是王富贵", "他是王富贵"),  # three chars away from any name -> untouched
        ("她叫叶轻眉", "她叫叶轻眉"),  # exact name -> not rewritten or duplicated
    ],
)
def test_correct_text_with_glossary_only_fixes_one_char_substitutions(text, expected):
    assert _correct_text_with_glossary(text, ["叶轻眉", "范闲"]) == expected


def test_one_char_diff_helper_is_strict():
    assert asr._one_char_diff("叶青眉", "叶轻眉") is True
    assert asr._one_char_diff("叶轻眉", "叶轻眉") is False   # zero diff
    assert asr._one_char_diff("王富贵", "叶轻眉") is False   # three diffs
    assert asr._one_char_diff("叶轻", "叶轻眉") is False     # length mismatch


@pytest.mark.parametrize("characters", [None, {}], ids=["absent_research", "no_names"])
def test_missing_or_empty_glossary_is_noop(tmp_path, characters):
    if characters is not None:
        _write_research(tmp_path, characters=characters)
    segments = [{"start": 0.0, "end": 3.0, "text": "她叫叶青眉"}]
    _apply_glossary_corrections(segments, tmp_path)
    assert segments[0]["text"] == "她叫叶青眉"
    assert _load_name_glossary(tmp_path) == []
