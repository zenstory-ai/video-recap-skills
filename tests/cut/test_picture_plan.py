"""Locked picture decisions are exact source frames, never auto-snapped seconds."""

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


def plan():
    return {
        "schema_version": 1,
        "artifact": "picture_plan",
        "fps": "24/1",
        "canvas": [64, 96],
        "tail_frames": 2,
        "sources": {"s": {"path": "/unused/source.mp4", "sha256": "a" * 64}},
        "shots": [
            {
                "id": "a",
                "source_id": "s",
                "source_frames": [10, 15],
                "output_frames": [0, 5],
                "crop": {"x": 0, "y": 0, "width": 64, "height": 64},
                "window": [0, 15, 64, 64],
            }
        ],
    }


def facts():
    return {
        "s": {
            "width": 96,
            "height": 64,
            "frame_count": 48,
            "fps": "24",
            "time_base": "1/12288",
            "origin": "0",
            "duration": "2",
            "sha256": "a" * 64,
            "path": "/unused/source.mp4",
        }
    }


def test_normalizes_half_open_mapping_and_explicit_black_tail():
    result = picture.validate_plan(plan(), facts())
    assert result["picture_frames"] == 5 and result["total_frames"] == 7
    assert result["shots"][0]["source_frames"] == [10, 15]
    assert result["shots"][0]["crop_x_by_frame"] == [0] * 5
    assert result["audio"] == "NOT_PRODUCED"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(schema_version=True),
        lambda p: p.update(fps=24.0),
        lambda p: p.update(tail_frames=-1),
        lambda p: p["shots"][0].update(source_frames=[10, 10]),
        lambda p: p["shots"][0].update(source_frames=[10, 16]),
        lambda p: p["shots"][0].update(output_frames=[1, 6]),
        lambda p: p["shots"][0].update(source_id="missing"),
        lambda p: p["shots"][0]["crop"].update(x=1),
        lambda p: p["shots"][0]["crop"].update(x=34),
        lambda p: p["shots"][0]["crop"].update(width=62),
        lambda p: p["shots"][0]["crop"].update(x="2*floor(n)"),
        lambda p: p["shots"][0]["crop"].update(y=True),
        lambda p: p["shots"][0].update(window=[0, 33, 64, 64]),
        lambda p: p["shots"][0].update(speed=1.5),
        lambda p: p["sources"]["s"].update(sha256="b" * 64),
    ],
)
def test_invalid_or_unsupported_decisions_fail_instead_of_repairing(mutate):
    data = plan()
    mutate(data)
    with pytest.raises(ValueError):
        picture.validate_plan(data, facts())


def test_dynamic_crop_quantization_and_declared_endpoints():
    data = plan()
    data["shots"][0]["crop"].update(x_by_frame=[0, 2, 4, 6, 10])
    assert picture.validate_plan(data, facts())["shots"][0]["crop_x_by_frame"] == [
        0,
        2,
        4,
        6,
        10,
    ]
    data["shots"][0]["crop"]["x_by_frame"] = [0, 2, 4, 6]
    with pytest.raises(ValueError, match="x_by_frame"):
        picture.validate_plan(data, facts())


def test_repeated_reordered_ranges_have_distinct_output_frames():
    data = plan()
    b = copy.deepcopy(data["shots"][0])
    b.update(id="b", source_frames=[0, 5], output_frames=[5, 10])
    c = copy.deepcopy(data["shots"][0])
    c.update(id="c", output_frames=[10, 15])
    data["shots"] += [b, c]
    result = picture.validate_plan(data, facts())
    assert [s["output_frames"] for s in result["shots"]] == [[0, 5], [5, 10], [10, 15]]
    data["shots"][1]["id"] = "a"
    with pytest.raises(ValueError, match="id"):
        picture.validate_plan(data, facts())


def _run(command):
    return subprocess.run(command, capture_output=True, check=True)


@pytest.fixture
def media(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("actual locked-frame renderer requires ffmpeg/ffprobe")
    path = tmp_path / "source.mp4"
    # Non-keyframe selections and a visibly different source each frame.
    _run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=96x64:r=24:d=2",
            "-c:v",
            "libx264",
            "-crf",
            "0",
            "-g",
            "48",
            str(path),
        ]
    )
    data = plan()
    data["sources"]["s"] = {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(data))
    return p, data


def test_plan_only_is_explicit_and_never_invokes_render(media, tmp_path, monkeypatch):
    p, _ = media
    monkeypatch.setattr(
        picture, "render_picture", lambda *_a, **_kw: pytest.fail("rendered plan-only")
    )
    result = picture.execute(p, tmp_path / "planned", plan_only=True)
    assert result["status"] == "PLANNED"
    assert not (tmp_path / "planned/picture.mp4").exists()
    assert json.loads((tmp_path / "planned/picture_plan.validated.json").read_text())[
        "shots"
    ][0]["source_frames"] == [10, 15]


def _rgb(path, select=None):
    vf = [f"select={select}"] if select else []
    return _run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            *(["-vf", ",".join(vf)] if vf else []),
            "-fps_mode",
            "passthrough",
            "-an",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ]
    ).stdout


def test_actual_source_selection_odd_y_tail_and_audio_absence(media, tmp_path):
    p, data = media
    result = picture.execute(p, tmp_path / "rendered", crf=0)
    assert result["status"] == "PICTURE_RENDERED"
    assert result["audio"] == "NOT_PRODUCED"
    assert result["normal_speed_review"] == "NOT_CHECKED"
    mapping = json.loads((tmp_path / "rendered/picture_map.json").read_text())
    assert [Fraction(t) for t in mapping["shots"][0]["source_pts_exact"]] == [
        Fraction(n, 24) for n in range(10, 15)
    ]
    assert mapping["shots"][0]["source_frames"] == [10, 15]
    assert mapping["shots"][0]["decoded_source_selection_verified"] is True
    output = tmp_path / "rendered/picture.mp4"
    raw = _rgb(output)
    size = 64 * 96 * 3
    assert len(raw) == size * 7
    # Odd canvas position is not rounded down to y14 by chroma subsampling.
    first = raw[:size]
    luma = _run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(output),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "yuv420p",
            "-",
        ]
    ).stdout[: 64 * 96]
    assert set(luma[: 15 * 64]) == {16}
    assert max(first[15 * 64 * 3 : 16 * 64 * 3]) > 100
    assert max(raw[-2 * size :]) <= 3
    streams = json.loads(
        _run(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)]
        ).stdout
    )["streams"]
    assert [s["codec_type"] for s in streams] == ["video"]
    assert (
        mapping["output"]["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    )


def test_stale_source_does_not_publish_picture(media, tmp_path):
    p, data = media
    Path(data["sources"]["s"]["path"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="identity"):
        picture.execute(p, tmp_path / "failed")
    assert not (tmp_path / "failed/picture.mp4").exists()


def test_existing_directory_is_never_overwritten(media, tmp_path):
    p, _ = media
    target = tmp_path / "existing"
    target.mkdir()
    saved = target / "picture.mp4"
    saved.write_bytes(b"adopted")
    with pytest.raises(FileExistsError):
        picture.execute(p, target)
    assert saved.read_bytes() == b"adopted"


def test_cut_cli_picture_branch_does_not_read_legacy_plan(media, tmp_path):
    p, _ = media
    output = tmp_path / "via-cli"
    _run(
        [
            sys.executable,
            str(Path(picture.__file__).with_name("cut.py")),
            "--picture-plan",
            str(p),
            "--work-dir",
            str(output),
            "--normalize-only",
        ]
    )
    assert json.loads((output / "picture_run.json").read_text())["status"] == "PLANNED"
    assert not (output / "clip_plan_validated.json").exists()


def test_same_count_but_wrong_interior_pts_rejected(media, tmp_path, monkeypatch):
    import picture_render

    original = picture_render.decoded_selection

    def wrong(log):
        values = original(log)
        values[2] = values[1]
        return values

    monkeypatch.setattr(picture_render, "decoded_selection", wrong)
    p, _ = media
    with pytest.raises(ValueError, match="decoded source selection"):
        picture.execute(p, tmp_path / "wrongpts")
    assert (
        json.loads((tmp_path / "wrongpts/picture_run.json").read_text())["status"]
        == "FAILED"
    )
    assert not (tmp_path / "wrongpts/picture.mp4").exists()


@pytest.mark.parametrize("change", ["plan", "source"])
def test_changed_binding_before_publish_fails(media, tmp_path, monkeypatch, change):
    p, data = media
    original = picture.render_picture

    def mutate(*args, **kwargs):
        value = original(*args, **kwargs)
        path = p if change == "plan" else Path(data["sources"]["s"]["path"])
        with path.open("ab") as handle:
            handle.write(b"changed")
        return value

    monkeypatch.setattr(picture, "render_picture", mutate)
    with pytest.raises(ValueError, match="identity changed"):
        picture.execute(p, tmp_path / "stale")
    assert not (tmp_path / "stale/picture.mp4").exists()
    assert (
        json.loads((tmp_path / "stale/picture_run.json").read_text())["status"]
        == "FAILED"
    )


@pytest.mark.parametrize("clock_change", ["vfr", "missing", "tail", "offset"])
def test_cfr_wrapper_rejects_inaccurate_clock(
    media, tmp_path, monkeypatch, clock_change
):
    p, _ = media
    pts = [Fraction(n, 24) for n in range(48)]
    end = Fraction(2)
    origin = Fraction(0)
    if clock_change == "vfr":
        pts[15] += Fraction(1, 48)
    elif clock_change == "missing":
        pts.pop(15)
    elif clock_change == "tail":
        end += Fraction(1, 48)
    else:
        origin = Fraction(1)
    monkeypatch.setattr(picture, "probe_frame_clock", lambda *_: (pts, end, origin))
    with pytest.raises(ValueError, match="CFR"):
        picture.execute(p, tmp_path / "badclock")


def test_full_perframe_crop_executed_and_repeated_sources_match(media, tmp_path):
    p, data = media
    # A grayscale source has a known left-edge value at every horizontal offset.
    source = Path(data["sources"]["s"]["path"])
    _run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "nullsrc=s=96x64:r=24:d=2,geq=lum=32+N+X*2:cb=128:cr=128",
            "-c:v",
            "libx264",
            "-crf",
            "0",
            "-g",
            "48",
            str(source),
        ]
    )
    data["sources"]["s"]["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    data["shots"][0]["crop"]["x_by_frame"] = [0, 2, 6, 16, 32]
    repeat = copy.deepcopy(data["shots"][0])
    repeat.update(id="b", output_frames=[5, 10])
    data["shots"].append(repeat)
    p.write_text(json.dumps(data))
    picture.execute(p, tmp_path / "dynamic", crf=0)
    output = tmp_path / "dynamic/picture.mp4"
    raw = _run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(output),
            "-an",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "yuv420p",
            "-",
        ]
    ).stdout
    frame_bytes = 64 * 96 * 3 // 2
    frames = [raw[i : i + frame_bytes] for i in range(0, len(raw), frame_bytes)]
    assert frames[:5] == frames[5:10]
    # Luma inside the window follows the exact crop samples (small color roundtrip tolerance).
    actual = [f[30 * 64 + 12] for f in frames[:5]]
    expected = [32 + n + (12 + x) * 2 for n, x in zip(range(10, 15), [0, 2, 6, 16, 32])]
    assert all(abs(a - b) <= 2 for a, b in zip(actual, expected)), (actual, expected)


def test_real_reorder_returns_the_correct_temporal_source_frames(media, tmp_path):
    p, data = media
    source = Path(data["sources"]["s"]["path"])
    _run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "nullsrc=s=96x64:r=24:d=2,geq=lum=32+N*3:cb=128:cr=128",
            "-c:v",
            "libx264",
            "-crf",
            "0",
            "-g",
            "48",
            str(source),
        ]
    )
    data["sources"]["s"]["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    data["shots"][0]["source_frames"] = [30, 35]
    second = copy.deepcopy(data["shots"][0])
    second.update(id="earlier", source_frames=[10, 15], output_frames=[5, 10])
    data["shots"].append(second)
    p.write_text(json.dumps(data))
    picture.execute(p, tmp_path / "reordered", crf=0)
    raw = _run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(tmp_path / "reordered/picture.mp4"),
            "-an",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "yuv420p",
            "-",
        ]
    ).stdout
    size = 64 * 96 * 3 // 2
    observed = [raw[i * size + 30 * 64 + 12] for i in range(10)]
    expected = [32 + n * 3 for n in [*range(30, 35), *range(10, 15)]]
    assert all(abs(a - b) <= 2 for a, b in zip(observed, expected)), (
        observed,
        expected,
    )


@pytest.mark.parametrize(
    "color",
    [
        {"color_primaries": "smpte432"},
        {"color_space": "bt2020nc"},
        {"color_transfer": "smpte2084"},
        {"color_transfer": "arib-std-b67"},
        {"color_primaries": "smpte170m"},
    ],
)
def test_tagged_other_gamuts_do_not_get_silently_relabelled_bt709(color):
    with pytest.raises(ValueError, match="color"):
        picture.validate_source_color(color)


def test_known_bt709_or_unlabelled_source_color_boundary():
    picture.validate_source_color({})
    picture.validate_source_color(
        {"color_space": "bt709", "color_transfer": "bt709", "color_primaries": "bt709"}
    )
