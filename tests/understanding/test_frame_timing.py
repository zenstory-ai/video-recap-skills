import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'skills' / 'video-recap' / 'scripts'))
"""The frame-number ↔ source-time contract owned by extract.py.

ffmpeg's `fps` filter emits its FIRST frame at source time 0, while the image2 muxer
numbers files from 1. So frame_00001.jpg is t=0 and frame n is (n-1)/fps. Getting this
backwards shifts every visual anchor (frame_facts, scene→frame assignment, storyboard
labels) later by 1/fps — a full second at the default fps=1 used for videos over 5 min.
"""
import shutil
import subprocess

import pytest

import storyboard
import vlm
from extract import (
    FRAME_START_NUMBER,
    frame_number_for_time,
    frame_time,
    parse_frame_number,
)
from lib import CONFIG
from vlm import analyze_scenes


def test_first_extracted_frame_is_source_time_zero():
    assert frame_time(1, 1.0) == 0.0
    assert frame_time(1, 2.0) == 0.0
    assert frame_time(2, 1.0) == 1.0
    assert frame_time(2, 2.0) == 0.5
    assert frame_time(31, 1.5) == 20.0


def test_frame_time_and_frame_number_round_trip():
    for fps in (1.0, 1.5, 2.0):
        for number in (1, 2, 7, 100):
            assert frame_number_for_time(frame_time(number, fps), fps) == pytest.approx(number)


def test_frame_time_rejects_non_positive_fps():
    for bad in (0, -1.0):
        with pytest.raises(ValueError):
            frame_time(1, bad)
        with pytest.raises(ValueError):
            frame_number_for_time(0.0, bad)


def test_parse_frame_number_ignores_foreign_names(tmp_path):
    assert parse_frame_number(tmp_path / "frame_00007.jpg") == 7
    assert parse_frame_number(tmp_path / "storyboard.jpg") is None
    assert parse_frame_number(tmp_path / "frame_x_1.jpg") is None


def test_vlm_labels_frames_with_corrected_source_time(monkeypatch, tmp_path):
    """The 帧时间点 header injected into the VLM prompt drives frame_facts, which the writing
    brief presents as authoritative visual anchors. frame_00001 must be labelled 0.0s."""
    frames = []
    for n in (1, 2, 3):
        f = tmp_path / f"frame_{n:05d}.jpg"
        f.write_bytes(b"\xff\xd8\xff\xd9")
        frames.append(f)
    monkeypatch.setitem(CONFIG, "fps", 1.0)
    monkeypatch.setitem(CONFIG, "vlm_model", "mimo-v2.5")
    monkeypatch.setitem(CONFIG, "vlm_workers", 1)
    monkeypatch.setitem(CONFIG, "context_info", "")
    seen = []

    def fake_api_call(payload):
        seen.append(payload["messages"][0]["content"][-1]["text"])
        return {"choices": [{"message": {"content": "【描述】测试画面"}}]}

    monkeypatch.setattr("vlm.api_call", fake_api_call)
    analyze_scenes([{"start": 0.0, "end": 2.0}], frames, tmp_path)

    assert seen and seen[0].startswith("帧时间点: 0.0s, 1.0s, 2.0s")


def test_storyboard_resolves_source_time_through_the_same_contract(tmp_path):
    """storyboard tiles and VLM anchors must agree; both go through extract.py."""
    numbers = [1, 2, 3, 4]
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    for n in numbers:
        (frames_dir / f"frame_{n:05d}.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    paths, found = storyboard._frame_index(tmp_path)
    assert found == numbers
    assert storyboard._nearest_existing_frame(0.0, 1.0, paths, found).name == "frame_00001.jpg"
    assert storyboard._nearest_existing_frame(2.0, 1.0, paths, found).name == "frame_00003.jpg"


def test_vlm_frame_base64_cache_is_bounded(monkeypatch, tmp_path):
    """The per-frame base64 cache used to be an unbounded dict, so the whole extracted frame
    set stayed resident (base64 is 1/3 larger than the JPEG) for the entire VLM stage —
    hundreds of MB on a 40-minute video. Only nearby scenes can reuse a frame anyway."""
    frames = []
    for n in range(1, 41):
        f = tmp_path / f"frame_{n:05d}.jpg"
        f.write_bytes(b"\xff\xd8" + bytes([n]) * 2048 + b"\xff\xd9")
        frames.append(f)
    monkeypatch.setitem(CONFIG, "fps", 1.0)
    monkeypatch.setitem(CONFIG, "vlm_model", "mimo-v2.5")
    monkeypatch.setitem(CONFIG, "vlm_workers", 1)
    monkeypatch.setitem(CONFIG, "vlm_max_frames", 3)
    monkeypatch.setitem(CONFIG, "context_info", "")
    monkeypatch.setattr(
        "vlm.api_call",
        lambda payload: {"choices": [{"message": {"content": "【描述】x"}}]},
    )
    caches = []
    real_ordered_dict = vlm.OrderedDict
    monkeypatch.setattr(
        "vlm.OrderedDict", lambda *a, **k: caches.append(real_ordered_dict(*a, **k)) or caches[-1]
    )

    scenes = [{"start": float(i), "end": float(i) + 1.0} for i in range(39)]
    analyze_scenes(scenes, frames, tmp_path)

    assert caches, "analyze_scenes no longer builds a frame cache"
    capacity = max(8, 3 * 1)
    assert len(caches[0]) <= capacity, f"cache grew to {len(caches[0])} entries"


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)
def test_extracted_frame_numbering_matches_real_ffmpeg_pts(tmp_path):
    """Pin the contract to what ffmpeg actually does, not to what we assume it does."""
    source = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=30:duration=5",
         "-pix_fmt", "yuv420p", str(source)],
        check=True,
    )
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-f", "lavfi", "-i", f"movie={source},fps=1",
         "-show_entries", "frame=pts_time", "-of", "csv=p=0"],
        capture_output=True, text=True, check=True,
    )
    emitted = [float(line) for line in probe.stdout.split() if line.strip()]

    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source),
         "-vf", "fps=1", "-q:v", "2", "-start_number", str(FRAME_START_NUMBER),
         str(frames_dir / "frame_%05d.jpg")],
        check=True,
    )
    written = sorted(frames_dir.glob("frame_*.jpg"))
    assert len(written) == len(emitted)
    for path, pts in zip(written, emitted):
        assert frame_time(parse_frame_number(path), 1.0) == pytest.approx(pts, abs=1e-6)
