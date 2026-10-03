"""Delivered true peak: the AAC file, not the PCM mix, stays under TP, and QC says so."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "video-assemble" / "scripts"))

import assembly_contract  # noqa: E402
import codec_peak  # noqa: E402
import loudness  # noqa: E402
from lib import CONFIG  # noqa: E402

_HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
_DELIVERY = {"video_encode_passes": 1, "reencode_reason": [], "audio_sample_rate": 48000,
             "final_compat_notes": []}


@pytest.fixture(autouse=True)
def _targets(monkeypatch):
    monkeypatch.setitem(CONFIG, "final_loudnorm", True)
    monkeypatch.setitem(CONFIG, "target_lufs", -14.0)
    monkeypatch.setitem(CONFIG, "target_true_peak", -1.0)
    monkeypatch.setitem(CONFIG, "target_lra", 11.0)
    monkeypatch.setitem(CONFIG, "loudness_limiter_max_db", 6.0)


def _deliver(monkeypatch, peaks):
    """Run the correction loop over delivered true-peak readings; return (record, state, targets)."""
    readings = iter(peaks)
    monkeypatch.setattr(codec_peak, "delivered_loudness", lambda _path: (
        None if (peak := next(readings)) is None else {"integrated": -14.0, "true_peak": peak}))
    targets = []

    def reencode(target):
        targets.append(target)
        return f"state at {target}"

    record, state = codec_peak.deliver_under_true_peak("output.mp4", -1.5, reencode)
    return record, state, targets


def test_overshoot_lowers_the_pre_encode_target_by_the_measured_amount(monkeypatch):
    # +0.31 dBTP delivered (the release run): 1.31 dB over, plus the 0.1 dB margin.
    record, state, targets = _deliver(monkeypatch, [0.31, -0.95, -1.05])
    assert targets == [-2.91, -3.06]
    assert state == "state at -3.06"
    assert record == {"integrated": -14.0, "true_peak": -1.05, "peak_target_dbtp": -3.06,
                      "corrections": 2}


@pytest.mark.parametrize("peaks, expected_peak", [([-1.2], -1.2), ([None], None)],
                         ids=["under-true-peak", "unmeasured"])
def test_no_correction_when_the_file_is_under_or_unmeasured(monkeypatch, peaks, expected_peak):
    record, state, targets = _deliver(monkeypatch, peaks)
    assert targets == [] and state is None
    assert record == {"integrated": None if expected_peak is None else -14.0,
                      "true_peak": expected_peak, "peak_target_dbtp": -1.5, "corrections": 0}


def test_corrections_stop_at_the_maximum_and_assembly_qc_blocks(monkeypatch):
    record, _state, targets = _deliver(monkeypatch, [0.5, -0.5, -0.8])
    assert len(targets) == codec_peak.CODEC_PEAK_MAX_CORRECTIONS
    assert record["true_peak"] == -0.8

    def qc(delivered):
        return assembly_contract._build_assembly_qc(
            [], 3.0, audio_operations={}, render_delivery=_DELIVERY, audio_mode="source-mix",
            loudnorm_final_pass={"delivered": delivered},
        )

    assert qc(record)["blocking_codes"] == ["delivered_true_peak_over_target"]
    assert qc({**record, "true_peak": -1.0})["verdict"] == "PASS"
    assert qc({**record, "true_peak": None})["verdict"] == "PASS"
    assert qc(None)["verdict"] == "PASS"


# ── real ffmpeg ─────────────────────────────────────────────────────────────────────


def _ffmpeg(*args):
    return subprocess.run(["ffmpeg", "-hide_banner", "-y", "-v", "error", *map(str, args)],
                          check=True, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def _video_md5(path):
    """MD5 of the video stream's packets: equal only when the picture was stream-copied."""
    return _ffmpeg("-i", path, "-map", "0:v:0", "-c", "copy", "-f", "md5", "-").stdout.strip()


def _ebur128_true_peak(path):
    text = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-map", "0:a:0",
         "-af", "ebur128=peak=true:framelog=quiet", "-f", "null", "-"],
        check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stderr.split("Summary:")[-1]
    return loudness._loudnorm_summary_value(text, "Peak")


@pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg/ffprobe not available")
def test_real_aac_overshoot_is_corrected_under_true_peak(tmp_path):
    """Square-wave bursts over a quiet bed: the limited PCM mix sits under -1.5 dBTP, but
    AAC rings the bursts well over TP. The audio is encoded again lower, the picture
    is copied untouched, and the delivered file measures under -1 dBTP."""
    source = tmp_path / "source.mkv"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=s=160x120:r=25:d=8",
        "-f", "lavfi", "-i",
        "aevalsrc='if(lt(mod(t,4),0.03),0.99*sgn(sin(2*PI*1000*t)),0.15*sin(2*PI*300*t))'"
        ":s=48000:d=8,aformat=channel_layouts=stereo",
        "-c:v", "mpeg4", "-c:a", "flac", source,
    )
    graph = "[0:a]anull[aout]"
    inputs = ["-i", str(source)]
    target = loudness.first_render_peak_target()
    measured, limiter = loudness.plan_final_loudness(
        source, source, [], [], graph, tmp_path, peak_target=target
    )
    output = tmp_path / "output.mp4"
    _ffmpeg(*inputs, "-filter_complex",
            f"{graph};[aout]{loudness.final_loudnorm_filter(measured, limiter, target)}[aoutln]",
            "-map", "0:v:0", "-map", "[aoutln]", "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", output)
    picture = _video_md5(output)
    assert codec_peak.delivered_loudness(output)["true_peak"] > -1.0

    def reencode(peak_target):
        stage = loudness.plan_final_loudness(
            source, source, [], [], graph, tmp_path, peak_target=peak_target, measured=measured
        )
        stderr = codec_peak.reencode_audio_track(
            output, inputs,
            f"{graph};[aout]{loudness.final_loudnorm_filter(*stage, peak_target)}[aoutln]",
            "[aoutln]", 8.0, tmp_path,
        )
        return (stderr, *stage)

    record, state = codec_peak.deliver_under_true_peak(output, target, reencode)
    assert record["corrections"] >= 1 and state is not None
    assert record["true_peak"] <= -1.0
    assert _ebur128_true_peak(output) <= -1.0
    assert record["peak_target_dbtp"] < target
    assert loudness.loudnorm_final_pass(state[0], *state[1:])["normalization_type"] == "linear"
    assert _video_md5(output) == picture
    assert not list(tmp_path.glob(".output.audio-reencode*"))
