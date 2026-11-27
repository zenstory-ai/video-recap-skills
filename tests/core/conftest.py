"""Shared pytest fixtures for tests/core (isolated skill import path + synthetic media builders)."""

from __future__ import annotations

import math
import os
import shutil
import struct
import subprocess
import sys
import wave
import zlib
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "skills" / "video-recap" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def write_sine_wav(path: Path, duration_s: float = 0.8, freq_hz: float = 440.0, amplitude: float = 0.25, rate: int = 48000) -> Path:
    """Create a real 16-bit PCM mono WAV file using stdlib only."""
    n_frames = max(1, int(round(duration_s * rate)))
    buf = bytearray()
    for i in range(n_frames):
        val = int(round(32767 * amplitude * math.sin(2.0 * math.pi * freq_hz * i / rate)))
        buf.extend(struct.pack("<h", max(-32768, min(32767, val))))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(bytes(buf))
    return path


def write_solid_png(path: Path, width: int = 64, height: int = 32, rgba: tuple[int, int, int, int] = (255, 200, 40, 220)) -> Path:
    """Write a minimal valid RGBA PNG using stdlib zlib + struct."""
    def _chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    raw_rows = b"".join(b"\x00" + bytes(rgba) * width for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(raw_rows)) + _chunk(b"IEND", b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)
    return path


def ffmpeg_required() -> bool:
    raw = os.environ.get("RECAP_REQUIRE_FFMPEG", "").strip().lower()
    return raw not in {"", "0", "false", "no"}


@pytest.fixture(scope="session")
def has_ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


@pytest.fixture(scope="session")
def synthetic_media(tmp_path_factory: pytest.TempPathFactory, has_ffmpeg: bool) -> dict[str, Path]:
    if not has_ffmpeg:
        if ffmpeg_required():
            pytest.fail("RECAP_REQUIRE_FFMPEG is set, but ffmpeg/ffprobe are unavailable")
        pytest.skip("ffmpeg/ffprobe unavailable")
    root = tmp_path_factory.mktemp("core_media")
    v1 = root / "clip_landscape.mp4"
    v2 = root / "clip_portrait_silent.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=2.0",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2.0",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(v1),
    ], check=True)
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "smptebars=size=240x320:rate=30:duration=1.5",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(v2),
    ], check=True)
    narr = write_sine_wav(root / "narr.wav", duration_s=0.9, freq_hz=520.0, amplitude=0.35)
    bgm = write_sine_wav(root / "bgm.wav", duration_s=3.5, freq_hz=220.0, amplitude=0.18)
    sfx = write_sine_wav(root / "sfx.wav", duration_s=0.25, freq_hz=880.0, amplitude=0.4)
    overlay = write_solid_png(root / "plate.png", width=120, height=40)
    return {"v1": v1, "v2": v2, "narr": narr, "bgm": bgm, "sfx": sfx, "overlay": overlay}
