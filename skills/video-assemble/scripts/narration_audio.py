"""Narration tempo fitting and sample-accurate timeline WAV placement."""

import os
import wave
from pathlib import Path

from lib import CONFIG, get_video_duration, log, narration_tempo_budget, run_cmd

def _apply_narration_speed(
    tts_segments,
    work_dir,
    *,
    command_runner=run_cmd,
    duration_probe=get_video_duration,
    logger=log,
    tempo_policy=None,
):
    """Globally speed up narration audio via atempo (CONFIG['narration_speed']).

    MiMo TTS reads a touch slowly for short-form recaps; a 1.1-1.2x bump makes it
    snappier without the chipmunk effect. Rewrites each segment's audio_path/duration
    to the sped copy so the rest of assembly is unchanged. No-op at speed 1.0.
    """
    speed = tempo_policy["global_atempo"] if tempo_policy else CONFIG["narration_speed"]
    if abs(speed - 1.0) <= 1e-3:
        return
    done = 0
    for seg in tts_segments:
        src = seg["audio_path"]
        if not os.path.exists(src):
            continue  # reported as a skipped segment during placement
        out = str(Path(work_dir) / f"_spd_{seg['index']}.wav")
        res = command_runner(["ffmpeg", "-y", "-i", src, "-filter:a", f"atempo={speed:.3f}",
                              "-ar", "44100", "-ac", "1", "-acodec", "pcm_s16le", out])
        if res.returncode != 0:
            raise RuntimeError(f"解说提速失败 {src}: {res.stderr}")
        seg["audio_path"] = out
        seg["narration_conversion_path"] = out
        seg["audio_duration"] = duration_probe(out)
        done += 1
    logger(f"解说整体提速: atempo={speed:.2f} ({done} 段)")


def _adjust_tts_speed(
    audio_path,
    target_duration,
    tts_rate_offset=0.0,
    *,
    command_runner=run_cmd,
    duration_probe=get_video_duration,
    logger=log,
    tempo_policy=None,
):
    """Fit overlong TTS with bounded atempo; never time-trim speech in assemble.

    Assemble has no word/sentence timestamps, so if bounded atempo cannot make the
    audio fit, it returns `fit_status=no_safe_fit` and leaves the original audio
    untouched for QC to block instead of guessing a spoken_text truncation.
    """
    audio_path = Path(audio_path)
    current_dur = duration_probe(audio_path)
    budget = narration_tempo_budget(tts_rate_offset)
    if tempo_policy:
        budget.update({
            "global_narration_speed": tempo_policy["global_atempo"],
            "tts_rate_factor": 1.0,
            "segment_tempo_max": tempo_policy["segment_tempo_max"],
            "cumulative_tempo_max": tempo_policy["cumulative_tempo_max"],
            "cumulative_tempo_hard_max": tempo_policy["cumulative_tempo_hard_max"],
        })
    meta = {
        "fit_status": "fits",
        "blocking": False,
        "tempo_factor": 1.0,
        "segment_tempo_factor": 1.0,
        "truncated": False,
        "truncate_reason": "none",
        "tts_rate_offset": float(tts_rate_offset),
        "audio_duration": current_dur,
        "placed_audio_duration": current_dur,
        "global_narration_speed": budget["global_narration_speed"],
        "effective_tempo": budget["global_narration_speed"] * budget["tts_rate_factor"],
        "cumulative_tempo_max": budget["cumulative_tempo_max"],
        "cumulative_tempo_hard_max": budget["cumulative_tempo_hard_max"],
    }
    if current_dur <= target_duration:
        return (str(audio_path), current_dur, meta)

    if tempo_policy and not tempo_policy["bounded_segment_fit"]:
        meta.update({
            "fit_status": "no_safe_fit", "blocking": True,
            "truncate_reason": "no_safe_boundary", "placed_audio_duration": 0.0,
            "needed_tempo_factor": current_dur / target_duration,
        })
        return (str(audio_path), current_dur, meta)

    ratio = current_dur / target_duration
    effective_max = budget["segment_tempo_max"]
    if ratio > effective_max:
        meta.update({
            "fit_status": "no_safe_fit",
            "blocking": True,
            "truncate_reason": "no_safe_boundary",
            "placed_audio_duration": 0.0,
            "needed_tempo_factor": ratio,
        })
        logger(
            f"  TTS 无安全放置: {current_dur:.1f}s 需 x{ratio:.2f}，"
            f"超过段内预算 x{effective_max:.2f}（assemble 不按时间硬切）"
        )
        return (str(audio_path), current_dur, meta)

    # 温和加速。给 atempo/容器时长舍入留出 0.2% 安全余量；宁可极轻微
    # 多加速，也不能在写入时间线时裁掉最后一个音节。
    tempo = min(ratio * 1.002, effective_max)
    adjusted_path = audio_path.with_name(f"{audio_path.stem}_adj{audio_path.suffix}")
    cmd = ["ffmpeg", "-y", "-i", str(audio_path),
           "-filter:a", f"atempo={tempo:.6f}",
           "-ar", "44100", "-ac", "1", str(adjusted_path)]
    result = command_runner(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"TTS 加速失败 {audio_path}: {result.stderr}")
    new_dur = duration_probe(adjusted_path)
    if new_dur > target_duration + (1.0 / 44100.0):
        adjusted_path.unlink(missing_ok=True)
        meta.update({
            "fit_status": "no_safe_fit",
            "blocking": True,
            "truncate_reason": "no_safe_boundary",
            "placed_audio_duration": 0.0,
            "needed_tempo_factor": new_dur / target_duration,
        })
        logger(
            f"  TTS 加速后仍超出安全窗口 {new_dur - target_duration:.3f}s；"
            "禁止裁尾，交由 Agent 缩短/移动文本"
        )
        return (str(audio_path), current_dur, meta)
    meta.update({
        "fit_status": "tempo_adjusted",
        "tempo_factor": tempo,
        "segment_tempo_factor": tempo,
        "placed_audio_duration": new_dur,
        "effective_tempo": budget["global_narration_speed"] * budget["tts_rate_factor"] * tempo,
    })
    logger(f"  TTS 温和加速: {current_dur:.1f}s → {new_dur:.1f}s (x{tempo:.2f})")
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
    *,
    adjust_speed=_adjust_tts_speed,
    command_runner=run_cmd,
    logger=log,
    tempo_policy=None,
):
    """将 TTS 片段按时间轴放置到一条与视频等长的音轨上"""
    sample_rate = 44100
    total_samples = int(video_duration * sample_rate)
    buffer = bytearray(total_samples * 2)
    last_written_end = 0  # 追踪已写入位置，防止重叠
    prev_pause_samples = 0  # 前一段的 pause_after_ms，控制段间间隔
    skipped_count = 0  # 因 WAV 缺失或无安全放置而被跳过的段数
    placed_count = 0  # 真正写入音频的段数；防止"成功"生成全静音旁白
    no_safe_fit_count = 0  # 超预算但不能安全截断；交由 QC/manifest 阻断
    prev_authored_end = None  # 上一段作者标注的结束时间，用于判断"段落"边界
    run_gap = CONFIG["narration_run_gap_seconds"]   # 作者留白 > 此值 = 新段落
    tighten = CONFIG["narration_tighten"]
    tight_pause_samples = int(CONFIG["narration_tight_pause_seconds"] * sample_rate)
    # 漂移上限：收紧时一句最多比作者标注的时间提前 max_pull 秒，避免整段解说被全部压到前面、与画面脱节
    max_pull_samples = int(CONFIG["narration_max_pull_seconds"] * sample_rate)
    configured_delay = CONFIG["narration_delay_seconds"]
    tail_pad = CONFIG["narration_tail_pad_seconds"]

    for seg in tts_segments:
        wav_path = seg["audio_path"]
        pause_samples = int(seg.get("pause_after_ms", CONFIG["breath_ms"]) * sample_rate / 1000)
        # 段落收紧：同一段落内（与上一句作者留白 <= run_gap）把这一句紧贴上一句的实际收尾播放，
        # 句间间隔固定为 tight_pause，不受 slot 内居中延迟 / TTS 时长波动影响。段落之间（作者特意留
        # 的大留白，让精彩原声透出）才放回原声。这样句间间隔稳定、不会出现"一句解说一段空白"。
        cur_authored_start = float(seg["start"])
        is_run_start = (placed_count == 0 or prev_authored_end is None
                        or cur_authored_start - prev_authored_end > run_gap)
        prev_authored_end = float(seg["end"])

        if not os.path.exists(wav_path):
            _unplaced(seg, seg["start"], "skipped", "missing_wav")
            prev_pause_samples = pause_samples
            skipped_count += 1
            continue

        original_wav_path = wav_path
        try:
            with wave.open(wav_path, "rb") as wf_check:
                needs_resample = (
                    wf_check.getnchannels(), wf_check.getsampwidth(), wf_check.getframerate()
                ) != (1, 2, sample_rate)
        except (wave.Error, EOFError):
            # Valid post-processed WAV may use IEEE float, which Python's wave
            # reader does not support. FFmpeg performs the explicit PCM conversion.
            needs_resample = True

        tts_rate_offset = seg.get("tts_rate_offset", 0.0)
        tts_dur = seg["audio_duration"]

        slot_duration = max(0.0, float(seg["end"]) - float(seg["start"]))
        max_delay = max(0.0, slot_duration - tts_dur - tail_pad)
        narration_delay = min(configured_delay, max_delay)
        start_sample = int((seg["start"] + narration_delay) * sample_rate)
        end_boundary = int(min(seg["end"], video_duration) * sample_rate)

        # 段间间隔：使用前一段的 pause_after_ms（来自 narration.json）
        min_start_with_pause = last_written_end + prev_pause_samples
        if tighten and not is_run_start:
            # 段落内：紧贴上一句的实际收尾播放，句间间隔固定为 tight_pause（不被 slot 内居中延迟撑大），
            # 但不早于"作者标注起始 - max_pull"，防止整段被压到前面与画面脱节。
            drift_floor = int(cur_authored_start * sample_rate) - max_pull_samples
            actual_start = max(last_written_end + tight_pause_samples, drift_floor)
        else:
            # 段落起点（或关闭收紧）：尊重作者标注的起始 + 入场延迟，让画面/原声先立住
            actual_start = max(start_sample, min_start_with_pause)
        actual_start = min(actual_start, end_boundary)  # 不超出 slot 边界

        # 根据实际可用空间决定是否加速
        available_samples = end_boundary - actual_start
        available_duration = max(available_samples / sample_rate, 0)
        if tts_dur > available_duration > 0:
            if tempo_policy:
                wav_path, _actual_dur, fit_meta = adjust_speed(
                    wav_path, available_duration, tts_rate_offset,
                    tempo_policy=tempo_policy,
                )
            else:
                wav_path, _actual_dur, fit_meta = adjust_speed(
                    wav_path, available_duration, tts_rate_offset
                )
            seg.update({
                "fit_status": fit_meta["fit_status"],
                "segment_tempo_factor": fit_meta["segment_tempo_factor"],
                "effective_tempo": fit_meta["effective_tempo"],
                "global_narration_speed": fit_meta["global_narration_speed"],
                "blocking": fit_meta["blocking"],
            })
            if fit_meta["fit_status"] == "no_safe_fit":
                _unplaced(seg, actual_start / sample_rate, "no_safe_fit",
                          fit_meta["truncate_reason"], blocking=True)
                prev_pause_samples = pause_samples
                skipped_count += 1
                no_safe_fit_count += 1
                continue
        else:
            budget = narration_tempo_budget(tts_rate_offset)
            if tempo_policy:
                budget.update({
                    "global_narration_speed": tempo_policy["global_atempo"],
                    "tts_rate_factor": 1.0,
                })
            seg.update({
                "fit_status": "fits",
                "segment_tempo_factor": 1.0,
                "global_narration_speed": budget["global_narration_speed"],
                "effective_tempo": budget["global_narration_speed"] * budget["tts_rate_factor"],
                "blocking": False,
            })

        # _adjust_tts_speed 输出固定 44100Hz mono 16bit，若文件被替换则无需 resample
        if wav_path != original_wav_path:
            needs_resample = False
        if needs_resample:
            tmp_path = str(Path(work_dir) / f"_rs_{seg['index']}.wav")
            rs_result = command_runner(["ffmpeg", "-y", "-i", wav_path,
                                        "-ar", str(sample_rate), "-ac", "1",
                                        "-acodec", "pcm_s16le", tmp_path])
            if rs_result.returncode != 0:
                logger(f"  跳过: 重采样失败 {wav_path}: {rs_result.stderr}")
                _unplaced(seg, seg["start"], "skipped", "resample_failed")
                prev_pause_samples = pause_samples
                skipped_count += 1
                continue
            wav_path = tmp_path
            seg["narration_conversion_path"] = tmp_path

        with wave.open(wav_path, "rb") as wf:
            wf_data = bytearray(wf.readframes(wf.getnframes()))

        # 按场景边界裁剪
        audio_samples = len(wf_data) // 2
        available = end_boundary - actual_start
        write_samples = audio_samples

        if write_samples <= 0 or available <= 0:
            logger(f"  跳过: {seg['start']:.1f}s-{seg['end']:.1f}s (无空间)")
            _unplaced(seg, seg["start"], "no_safe_fit", "no_room", blocking=True)
            prev_pause_samples = pause_samples
            no_safe_fit_count += 1
            continue

        if audio_samples > available:
            # No tolerance-based trimming: even a few milliseconds may contain a
            # consonant/vowel release. _adjust_tts_speed must produce a complete file
            # that fits; otherwise block and ask the Agent to shorten/move the block.
            over = (audio_samples - available) / sample_rate
            logger(f"  TTS 无安全放置: 段 {seg['index']} 超出可用窗口 {over:.3f}s；禁止裁尾，交由 QC 阻断")
            _unplaced(seg, actual_start / sample_rate, "no_safe_fit", "no_safe_boundary", blocking=True)
            prev_pause_samples = pause_samples
            skipped_count += 1
            no_safe_fit_count += 1
            continue

        # 重叠检测：跳过与前段重叠的部分（在 fade 之前，避免截断后丢失 fade-in）
        if actual_start < last_written_end:
            overlap_ms = (last_written_end - actual_start) * 1000 / sample_rate
            if last_written_end >= actual_start + write_samples:
                logger(f"  跳过重叠段: {actual_start/sample_rate:.1f}s "
                       f"(与前段重叠 {overlap_ms:.0f}ms)")
                _unplaced(seg, seg["start"], "no_safe_fit", "no_room", blocking=True)
                prev_pause_samples = pause_samples
                no_safe_fit_count += 1
                continue
            actual_start = last_written_end
            available = end_boundary - actual_start
            if write_samples > available:
                logger(f"  重叠 {overlap_ms:.0f}ms 后无安全完整窗口，跳过")
                _unplaced(seg, actual_start / sample_rate, "no_safe_fit", "no_safe_boundary", blocking=True)
                prev_pause_samples = pause_samples
                skipped_count += 1
                no_safe_fit_count += 1
                continue

        # fade-in / fade-out（在 overlap 裁剪之后应用，确保正确的音频包络）
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
            if s < 0:
                break
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
        prev_pause_samples = pause_samples
        placed_count += 1

    with wave.open(str(output_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(buffer))

    if tts_segments and placed_count == 0 and no_safe_fit_count == 0:
        output_wav.unlink(missing_ok=True)
        raise RuntimeError(
            f"全部 {len(tts_segments)} 段解说均被跳过或未能写入"
            f"（WAV 缺失或无可用时间；跳过 {skipped_count} 段），"
            "已中止以避免生成无解说视频"
        )

    logger(f"解说音轨: {video_duration:.1f}s, {len(tts_segments)} 段")
