"""Regression tests for narration punctuation + sentence-truncation fixes."""
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "skills" / "video-script" / "scripts")
)
from agent_text import _text_char_count, _truncate_at_sentence
from narration_lint import _validate_narration_budget

_PAUSES = "，：、；,—"


def _narrate(text, start=0.0, end=10.0):
    """Run one segment through the budget validator and return its final narration."""
    out = _validate_narration_budget(
        [{"start": start, "end": end, "narration": text}], None
    )
    assert len(out) == 1
    return out[0]["narration"]


# ── BUG 9: a trailing pause mark is replaced by the terminal, never followed by it ──


@pytest.mark.parametrize(
    "tail, expected_tail",
    [(tail, "。") for tail in _PAUSES]  # pause -> replaced by 。
    + [(tail, tail) for tail in "。！？!?…"],  # already terminal (incl. …) -> untouched
)
def test_trailing_punctuation_is_normalized_without_doubling(tail, expected_tail):
    result = _narrate(f"他停在门口{tail}")
    assert result.endswith(f"门口{expected_tail}"), (tail, result)
    assert result[-2] not in _PAUSES  # no "<pause><terminal>" such as "，。"


# ── BUG 10: truncation keeps the LAST fitting sentence boundary ──


@pytest.mark.parametrize(
    "text, budget, expected",
    [
        pytest.param(
            "他冲了进去。所有人都惊了！" + "尾巴" * 200,
            20,
            "他冲了进去。所有人都惊了！",
            id="keeps_all_fitting_sentences",
        ),
        pytest.param(
            "他走进门，环顾四周，握紧了拳头" + "尾巴" * 200,
            12,
            "他走进门，环顾四周。",
            id="falls_back_to_last_fitting_pause",
        ),
        pytest.param("他走进了那扇门。", 100, "他走进了那扇门。", id="noop_within_budget"),
    ],
)
def test_truncate_at_sentence_cuts_at_last_fitting_boundary(text, budget, expected):
    assert _truncate_at_sentence(text, budget) == expected


# ── BLOCK-TRUNCATION BUG: full-mode scene clamp must not chop a block that spans a cut ──

_TWO_SCENES = [
    {"scene_id": 0, "start": 0.0, "end": 10.0},
    {"scene_id": 1, "start": 10.0, "end": 30.0},
]


def test_multi_sentence_block_spanning_scene_cut_keeps_full_text():
    # An authored 3-sentence BLOCK spans the cut at 10s (midpoint 10.0 is on the boundary,
    # _find_scene_for_midpoint resolves it to a single scene). Before the fix the window was
    # clamped to that one scene and _truncate_at_sentence dropped trailing sentences.
    # ~50 chars: overflows the clamped single-scene window [2,10] (budget ~30) but fits the
    # authored window [2,18] (budget ~61). Old code clamped to 10s and truncated the tail.
    block = "他猛地推开那扇沉重的木门冲进房间，屋里却空无一人。桌上的台灯还亮着，茶杯里的水汽未散。窗帘在夜风里轻轻晃动着。"
    out = _validate_narration_budget(
        [{"start": 2.0, "end": 18.0, "narration": block}], _TWO_SCENES
    )
    assert len(out) == 1
    # Full authored text survives (no trailing-sentence truncation).
    assert _text_char_count(out[0]["narration"]) == _text_char_count(block)
    assert "窗帘在夜风里轻轻晃动着" in out[0]["narration"]
    # Author timing is preserved because clamping would have forced truncation.
    assert out[0]["end"] == 18.0


def test_single_sentence_beat_preserves_agent_authored_timing_across_scene():
    # Scene detection is approximate and must not silently move audio-safe timing.
    # The lint reports the crossing; the validator preserves the Agent's exact window.
    out = _validate_narration_budget(
        [{"start": 2.0, "end": 12.0, "narration": "他点了点头。"}], _TWO_SCENES
    )
    assert len(out) == 1
    assert out[0]["start"] == 2.0
    assert out[0]["end"] == 12.0
    assert out[0]["narration"].startswith("他点了点头")
