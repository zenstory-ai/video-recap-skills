"""A narration tone must reach the final mix, and the run leaves no input record behind."""

import array
import math
from pathlib import Path
import shutil
import subprocess
import sys
import wave

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills/video-recap/scripts"
sys.path.insert(0, str(SCRIPTS))

import assemble  # noqa: E402
from lib import CONFIG  # noqa: E402
from tts_fixtures import tts_segment


HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _tone(path, frequency=997, *, seconds=1.0):
    sample_rate = 44100
    frames = int(sample_rate * seconds)
    pcm = array.array(
        "h",
        (
            int(6000 * math.sin(2 * math.pi * frequency * sample / sample_rate))
            for sample in range(frames)
        ),
    )
    if sys.byteorder != "little":
        pcm.byteswap()
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, sample_rate, frames, "NONE", "not compressed"))
        audio.writeframes(pcm.tobytes())
    return path


def _segment(audio_path, *, seconds=1.0):
    return tts_segment(
        index=0,
        start=0.25,
        end=1.75,
        narration="identity fixture",
        spoken_text="identity fixture",
        audio_path=str(audio_path),
        audio_duration=seconds,
        pause_after_ms=0,
        overlaps_speech=False,
        tts_rate_offset=0.0,
    )


@pytest.fixture
def render_media(tmp_path, monkeypatch):
    if not HAVE_FFMPEG:
        pytest.skip("ffmpeg/ffprobe required for the full narration chain")
    for key, value in {
        "narration_speed": 1.15,
        "tts_segment_tempo_max": 1.2,
        "narration_cumulative_tempo_max": 1.3,
        "narration_cumulative_tempo_hard_max": 1.4,
        "fade_ms": 0.0,
        "burn_subtitles": False,
        "export_jianying": False,
        "mask_source_subtitles": False,
        "subtitle_original_in_gaps": False,
        "output_max_height": 0,
        "bgm_path": "",
        "final_loudnorm": False,
        "speech_ducking_volume": 0.0,
        "idle_orig_volume": 0.0,
    }.items():
        monkeypatch.setitem(CONFIG, key, value)
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-v", "error", "-y",
            "-f", "lavfi", "-i", "color=black:s=160x120:r=24:d=2",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
            "-t", "2", "-c:v", "libx264", "-threads", "2", "-c:a", "aac",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    work = tmp_path / "work"
    work.mkdir()
    return source, work


def _frequency_magnitudes(video):
    raw = subprocess.check_output(
        [
            "ffmpeg", "-v", "error", "-i", str(video), "-ss", "0.4", "-t", "0.5",
            "-vn", "-ac", "1", "-ar", "44100", "-f", "s16le", "-",
        ]
    )
    pcm = array.array("h", raw)
    if sys.byteorder != "little":
        pcm.byteswap()
    result = {}
    for frequency in (330, 997):
        real = sum(
            value * math.cos(2 * math.pi * frequency * sample / 44100)
            for sample, value in enumerate(pcm)
        )
        imaginary = sum(
            value * math.sin(2 * math.pi * frequency * sample / 44100)
            for sample, value in enumerate(pcm)
        )
        result[frequency] = math.hypot(real, imaginary) / len(pcm)
    return result


def _f32_stereo_tone(path, frequency=997, *, seconds=1.0):
    """IEEE-float WAV, which Python's wave module cannot read: FFmpeg must convert it."""
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
         "-i", f"sine=frequency={frequency}:sample_rate=48000:duration={seconds}",
         "-af", "volume=1.5", "-ac", "2", "-c:a", "pcm_f32le", str(path)],
        check=True, capture_output=True,
    )
    return path


@pytest.mark.parametrize(
    ("make_voice", "speed"), [(_tone, 1.15), (_f32_stereo_tone, 1.0)], ids=["pcm16_mono", "f32_stereo"],
)
def test_unadopted_narration_tone_is_audible_in_final_mix(
    render_media, tmp_path, monkeypatch, make_voice, speed
):
    source, work = render_media
    monkeypatch.setitem(CONFIG, "narration_speed", speed)
    output = work / "output.mp4"

    assemble.assemble_video(source, [_segment(make_voice(tmp_path / "voice.wav"))], work, output)

    magnitudes = _frequency_magnitudes(output)
    assert magnitudes[997] > 100 * magnitudes[330], magnitudes
    assert not (work / "narration_input_binding.json").exists()
