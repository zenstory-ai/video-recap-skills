"""Put skills/video-recap/scripts on sys.path for the companions pytest group."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "video-recap" / "scripts"))

import pytest


@pytest.fixture
def sample_timeline(tmp_path: Path) -> dict:
    vid = tmp_path / "ep01.mp4"
    vid.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    narr = tmp_path / "n1.wav"
    narr.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")
    bgm = tmp_path / "bgm.mp3"
    bgm.write_bytes(b"ID3\x03\x00\x00\x00\x00\x00\x00")
    sfx = tmp_path / "whoosh.wav"
    sfx.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")
    plate = tmp_path / "plate.png"
    plate.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    return {
        "schema_version": 3,
        "meta": {
            "title": "Demo Cut",
            "voice": {"engine": "local", "voice_id": "v_demo", "consent": "self"},
        },
        "canvas": {"width": 1080, "height": 1920, "fps": "30000/1001"},
        "sources": {
            "s1": str(vid),
            "bgm1": str(bgm),
            "sfx1": str(sfx),
            "plate1": str(plate),
        },
        "video": [
            {"id": "v1", "source": "s1", "in": 2.0, "out": 8.0, "audio": "keep", "audio_gain": 1.0, "keep_windows": [[4.0, 5.5]]},
            {"id": "v2", "source": "s1", "in": 10.0, "out": 14.0, "audio": "mute", "audio_gain": 1.0},
        ],
        "audio": {
            "narration": [
                {"id": "n1", "at": 0.5, "file": str(narr), "text": "Opening hook.", "gain": 1.0, "duration": 2.0}
            ],
            "bgm": [
                {"id": "b1", "source": "bgm1", "in": 0.0, "at": 0.0, "out": 10.0, "loop": True, "gain": 0.25}
            ],
            "sfx": [
                {"id": "x1", "source": "sfx1", "at": 6.0, "gain": 0.7}
            ],
        },
        "overlays": [
            {"id": "o1", "source": "plate1", "start": 0.0, "end": 3.0, "rect": {"x": 0.05, "y": 0.05, "w": 0.90, "h": 0.12}}
        ],
        "subtitles": [
            {"start": 0.5, "end": 2.5, "text": "Opening hook 🎬", "style": "narration"}
        ],
        "subtitle_style": {"safe_y": [0.74, 0.88]},
        "mix": {
            "target_lufs": -14.0,
            "true_peak_dbtp": -1.5,
            "duck": {"under_narration": 0.18, "bgm_under_narration": 0.10, "fade_s": 0.2, "bridge_s": 0.5},
        },
    }
