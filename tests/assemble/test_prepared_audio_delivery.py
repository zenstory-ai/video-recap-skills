"""Real-media tests for explicit prepared-bed delivery without narration or score."""

import array
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills/video-assemble/scripts"
sys.path.insert(0, str(SCRIPTS))
from assemble_constants import frame_clock_samples
import audio_mix_binding
from frozen_audio import probe_audio_packets
from pair_media import probe_picture
import render_prepared_audio
import source_score


pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg/ffprobe required for prepared-audio delivery tests",
)


def run(*args, check=True):
    return subprocess.run(list(map(str, args)), capture_output=True, check=check)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def decoded(path):
    raw = subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0",
        "-f", "f32le", "-acodec", "pcm_f32le", "-ar", "48000", "-ac", "2", "-",
    ])
    values = array.array("f")
    values.frombytes(raw)
    return values


def write_float_wav(path, samples, *, amplitude=0.22):
    values = array.array("f")
    for index in range(samples):
        phase = 2 * math.pi * (173 * index / 48_000 + 1301 * index * index /
                               (2 * 48_000 * samples))
        left = amplitude * math.sin(phase) + (0.09 if index == 731 else 0)
        right = amplitude * 0.63 * math.cos(phase * 1.07) - \
            (0.07 if index == 1703 else 0)
        values.extend((left, right))
    raw = path.with_suffix(".f32")
    raw.write_bytes(values.tobytes())
    run("ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", "48000", "-ac", "2",
        "-i", raw, "-c:a", "pcm_f32le", path)
    return path


@pytest.fixture
def delivery_case(tmp_path):
    picture = tmp_path / "picture.mp4"
    run(
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=black:size=64x48:rate=24", "-frames:v", "32", "-an",
        "-c:v", "libx264", "-threads", "2", "-pix_fmt", "yuv420p", picture,
    )
    beds = tmp_path / "beds"
    beds.mkdir()
    prepared = write_float_wav(beds / "prepared_bed.wav", 64_000)
    shutil.copyfile(prepared, beds / "source_bed.wav")
    run("ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "anullsrc=r=48000:cl=stereo", "-af", "atrim=end_sample=64000",
        "-c:a", "pcm_f32le", beds / "score_bed.wav")
    identities = {
        name: source_score._output_identity(beds / name)
        for name in ("source_bed.wav", "score_bed.wav", "prepared_bed.wav")
    }
    receipt = beds / "prepared_bed_receipt.json"
    receipt.write_text(json.dumps({
        "artifact": "prepared_bed_receipt", "schema_version": 1,
        "status": "PREPARED",
        "format": {"sample_rate": 48000, "channels": 2,
                   "total_samples": 64000, "codec": "pcm_f32le"},
        "outputs": identities,
    }))
    adoption = tmp_path / "prepared_audio_adoption.json"
    document = {
        "artifact": "prepared_audio_adoption", "schema_version": 1,
        "picture": {"path": str(picture), "sha256": sha(picture)},
        "prepared_receipt": {"path": str(receipt), "sha256": sha(receipt)},
        "master_gain_db": 0,
    }
    adoption.write_text(json.dumps(document))
    return {
        "picture": picture, "beds": beds, "prepared": prepared,
        "receipt": receipt, "adoption": adoption, "document": document,
    }


def _mse(reference, candidate, lag, start=4000, count=1024):
    total = 0.0
    for frame in range(start, start + count):
        delta = reference[2 * frame] - candidate[2 * (frame + lag)]
        total += delta * delta
    return total / count


def test_fractional_picture_all_samples_negative_priming_and_chirp_lag_zero(
    delivery_case, tmp_path
):
    target = tmp_path / "rendered"
    report = render_prepared_audio.render_prepared_audio(
        delivery_case["adoption"], target
    )
    output = target / "prepared_audio.mp4"
    picture = probe_picture(output)
    audio = probe_audio_packets(output, 0)

    assert report["status"] == "PREPARED_AUDIO_RENDERED"
    inner = Path(report["pair"]["hidden_staging"]) / "paired.mp4"
    inner_report = json.loads(
        (Path(report["pair"]["hidden_staging"]) / "pair_run.json").read_text()
    )
    assert inner.exists()
    assert inner_report["status"] == "PAIR_RENDERED"
    assert Path(inner_report["output"]["path"]) == inner
    assert inner_report["output"]["sha256"] == sha(inner) == sha(output)
    assert picture == probe_picture(delivery_case["picture"])
    assert picture["duration"] == "4/3"
    assert audio["start_time"] == "0.000000"
    assert Fraction(audio["packets"][0]["pts"]) < 0
    assert audio["packets"][0]["side_data_list"][0]["skip_samples"] == 1024
    assert report["aac"]["identity"]["packet_end"] == "4/3"
    assert report["master"]["pcm_payload_sha256"] == \
        report["prepared"]["prepared_bed.wav"]["pcm_payload_sha256"]
    assert report["aac_pcm_identity_claimed"] is False
    assert report["master"]["aac_true_peak"] == "NOT_MEASURED"
    commands = json.dumps(report["commands"])
    assert all(term not in commands for term in (
        "atempo", "sidechaincompress", "loudnorm", "alimiter", "apad",
        "-shortest", "make_zero",
    ))

    reference = decoded(delivery_case["prepared"])
    actual = decoded(output)
    lag_errors = {
        lag: _mse(reference, actual, lag) for lag in range(-1100, 1101)
    }
    assert min(lag_errors, key=lag_errors.get) == 0
    assert any(abs(actual[index] - actual[index + 1]) > 0.01
               for index in range(8000, 12000, 2))


def test_nonzero_master_gain_scales_complete_pcm(delivery_case, tmp_path):
    delivery_case["document"]["master_gain_db"] = -6
    delivery_case["adoption"].write_text(json.dumps(delivery_case["document"]))
    report = render_prepared_audio.render_prepared_audio(
        delivery_case["adoption"], tmp_path / "attenuated"
    )
    source = decoded(delivery_case["prepared"])
    master = decoded(report["master"]["path"])
    expected = 10 ** (-6 / 20)
    assert len(master) == len(source) == 128_000
    for index in (2, 731 * 2, 1703 * 2 + 1, 63_999 * 2):
        assert master[index] == pytest.approx(source[index] * expected, abs=2e-7)


@pytest.mark.parametrize(
    "gain", [True, 24.01, -24.01, float("inf"), float("nan")]
)
def test_invalid_gain_fails_before_encode(delivery_case, tmp_path, gain):
    delivery_case["document"]["master_gain_db"] = gain
    delivery_case["adoption"].write_text(json.dumps(delivery_case["document"]))
    target = tmp_path / "invalid"
    with pytest.raises(ValueError, match="master_gain_db"):
        render_prepared_audio.render_prepared_audio(delivery_case["adoption"], target)
    assert not (target / "prepared_audio.mp4").exists()
    assert not (target / ".prepared_audio_staging").exists()


def test_one_sample_receipt_mismatch_fails_before_encode(delivery_case, tmp_path):
    receipt = json.loads(delivery_case["receipt"].read_text())
    receipt["format"]["total_samples"] -= 1
    delivery_case["receipt"].write_text(json.dumps(receipt))
    delivery_case["document"]["prepared_receipt"]["sha256"] = sha(
        delivery_case["receipt"]
    )
    delivery_case["adoption"].write_text(json.dumps(delivery_case["document"]))
    target = tmp_path / "off-by-one"
    with pytest.raises(ValueError, match="format differs"):
        render_prepared_audio.render_prepared_audio(delivery_case["adoption"], target)
    assert not (target / ".prepared_audio_staging").exists()


@pytest.mark.parametrize(
    "changed", ["receipt_hash", "stem", "sample_rate", "channels", "codec", "extra"]
)
def test_invalid_adoption_or_receipt_binding_fails_before_encode(
    delivery_case, tmp_path, changed
):
    receipt = json.loads(delivery_case["receipt"].read_text())
    if changed == "receipt_hash":
        delivery_case["document"]["prepared_receipt"]["sha256"] = "0" * 64
    elif changed == "stem":
        receipt["outputs"]["wrong_bed.wav"] = receipt["outputs"].pop(
            "prepared_bed.wav"
        )
    elif changed in {"sample_rate", "channels", "codec"}:
        receipt["format"][changed] = {
            "sample_rate": 44_100, "channels": 1, "codec": "pcm_s16le"
        }[changed]
    else:
        delivery_case["document"]["narration"] = {"placeholder": True}
    if changed not in {"receipt_hash", "extra"}:
        delivery_case["receipt"].write_text(json.dumps(receipt))
        delivery_case["document"]["prepared_receipt"]["sha256"] = sha(
            delivery_case["receipt"]
        )
    delivery_case["adoption"].write_text(json.dumps(delivery_case["document"]))
    target = tmp_path / changed
    with pytest.raises(ValueError):
        render_prepared_audio.render_prepared_audio(delivery_case["adoption"], target)
    assert not (target / ".prepared_audio_staging").exists()
    assert not (target / "prepared_audio.mp4").exists()


def test_bed_that_misses_the_fractional_picture_clock_fails_before_encode(
    delivery_case, tmp_path
):
    """A 30000/1001 clock is supported; a bed cut for a different clock still is not."""
    picture = tmp_path / "fractional-clock.mp4"
    run(
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=black:size=64x48:rate=30000/1001", "-frames:v", "32", "-an",
        "-c:v", "libx264", "-threads", "2", "-pix_fmt", "yuv420p", picture,
    )
    # The picture clock is now exactly defined even though it is not whole samples.
    assert frame_clock_samples(32, Fraction(30_000, 1_001)) == 51_251
    delivery_case["document"]["picture"] = {
        "path": str(picture), "sha256": sha(picture)
    }
    delivery_case["adoption"].write_text(json.dumps(delivery_case["document"]))
    target = tmp_path / "mismatched-clock"
    # the fixture's bed is 64000 samples, cut for the 24 fps picture
    with pytest.raises(ValueError, match="picture clock"):
        render_prepared_audio.render_prepared_audio(delivery_case["adoption"], target)
    assert not (target / ".prepared_audio_staging").exists()


def test_nonzero_origin_prepared_stem_fails_before_encode(delivery_case, tmp_path):
    shifted = tmp_path / "shifted.nut"
    run(
        "ffmpeg", "-v", "error", "-y", "-itsoffset", "0.01", "-i",
        delivery_case["prepared"], "-map", "0:a:0", "-c:a", "pcm_f32le",
        "-copyts", "-avoid_negative_ts", "disabled", "-f", "nut", shifted,
    )
    shifted.replace(delivery_case["prepared"])
    receipt = json.loads(delivery_case["receipt"].read_text())
    receipt["outputs"]["prepared_bed.wav"]["sha256"] = sha(
        delivery_case["prepared"]
    )
    delivery_case["receipt"].write_text(json.dumps(receipt))
    delivery_case["document"]["prepared_receipt"]["sha256"] = sha(
        delivery_case["receipt"]
    )
    delivery_case["adoption"].write_text(json.dumps(delivery_case["document"]))
    target = tmp_path / "nonzero-origin"
    with pytest.raises(ValueError, match="zero-origin"):
        render_prepared_audio.render_prepared_audio(delivery_case["adoption"], target)
    assert not (target / ".prepared_audio_staging").exists()


@pytest.mark.parametrize("nonfinite", [float("nan"), float("inf")])
def test_actual_nonfinite_master_pcm_is_rejected(
    delivery_case, tmp_path, nonfinite
):
    values = array.array("f", [0.0] * 128_000)
    values[17] = nonfinite
    raw = tmp_path / "nonfinite.f32"
    raw.write_bytes(values.tobytes())
    run(
        "ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", "48000",
        "-ac", "2", "-i", raw, "-c:a", "pcm_f32le",
        delivery_case["prepared"],
    )
    receipt = json.loads(delivery_case["receipt"].read_text())
    receipt["outputs"]["prepared_bed.wav"]["sha256"] = sha(
        delivery_case["prepared"]
    )
    delivery_case["receipt"].write_text(json.dumps(receipt))
    delivery_case["document"]["prepared_receipt"]["sha256"] = sha(
        delivery_case["receipt"]
    )
    delivery_case["adoption"].write_text(json.dumps(delivery_case["document"]))
    target = tmp_path / "nonfinite"
    with pytest.raises(ValueError, match="non-finite"):
        render_prepared_audio.render_prepared_audio(delivery_case["adoption"], target)
    assert not (target / ".prepared_audio_staging").exists()


def test_make_zero_aac_rewrite_is_rejected(delivery_case, tmp_path, monkeypatch):
    original = render_prepared_audio._run

    def intercepted(command, directory, label, report, report_path):
        original(command, directory, label, report, report_path)
        if label == "aac":
            source = directory / "master.m4a"
            rewritten = directory / "make-zero.m4a"
            run(
                "ffmpeg", "-v", "error", "-y", "-i", source, "-map", "0:a:0",
                "-c", "copy", "-avoid_negative_ts", "make_zero", rewritten,
            )
            rewritten.replace(source)

    monkeypatch.setattr(render_prepared_audio, "_run", intercepted)
    target = tmp_path / "make-zero"
    with pytest.raises(ValueError, match="priming"):
        render_prepared_audio.render_prepared_audio(delivery_case["adoption"], target)
    assert not (target / ".prepared_audio_staging").exists()


def test_coarse_millisecond_aac_header_is_rejected(
    delivery_case, tmp_path, monkeypatch
):
    original = render_prepared_audio.probe_audio_packets

    def coarse_header(path, stream):
        identity = original(path, stream)
        identity["duration"] = "1.333"
        return identity

    monkeypatch.setattr(render_prepared_audio, "probe_audio_packets", coarse_header)
    target = tmp_path / "coarse-header"
    with pytest.raises(ValueError, match="packet clock|endpoint"):
        render_prepared_audio.render_prepared_audio(delivery_case["adoption"], target)
    assert not (target / ".prepared_audio_staging").exists()


def test_real_millisecond_movie_header_remux_is_rejected(
    delivery_case, tmp_path, monkeypatch
):
    original = render_prepared_audio._run

    def intercepted(command, directory, label, report, report_path):
        original(command, directory, label, report, report_path)
        if label == "aac":
            source = directory / "master.m4a"
            rewritten = directory / "millisecond-header.m4a"
            run(
                "ffmpeg", "-v", "error", "-y", "-i", source, "-map", "0:a:0",
                "-c", "copy", "-movie_timescale", "1000", rewritten,
            )
            rewritten.replace(source)

    monkeypatch.setattr(render_prepared_audio, "_run", intercepted)
    target = tmp_path / "millisecond-header"
    with pytest.raises(ValueError, match="packet clock|endpoint|movie header"):
        render_prepared_audio.render_prepared_audio(delivery_case["adoption"], target)
    assert not (target / "prepared_audio.mp4").exists()
    assert not (target / ".prepared_audio_staging").exists()
    assert json.loads((target / "prepared_audio_run.json").read_text())["status"] == \
        "FAILED"


@pytest.mark.parametrize("peak, succeeds", [(1.0, True), (1.0001, False)])
def test_exact_sample_peak_boundary(delivery_case, tmp_path, peak, succeeds):
    replacement = delivery_case["beds"] / "peak.wav"
    run(
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        f"aevalsrc={peak}|-{peak}:s=48000", "-af", "atrim=end_sample=64000",
        "-c:a", "pcm_f32le", replacement,
    )
    replacement.replace(delivery_case["prepared"])
    receipt = json.loads(delivery_case["receipt"].read_text())
    receipt["outputs"]["prepared_bed.wav"] = source_score._output_identity(
        delivery_case["prepared"]
    )
    delivery_case["receipt"].write_text(json.dumps(receipt))
    delivery_case["document"]["prepared_receipt"]["sha256"] = sha(
        delivery_case["receipt"]
    )
    delivery_case["adoption"].write_text(json.dumps(delivery_case["document"]))
    target = tmp_path / f"peak-{peak}"
    if succeeds:
        report = render_prepared_audio.render_prepared_audio(
            delivery_case["adoption"], target
        )
        assert report["master"]["sample_peak"] == pytest.approx(1.0)
    else:
        with pytest.raises(ValueError, match="sample peak exceeds 1"):
            render_prepared_audio.render_prepared_audio(
                delivery_case["adoption"], target
            )
        assert not (target / ".prepared_audio_staging").exists()


@pytest.mark.parametrize("changed", ["adoption", "receipt", "bed", "picture"])
def test_input_mutation_after_aac_consumption_cleans_all_candidates(
    delivery_case, tmp_path, monkeypatch, changed
):
    original = render_prepared_audio._run

    def intercepted(command, directory, label, report, report_path):
        result = original(command, directory, label, report, report_path)
        selected = {
            "adoption": delivery_case["adoption"],
            "receipt": delivery_case["receipt"],
            "bed": delivery_case["prepared"],
            "picture": delivery_case["picture"],
        }[changed]
        if label == "aac":
            selected.write_bytes(selected.read_bytes() + b"changed")
        return result

    monkeypatch.setattr(render_prepared_audio, "_run", intercepted)
    target = tmp_path / changed
    with pytest.raises(ValueError, match="changed"):
        render_prepared_audio.render_prepared_audio(delivery_case["adoption"], target)
    assert not (target / "prepared_audio.mp4").exists()
    assert not (target / ".prepared_audio_staging").exists()
    assert json.loads((target / "prepared_audio_run.json").read_text())["status"] == "FAILED"


def test_pair_failure_cleans_generated_master_aac_and_nested_output(
    delivery_case, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        render_prepared_audio.pair_media,
        "run_pair",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("pair failed")),
    )
    target = tmp_path / "pair-failure"
    with pytest.raises(RuntimeError, match="pair failed"):
        render_prepared_audio.render_prepared_audio(delivery_case["adoption"], target)
    assert not (target / "prepared_audio.mp4").exists()
    assert not (target / ".prepared_audio_staging").exists()


@pytest.mark.parametrize("changed", ["prepared", "picture", "master", "aac"])
def test_mutation_after_pair_before_publish_cleans_all_candidates(
    delivery_case, tmp_path, monkeypatch, changed
):
    original = render_prepared_audio.pair_media.run_pair

    def intercepted(*args, **kwargs):
        result = original(*args, **kwargs)
        if changed == "prepared":
            target = delivery_case["prepared"]
        elif changed == "picture":
            target = delivery_case["picture"]
        elif changed == "master":
            target = Path(args[0]).parent / "master.wav"
        else:
            target = Path(args[0]).parent / "master.m4a"
        target.write_bytes(target.read_bytes() + b"changed")
        return result

    monkeypatch.setattr(render_prepared_audio.pair_media, "run_pair", intercepted)
    target = tmp_path / f"post-pair-{changed}"
    with pytest.raises(ValueError, match="changed"):
        render_prepared_audio.render_prepared_audio(delivery_case["adoption"], target)
    assert not (target / "prepared_audio.mp4").exists()
    assert not (target / ".prepared_audio_staging").exists()


def test_nested_pair_mutation_after_audio_verification_fails_before_publish(
    delivery_case, tmp_path, monkeypatch
):
    original = render_prepared_audio.verify_adopted_audio

    def intercepted(input_path, output_path, input_stream, output_stream):
        result = original(input_path, output_path, input_stream, output_stream)
        Path(output_path).write_bytes(Path(output_path).read_bytes() + b"changed")
        return result

    monkeypatch.setattr(render_prepared_audio, "verify_adopted_audio", intercepted)
    target = tmp_path / "nested-mutated-after-verify"
    with pytest.raises(ValueError, match="paired output changed"):
        render_prepared_audio.render_prepared_audio(delivery_case["adoption"], target)
    assert not (target / "prepared_audio.mp4").exists()
    assert not (target / ".prepared_audio_staging").exists()
    assert json.loads((target / "prepared_audio_run.json").read_text())["status"] == \
        "FAILED"


def test_outer_publication_copy_mismatch_cleans_all_candidates(
    delivery_case, tmp_path, monkeypatch
):
    original = render_prepared_audio.shutil.copyfile

    def intercepted(source, destination):
        result = original(source, destination)
        if Path(destination).name == ".prepared_audio.rendering.mp4":
            Path(destination).write_bytes(Path(destination).read_bytes() + b"changed")
        return result

    monkeypatch.setattr(render_prepared_audio.shutil, "copyfile", intercepted)
    target = tmp_path / "outer-copy-mismatch"
    with pytest.raises(ValueError, match="published copy differs"):
        render_prepared_audio.render_prepared_audio(delivery_case["adoption"], target)
    assert not (target / ".prepared_audio.rendering.mp4").exists()
    assert not (target / "prepared_audio.mp4").exists()
    assert not (target / ".prepared_audio_staging").exists()


def test_isolated_copied_skill_cli_and_existing_target(delivery_case, tmp_path):
    copied = tmp_path / "installed"
    shutil.copytree(SCRIPTS, copied)
    target = tmp_path / "cli"
    launcher = (
        "import runpy,sys;p=sys.argv.pop(1);sys.path.insert(0,p);"
        "sys.argv[0]=p+'/render_prepared_audio.py';"
        "runpy.run_path(sys.argv[0],run_name='__main__')"
    )
    result = run(
        sys.executable, "-I", "-c", launcher, copied, delivery_case["adoption"],
        "--output-dir", target,
    )
    assert json.loads(result.stdout.splitlines()[-1])["status"] == \
        "PREPARED_AUDIO_RENDERED"
    with pytest.raises(FileExistsError):
        render_prepared_audio.render_prepared_audio(
            delivery_case["adoption"], target
        )


def test_ntsc_frame_count_not_divisible_by_five_shares_one_whole_picture_clock(tmp_path):
    """31 frames at 30000/1001 is 49649.6 samples; all three modules must round it alike."""
    picture = tmp_path / "ntsc.mp4"
    run("ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=black:size=64x48:rate=30000/1001", "-f", "lavfi", "-i",
        "sine=frequency=440:sample_rate=48000", "-map", "0:v", "-map", "1:a",
        "-frames:v", "31", "-c:v", "libx264", "-threads", "2", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-video_track_timescale", "30000", "-shortest", picture)

    expected = frame_clock_samples(31, Fraction(30_000, 1_001))
    assert expected == 49_650

    probed = probe_picture(picture)
    assert probed["frame_count"] == 31 and probed["fps"] == "30000/1001"

    # audio_mix_binding's adopted-mix clock
    mix_samples, _summary = audio_mix_binding._picture_format(probed)
    assert mix_samples == expected

    # source_score's plan clock over the very same frame range
    plan_path = tmp_path / "source_score.json"
    plan_path.write_text(json.dumps({
        "artifact": "source_score_plan", "schema_version": 1,
        "output": {"sample_rate": 48_000, "channels": 2, "total_samples": expected},
        "source_segments": [
            {"id": "whole", "path": str(picture), "sha256": sha(picture),
             "audio_stream": 0, "source_fps": "30000/1001", "source_start_frame": 0,
             "source_end_frame": 31, "output_start_sample": 0, "gain": 1.0,
             "fade_in_samples": 0, "fade_out_samples": 0, "fade_shape": "linear",
             "role": "protected_original"},
        ],
        "source_silence": [], "score": {"kind": "none"},
    }))
    loaded = source_score.load_plan(plan_path)
    assert loaded["source_segments"][0]["output_end_sample"] == expected

    # render_prepared_audio's delivery clock, through the strict adoption preflight
    beds = tmp_path / "beds"
    beds.mkdir()
    write_float_wav(beds / "prepared_bed.wav", expected)
    shutil.copyfile(beds / "prepared_bed.wav", beds / "source_bed.wav")
    run("ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "anullsrc=r=48000:cl=stereo", "-af", f"atrim=end_sample={expected}",
        "-c:a", "pcm_f32le", beds / "score_bed.wav")
    receipt = beds / "prepared_bed_receipt.json"
    receipt.write_text(json.dumps({
        "artifact": "prepared_bed_receipt", "schema_version": 1, "status": "PREPARED",
        "format": {"sample_rate": 48_000, "channels": 2, "total_samples": expected,
                   "codec": "pcm_f32le"},
        "outputs": {name: source_score._output_identity(beds / name)
                    for name in ("source_bed.wav", "score_bed.wav", "prepared_bed.wav")},
    }))
    adoption = tmp_path / "prepared_audio_adoption.json"
    adoption.write_text(json.dumps({
        "artifact": "prepared_audio_adoption", "schema_version": 1,
        "picture": {"path": str(picture), "sha256": sha(picture)},
        "prepared_receipt": {"path": str(receipt), "sha256": sha(receipt)},
        "master_gain_db": 0,
    }))

    assert render_prepared_audio.load_adoption(adoption)["format"] == {
        "sample_rate": 48_000, "channels": 2, "total_samples": expected,
    }
