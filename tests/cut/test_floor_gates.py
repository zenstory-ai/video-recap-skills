"""Deterministic floor-quality gates in cut/shot-review/sentence-boundaries."""

from fractions import Fraction
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "video-recap" / "scripts"))

import cut_cli  # noqa: E402
from lib import CONFIG  # noqa: E402
import sentence_boundaries  # noqa: E402
import shot_review  # noqa: E402


def test_cut_floor_defaults():
    assert CONFIG["min_clip_duration"] == pytest.approx(0.8)
    assert CONFIG["scene_cut_detect_threshold"] == pytest.approx(0.32)
    assert CONFIG["flash_sliver_max_seconds"] == pytest.approx(0.20)
    assert CONFIG["clip_end_pause_pad"] == pytest.approx(0.30)


def test_shot_review_blocks_flash_slivers_under_0_20s_and_keeps_0_50s_advisory():
    # 30 fps clock over 3.0s (90 frames)
    pts = [Fraction(i, 30) for i in range(90)]
    end = Fraction(3, 1)

    # Cut at frame 4 (0.133s <= 0.20s) -> blocking flash sliver
    with_sliver = shot_review.summarize_candidates(
        pts, end, [4], flash_sliver_max_seconds=0.20
    )
    assert len(with_sliver["flash_slivers"]) == 1
    assert with_sliver["flash_slivers"][0]["review_status"] == "BLOCKING_FLASH_SLIVER"
    assert with_sliver["flash_slivers"][0]["frame_count"] == 4

    # Cut at frame 15 (0.50s > 0.20s) -> advisory short_span only, zero blocking flash_slivers
    montage_short = shot_review.summarize_candidates(
        pts, end, [15], flash_sliver_max_seconds=0.20
    )
    assert montage_short["flash_slivers"] == []
    assert len(montage_short["short_spans"]) == 1
    assert montage_short["short_spans"][0]["review_status"] == "NEEDS_DYNAMIC_REVIEW"


def test_cut_cli_shot_review_blocks_flash_frame_sliver_and_persists_plan_qc(
    monkeypatch, tmp_path
):
    edited = tmp_path / "edited_source.mp4"
    edited.write_bytes(b"mp4")
    plan_path = tmp_path / "clip_plan_validated.json"
    plan = {"clips": [{"clip_id": "c1"}], "qc": {}}
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    fake_report = {
        "status": "NEEDS_REVIEW",
        "scan_complete": True,
        "flash_slivers": [
            {
                "start_frame": 0,
                "end_frame": 3,
                "frame_count": 3,
                "duration_seconds": 0.1,
                "review_status": "BLOCKING_FLASH_SLIVER",
            }
        ],
    }

    def fake_write_scan(_video, out_path, **_kwargs):
        Path(out_path).write_text(json.dumps(fake_report), encoding="utf-8")
        return fake_report

    monkeypatch.setattr(cut_cli.shot_review, "write_scan", fake_write_scan)

    with pytest.raises(SystemExit, match="flash-frame sliver"):
        cut_cli.run_cut_shot_review(edited, plan_path, tmp_path, plan)

    persisted = json.loads(plan_path.read_text(encoding="utf-8"))
    assert persisted["qc"]["blocking"][0]["code"] == "flash_frame_sliver"
    assert persisted["qc"]["blocking"][0]["flash_sliver_count"] == 1


def test_revert_unsafe_shot_snaps_blocks_when_sliver_collides_with_speech():
    before = [{"clip_id": "c1", "source_start": 10.0, "source_end": 15.0}]
    # Shot snap moved start forward by 0.12s (<= 0.20s flash sliver) into active speech
    snapped_sliver = {
        "allow_overlap": False,
        "clips": [{"clip_id": "c1", "source_start": 10.12, "source_end": 15.0}],
        "qc": {
            "boundary_status": {
                "shot_snaps": [
                    {
                        "clip_id": "c1",
                        "original_start": 10.0,
                        "original_end": 15.0,
                        "start_action": "moved_forward",
                        "new_start": 10.12,
                        "end_action": "kept",
                    }
                ]
            }
        },
    }

    def classify(edge, when, _joined):
        if edge == "start" and abs(when - 10.0) < 1e-3:
            return ("safe", "quiet_window")
        if edge == "start":
            return ("blocking", "mid_sentence_speech")
        return ("safe", "quiet_window")

    blocked = sentence_boundaries._revert_unsafe_shot_snaps(
        snapped_sliver, before, classify, flash_sliver_max_seconds=0.20
    )
    assert blocked["qc"]["blocking"][0]["code"] == "shot_speech_conflict"
    assert blocked["qc"]["blocking"][0]["sliver_seconds"] == pytest.approx(0.12)

    # Larger L-cut offset (0.32s > 0.20s) is not a 1-5 frame flash sliver -> reverts cleanly
    snapped_lcut = {
        "allow_overlap": False,
        "clips": [{"clip_id": "c1", "source_start": 10.32, "source_end": 15.0}],
        "qc": {
            "boundary_status": {
                "shot_snaps": [
                    {
                        "clip_id": "c1",
                        "original_start": 10.0,
                        "original_end": 15.0,
                        "start_action": "moved_forward",
                        "new_start": 10.32,
                        "end_action": "kept",
                    }
                ]
            }
        },
    }
    reverted = sentence_boundaries._revert_unsafe_shot_snaps(
        snapped_lcut, before, classify, flash_sliver_max_seconds=0.20
    )
    assert not reverted["qc"].get("blocking")
    assert reverted["clips"][0]["source_start"] == pytest.approx(10.0)


def test_snap_clip_ends_adds_reaction_pause_pad_and_respects_shot_cuts():
    plan = {
        "allow_overlap": False,
        "clips": [
            {
                "clip_id": "c1",
                "source_start": 1.0,
                "source_end": 3.8,
                "output_start": 0.0,
                "output_end": 2.8,
                "duration": 2.8,
            }
        ],
    }
    silences = [{"start": 4.0, "end": 5.0}]

    # Extends to pause start 4.0 + 0.22s reaction pad = 4.22s
    padded = sentence_boundaries.snap_clip_ends_to_lines(
        plan, silences, 10.0, max_extend=2.0, pause_pad=0.22
    )
    assert padded["clips"][0]["source_end"] == pytest.approx(4.22)

    # If a shot cut occurs at 4.10s inside the pause pad, stops just before 4.10s (4.099s)
    capped = sentence_boundaries.snap_clip_ends_to_lines(
        plan, silences, 10.0, max_extend=2.0, pause_pad=0.22, shot_cuts=[4.10]
    )
    assert capped["clips"][0]["source_end"] == pytest.approx(4.099)
