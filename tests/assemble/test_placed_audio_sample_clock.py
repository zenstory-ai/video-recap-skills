"""Serialized narration windows must never shorten a complete sample interval."""

from pathlib import Path
import sys
import wave

SCRIPTS = Path(__file__).resolve().parents[2] / "skills/video-assemble/scripts"
sys.path.insert(0, str(SCRIPTS))

import assembly_contract  # noqa: E402
from timeline import build_timeline  # noqa: E402


def _write_wav(path, frames=216_576, rate=44_100):
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, rate, frames, "NONE", "not compressed"))
        output.writeframes(b"\0\0" * frames)


def test_serialized_narration_window_never_shortens_complete_sample_clock(tmp_path):
    placed = tmp_path / "_placed_0002.wav"
    _write_wav(placed)
    start = 23 + 2 / 3
    duration = 216_576 / 44_100
    end = start + duration
    segment = {
        "index": 2, "narration": "sample clock", "placed_audio_path": str(placed),
        "placed_audio_duration": duration, "actual_place_start": start,
        "actual_place_end": end, "fit_status": "fits", "effective_tempo": 1.0,
    }
    timeline = build_timeline(
        {"width": 64, "height": 48, "fps": 24}, 40.0,
        [{"source_path": "source.mp4", "source_start": 0, "source_end": 40,
          "timeline_start": 0, "timeline_end": 40}],
        [{"source_path": str(placed), "timeline_start": start,
          "timeline_end": end, "text": "sample clock", "overlaps_speech": False}],
    )
    serialized = next(track for track in timeline["tracks"] if track["name"] == "narration")
    item = serialized["segments"][0]
    assert item["timeline_end"] - item["timeline_start"] >= duration
    assert assembly_contract._placed_audio_matches_timeline(segment) is True
