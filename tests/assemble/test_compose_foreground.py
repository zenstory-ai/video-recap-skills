"""Real-media contract tests for immutable full-canvas foreground composition."""

import copy
from fractions import Fraction
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills/video-assemble/scripts"
sys.path.insert(0, str(SCRIPTS))
import compose_foreground
from adoption.frozen_audio import probe_audio_packets
from pair_media import run_pair


pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg/ffprobe unavailable: actual foreground composition not checked",
)


AAC_FRAME_SAMPLES = 1024


def run(*args, check=True):
    return subprocess.run(list(map(str, args)), capture_output=True, check=check)


def png(path, source, *, size="64x48", pix_fmt="rgba"):
    source = (source.replace(",", f":s={size},", 1)
              if "," in source else f"{source}:s={size}")
    run(
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        f"{source},format={pix_fmt}", "-frames:v", "1", "-threads", "1", path,
    )


def packet_clock_package(tmp_path, *, endcard_kind="still", audio_case="overhang"):
    """Build a fractional 32f/24fps picture with a pair-accepted AAC edge clock."""
    picture = tmp_path / "picture.mp4"
    run(
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=c=blue:size=64x48:rate=24", "-frames:v", "32",
        "-c:v", "libx264", "-threads", "2", "-pix_fmt", "yuv420p",
        "-color_range", "tv", "-colorspace", "bt709",
        "-color_primaries", "bt709", "-color_trc", "bt709", "-x264-params",
        "colorprim=bt709:transfer=bt709:colormatrix=bt709:range=limited", picture,
    )
    audio = tmp_path / f"{audio_case}.m4a"
    samples = 62976 if audio_case == "offset" else 64000
    encoded = audio if audio_case != "offset" else tmp_path / "offset_source.m4a"
    audio_command = [
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "sine=frequency=440:sample_rate=48000", "-af", f"atrim=end_sample={samples}",
        "-c:a", "aac", "-b:a", "192k",
    ]
    if audio_case != "primed":
        audio_command += ["-avoid_negative_ts", "make_zero"]
    run(*audio_command, "-movie_timescale", "48000", encoded)
    if audio_case == "offset":
        run(
            "ffmpeg", "-v", "error", "-y", "-copyts", "-itsoffset", "0.010",
            "-i", encoded, "-map", "0:a:0", "-c", "copy",
            "-movie_timescale", "48000", audio,
        )
    pair_plan = tmp_path / "pair.json"
    pair_plan.write_text(json.dumps({
        "artifact": "media_pair", "schema_version": 1,
        "picture": {"path": str(picture)},
        "audio": {"path": str(audio), "selected_stream": 0},
    }))
    pair_dir = tmp_path / "paired"
    pair_report = run_pair(pair_plan, pair_dir)
    base = pair_dir / "paired.mp4"

    clear = tmp_path / "clear.png"
    png(clear, "color=c=black@0.0")
    foreground = tmp_path / "foreground"
    foreground.mkdir()
    for index in range(30):
        (foreground / f"frame_{index:06d}.png").symlink_to(clear)
    if endcard_kind == "still":
        endcard = {
            "kind": "still", "path": str(clear),
            "start_frame": 30, "end_frame": 32,
        }
    else:
        sequence = tmp_path / "endcard_sequence"
        sequence.mkdir()
        for index in range(2):
            (sequence / f"frame_{index:06d}.png").symlink_to(clear)
        endcard = {
            "kind": "sequence", "directory": str(sequence),
            "pattern": "frame_%06d.png", "start_frame": 30, "end_frame": 32,
        }
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"approved": False}))
    compose_plan = tmp_path / "compose.json"
    compose_plan.write_text(json.dumps({
        "artifact": "foreground_compose_plan", "schema_version": 1,
        "base": {"path": str(base)},
        "video": {"fps": "24/1", "width": 64, "height": 48, "total_frames": 32},
        "foreground": {
            "directory": str(foreground), "pattern": "frame_%06d.png",
            "start_frame": 0, "end_frame": 30,
        },
        "endcard": endcard,
        "producer_receipt": {"path": str(receipt)},
    }))
    return {
        "plan": compose_plan, "base": base, "audio": audio,
        "pair_report": pair_report, "audio_samples": samples,
    }


def pixel(path, frame, x, y):
    result = run(
        "ffmpeg", "-v", "error", "-i", path, "-vf",
        f"select=eq(n\\,{frame}),format=rgb24,crop=1:1:{x}:{y}", "-frames:v", "1",
        "-f", "rawvideo", "-",
    )
    assert len(result.stdout) == 3
    return tuple(result.stdout)


@pytest.fixture
def package(tmp_path):
    base = tmp_path / "base.mp4"
    run(
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=c=blue:size=64x48:rate=4:duration=2", "-f", "lavfi", "-i",
        "sine=frequency=440:sample_rate=48000:duration=2", "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-threads", "2", "-pix_fmt", "yuv420p",
        "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709",
        "-color_trc", "bt709", "-x264-params",
        "colorprim=bt709:transfer=bt709:colormatrix=bt709:range=limited", "-c:a", "aac", base,
    )
    foreground = tmp_path / "foreground"
    foreground.mkdir()
    clear = tmp_path / "clear.png"
    note = tmp_path / "note.png"
    png(clear, "color=c=black@0.0")
    png(note, "color=c=black@0.0,drawbox=x=10:y=10:w=12:h=12:color=red@1:t=fill:replace=1")
    for index in range(6):
        source = note if index in {2, 3} else clear
        (foreground / f"frame_{index:06d}.png").symlink_to(source)
    endcard = tmp_path / "endcard.png"
    png(endcard, "color=c=green@1.0")
    receipt = tmp_path / "producer_receipt.json"
    receipt.write_text(json.dumps({
        "artifact": "project_foreground_receipt", "schema_version": 1,
        "declarations": {"dialogues": [], "markers_visible": False,
                         "roles": ["title", "note", "brand", "endcard"]},
    }))
    document = {
        "artifact": "foreground_compose_plan", "schema_version": 1,
        "base": {"path": str(base)},
        "video": {"fps": "4/1", "width": 64, "height": 48, "total_frames": 8},
        "foreground": {
            "directory": str(foreground), "pattern": "frame_%06d.png",
            "start_frame": 0, "end_frame": 6,
        },
        "endcard": {"kind": "still", "path": str(endcard),
                    "start_frame": 6, "end_frame": 8},
        "producer_receipt": {"path": str(receipt)},
    }
    plan = tmp_path / "foreground_plan.json"
    plan.write_text(json.dumps(document))
    return {"base": base, "foreground": foreground, "endcard": endcard,
            "receipt": receipt, "plan": plan, "document": document}


def test_real_foreground_half_open_endcard_and_frozen_audio(package, tmp_path):
    target = tmp_path / "render"
    report = compose_foreground.run_compose(package["plan"], target)
    output = target / "foreground.mp4"
    assert report["status"] == "FOREGROUND_RENDERED"
    assert report["output"]["path"] == str(output) and output.is_file()
    assert report["foreground"]["frame_count"] == 6
    assert report["plan"] == {"path": str(package["plan"].resolve())}
    assert report["direct_listening"] == report["normal_speed_review"] == "NOT_CHECKED"
    assert report["release_approved"] is False
    # Transparent sequence pixels preserve the blue base; note is visible only [2, 4).
    for frame in (0, 1, 4, 5):
        r, g, b = pixel(output, frame, 15, 15)
        assert b > 120 and r < 80 and g < 100
    for frame in (2, 3):
        r, g, b = pixel(output, frame, 15, 15)
        assert r > 120 and g < 100 and b < 100
    # Transparent corner survives even while the note is active.
    r, g, b = pixel(output, 2, 2, 2)
    assert b > 120 and r < 80
    for frame in (6, 7):
        r, g, b = pixel(output, frame, 2, 2)
        assert g > 70 and r < 80 and b < 80
    assert compose_foreground.probe_picture(output)["frame_pts"] == [
        str(Fraction(i, 4)) for i in range(8)
    ]
    assert probe_audio_packets(package["base"], 0) == probe_audio_packets(output, 0)
    command = json.loads((target / "compose.command.json").read_text())
    for forbidden in ("-r", "-shortest", "-t", "-af", "-c:a:a", "-frames:v"):
        assert forbidden not in command
    filters = command[command.index("-filter_complex") + 1]
    assert filters.endswith("format=yuv420p,trim=end_frame=8[outv]")
    assert command[command.index("-c:a") + 1] == "copy"
    assert command[command.index("-preset") + 1] == "fast"
    assert command[command.index("-crf") + 1] == "18"
    assert report["encoding"] == {
        "video_codec": "libx264", "preset": "fast", "crf": 18,
        "pixel_format": "yuv420p", "color": "bt709_tv",
        "audio_codec": "copy",
    }


def test_real_full_length_foreground_without_endcard_preserves_last_frame(package, tmp_path):
    red = tmp_path / "red.png"
    png(red, "color=c=red@1.0")
    (package["foreground"] / "frame_000006.png").symlink_to(red)
    (package["foreground"] / "frame_000007.png").symlink_to(red)
    package["document"]["foreground"].update(end_frame=8)
    package["document"]["endcard"] = {"kind": "none"}
    package["plan"].write_text(json.dumps(package["document"]))

    target = tmp_path / "no-tail"
    report = compose_foreground.run_compose(package["plan"], target)
    output = target / "foreground.mp4"

    assert report["status"] == "FOREGROUND_RENDERED"
    assert report["endcard"] == {"kind": "none"}
    r, g, b = pixel(output, 7, 2, 2)
    assert r > 120 and g < 100 and b < 100
    assert probe_audio_packets(package["base"], 0) == probe_audio_packets(output, 0)
    command = json.loads((target / "compose.command.json").read_text())
    assert command.count("-i") == 2
    filters = command[command.index("-filter_complex") + 1]
    assert "[2:v]" not in filters
    assert "pad=" not in filters
    assert filters.endswith("format=yuv420p,trim=end_frame=8[outv]")


@pytest.mark.parametrize("endcard", [
    {"kind": "none"},
    {"kind": "none", "start_frame": 6},
])
def test_none_endcard_requires_exact_full_foreground_and_no_extra_fields(
    package, tmp_path, endcard
):
    package["document"]["endcard"] = endcard
    package["plan"].write_text(json.dumps(package["document"]))
    target = tmp_path / "invalid-none"

    with pytest.raises(ValueError):
        compose_foreground.run_compose(package["plan"], target)

    assert not (target / "foreground.mp4").exists()
    assert json.loads((target / "foreground_run.json").read_text())["status"] == "FAILED"


def test_asset_endcard_interval_must_be_nonempty(package):
    endcard = copy.deepcopy(package["document"]["endcard"])
    endcard.update(start_frame=8, end_frame=8)

    with pytest.raises(ValueError):
        compose_foreground.validate_endcard(
            endcard, foreground_end=8, total_frames=8, width=64, height=48
        )


@pytest.mark.parametrize("endcard_kind", ["still", "sequence"])
def test_pair_accepted_aac_packet_overhang_survives_composition(
    tmp_path, endcard_kind
):
    package = packet_clock_package(tmp_path, endcard_kind=endcard_kind)
    assert package["pair_report"]["status"] == "PAIR_RENDERED"
    expected_audio = probe_audio_packets(package["base"], 0)
    # every 1024-sample AAC frame the fixture's PCM needs, plus one encoder priming frame
    assert expected_audio["packet_count"] == (
        math.ceil(package["audio_samples"] / AAC_FRAME_SAMPLES) + 1
    )
    assert expected_audio["start_time"] == "0.000000"

    target = tmp_path / "composed"
    report = compose_foreground.run_compose(package["plan"], target)
    output = target / "foreground.mp4"

    assert report["status"] == "FOREGROUND_RENDERED"
    assert compose_foreground.probe_picture(output)["frame_pts"] == [
        str(Fraction(i, 24)) for i in range(32)
    ]
    assert probe_audio_packets(output, 0) == expected_audio


def test_standard_aac_negative_priming_and_negative_end_delta_remain_exact(tmp_path):
    package = packet_clock_package(tmp_path, audio_case="primed")
    assert Fraction(package["pair_report"]["timing"]["end_delta"]) < 0
    expected_audio = probe_audio_packets(package["base"], 0)
    first_packet = expected_audio["packets"][0]
    assert Fraction(first_packet["pts"]) < 0
    assert first_packet["side_data_list"][0]["side_data_type"] == "Skip Samples"
    assert first_packet["side_data_list"][0]["skip_samples"] == 1024

    target = tmp_path / "composed"
    compose_foreground.run_compose(package["plan"], target)
    output = target / "foreground.mp4"

    assert compose_foreground.probe_picture(output)["frame_pts"] == [
        str(Fraction(i, 24)) for i in range(32)
    ]
    assert probe_audio_packets(output, 0) == expected_audio


def test_pair_tolerated_nonzero_audio_start_remains_exact(tmp_path):
    package = packet_clock_package(tmp_path, audio_case="offset")
    assert package["pair_report"]["timing"]["start_delta"] == "1/100"
    expected_audio = probe_audio_packets(package["base"], 0)

    target = tmp_path / "composed"
    compose_foreground.run_compose(package["plan"], target)
    output = target / "foreground.mp4"

    assert compose_foreground.probe_picture(output)["frame_pts"] == [
        str(Fraction(i, 24)) for i in range(32)
    ]
    assert probe_audio_packets(output, 0) == expected_audio


def test_plan_only_and_isolated_copied_skill_cli(package, tmp_path):
    copied = tmp_path / "installed"
    shutil.copytree(SCRIPTS, copied)
    launcher = (
        "import sys,runpy;p=sys.argv.pop(1);sys.path.insert(0,p);"
        "sys.argv[0]=p+'/compose_foreground.py';runpy.run_path(sys.argv[0],run_name='__main__')"
    )
    planned = tmp_path / "planned"
    run(sys.executable, "-I", "-c", launcher, copied, package["plan"],
        "--output-dir", planned, "--plan-only")
    assert json.loads((planned / "foreground_run.json").read_text())["status"] == "PLANNED"
    assert {path.name for path in planned.iterdir()} == {"foreground_run.json"}
    rendered = tmp_path / "isolated-rendered"
    run(sys.executable, "-I", "-c", launcher, copied, package["plan"],
        "--output-dir", rendered)
    assert (rendered / "foreground.mp4").is_file()
    with pytest.raises(FileExistsError):
        compose_foreground.run_compose(package["plan"], planned)


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(schema_version=2),
    lambda d: d.update(artifact="wrong"),
    lambda d: d.update(extra=True),
    lambda d: d.pop("producer_receipt"),
    lambda d: d["base"].update(path="/nonexistent/base.mp4"),
    lambda d: d["video"].update(fps="5/1"),
    lambda d: d["video"].update(width=32),
    lambda d: d["video"].update(total_frames=9),
    lambda d: d["foreground"].update(pattern="frame_%d.png"),
    lambda d: d["foreground"].update(start_frame=1),
    lambda d: d["foreground"].update(end_frame=5),
    lambda d: d["endcard"].update(start_frame=5),
    lambda d: d["endcard"].update(end_frame=7),
    lambda d: d["endcard"].update(path="/nonexistent/endcard.png"),
    lambda d: d["producer_receipt"].update(path="/nonexistent/receipt.json"),
])
def test_invalid_plan_or_missing_input_never_publishes(package, tmp_path, mutation):
    document = copy.deepcopy(package["document"])
    mutation(document)
    package["plan"].write_text(json.dumps(document))
    target = tmp_path / "failed"
    with pytest.raises((ValueError, RuntimeError, FileNotFoundError)):
        compose_foreground.run_compose(package["plan"], target)
    assert not (target / "foreground.mp4").exists()
    assert json.loads((target / "foreground_run.json").read_text())["status"] == "FAILED"


@pytest.mark.parametrize("fault", ["missing", "extra", "bad_size", "non_rgba"])
def test_sequence_shape_and_inventory_are_exact(package, tmp_path, fault):
    directory = package["foreground"]
    if fault == "missing":
        (directory / "frame_000005.png").unlink()
    elif fault == "extra":
        (directory / "frame_000006.png").symlink_to(directory / "frame_000000.png")
    elif fault == "bad_size":
        (directory / "frame_000005.png").unlink()
        png(directory / "frame_000005.png", "color=c=black@0.0", size="32x24")
    else:
        (directory / "frame_000005.png").unlink()
        png(directory / "frame_000005.png", "color=c=black", pix_fmt="rgb24")
    target = tmp_path / fault
    with pytest.raises((ValueError, FileNotFoundError)):
        compose_foreground.run_compose(package["plan"], target)
    assert not (target / "foreground.mp4").exists()


def test_mux_failure_never_publishes(package, tmp_path, monkeypatch):
    def failed(command, directory):
        raise RuntimeError("simulated mux failure")

    monkeypatch.setattr(compose_foreground, "_run_ffmpeg", failed)
    target = tmp_path / "mux_fail"
    with pytest.raises(RuntimeError):
        compose_foreground.run_compose(package["plan"], target)
    assert not (target / "foreground.mp4").exists()
    assert json.loads((target / "foreground_run.json").read_text())["status"] == "FAILED"


def test_explicit_empty_non_dialogue_foreground_is_permitted(package, tmp_path):
    # The receipt declaration is intentionally not interpreted as semantic truth.
    report = compose_foreground.run_compose(package["plan"], tmp_path / "empty")
    assert report["producer_receipt"]["semantic_validation"] == "DECLARED_NOT_CHECKED"


def test_caller_rendered_dynamic_endcard_sequence(package, tmp_path):
    sequence = tmp_path / "endcard_sequence"
    sequence.mkdir()
    png(sequence / "frame_000000.png", "color=c=black@1.0")
    png(sequence / "frame_000001.png", "color=c=green@1.0")
    package["document"]["endcard"] = {
        "kind": "sequence", "directory": str(sequence), "pattern": "frame_%06d.png",
        "start_frame": 6, "end_frame": 8,
    }
    package["plan"].write_text(json.dumps(package["document"]))
    output_dir = tmp_path / "dynamic"
    compose_foreground.run_compose(package["plan"], output_dir)
    assert max(pixel(output_dir / "foreground.mp4", 6, 2, 2)) < 25
    assert pixel(output_dir / "foreground.mp4", 7, 2, 2)[1] > 70
