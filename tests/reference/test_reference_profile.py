import pytest

from reference_profile import derive


def _derive(breakdown, measurements, asr_segments, status="AVAILABLE_COARSE"):
    return derive(breakdown["labels"], measurements, asr_segments, status)


def test_by_owner_counts_share_blocks_and_in_span_cut_density(breakdown, measurements, asr_segments):
    by_owner = _derive(breakdown, measurements, asr_segments)["by_owner"]

    assert by_owner["narration"] == {
        "seconds": 30.0, "share": 0.5, "blocks": 2, "block_median_s": 15.0,
        # cuts strictly inside narration spans: 5.0, 20.2, 30.0, 31.0 over half a minute
        "cuts_per_min": 8.0, "mean_short_term_lufs": -18.7,
    }
    assert by_owner["original_dialogue"]["share"] == pytest.approx(1 / 3, abs=1e-3)
    assert by_owner["original_dialogue"]["cuts_per_min"] == 6.0
    assert by_owner["music"]["cuts_per_min"] == 0.0
    assert "silence" not in by_owner


def test_switch_on_cut_share_counts_owner_switches_near_picture_cuts(breakdown, measurements, asr_segments):
    # switches at 10, 20, 40, 50; cuts at 10.0, 20.2 (within 0.25 s), 50.0 — 40.4 misses
    assert _derive(breakdown, measurements, asr_segments)["switch_on_cut_share"] == 0.75


def test_narration_rate_uses_only_windows_mostly_covered_by_narration(breakdown, measurements, asr_segments):
    rate = _derive(breakdown, measurements, asr_segments)["narration_chars_per_s"]

    # windows 0,5,20,25,30,35 are narration: 11 + 5 * 8 chars over 30 s
    assert rate == {"value": 1.7, "windows_used": 6, "window_s": 5.0, "precision": "coarse_asr_windows"}


@pytest.mark.parametrize("status", ["FAILED_PROVIDER", "EXPLICITLY_SKIPPED"])
def test_narration_rate_is_null_when_asr_is_unusable(breakdown, measurements, asr_segments, status):
    assert _derive(breakdown, measurements, asr_segments, status)["narration_chars_per_s"]["value"] is None


def test_structure_positions_sections_as_fractions_with_lead_owner(breakdown, measurements, asr_segments):
    derived = _derive(breakdown, measurements, asr_segments)

    assert derived["structure"][0] == {"function": "hook", "at": [0.0, 0.1], "lead_owner": "narration"}
    assert derived["structure"][1]["lead_owner"] == "narration"  # 14 s narration vs 10 s dialogue
    assert derived["structure"][-1] == {"function": "payoff", "at": [0.833, 1.0], "lead_owner": "music"}
    assert derived["by_section"]["hook"] == {"seconds": 6.0, "cuts_per_min": 10.0, "narration_share": 1.0}
    assert derived["first_original_at"] == {"s": 10.0, "fraction": 0.167}
    assert derived["narration_jobs"] == {"context": 0.333, "causal_link": 0.667}
