import sys
from pathlib import Path


sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "skills" / "video-script" / "scripts")
)

import review_response


def test_creative_case_rules_enter_review_prompt_without_schema_drift():
    expected_categories = [
        "hallucination",
        "weak_hook",
        "no_throughline",
        "narrating_picture",
        "density",
        "pacing",
        "cliche",
        "incomplete",
        "disjoint_handoff",
        "promise_mismatch",
        "low_information_gain",
        "not_write_for_ear",
        "grounding_risk",
        "original_audio_conflict",
        "subtitle_readability",
        "ai_flavor",
        "weak_payoff",
        "style_mismatch",
        "packaging_mismatch",
        "example_entity_leak",
        "other",
    ]
    messages = review_response.build_review_messages(
        [{"start": 0, "end": 2, "narration": "他接受挑战。"}], [], []
    )

    assert review_response.CATEGORIES == expected_categories
    assert len(messages) == 1 and messages[0]["role"] == "user"
    assert set(messages[0]) == {"role", "content"}

    content = messages[0]["content"]
    # Category names are the stable contract; prose fragments are only pinned where the
    # rule would silently disappear if the wording were dropped.
    # `not_write_for_ear`/`subtitle_readability`/`other` are accepted finding categories the
    # rubric never prescribes (subtitle_readability was only named by the removed scorecard).
    unprescribed = {"not_write_for_ear", "subtitle_readability", "other"}
    prescribed = [c for c in expected_categories if c not in unprescribed]
    assert all(category in content for category in prescribed)
    required_rules = ["已呈现的结果", "反打", "具体局部修法"]
    assert all(rule in content for rule in required_rules)

