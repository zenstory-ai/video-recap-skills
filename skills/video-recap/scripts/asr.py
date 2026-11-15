import array
import base64
import json
import math
import os
import re
import sys
import time
import wave
from pathlib import Path

from lib import CONFIG
from lib import log, run_cmd, get_video_duration, mimo_asr_api_call, is_moderation_refusal
from detect import _audio_meta_path, _write_audio_meta
from asr_timing_evidence import (
    EVIDENCE_FILENAME,
    write_asr_timing_evidence,
)

# ── Step 3: ASR 转录（MiMo mimo-v2.5-asr，云端 API）────────────────────────

_ASR_AUDIO_MIME = "audio/wav"


class ASRProviderError(RuntimeError):
    """Provider/API response failed, so an empty transcript is not cacheable success."""


def _is_no_audio_stream_error(stderr):
    text = str(stderr or "")
    return (
        "Output file does not contain any stream" in text
        or "matches no streams" in text
    )


def transcribe_audio(video_path, work_dir):
    """提取音频并用 MiMo ASR 分段转录，通过分段合成时间戳。"""
    work_dir = Path(work_dir)
    asr_file = work_dir / "asr_result.json"
    (work_dir / EVIDENCE_FILENAME).unlink(missing_ok=True)

    if not CONFIG["mimo_asr_api_key"]:
        key_name = CONFIG["mimo_asr_env_var"]
        log(f"ASR 跳过：未设置 {key_name}（MiMo ASR 需要；VLM/TTS 也需要同一个 key）。"
            f"如不需要对白可加 --skip-asr")
        asr_file.write_text(json.dumps([], ensure_ascii=False, indent=2), encoding="utf-8")
        write_asr_timing_evidence(
            work_dir, video_path, "UNAVAILABLE_NO_KEY", final_segments=[]
        )
        return []

    # 提取音频
    audio_wav = work_dir / "audio.wav"
    audio_meta = _audio_meta_path(work_dir)
    audio_wav.unlink(missing_ok=True)
    audio_meta.unlink(missing_ok=True)
    cmd = ["ffmpeg", "-y", "-i", str(video_path), "-vn",
           "-ar", "16000", "-ac", "1", str(audio_wav)]
    try:
        result = run_cmd(cmd)
    except Exception as exc:
        audio_wav.unlink(missing_ok=True)
        audio_meta.unlink(missing_ok=True)
        asr_file.unlink(missing_ok=True)
        write_asr_timing_evidence(work_dir, video_path, "FAILED_AUDIO_EXTRACTION")
        raise RuntimeError("音频提取失败: ffmpeg 无法完成") from exc
    if result.returncode != 0:
        audio_wav.unlink(missing_ok=True)
        audio_meta.unlink(missing_ok=True)
        if _is_no_audio_stream_error(result.stderr):
            log("ASR 跳过：源视频无音轨，记录空转写")
            asr_file.write_text(json.dumps([], ensure_ascii=False, indent=2), encoding="utf-8")
            write_asr_timing_evidence(
                work_dir, video_path, "NO_AUDIO_STREAM", final_segments=[]
            )
            return []
        asr_file.unlink(missing_ok=True)
        write_asr_timing_evidence(work_dir, video_path, "FAILED_AUDIO_EXTRACTION")
        raise RuntimeError(f"音频提取失败: {result.stderr}")
    _write_audio_meta(work_dir, video_path)

    # 获取音频时长；ffprobe 失败时不伪造时长（否则会向 asr_result.json 写入虚构时间戳），
    # 而是记录 UNAVAILABLE_NO_DURATION 证据并跳过转录
    try:
        duration = get_video_duration(audio_wav)
    except RuntimeError as exc:
        log(f"ASR 警告: 无法获取音频时长，跳过 ASR 转录: {exc}")
        asr_file.write_text(json.dumps([], ensure_ascii=False, indent=2), encoding="utf-8")
        write_asr_timing_evidence(
            work_dir,
            video_path,
            "UNAVAILABLE_NO_DURATION",
            final_segments=[],
            audio_path=audio_wav,
        )
        return []

    segments_dir = work_dir / "audio_segments"
    segments_dir.mkdir(exist_ok=True)

    segment_length = int(CONFIG["asr_segment_seconds"])
    try:
        if duration <= segment_length:
            # 短音频，整段转录
            text = _run_asr(audio_wav)
            asr_result = [{"start": 0.0, "end": round(duration, 2), "text": text}]
        else:
            # 长音频，分段转录（固定粗窗口，不是词级或对白边界对齐）
            asr_result = _segment_and_transcribe(
                audio_wav, segments_dir, duration, segment_length
            )
    except ASRProviderError:
        asr_file.unlink(missing_ok=True)
        write_asr_timing_evidence(
            work_dir, video_path, "FAILED_PROVIDER", audio_path=audio_wav
        )
        raise

    # 保存
    asr_file.write_text(json.dumps(asr_result, ensure_ascii=False, indent=2), encoding="utf-8")
    status = "AVAILABLE_COARSE" if any(s["text"] for s in asr_result) else "EMPTY_UNKNOWN"
    write_asr_timing_evidence(
        work_dir,
        video_path,
        status,
        final_segments=asr_result,
        audio_path=audio_wav,
    )

    total_text = " ".join(s["text"] for s in asr_result if s["text"])
    empty = sum(1 for s in asr_result if not s["text"])
    suffix = f"（{empty} 段无文本：原因未知，不代表已证实静音）" if empty else ""
    log(f"ASR 转录完成: {len(asr_result)} 段, 共 {len(total_text)} 字{suffix}")
    return asr_result


def _strip_reasoning_residue(text):
    """Remove MiMo reasoning-model <think>…</think> leakage from ASR content.

    Thinking-disable is not applied to -asr models (lib._prepare_api_payload), so the reasoning
    model can leak a <think> block — full, truncated/unclosed, or a leading orphan/residual tag
    (a bare "think>" prefix) — into the transcript.
    """
    text = re.sub(r"(?is)<think\b.*?</think\s*>", "", text)  # full <think>…</think> block
    text = re.sub(r"(?is)<think\b.*\Z", "", text)            # unclosed/truncated <think tail
    return re.sub(r"(?i)^\s*<?/?think\s*>\s*", "", text)     # leading orphan/residual think tag


def _run_asr(wav_path):
    """用 MiMo ASR (mimo-v2.5-asr) 转录单个 wav 文件，返回纯文本。

    音频以 base64 data-URI 放进 OpenAI 风格的 chat/completions 消息里，转写文本回到
    choices[0].message.content。API/响应结构失败会抛错，避免把瞬时失败缓存成空转写；
    音频文件缺失/不可读同样抛错（那是上游 bug，不是静音）；只有空音频、超体积等
    确定不可发送的片段返回空串。
    """
    try:
        raw = Path(wav_path).read_bytes()
    except OSError as e:
        raise RuntimeError(f"ASR: 无法读取音频 {wav_path}: {e}") from e
    if not raw:
        return ""

    b64 = base64.b64encode(raw).decode("ascii")
    max_b64_bytes = int(float(CONFIG["mimo_asr_base64_max_mb"]) * 1024 * 1024)
    if len(b64) > max_b64_bytes:
        log(f"ASR 警告: 分片 base64 体积 {len(b64) / 1024 / 1024:.1f}MB 超过 MiMo 上限 "
            f"{CONFIG['mimo_asr_base64_max_mb']}MB，跳过该段；可调小 ASR_SEGMENT_SECONDS")
        return ""

    payload = {
        "model": CONFIG["mimo_asr_model"],
        "messages": [{
            "role": "user",
            "content": [{
                "type": "input_audio",
                "input_audio": {"data": f"data:{_ASR_AUDIO_MIME};base64,{b64}"},
            }],
        }],
        "asr_options": {"language": CONFIG["mimo_asr_language"]},
    }
    try:
        resp = mimo_asr_api_call(payload)
    except Exception as e:
        raise ASRProviderError(f"MiMo ASR 调用失败: {e}") from e
    try:
        text = _strip_reasoning_residue(str(resp["choices"][0]["message"]["content"] or "")).strip()
    except (KeyError, IndexError, TypeError):
        raise ASRProviderError(
            f"MiMo ASR 返回结构异常: {json.dumps(resp, ensure_ascii=False)[:200]}"
        )
    if is_moderation_refusal(text):
        # The refusal sentence is not dialogue: storing it would put it in the briefs and
        # make cut/narration safety treat the window as speech. Record no text instead.
        log(f"ASR 警告: MiMo 内容审核拒绝转写 {Path(wav_path).name}，该段记为无文本")
        return ""
    return text


# 16-bit PCM peak <= 582 (~ -35 dBFS) is treated as dead silence when trimming window edges.
_DEAD_SILENCE_PEAK_16BIT = 582


def _fixed_asr_windows(total_duration, segment_length):
    step = max(1, int(segment_length))
    return [
        (round(float(start), 2), round(float(min(start + step, total_duration)), 2))
        for start in range(0, max(1, math.ceil(total_duration)), step)
    ]


def _plan_asr_windows(audio_wav, total_duration, segment_length):
    """Split long 16-bit mono WAV at local acoustic valleys and trim dead-silence edges.

    Even when BGM/SFX sits at -16..-22 dBFS, inter-sentence pauses create clear 150ms energy
    valleys, avoiding mid-word cuts at fixed 15s multiples. Falls back to the fixed grid when
    `audio_wav` is absent or not standard 16-bit mono PCM (e.g. unit-test stubs).
    """
    try:
        with wave.open(str(audio_wav), "rb") as wf:
            sr = wf.getframerate()
            if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or sr < 1000:
                return _fixed_asr_windows(total_duration, segment_length)
            raw = wf.readframes(wf.getnframes())
    except (OSError, wave.Error, EOFError):
        return _fixed_asr_windows(total_duration, segment_length)

    samples = array.array("h")
    samples.frombytes(raw[: len(raw) - (len(raw) % 2)])
    if sys.byteorder != "little":
        samples.byteswap()
    hop = max(1, sr // 20)  # 50 ms frame
    hop_sec = hop / sr
    peaks = [
        max(-min(samples[idx : idx + hop]), max(samples[idx : idx + hop]))
        for idx in range(0, len(samples), hop)
        if samples[idx : idx + hop]
    ]
    if len(peaks) < 20:
        return _fixed_asr_windows(total_duration, segment_length)

    smooth = [
        (peaks[max(0, i - 1)] + peaks[i] + peaks[min(len(peaks) - 1, i + 1)]) / 3.0
        for i in range(len(peaks))
    ]
    raw_windows = []
    cur = 0.0
    target = max(1.0, float(segment_length))
    while cur < total_duration - 0.05:
        if total_duration - cur <= target * 1.15:
            raw_windows.append((round(cur, 2), round(float(total_duration), 2)))
            break
        lo_i = max(1, int((cur + target * 0.6) / hop_sec))
        hi_i = min(len(smooth) - 1, int(min(total_duration - 0.8, cur + target * 1.2) / hop_sec))
        if hi_i <= lo_i:
            cut_t = round(min(cur + target, total_duration), 2)
        else:
            best_i = min(range(lo_i, hi_i + 1), key=lambda k: smooth[k])
            cut_t = round(best_i * hop_sec, 2)
            if cut_t <= cur + 0.5:
                cut_t = round(min(cur + target, total_duration), 2)
        raw_windows.append((round(cur, 2), cut_t))
        cur = cut_t

    trimmed = []
    for win_s, win_e in raw_windows:
        i_s = max(0, int(win_s / hop_sec))
        i_e = min(len(peaks), max(i_s + 1, math.ceil(win_e / hop_sec)))
        active = [k for k in range(i_s, i_e) if peaks[k] > _DEAD_SILENCE_PEAK_16BIT]
        if not active:
            continue
        pad_s = round(max(win_s, (active[0] - 3) * hop_sec), 2)
        pad_e = round(min(win_e, (active[-1] + 4) * hop_sec), 2)
        adj_s = pad_s if pad_s - win_s >= 0.6 else win_s
        adj_e = pad_e if win_e - pad_e >= 0.6 else win_e
        if adj_e > adj_s:
            trimmed.append((adj_s, adj_e))
    return trimmed or [(0.0, round(float(total_duration), 2))]


def _segment_and_transcribe(audio_wav, segments_dir, total_duration, segment_length=None):
    """分段转录长音频"""
    if segment_length is None:
        segment_length = int(CONFIG["asr_segment_seconds"])
    # 长视频 ASR 是顺序调用；可选节流让调用间隔开，降低踩到集群限流的频率（默认 0=不节流）
    try:
        throttle = max(0.0, float(os.environ.get("ASR_THROTTLE_SECONDS", "0") or 0))
    except ValueError:
        throttle = 0.0
    results = []
    windows = _plan_asr_windows(audio_wav, total_duration, segment_length)

    for i, (start, end) in enumerate(windows):
        if throttle and i:
            time.sleep(throttle)
        seg_wav = segments_dir / f"seg_{i:03d}.wav"

        cmd = ["ffmpeg", "-y", "-i", str(audio_wav),
               "-ss", str(start), "-to", str(end),
               "-ar", "16000", "-ac", "1", str(seg_wav)]
        cut = run_cmd(cmd)
        if cut.returncode != 0:
            # 切分失败时不要对磁盘上的陈旧/残缺音频转录，否则会得到错位文本
            log(f"  段 {i+1}: 切分失败，跳过转录 ({cut.stderr.strip()[:200]})")
            text = ""
        else:
            text = _run_asr(seg_wav)
        results.append({
            "start": round(start, 2),
            "end": round(end, 2),
            "text": text,
        })
        log(f"  段 {i+1}: {start:.1f}s-{end:.1f}s => {len(text)} 字")

    return results
