"""Adopted frame resampling is an explicit decision, never inferred from duration."""

import copy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "skills/video-cut/scripts")
)
import picture_plan as picture


def _plan(frame_map=None):
    shot = {
        "id": "retimed",
        "source_id": "s",
        "source_frames": [10, 16],
        "output_frames": [0, 6],
        "crop": {"x": 0, "y": 0, "width": 64, "height": 64},
        "window": [0, 15, 64, 64],
    }
    if frame_map is not None:
        shot["source_frame_by_output"] = frame_map
        shot["output_frames"][1] = len(frame_map)
    return {
        "artifact": "picture_plan",
        "schema_version": 1,
        "fps": "24/1",
        "canvas": [64, 96],
        "tail_frames": 2,
        "sources": {"s": {"path": "/unused/source.mov", "sha256": "a" * 64}},
        "shots": [shot],
    }


def _facts():
    return {
        "s": {
            "width": 96,
            "height": 64,
            "frame_count": 48,
            "fps": "24",
            "sha256": "a" * 64,
        }
    }


def _run(command):
    return subprocess.run(command, check=True, capture_output=True)


@pytest.fixture
def source(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("actual FFmpeg frame mapping required")
    path = tmp_path / "source.mp4"
    _run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "nullsrc=s=96x64:r=24:d=2,geq=lum='32+N*2+X':cb=128:cr=128",
            "-c:v",
            "libx264",
            "-crf",
            "0",
            "-g",
            "48",
            str(path),
        ]
    )
    return path


def _save(data, source, tmp_path):
    data["sources"]["s"] = {
        "path": str(source),
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(data))
    return path


def test_default_still_requires_equal_counts_and_expands_identity_map():
    data = _plan()
    result = picture.validate_plan(data, _facts())
    assert result["shots"][0]["source_frame_by_output"] == list(range(10, 16))
    assert result["shots"][0]["mapping_kind"] == "implicit_identity"
    assert result["algorithm"] == "locked-cfr-rgb-pad-bt709-v1"
    data["shots"][0]["output_frames"] = [0, 7]
    with pytest.raises(ValueError):
        picture.validate_plan(data, _facts())


@pytest.mark.parametrize(
    "frame_map",
    [
        [],
        [10, 11, True, 13, 14, 15],
        [10, 11, 12.0, 13, 14, 15],
        [9, 10, 11, 12, 13, 14],
        [10, 11, 12, 13, 14, 16],
        [10, 12, 11, 13, 14, 15],
        "10 11",
    ],
)
def test_bad_map_is_not_repaired(frame_map):
    data = _plan()
    data["shots"][0]["source_frame_by_output"] = frame_map
    with pytest.raises(ValueError):
        picture.validate_plan(data, _facts())


def test_map_length_not_source_count_controls_output_crop_samples():
    data = _plan([10, 10, 11, 12, 13, 14, 15, 15])
    data["shots"][0]["crop"]["x_by_frame"] = [0] * 6
    with pytest.raises(ValueError, match="x_by_frame"):
        picture.validate_plan(data, _facts())
    data["shots"][0]["crop"]["x_by_frame"] = [0] * 8
    assert picture.validate_plan(data, _facts())["picture_frames"] == 8


@pytest.mark.parametrize(
    "frame_map",
    [
        [10, 12, 13, 15],
        [10, 10, 11, 12, 13, 14, 15, 15],
        [10, 10, 12, 12, 15, 15],
    ],
)
def test_real_declared_drop_duplicate_and_output_clock(source, tmp_path, frame_map):
    data = _plan(frame_map)
    count = len(frame_map)
    # Different crops on repeated source frames must use output, not source time.
    xs = [i * 2 for i in range(count)]
    data["shots"][0]["crop"]["x_by_frame"] = xs
    second = copy.deepcopy(data["shots"][0])
    second.update(id="repeat", output_frames=[count, count * 2])
    data["shots"].append(second)
    p = _save(data, source, tmp_path)
    out = tmp_path / "candidate"
    result = picture.execute(p, out, crf=0)
    assert result["status"] == "PICTURE_RENDERED"
    mapping = json.loads((out / "picture_map.json").read_text())
    shot = mapping["shots"][0]
    assert shot["source_frame_by_output"] == frame_map
    assert shot["mapping_kind"] == "explicit"
    assert shot["resampling_evidence"]["mapped_checksum_sequence_verified"] is True
    assert shot["resampling_evidence"]["buffer_frames"] == max(6, count)
    assert mapping["algorithm"] == "explicit-frame-map-rgb-pad-bt709-v2"
    assert [Fraction(t) for t in shot["source_pts_by_output_exact"]] == [
        Fraction(n, 24) for n in frame_map
    ]
    assert [Fraction(t) for t in shot["source_pts_exact"]] == [
        Fraction(n, 24) for n in range(10, 16)
    ]
    assert mapping["output"]["frames"] == count * 2 + 2
    assert mapping["audio"] == "NOT_PRODUCED"
    raw = _run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(out / "picture.mp4"),
            "-an",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "yuv420p",
            "-",
        ]
    ).stdout
    size = 64 * 96 * 3 // 2
    frames = [raw[i : i + size] for i in range(0, len(raw), size)]
    assert len(frames) == count * 2 + 2
    assert frames[:count] == frames[count : count * 2]
    actual = [frame[30 * 64 + 12] for frame in frames[:count]]
    expected = [32 + 2 * n + 12 + x for n, x in zip(frame_map, xs)]
    assert all(abs(a - b) <= 2 for a, b in zip(actual, expected)), (actual, expected)
    assert all(set(f[: 64 * 96]) == {16} for f in frames[-2:])


def test_explicit_retime_still_rejects_wrong_decoded_source_pts(
    source, tmp_path, monkeypatch
):
    import picture_render

    original = picture_render.decoded_selection

    def corrupt(log):
        values = original(log)
        values[2] = values[1]
        return values

    monkeypatch.setattr(picture_render, "decoded_selection", corrupt)
    p = _save(_plan([10, 12, 13, 15]), source, tmp_path)
    out = tmp_path / "bad-pts"
    with pytest.raises(ValueError, match="decoded source selection"):
        picture.execute(p, out)
    assert not (out / "picture.mp4").exists()
    assert json.loads((out / "picture_run.json").read_text())["status"] == "FAILED"


def test_same_count_wrong_frame_sampling_is_not_published(
    source, tmp_path, monkeypatch
):
    import picture_render

    original = picture_render._run

    def corrupt(command, log_path):
        if "-filter_complex_script" in command:
            graph_path = Path(command[command.index("-filter_complex_script") + 1])
            graph = graph_path.read_text()
            assert "shuffleframes=0 2 3 5 -1 -1" in graph
            graph_path.write_text(
                graph.replace(
                    "shuffleframes=0 2 3 5 -1 -1", "shuffleframes=0 1 3 5 -1 -1"
                )
            )
        return original(command, log_path)

    monkeypatch.setattr(picture_render, "_run", corrupt)
    p = _save(_plan([10, 12, 13, 15]), source, tmp_path)
    out = tmp_path / "bad-sampling"
    with pytest.raises(ValueError, match="resampling"):
        picture.execute(p, out)
    assert not (out / "picture.mp4").exists()


def test_actual_implicit_identity_never_enters_shuffle(source, tmp_path):
    p = _save(_plan(), source, tmp_path)
    out = tmp_path / "legacy-identity"
    picture.execute(p, out, crf=0)
    graph = (out / "chunks/0000.ffscript").read_text()
    assert "shuffleframes" not in graph and "tpad" not in graph
    mapping = json.loads((out / "picture_map.json").read_text())
    assert mapping["algorithm"] == "locked-cfr-rgb-pad-bt709-v1"
    assert (
        mapping["shots"][0]["resampling_evidence"]["method"]
        == "unchanged_identity_path"
    )


@pytest.mark.parametrize(
    "frame_map,fragment",
    [
        ([10, 12, 13, 15], "shuffleframes=0 2 3 5 -1 -1"),
        (
            [10, 10, 11, 12, 13, 14, 15, 15],
            "tpad=stop_mode=clone:stop=2,shuffleframes=0 0 1 2 3 4 5 5",
        ),
    ],
)
def test_filter_uses_only_declared_source_indexes(frame_map, fragment):
    import picture_render

    normalized = picture.validate_plan(_plan(frame_map), _facts())
    graph = picture_render._resampling_filter(normalized["shots"][0], Fraction(24))
    assert fragment in graph
    assert graph.endswith("settb=1/24,setpts=N,showinfo@mapped,")
