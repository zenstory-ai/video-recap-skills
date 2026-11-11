"""Narration tempo fitting and sample-accurate timeline WAV placement."""

import os
import wave
from pathlib import Path

from lib import CONFIG, get_video_duration, log, narration_tempo_budget, run_cmd

def _apply_narration_speed(tts_segments, work_dir):
    """Globally speed up narration audio via atempo (CONFIG['narration_speed']).

    MiMo TTS reads a touch slowly for short-form recaps; a 1.1-1.2x bump makes it
    snappier without the chipmunk effect. Rewrites each segment's audio_path/duration
    to the sped copy so the rest of assembly is unchanged. No-op at speed 1.0.
    """
    speed = CONFIG["narration_speed"]
    if abs(speed - 1.0) <= 1e-3:
        return
    done = 0
    for seg in tts_segments:
        src = seg["audio_path"]
        if not os.path.exists(src):
            continue  # reported as a skipped segment during placement
        out = str(Path(work_dir) / f"_spd_{seg['index']}.wav")
        res = run_cmd(["ffmpeg", "-y", "-i", src, "-filter:a", f"atempo={speed:.3f}",
                              "-ar", "44100", "-ac", "1", "-acodec", "pcm_s16le", out])
        if res.returncode != 0:
            raise RuntimeError(f"解说提速失败 {src}: {res.stderr}")
        seg["_unsped_audio_path"] = src
        seg["_applied_global_speed"] = speed
        seg["audio_path"] = out
        seg["audio_duration"] = get_video_duration(out)
        done += 1
    log(f"解说整体提速: atempo={speed:.2f} ({done} 段)")


def _atempo_filter_chain(factor):
    """Build an FFmpeg atempo chain (each stage within [0.5, 2.0]) for a single-pass stretch."""
    parts = []
    rem = float(factor)
    while rem > 2.0:
        parts.append("atempo=2.0")
        rem /= 2.0
    while rem < 0.5:
        parts.append("atempo=0.5")
        rem /= 0.5
    parts.append(f"atempo={rem:.6f}")
    return ",".join(parts)


def _adjust_tts_speed(
    audio_path,
    target_duration,
    tts_rate_offset=0.0,
    *,
    segment_index=None,
    raw_source_path=None,
    base_speed=1.0,
):
    """Fit overlong TTS with bounded atempo; never time-trim speech in assemble.

    Assemble has no word/sentence timestamps, so if bounded atempo cannot make the
    audio fit, it returns `fit_status=no_safe_fit` and leaves the original audio
    untouched for QC to block instead of guessing a spoken_text truncation.
    """
    audio_path = Path(audio_path)
    current_dur = get_video_duration(audio_path)
    budget = narration_tempo_budget(tts_rate_offset)
    meta = {"fit_status": "fits", "segment_tempo_factor": 1.0}
    if current_dur <= target_duration:
        return (str(audio_path), current_dur, meta)

    ratio = current_dur / target_duration
    effective_max = budget["segment_tempo_max"]
    if ratio > effective_max:
        meta.update({
            "fit_status": "no_safe_fit",
            "truncate_reason": "no_safe_boundary",
            "needed_tempo_factor": ratio,
        })
        label = "" if segment_index is None else f"段 {segment_index + 1} "
        log(
            f"  TTS 无安全放置: {label}{current_dur:.1f}s 需 x{ratio:.2f}，"
            f"超过段内预算 x{effective_max:.2f}（assemble 不按时间硬切）"
        )
        return (str(audio_path), current_dur, meta)

    # 温和加速。给 atempo/容器时长舍入留出 0.2% 安全余量；宁可极轻微
    # 多加速，也不能在写入时间线时裁掉最后一个音节。
    tempo = min(ratio * 1.002, effective_max)
    adjusted_path = audio_path.with_name(f"{audio_path.stem}_adj{audio_path.suffix}")
    use_single_pass = bool(raw_source_path and os.path.exists(raw_source_path) and base_speed > 0)
    encode_src = str(raw_source_path) if use_single_pass else str(audio_path)
    encode_tempo = (float(base_speed) * tempo) if use_single_pass else tempo
    cmd = ["ffmpeg", "-y", "-i", encode_src,
           "-filter:a", _atempo_filter_chain(encode_tempo),
           "-ar", "44100", "-ac", "1", str(adjusted_path)]
    result = run_cmd(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"TTS 加速失败 {audio_path}: {result.stderr}")
    new_dur = get_video_duration(adjusted_path)
    if new_dur > target_duration + (1.0 / 44100.0):
        adjusted_path.unlink(missing_ok=True)
        meta.update({
            "fit_status": "no_safe_fit",
            "truncate_reason": "no_safe_boundary",
            "needed_tempo_factor": new_dur / target_duration,
        })
        log(
            f"  TTS 加速后仍超出安全窗口 {new_dur - target_duration:.3f}s；"
            "禁止裁尾，交由 Agent 缩短/移动文本"
        )
        return (str(audio_path), current_dur, meta)
    meta.update({"fit_status": "tempo_adjusted", "segment_tempo_factor": tempo})
    log(f"  TTS 温和加速: {current_dur:.1f}s → {new_dur:.1f}s (x{tempo:.2f})")
    return (str(adjusted_path), new_dur, meta)


def _edge_quiet_samples(pcm16_mono, sample_count, *, from_start, threshold=260):
    """Count near-silent PCM16 samples at one edge of a mono buffer."""
    indices = range(sample_count) if from_start else range(sample_count - 1, -1, -1)
    quiet = 0
    for index in indices:
        offset = index * 2
        value = int.from_bytes(pcm16_mono[offset:offset + 2], "little", signed=True)
        if abs(value) > threshold:
            break
        quiet += 1
    return quiet


def _speech_safe_fade_lengths(pcm16_mono, sample_count, sample_rate, configured_ms):
    """Limit fades to edge silence so first/last syllables are never attenuated.

    When TTS has no measurable edge silence, retain only a 5ms anti-click ramp.
    """
    configured = min(int(max(0.0, float(configured_ms)) * sample_rate / 1000), sample_count // 4)
    if configured <= 0:
        return 0, 0
    anti_click = min(int(0.005 * sample_rate), configured)
    leading = _edge_quiet_samples(pcm16_mono, sample_count, from_start=True)
    trailing = _edge_quiet_samples(pcm16_mono, sample_count, from_start=False)
    return min(configured, max(anti_click, leading)), min(configured, max(anti_click, trailing))


def _unplaced(seg, at, fit_status, reason, *, blocking=False):
    """Record a segment that contributes no audio: a zero-width window at `at` seconds."""
    seg["actual_place_start"] = at
    seg["actual_place_end"] = at
    seg["placed_audio_duration"] = 0.0
    seg["fit_status"] = fit_status
    seg["truncate_reason"] = reason
    seg["blocking"] = blocking


def _build_timed_narration(
    tts_segments,
    output_wav,
    video_duration,
    work_dir,
):
    """将 TTS 片段按时间轴放置到一条与视频等长的音轨上"""
    sample_rate = 44100
    total_samples = int(video_duration * sample_rate)
    buffer = bytearray(total_samples * 2)
    last_written_end = 0  # 追踪已写入位置，防止重叠
    pause_samples = 0  # 这一段的 pause_after_ms（负值按 0），下一段据此留间隔

    for seg in tts_segments:
        wav_path = seg["audio_path"]
        prev_pause_samples, pause_samples = (
            pause_samples,
            max(0, int(seg["pause_after_ms"] * sample_rate / 1000)),
        )

        # 放置状态的起始值；只有实际加速或未放置才覆盖。缺 WAV 的段也要有，QC 按键直读。
        tts_rate_offset = seg["tts_rate_offset"]
        budget = narration_tempo_budget(tts_rate_offset)
        seg.update({
            "fit_status": "fits",
            "segment_tempo_factor": 1.0,
            "global_narration_speed": budget["global_narration_speed"],
            "effective_tempo": budget["global_narration_speed"] * budget["tts_rate_factor"],
            "blocking": False,
        })

        if not os.path.exists(wav_path):
            _unplaced(seg, seg["start"], "skipped", "missing_wav")
            continue

        tts_dur = seg["audio_duration"]

        start_sample = int(seg["start"] * sample_rate)
        end_boundary = int(min(seg["end"], video_duration) * sample_rate)

        # 段间间隔：使用前一段的 pause_after_ms（来自 narration.json）
        min_start_with_pause = last_written_end + prev_pause_samples
        actual_start = min(max(start_sample, min_start_with_pause), end_boundary)

        # 根据实际可用空间决定是否加速
        available = end_boundary - actual_start
        available_duration = max(available / sample_rate, 0)
        if tts_dur > available_duration > 0:
            wav_path, _actual_dur, fit_meta = _adjust_tts_speed(
                wav_path, available_duration, tts_rate_offset,
                segment_index=seg["index"],
                raw_source_path=seg.get("_unsped_audio_path"),
                base_speed=seg.get("_applied_global_speed", 1.0),
            )
            if fit_meta["fit_status"] == "no_safe_fit":
                if "needed_tempo_factor" in fit_meta:
                    seg["needed_tempo_factor"] = round(fit_meta["needed_tempo_factor"], 4)
                _unplaced(seg, actual_start / sample_rate, "no_safe_fit",
                          fit_meta["truncate_reason"], blocking=True)
                continue
            seg["fit_status"] = fit_meta["fit_status"]
            seg["segment_tempo_factor"] = fit_meta["segment_tempo_factor"]
            seg["effective_tempo"] *= fit_meta["segment_tempo_factor"]

        # 放在拟合之后判断：_adjust_tts_speed 的输出读回来就是 44100Hz mono 16bit，无需再转
        try:
            with wave.open(wav_path, "rb") as wf_check:
                needs_resample = (
                    wf_check.getnchannels(), wf_check.getsampwidth(), wf_check.getframerate()
                ) != (1, 2, sample_rate)
        except (wave.Error, EOFError):
            # Valid post-processed WAV may use IEEE float, which Python's wave
            # reader does not support. FFmpeg performs the explicit PCM conversion.
            needs_resample = True
        if needs_resample:
            tmp_path = str(Path(work_dir) / f"_rs_{seg['index']}.wav")
            rs_result = run_cmd(["ffmpeg", "-y", "-i", wav_path,
                                        "-ar", str(sample_rate), "-ac", "1",
                                        "-acodec", "pcm_s16le", tmp_path])
            if rs_result.returncode != 0:
                log(f"  跳过: 重采样失败 {wav_path}: {rs_result.stderr}")
                _unplaced(seg, seg["start"], "skipped", "resample_failed")
                continue
            wav_path = tmp_path

        with wave.open(wav_path, "rb") as wf:
            wf_data = bytearray(wf.readframes(wf.getnframes()))

        # 按场景边界裁剪
        write_samples = len(wf_data) // 2

        if write_samples <= 0 or available <= 0:
            log(f"  跳过: {seg['start']:.1f}s-{seg['end']:.1f}s (无空间)")
            _unplaced(seg, seg["start"], "no_safe_fit", "no_room", blocking=True)
            continue

        if write_samples > available:
            # No tolerance-based trimming: even a few milliseconds may contain a
            # consonant/vowel release. _adjust_tts_speed must produce a complete file
            # that fits; otherwise block and ask the Agent to shorten/move the block.
            over = (write_samples - available) / sample_rate
            log(f"  TTS 无安全放置: 段 {seg['index'] + 1} 超出可用窗口 {over:.3f}s；禁止裁尾，交由 QC 阻断")
            _unplaced(seg, actual_start / sample_rate, "no_safe_fit", "no_safe_boundary", blocking=True)
            continue

        # fade-in / fade-out
        fade_in_len, fade_out_len = _speech_safe_fade_lengths(
            wf_data, write_samples, sample_rate, CONFIG["fade_ms"]
        )
        for i in range(fade_in_len):
            gain = i / fade_in_len
            s = i * 2
            sample = int.from_bytes(wf_data[s:s+2], 'little', signed=True)
            sample = int(sample * gain)
            wf_data[s:s+2] = sample.to_bytes(2, 'little', signed=True)
        for i in range(fade_out_len):
            gain = 1.0 - i / fade_out_len
            s = (write_samples - 1 - i) * 2
            sample = int.from_bytes(wf_data[s:s+2], 'little', signed=True)
            sample = int(sample * gain)
            wf_data[s:s+2] = sample.to_bytes(2, 'little', signed=True)

        # Persist the exact complete per-beat PCM used by the canonical mix. Editable
        # exports must reference this file, not the longer pre-fit TTS input; otherwise
        # their timeline_end silently chops the final word even when ffmpeg is correct.
        placed_path = Path(work_dir) / f"_placed_{seg['index']:04d}.wav"
        with wave.open(str(placed_path), "wb") as placed_wav:
            placed_wav.setnchannels(1)
            placed_wav.setsampwidth(2)
            placed_wav.setframerate(sample_rate)
            placed_wav.writeframes(bytes(wf_data))
        seg["placed_audio_path"] = str(placed_path)

        buffer[actual_start * 2: actual_start * 2 + write_samples * 2] = wf_data
        seg["actual_place_start"] = actual_start / sample_rate
        seg["actual_place_end"] = (actual_start + write_samples) / sample_rate
        seg["placed_audio_duration"] = write_samples / sample_rate
        last_written_end = actual_start + write_samples

    with wave.open(str(output_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(buffer))

    if tts_segments and all(seg["fit_status"] == "skipped" for seg in tts_segments):
        output_wav.unlink(missing_ok=True)
        raise RuntimeError(
            f"全部 {len(tts_segments)} 段解说均被跳过或未能写入"
            f"（WAV 缺失或无可用时间；跳过 {len(tts_segments)} 段），"
            "已中止以避免生成无解说视频"
        )

    log(f"解说音轨: {video_duration:.1f}s, {len(tts_segments)} 段")
