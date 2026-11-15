"""Loudness normalization and a duration sanity bound for synthesized TTS blocks.

Split out of voiceover.py to keep that module within the bundle's per-module line
budget. Dependency-free on purpose (stdlib `wave` + `array` only) so QC and assembly
can reuse the returned metadata without pulling in the synthesis path.
"""
import array
import math
import os
import re
import struct
import sys
import wave
from pathlib import Path

from lib import CONFIG


def _parse_riff_wav_chunks(raw_bytes, input_wav):
    """Parse RIFF/WAVE fmt + data chunks for PCM (tag 1) or IEEE float (tag 3)."""
    if len(raw_bytes) < 44 or raw_bytes[:4] != b"RIFF" or raw_bytes[8:12] != b"WAVE":
        raise ValueError(f"无效 WAV 文件: {input_wav}")
    offset = 12
    fmt = None
    data = None
    while offset + 8 <= len(raw_bytes):
        chunk_id = raw_bytes[offset:offset + 4]
        chunk_size = struct.unpack_from("<I", raw_bytes, offset + 4)[0]
        body_start = offset + 8
        body_end = min(len(raw_bytes), body_start + chunk_size)
        if chunk_id == b"fmt " and body_end - body_start >= 16:
            fmt_tag, channels, framerate, _, block_align, bits = struct.unpack_from(
                "<HHIIHH", raw_bytes, body_start
            )
            if fmt_tag == 0xFFFE and body_end - body_start >= 26:
                fmt_tag = struct.unpack_from("<H", raw_bytes, body_start + 24)[0]
            fmt = (fmt_tag, channels, framerate, block_align, bits)
        elif chunk_id == b"data":
            data = raw_bytes[body_start:body_end]
            break
        offset = body_start + chunk_size + (chunk_size & 1)
    if fmt is None or data is None:
        raise ValueError(f"WAV 缺少 fmt 或 data 块: {input_wav}")
    if fmt[1] < 1 or fmt[2] < 1:
        raise ValueError(f"无效 WAV 声道数或采样率 (channels={fmt[1]}, rate={fmt[2]}): {input_wav}")
    return fmt, data


def _decode_wav_samples_16bit_scale(input_wav):
    """Return (channels, framerate, is_pcm16, samples_in_int16_scale)."""
    raw_bytes = input_wav.read_bytes()
    (fmt_tag, channels, framerate, _block_align, bits), data = _parse_riff_wav_chunks(
        raw_bytes, input_wav
    )
    is_pcm16 = False
    if fmt_tag == 1 and bits == 16:
        samples = array.array("h")
        usable = len(data) - (len(data) % 2)
        samples.frombytes(data[:usable])
        if sys.byteorder != "little":
            samples.byteswap()
        is_pcm16 = True
    elif fmt_tag == 1 and bits == 8:
        samples = [(b - 128) * 256.0 for b in data]
    elif fmt_tag == 1 and bits == 24:
        usable = len(data) - (len(data) % 3)
        samples = [
            int.from_bytes(data[i:i + 3], "little", signed=True) / 256.0
            for i in range(0, usable, 3)
        ]
    elif fmt_tag == 1 and bits == 32:
        usable = len(data) - (len(data) % 4)
        ints = array.array("i")
        ints.frombytes(data[:usable])
        if sys.byteorder != "little":
            ints.byteswap()
        samples = [v / 65536.0 for v in ints]
    elif fmt_tag == 3 and bits in (32, 64):
        step = bits // 8
        fmt_code = "<f" if bits == 32 else "<d"
        usable = len(data) - (len(data) % step)
        samples = []
        for (s,) in struct.iter_unpack(fmt_code, data[:usable]):
            if not math.isfinite(s):
                s = 0.0
            samples.append(int(round(max(-1.0, min(1.0, s)) * 32767.0)))
    else:
        raise ValueError(f"不支持的 WAV 编码 (format={fmt_tag}, bits={bits}): {input_wav}")

    aligned = (len(samples) // channels) * channels
    if aligned < len(samples):
        samples = samples[:aligned]
        is_pcm16 = False
    return channels, framerate, is_pcm16, samples


_decode_wav_samples_to_i16 = _decode_wav_samples_16bit_scale


def _normalize_tts_wav_rms(input_wav, output_wav, *, target_rms_dbfs=-20.0, peak_limit=0.98):
    """Normalize a PCM (8/16/24/32-bit) or IEEE-float WAV to 16-bit PCM at target RMS.

    This helper is intentionally dependency-free so QC/assembly can reuse the
    returned metadata even when normalization is applied in a later lane.
    """
    input_wav = Path(input_wav)
    output_wav = Path(output_wav)
    channels, framerate, is_pcm16, samples = _decode_wav_samples_16bit_scale(input_wav)
    if not samples:
        # A silent block has no RMS to normalize toward; copy 16-bit WAV through verbatim
        # (or write an empty 16-bit PCM WAV) rather than dividing by a zero sample count.
        if is_pcm16:
            output_wav.write_bytes(input_wav.read_bytes())
        else:
            with wave.open(str(output_wav), "wb") as wf:
                wf.setnchannels(channels)
                wf.setsampwidth(2)
                wf.setframerate(framerate)
                wf.writeframes(b"")
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
        wf.setsampwidth(2)
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


# Speech units for the duration bound: a CJK character or a digit (half or full width, read
# one by one as in 二〇二六) is one syllable, "%" is three (百分之), a Latin word about one and
# a half. Pause marks get a fixed allowance each; "……" is a long pause.
_CJK_OR_DIGIT = re.compile(r"[\u3007\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff0-9\uff10-\uff19]")
_PERCENT = re.compile(r"[%\uff05]")
_PERCENT_UNITS = 3
_LATIN_WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_LONG_PAUSE = re.compile(r"……|\.\.\.|——")
_PAUSE_MARK = re.compile(r"[，。！？；：、,.!?;:]")
TTS_EDGE_SILENCE_SECONDS = 1.5
TTS_PAUSE_SECONDS = 0.4
TTS_LONG_PAUSE_SECONDS = 0.8


def speech_units(text):
    """Syllable-like units a faithful reading of `text` speaks (pauses not included)."""
    rest = _LONG_PAUSE.sub("", text)
    return (len(_CJK_OR_DIGIT.findall(rest)) + _PERCENT_UNITS * len(_PERCENT.findall(rest))
            + 1.5 * len(_LATIN_WORD.findall(rest)))


def max_plausible_tts_seconds(text):
    """Longest a faithful reading of `text` can last, or None when the bound is disabled.

    Reading every unit at TTS_MIN_SPEECH_RATE (default 2.5 units/s; MiMo's median is ~3.5
    CJK chars/s) plus generous pause and edge-silence allowances. Audio past this is the
    provider reading more than the text: MiMo TTS has returned a sentence followed by
    invented speech at 1.17x this bound, while the slowest of ~100 faithful MiMo segments
    from real runs reached 0.86x."""
    min_rate = CONFIG["tts_min_speech_rate"]
    if min_rate <= 0:
        return None
    long_pauses = len(_LONG_PAUSE.findall(text))
    rest = _LONG_PAUSE.sub("", text)
    return (TTS_EDGE_SILENCE_SECONDS + speech_units(text) / min_rate
            + TTS_PAUSE_SECONDS * len(_PAUSE_MARK.findall(rest))
            + TTS_LONG_PAUSE_SECONDS * long_pauses)


def implausible_tts_duration(text, duration):
    """A user-facing reason when `duration` is too long to be a reading of `text`, else None."""
    limit = max_plausible_tts_seconds(text)
    if limit is None or duration <= limit:
        return None
    return (f"音频 {duration:.1f}s 超过这段文字（{len(text)} 字）的合理上限 {limit:.1f}s，"
            "疑似 TTS 多读了原稿以外的内容（幻读），这段音频不会被缓存或交付")


def rejected_take_path(output_wav):
    """Where the latest take rejected by the bound is kept for listening."""
    output_wav = Path(output_wav)
    return output_wav.with_name(f"{output_wav.stem}.rejected{output_wav.suffix}")


def rejected_take_hint(rejected):
    """Where the last rejected take is, and how to accept such takes; empty when none was kept."""
    if not Path(rejected).is_file():
        return ""
    return (f"；最后一次被拒的音频保留在 {rejected}，试听确认确实只读了原稿时，"
            "可调低 TTS_MIN_SPEECH_RATE（0 关闭检查）后重跑")
