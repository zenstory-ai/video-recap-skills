"""Real-media tests for the standalone source/score bed producer."""

import array
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import wave

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills/video-assemble/scripts"
sys.path.insert(0, str(SCRIPTS))
import source_score


pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg/ffprobe required for source/score producer tests",
)


def run(*args, check=True):
    return subprocess.run(list(map(str, args)), capture_output=True, text=True, check=check)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tone(path, frequency, seconds, rate=44_100):
    frames = round(seconds * rate)
    pcm = array.array("h", (
        int(12_000 * math.sin(2 * math.pi * frequency * sample / rate))
        for sample in range(frames)
    ))
    if sys.byteorder != "little":
        pcm.byteswap()
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, rate, frames, "NONE", "not compressed"))
        output.writeframes(pcm.tobytes())
    return path


def pcm(path):
    raw = subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-acodec",
        "pcm_f32le", "-ar", "48000", "-ac", "2", "-",
    ])
    return array.array("f", raw)


def constant_pcm(path, value, seconds):
    run("ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        f"aevalsrc={value}|{value}:s=48000:d={seconds}", "-c:a", "pcm_f32le", path)
    return path


def magnitude(samples, frequency, start_frame, frame_count):
    mono = [(samples[2 * frame] + samples[2 * frame + 1]) / 2
            for frame in range(start_frame, start_frame + frame_count)]
    real = sum(value * math.cos(2 * math.pi * frequency * n / 48_000)
               for n, value in enumerate(mono))
    imag = sum(value * math.sin(2 * math.pi * frequency * n / 48_000)
               for n, value in enumerate(mono))
    return math.hypot(real, imag) / len(mono)


@pytest.fixture
def plan(tmp_path):
    first = tone(tmp_path / "first.wav", 440, 1)
    second = tone(tmp_path / "second.wav", 880, 1)
    joined = tmp_path / "joined.wav"
    run("ffmpeg", "-v", "error", "-y", "-i", first, "-i", second,
        "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1[out]", "-map", "[out]",
        "-c:a", "pcm_s16le", joined)
    video = tmp_path / "source.mkv"
    run("ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=black:size=64x48:rate=4:duration=2", "-i", joined,
        "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-threads", "2",
        "-c:a", "pcm_s16le", video)
    score = tone(tmp_path / "score.wav", 220, 3)
    document = {
        "artifact": "source_score_plan", "schema_version": 1,
        "output": {"sample_rate": 48_000, "channels": 2, "total_samples": 96_000},
        "source_segments": [
            {"id": "second-first", "path": str(video), "sha256": sha(video),
             "audio_stream": 0, "source_fps": "4/1", "source_start_frame": 4,
             "source_end_frame": 8, "output_start_sample": 0, "gain": 1.0,
             "fade_in_samples": 0, "fade_out_samples": 0, "fade_shape": "linear",
             "role": "protected_original"},
            {"id": "first-low", "path": str(video), "sha256": sha(video),
             "audio_stream": 0, "source_fps": "4/1", "source_start_frame": 0,
             "source_end_frame": 4, "output_start_sample": 48_000, "gain": 0.25,
             "fade_in_samples": 0, "fade_out_samples": 0, "fade_shape": "linear",
             "role": "mixed_original_under_narration"},
        ],
        "source_silence": [],
        "score": {"kind": "raw", "path": str(score), "sha256": sha(score),
                  "audio_stream": 0, "source_offset_sample": 24_000, "gain": 0.1,
                  "fade_in_samples": 4_800, "fade_out_samples": 4_800,
                  "fade_shape": "half_cosine"},
    }
    path = tmp_path / "source_score.json"
    path.write_text(json.dumps(document))
    return path, document, video, score


def test_real_reorder_gain_continuous_score_and_receipt(plan, tmp_path):
    path, _document, _video, _score = plan
    output = tmp_path / "prepared"
    receipt = source_score.prepare_source_score(path, output)
    source = pcm(output / "source_bed.wav")
    score = pcm(output / "score_bed.wav")
    prepared = pcm(output / "prepared_bed.wav")
    assert magnitude(source, 880, 8_000, 24_000) > 30 * magnitude(source, 440, 8_000, 24_000)
    high = magnitude(source, 880, 8_000, 24_000)
    low = magnitude(source, 440, 56_000, 24_000)
    assert low == pytest.approx(high * 0.25, rel=0.08)
    # A single score decode/playhead spans the picture join; it is not restarted there.
    assert magnitude(score, 220, 43_000, 10_000) > 0.003
    assert prepared != source and prepared != score
    assert receipt["artifact"] == "prepared_bed_receipt"
    assert receipt["status"] == "PREPARED"
    assert receipt["direct_listening"] == "NOT_CHECKED"
    assert receipt["release_approved"] is False
    for name in ("source_bed.wav", "score_bed.wav", "prepared_bed.wav"):
        assert receipt["outputs"][name]["sha256"] == sha(output / name)
        assert receipt["outputs"][name]["pcm"]["codec_name"] == "pcm_f32le"
        assert receipt["outputs"][name]["finite"] is True
    assert receipt["outputs"]["prepared_bed.wav"]["headroom_policy"] == \
        "FLOAT_PRESERVED_NO_MASTER"


def test_no_score_keeps_reordered_faded_source_payload_and_writes_zero_score(
    plan, tmp_path
):
    path, document, _video, _score = plan
    for segment in document["source_segments"]:
        segment["fade_in_samples"] = 1_440
        segment["fade_out_samples"] = 1_440
    document["score"] = {"kind": "none"}
    path.write_text(json.dumps(document))

    output = tmp_path / "none"
    receipt = source_score.prepare_source_score(path, output)
    source_identity = source_score._output_identity(output / "source_bed.wav")
    prepared_identity = source_score._output_identity(output / "prepared_bed.wav")
    score = pcm(output / "score_bed.wav")

    assert receipt["score"] == {"kind": "none"}
    assert source_identity["pcm"]["samples"] == 96_000
    assert prepared_identity["pcm_payload_sha256"] == source_identity["pcm_payload_sha256"]
    assert len(score) == 96_000 * 2
    assert all(sample == 0.0 for sample in score)


def test_no_score_rejects_unknown_fields(plan, tmp_path):
    path, document, _video, _score = plan
    document["score"] = {"kind": "none", "path": "invented.wav"}
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="none score requires exactly fields"):
        source_score.prepare_source_score(path, tmp_path / "invalid-none")


def test_raw_half_cosine_fades_clamp_outside_their_windows(plan, tmp_path):
    path, document, _video, _score = plan
    score = constant_pcm(tmp_path / "constant.wav", 0.5, 3)
    document["score"].update(path=str(score), sha256=sha(score), gain=0.1)
    path.write_text(json.dumps(document))
    source_score.prepare_source_score(path, tmp_path / "half-cosine")
    samples = pcm(tmp_path / "half-cosine/score_bed.wav")
    left = [samples[2 * index] for index in (8_000, 24_000, 48_000, 72_000, 88_000)]
    assert left == pytest.approx([0.05] * len(left), abs=2e-6)
    assert samples[0] == pytest.approx(0.0, abs=1e-7)
    assert samples[2 * 4_799] == pytest.approx(0.05, abs=2e-6)
    assert samples[2 * 95_999] == pytest.approx(0.0, abs=1e-7)


def test_decoded_source_must_cover_every_selected_sample(plan, tmp_path):
    path, document, _video, _score = plan
    short = tone(tmp_path / "short.wav", 440, 1)
    video = tmp_path / "short-audio.mkv"
    run("ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=black:size=64x48:rate=4:duration=2", "-i", short,
        "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-threads", "2",
        "-c:a", "pcm_s16le", video)
    for segment in document["source_segments"]:
        segment.update(path=str(video), sha256=sha(video))
    path.write_text(json.dumps(document))
    output = tmp_path / "short-source"
    with pytest.raises(ValueError, match="too short"):
        source_score.prepare_source_score(path, output)
    assert not (output / "prepared_bed.wav").exists()


@pytest.mark.parametrize("changed", ["decoded", "source_bed", "score_bed"])
def test_derived_assets_are_sealed_across_consumption(plan, tmp_path, monkeypatch, changed):
    path, _document, _source, _score = plan
    original = source_score._run

    def intercepted(command, directory, label):
        result = original(command, directory, label)
        targets = {
            "decoded": ("source", directory / ".source_000.decoded.wav"),
            "source_bed": ("prepare", directory / ".source_bed.rendering.wav"),
            "score_bed": ("prepare", directory / ".score_bed.rendering.wav"),
        }
        trigger, target = targets[changed]
        if label == trigger:
            target.write_bytes(target.read_bytes() + b"changed")
        return result

    monkeypatch.setattr(source_score, "_run", intercepted)
    output = tmp_path / f"mutated-{changed}"
    with pytest.raises(ValueError, match="changed"):
        source_score.prepare_source_score(path, output)
    assert not (output / "prepared_bed.wav").exists()
    assert not (output / "prepared_bed_receipt.json").exists()


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(extra=True),
    lambda d: d["source_segments"][0].update(source_fps="3/1"),
    lambda d: d["source_segments"][0].update(source_start_frame=3),
    lambda d: d["source_segments"][1].update(output_start_sample=47_999),
    lambda d: d["source_segments"][0].update(gain=-1),
    lambda d: d["source_segments"][0].update(role="dialogue"),
    lambda d: d["score"].update(loop=True),
])
def test_invalid_semantics_never_publish(plan, tmp_path, mutation):
    path, document, _video, _score = plan
    mutation(document)
    path.write_text(json.dumps(document))
    output = tmp_path / "invalid"
    with pytest.raises((ValueError, RuntimeError)):
        source_score.prepare_source_score(path, output)
    assert not (output / "prepared_bed.wav").exists()
    assert not (output / "prepared_bed_receipt.json").exists()


@pytest.mark.parametrize("changed", ["plan", "source", "score", "ffmpeg"])
def test_toctou_or_ffmpeg_failure_never_publish(plan, tmp_path, monkeypatch, changed):
    path, _document, source, score = plan
    original = source_score._run

    def intercepted(command, directory, label):
        if changed == "ffmpeg" and label == "prepare":
            raise RuntimeError("injected ffmpeg failure")
        result = original(command, directory, label)
        selected = {"plan": path, "source": source, "score": score}.get(changed)
        if selected and label == "prepare":
            selected.write_bytes(selected.read_bytes() + b"changed")
        return result

    monkeypatch.setattr(source_score, "_run", intercepted)
    output = tmp_path / changed
    with pytest.raises((ValueError, RuntimeError)):
        source_score.prepare_source_score(path, output)
    assert not (output / "prepared_bed.wav").exists()
    assert not (output / "prepared_bed_receipt.json").exists()


def test_isolated_copied_skill_cli_and_existing_target(plan, tmp_path):
    path, _document, _source, _score = plan
    copied = tmp_path / "installed"
    shutil.copytree(SCRIPTS, copied)
    target = tmp_path / "cli"
    launcher = ("import runpy,sys;p=sys.argv.pop(1);sys.path.insert(0,p);"
                "sys.argv[0]=p+'/source_score.py';runpy.run_path(sys.argv[0],run_name='__main__')")
    result = run(sys.executable, "-I", "-c", launcher, copied, path,
                 "--output-dir", target)
    assert json.loads(result.stdout)["status"] == "PREPARED"
    with pytest.raises(FileExistsError):
        source_score.prepare_source_score(path, target)


def test_accepts_ntsc_frame_clock_and_rounds_sample_bounds_consistently(tmp_path):
    """30000/1001 frames do not land on whole 48 kHz samples; bounds still line up."""
    audio = tone(tmp_path / "ntsc.wav", 440, 2)
    video = tmp_path / "ntsc.mp4"
    run("ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=black:size=64x48:rate=30000/1001", "-i", audio, "-map", "0:v",
        "-map", "1:a", "-frames:v", "30", "-c:v", "libx264", "-threads", "2",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-video_track_timescale", "30000",
        "-shortest", video)

    fps = Fraction(30_000, 1_001)
    total = source_score.frame_clock_samples(30, fps)
    assert total == 48_048
    document = {
        "artifact": "source_score_plan", "schema_version": 1,
        "output": {"sample_rate": 48_000, "channels": 2, "total_samples": total},
        "source_segments": [
            {"id": "ntsc", "path": str(video), "sha256": sha(video),
             "audio_stream": 0, "source_fps": "30000/1001", "source_start_frame": 1,
             "source_end_frame": 29, "output_start_sample": 0, "gain": 1.0,
             "fade_in_samples": 0, "fade_out_samples": 0, "fade_shape": "linear",
             "role": "protected_original"},
        ],
        "source_silence": [],
        "score": {"kind": "none"},
    }
    segment_samples = (source_score.frame_clock_samples(29, fps)
                       - source_score.frame_clock_samples(1, fps))
    document["source_silence"] = [
        {"output_start_sample": segment_samples, "output_end_sample": total,
         "role": "silence"},
    ]
    path = tmp_path / "ntsc_plan.json"
    path.write_text(json.dumps(document))

    loaded = source_score.load_plan(path)

    bound = loaded["source_segments"][0]
    # 1 frame = 1601.6 samples; each boundary rounds once and durations follow.
    assert bound["source_start_sample"] == 1_602
    assert bound["source_end_sample"] == 46_446
    assert bound["output_end_sample"] == segment_samples == 44_844
