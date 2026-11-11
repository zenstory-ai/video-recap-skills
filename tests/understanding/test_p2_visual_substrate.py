import sys
from pathlib import Path

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parents[2]
        / "skills"
        / "video-recap"
        / "scripts"
    ),
)
"""Regression tests for the P2 visual-substrate changes.

Q9: per-scene VLM frame sampling scales with scene length (~1 frame / vlm_seconds_per_frame,
floor 3, capped by vlm_max_frames) instead of the old hard cap of 6 that starved long scenes.
"""
from vlm import _max_frames_for_duration


def test_frame_count_scales_with_duration_and_caps():
    # short scene -> floor of 3 (old behaviour kept the floor)
    assert _max_frames_for_duration(4) == 3
    assert _max_frames_for_duration(8) == 3
    # mid scene -> ~1 frame / 4s (the old code hard-capped this at 6)
    assert _max_frames_for_duration(40) == 10
    assert _max_frames_for_duration(60) == 15
    # long/merged scene -> capped at vlm_max_frames (16), NOT the old 6 -> ~1 frame/20s problem gone
    assert _max_frames_for_duration(122) == 16
    assert _max_frames_for_duration(600) == 16
