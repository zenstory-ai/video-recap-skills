"""Agent-calibrated original-dialogue subtitles stay continuous across picture cuts in ASS."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/video-recap/scripts"))
from lib import CONFIG  # noqa: E402
from source_subtitles import _combined_subtitle_entries  # noqa: E402
from subtitles.render import _generate_ass  # noqa: E402


def test_agent_original_subtitles_through_ass_keep_first_syllable_and_continuous_display(
    tmp_path, monkeypatch
):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg unavailable")
    monkeypatch.setitem(CONFIG, "burn_subtitles", True)
    monkeypatch.setitem(CONFIG, "mask_source_subtitles", False)
    monkeypatch.setitem(CONFIG, "subtitle_original_in_gaps", True)
    (tmp_path / "original_subtitles.json").write_text(
        json.dumps([{"start": 0.8, "end": 1.6, "text": "老大"}], ensure_ascii=False),
        encoding="utf-8",
    )
    entries = _combined_subtitle_entries([], tmp_path, 3)
    assert len(entries) == 1
    assert entries[0]["start"] == pytest.approx(0.8)
    assert entries[0]["end"] == pytest.approx(1.6)
    ass = _generate_ass([], tmp_path, 3, {"width": 320, "height": 240})
    assert ass.read_text(encoding="utf-8").count("Dialogue:") == 1
    command = [
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=black:s=320x240:r=24:d=3",
        "-vf", f"subtitles={ass},select='eq(n,19)+eq(n,20)+eq(n,23)+eq(n,24)+eq(n,25)+eq(n,39)'",
        "-fps_mode", "passthrough", "-an", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]
    raw = subprocess.run(command, capture_output=True, check=True).stdout
    size = 320 * 240 * 3
    assert len(raw) == size * 6
    frames = [raw[i * size : (i + 1) * size] for i in range(6)]
    assert max(frames[0]) == max(frames[-1]) == 0
    assert min(max(frame) for frame in frames[1:-1]) > 100
    assert frames[1] == frames[2] == frames[3] == frames[4]
