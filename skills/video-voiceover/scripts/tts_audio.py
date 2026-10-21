"""Loudness normalization for synthesized TTS blocks (stdlib `wave` + `array` only)."""
import array
import math
import os
import sys
import wave
from pathlib import Path

from lib import CONFIG


def _normalize_tts_wav_rms(input_wav, output_wav, *, target_rms_dbfs=-20.0, peak_limit=0.98):
    """Normalize a mono/stereo 16-bit WAV to a target RMS with peak guard."""
    input_wav = Path(input_wav)
    output_wav = Path(output_wav)
    with wave.open(str(input_wav), "rb") as wf:
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        frames = wf.getnframes()
        data = wf.readframes(frames)
    if sampwidth != 2:
        raise ValueError(f"仅支持 16-bit PCM WAV: {input_wav}")

    # array('h') decodes the whole block in C, once.
    samples = array.array("h")
    samples.frombytes(data)
    if sys.byteorder != "little":  # wave data is little-endian regardless of host
        samples.byteswap()
    if not samples:
        # A silent block has no RMS to normalize toward; copy it through with neutral
        # metadata rather than dividing by a zero sample count.
        output_wav.write_bytes(input_wav.read_bytes())
        return {
            "rms_dbfs_before": None,
            "rms_dbfs_after": None,
            "peak_after": 0.0,
            "gain_db": 0.0,
        }
    count = len(samples)
    rms = math.sqrt(sum(s * s for s in samples) / count)
    peak = max(max(samples), -min(samples)) / 32768.0
    rms_dbfs_before = 20 * math.log10(max(rms, 1e-9) / 32768.0)
    target_linear = 10 ** (target_rms_dbfs / 20.0) * 32768.0
    gain = target_linear / max(rms, 1e-9)
    if peak > 0:
        gain = min(gain, peak_limit / peak)

    # Accumulate the output statistics while normalizing instead of decoding the result back.
    normalized = array.array("h", bytes(2 * count))
    out_square_sum = 0
    out_peak_raw = 0
    for i in range(count):
        value = max(-32768, min(32767, int(round(samples[i] * gain))))
        normalized[i] = value
        out_square_sum += value * value
        out_peak_raw = max(out_peak_raw, abs(value))
    out_bytes = normalized
    if sys.byteorder != "little":
        out_bytes = array.array("h", normalized)
        out_bytes.byteswap()
    with wave.open(str(output_wav), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(framerate)
        wf.writeframes(out_bytes.tobytes())

    out_rms = math.sqrt(out_square_sum / count)
    return {
        "rms_dbfs_before": rms_dbfs_before,
        "rms_dbfs_after": 20 * math.log10(max(out_rms, 1e-9) / 32768.0),
        "peak_after": out_peak_raw / 32768.0,
        "gain_db": 20 * math.log10(max(gain, 1e-9)),
    }


def _maybe_normalize_tts_wav(output_wav):
    """Normalize a synthesized TTS block in-place; None when normalization is disabled."""
    if not CONFIG["tts_segment_normalize"]:
        return None
    output_wav = Path(output_wav)
    tmp = output_wav.with_name(f"{output_wav.stem}_norm{output_wav.suffix}")
    meta = _normalize_tts_wav_rms(
        output_wav,
        tmp,
        target_rms_dbfs=CONFIG["tts_segment_target_rms_dbfs"],
        peak_limit=CONFIG["tts_segment_peak_limit"],
    )
    os.replace(tmp, output_wav)
    return meta
