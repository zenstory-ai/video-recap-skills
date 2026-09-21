"""Actual-audio regression: the selected TTS wav must reach the consumed mix."""

import array
import math
from pathlib import Path
import shutil
import subprocess
import sys
import wave

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills/video-assemble/scripts"
sys.path.insert(0, str(SCRIPTS))

import assemble  # noqa: E402
from lib import CONFIG  # noqa: E402
from tts_fixtures import tts_segment

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg/ffprobe required for actual audio regression",
)


def _tone(path, frequency):
    pcm = array.array("h", (
        int(6000 * math.sin(2 * math.pi * frequency * sample / 44100))
        for sample in range(44100)
    ))
    if sys.byteorder != "little":
        pcm.byteswap()
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 44100, 44100, "NONE", "not compressed"))
        audio.writeframes(pcm.tobytes())


@pytest.fixture
def media(tmp_path, monkeypatch):
    for key, value in {
        "narration_speed": 1.0, "narration_tighten": False,
        "narration_delay_seconds": 0.0, "narration_tail_pad_seconds": 0.0,
        "fade_ms": 0.0, "burn_subtitles": False, "export_jianying": False,
        "mask_source_subtitles": False, "subtitle_original_in_gaps": False,
        "output_max_height": 0, "bgm_path": "", "final_loudnorm": False,
        "speech_ducking_volume": 0.0, "idle_orig_volume": 0.0,
    }.items():
        monkeypatch.setitem(CONFIG, key, value)
    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-y",
        "-f", "lavfi", "-i", "color=black:s=160x120:r=24:d=2",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
        "-t", "2", "-c:v", "libx264", "-threads", "2", "-c:a", "aac",
        str(source),
    ], check=True, capture_output=True)
    old, intended = tmp_path / "old.wav", tmp_path / "intended.wav"
    _tone(old, 330)
    _tone(intended, 997)
    work = tmp_path / "work"
    work.mkdir()
    return source, old, intended, work


def _segment(audio_path):
    return tts_segment(
        index=0, start=0.25, end=1.75,
        narration="offline audio test", spoken_text="offline audio test",
        audio_path=str(audio_path), audio_duration=1.0,
        pause_after_ms=0, overlaps_speech=False,
    )


def _amplitudes(video):
    raw = subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", str(video), "-ss", "0.4", "-t", "0.5",
        "-vn", "-ac", "1", "-ar", "44100", "-f", "s16le", "-",
    ])
    pcm = array.array("h", raw)
    if sys.byteorder != "little":
        pcm.byteswap()
    result = {}
    for frequency in (330, 997):
        real = sum(value * math.cos(2 * math.pi * frequency * n / 44100)
                   for n, value in enumerate(pcm))
        imaginary = sum(value * math.sin(2 * math.pi * frequency * n / 44100)
                        for n, value in enumerate(pcm))
        result[frequency] = math.hypot(real, imaginary) / len(pcm)
    return result


def test_selected_wav_reaches_actual_final_audio(media):
    source, _old, intended, work = media
    output = work / "output.mp4"
    assemble.assemble_video(source, [_segment(intended)], work, output)
    magnitudes = _amplitudes(output)
    assert magnitudes[997] > 100 * magnitudes[330], magnitudes
