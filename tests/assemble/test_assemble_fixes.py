"""Regression tests for assemble.py graceful-degradation fixes (bugs 6 & 7)."""

import shutil
import sys
import wave
from pathlib import Path
from subprocess import CompletedProcess

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "video-recap" / "scripts"))

import narration_audio  # noqa: E402
from audio_mix import _seg_place_window  # noqa: E402
from subtitles.core import _subtitle_entries  # noqa: E402
from tts_fixtures import tts_segment


def _write_wav(path, sample_rate=44100, duration=0.8, channels=1, sampwidth=2):
    sample_count = int(sample_rate * duration)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\0" * (sample_count * channels * sampwidth))
    return path


def test_resample_failure_degrades_instead_of_raising(monkeypatch, tmp_path):
    """Bug 6: a failed resample must drop the segment gracefully, not raise."""
    # WAV at a non-target sample rate forces the resample branch.
    wav = _write_wav(tmp_path / "narr.wav", sample_rate=48000, duration=0.8)

    def fake_run_cmd(cmd):
        # Simulate ffmpeg resample failure: non-zero rc and no output file written.
        return CompletedProcess(cmd, 1, stdout="", stderr="resample boom")

    monkeypatch.setattr(narration_audio, "run_cmd", fake_run_cmd)

    segment = tts_segment(
        index=0,
        start=0.0,
        end=2.0,
        narration="需要重采样的解说。",
        audio_path=str(wav),
        audio_duration=0.8,
    )

    out = tmp_path / "out.wav"
    # Must fail cleanly instead of leaving a reusable silent narration track.
    with pytest.raises(RuntimeError, match="全部 1 段解说均被跳过"):
        narration_audio._build_timed_narration([segment], out, 3.0, tmp_path)

    assert segment["actual_place_start"] == segment["start"]
    assert segment["actual_place_end"] == segment["start"]
    assert not out.exists()


def test_missing_wav_skip_sets_zero_width_window(monkeypatch, tmp_path):
    """Bug 7: a missing-wav skip sets actual_place_start==end so it is dropped."""
    segment = tts_segment(
        index=0,
        start=1.0,
        end=3.0,
        narration="音频丢失的解说。",
        audio_path=str(tmp_path / "does_not_exist.wav"),
        audio_duration=0.8,
    )

    out = tmp_path / "out.wav"
    with pytest.raises(RuntimeError, match="全部 1 段解说均被跳过"):
        narration_audio._build_timed_narration([segment], out, 4.0, tmp_path)

    assert segment["actual_place_start"] == segment["start"]
    assert segment["actual_place_end"] == segment["start"]

    # _subtitle_entries must exclude it (end - start < 0.1).
    assert _subtitle_entries([segment]) == []

    # _seg_place_window yields a zero-width window -> no ducking over silence.
    s, e = _seg_place_window(segment)
    assert e - s < 0.1
    assert not out.exists()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not available")
def test_noncanonical_wav_is_resampled_and_placed(monkeypatch, tmp_path):
    """A stereo WAV is normalized at the external ffmpeg boundary and then placed.

    Unlike its siblings this drives the real resample, so it needs a real ffmpeg."""
    wav = _write_wav(tmp_path / "stereo.wav", sample_rate=44100, duration=0.8, channels=2)

    segment = tts_segment(
        index=0,
        start=1.0,
        end=3.0,
        narration="格式错误的解说。",
        audio_path=str(wav),
        audio_duration=0.8,
    )

    out = tmp_path / "out.wav"
    narration_audio._build_timed_narration([segment], out, 4.0, tmp_path)

    assert segment["actual_place_start"] == segment["start"]
    assert segment["actual_place_end"] == pytest.approx(1.8, abs=0.02)
    assert _subtitle_entries([segment])
    s, e = _seg_place_window(segment)
    assert e - s == pytest.approx(0.8, abs=0.02)
    assert out.exists()


def test_partial_skip_does_not_log_all_skipped_warning(monkeypatch, tmp_path):
    """Bug 7: a placed segment alongside a skipped one must NOT trigger the warning."""
    logs = []
    monkeypatch.setattr(narration_audio, "log", lambda msg: logs.append(msg))

    good = _write_wav(tmp_path / "good.wav", sample_rate=44100, duration=0.8)
    segments = [
        tts_segment(
            index=0,
            start=0.0,
            end=2.0,
            narration="正常解说。",
            audio_path=str(good),
            audio_duration=0.8,
        ),
        tts_segment(
            index=1,
            start=2.0,
            end=4.0,
            narration="缺失解说。",
            audio_path=str(tmp_path / "missing.wav"),
            audio_duration=0.8,
        ),
    ]

    out = tmp_path / "out.wav"
    narration_audio._build_timed_narration(segments, out, 5.0, tmp_path)

    assert not any("全部" in m and "跳过" in m for m in logs), logs

