import argparse
import base64
import json
import os
import re
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

from approved_text_policy import (
    PRESERVE_APPROVED_TEXT_POLICY,
    ApprovedTextDurationError,
    archive_current_meta,
    enforce_duration,
    policy_name,
    validate_required_texts,
    write_json_atomically as _write_tts_meta_atomically,
)
from fish_audio import synthesize_fish_audio
import index_tts as index_provider
from lib import (
    CONFIG,
    _text_char_count,
    _truncate_at_sentence,
    file_fingerprint,
    get_video_duration,
    log,
    mimo_tts_api_call,
    narration_tempo_budget,
    run_cmd,
    stable_hash,
)

# Re-exported: tests and callers reach these through voiceover, the module owns the flow.
from tts_audio import _maybe_normalize_tts_wav, _normalize_tts_wav_rms  # noqa: F401

SUPPORTED_TTS_ENGINES = {"mimo-tts", "fish-audio", "index-tts"}
SEGMENT_AUDIO_SCHEMA_VERSION = 1
TTS_CACHE_VERSION = 2
VOICE_REFERENCE_PREP_VERSION = 1
_VOICE_REFERENCE_LOCK = Lock()
# Process-wide voice-reference state; prepared bytes are invocation-scoped.
_VOICE_REFERENCE_STATE_KEYS = (
    "voice_ref_b64",
    "voice_ref_snapshot_path",
    "voice_ref_snapshot_signature",
    "voice_ref_source_signature",
    "voice_ref_fingerprint",
    "voice_ref_snapshot_locked",
)


def authored_text_policy():
    """Return the explicit cacheable policy governing creative text mutation."""
    return policy_name(CONFIG.get("preserve_approved_text", False))


def enforce_approved_text_policy(index, seg, authored_text, spoken_text, audio_duration,
                                  available_duration, max_raw_duration):
    """Fail closed when immutable approved text cannot fit its authored window."""
    enforce_duration(
        index, seg, authored_text, spoken_text, audio_duration, available_duration,
        max_raw_duration, CONFIG.get("preserve_approved_text", False), CONFIG["breath_ms"],
    )


def _parse_rate_offset(rate_str):
    """'+5%' -> 0.05, '-3%' -> -0.03, '+0%' -> 0.0"""
    return float(rate_str.rstrip("%")) / 100.0


def _compute_tts_params(text, narration, seg_index):
    """根据内容特征和位置计算 TTS 语速/音高参数"""
    rate = "+5%"
    pitch = "+0Hz"
    total = len(narration)
    # 位置相关
    if seg_index == 0:
        rate = "+5%"       # 开头稍快，抓住注意力
    elif seg_index >= total - 1:
        rate = "-5%"       # 结尾放慢，收束感
    elif seg_index >= total - 2:
        rate = "-2%"       # 倒数第二段略慢

    # 内容相关
    has_exclamation = any(c in text for c in "！!")
    has_question = "？" in text or "?" in text
    has_ellipsis = "……" in text or "..." in text

    if has_exclamation:
        rate = "+8%"
        pitch = "+3Hz"    # 感叹句加速+微升调
    elif has_question:
        pitch = "+5Hz"    # 疑问句升调
    elif has_ellipsis:
        rate = "-3%"      # 省略号（悬念/犹豫）放慢

    # 长文本稍快
    if len(text) > 35 and not has_ellipsis:
        rate = max(rate, "+6%", key=lambda x: int(x.rstrip('%+-')))

    return rate, pitch


def _clean_narration_text(text):
    """清理解说文本中 TTS 不应读出的内容"""
    # 移除 markdown 格式标记
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)  # **bold** → bold
    text = re.sub(r'\*(.+?)\*', r'\1', text)       # *italic* → italic
    text = re.sub(r'「(.+?)」', r'\1', text)        # 「quote」 → quote
    text = re.sub(r'」|「|『|』', '', text)
    # 移除方括号标注（[climax]、[suspense] 等舞台指示）
    text = re.sub(r'\[[^\]]*\]', '', text)
    # 移除圆括号标注（（旁白）、（转场）等）
    text = re.sub(r'[（(][^）)]*[）)]', '', text)
    # 规范化省略号和重复标点
    text = re.sub(r'\.{3,}|…{2,}', '……', text)
    text = re.sub(r'……+', '……', text)
    text = re.sub(r'([。！？，；：])\1+', r'\1', text)  # 重复标点 → 单个
    # 移除 emoji
    text = re.sub(r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF'
                  r'\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U0000FE00-\U0000FEFF]', '', text)
    # 清理多余空白
    return re.sub(r'\s+', ' ', text).strip()


def _synthesize_segment(i, seg, narration, tts_dir, engine):
    """合成单个 TTS 段（线程安全），支持 resume 跳过已有文件"""
    prepared = _prepare_tts_segment(i, seg, narration, tts_dir, engine)
    if prepared is None:
        return None
    text, output_wav, rate, pitch, cache_key = prepared
    cached = _reuse_tts_segment_cache(i, seg, output_wav, cache_key, engine)
    if cached:
        return cached

    provider_receipt = _run_tts_engine(
        engine, text, output_wav, rate=rate, pitch=pitch, emotion=seg.get("emotion")
    )

    dur = get_video_duration(output_wav)
    seg_slot = seg["end"] - seg["start"]
    seg_pause = seg.get("pause_after_ms", CONFIG["breath_ms"]) / 1000
    available = max(0.5, seg_slot - seg_pause)
    rate_offset = _parse_rate_offset(rate)
    budget = narration_tempo_budget(rate_offset)
    raw_budget = available * budget["max_raw_duration_factor"]
    truncated = False
    truncate_reason = "none"
    try:
        enforce_approved_text_policy(i, seg, seg["narration"], text, dur, available, raw_budget)
    except ApprovedTextDurationError:
        _cleanup_partial_tts_outputs(output_wav)
        raise
    if dur > raw_budget and len(text) > 5:
        chars_per_sec = _text_char_count(text) / dur
        target_chars = max(5, int(raw_budget * chars_per_sec) - 1)
        shortened = _truncate_at_sentence(text, target_chars)
        if shortened and len(shortened) >= 5 and shortened != text:
            log(f"  段 {i+1}: 解说超出片段时长，按累计语速预算句界缩短 {len(text)}→{len(shortened)} 字以适配（建议在解说里改写得更短）")
            text = shortened
            truncated = True
            truncate_reason = "sentence_boundary"
            provider_receipt = _run_tts_engine(
                engine, text, output_wav, rate=rate, pitch=pitch, emotion=seg.get("emotion")
            )
            dur = get_video_duration(output_wav)

    norm_meta = _maybe_normalize_tts_wav(output_wav)
    if norm_meta:
        dur = get_video_duration(output_wav)
    processed_hash = file_fingerprint(output_wav)
    provider_receipt = index_provider.finalize_receipt(provider_receipt, processed_hash)
    _write_tts_segment_cache(output_wav, cache_key, text, dur, rate_offset,
                             truncated, truncate_reason, norm_meta, provider_receipt)
    return _build_tts_segment_result(
        i, seg, text, output_wav, dur, rate_offset, truncated, truncate_reason, norm_meta,
        provider_receipt, processed_hash)


def _build_tts_segment_result(index, seg, text, output_wav, duration, rate_offset,
                              truncated=False, truncate_reason="none", norm_meta=None,
                              provider_receipt=None, processed_wav_sha256=None):
    budget = narration_tempo_budget(rate_offset)
    authored_text = _clean_narration_text(seg["narration"])
    resolved_truncated = truncated or text != authored_text
    result = {
        "segment_audio_schema_version": SEGMENT_AUDIO_SCHEMA_VERSION,
        "index": index,
        "start": seg["start"],
        "end": seg["end"],
        "narration": authored_text,
        "authored_text": seg["narration"],
        "spoken_text": text,
        "truncated": resolved_truncated,
        "truncate_reason": (truncate_reason if truncate_reason != "none" else "sentence_boundary") if resolved_truncated else "none",
        "fit_status": "pending_assembly",
        "audio_path": str(output_wav),
        "audio_duration": duration,
        "placed_audio_duration": None,
        "actual_place_start": None,
        "actual_place_end": None,
        "global_narration_speed": budget["global_narration_speed"],
        "segment_tempo_factor": 1.0,
        "effective_tempo": budget["global_narration_speed"] * budget["tts_rate_factor"],
        "rms_dbfs_before": norm_meta["rms_dbfs_before"] if norm_meta else None,
        "rms_dbfs_after": norm_meta["rms_dbfs_after"] if norm_meta else None,
        "peak_after": norm_meta["peak_after"] if norm_meta else None,
        "tts_rate_offset": rate_offset,
        "pause_after_ms": seg.get("pause_after_ms", CONFIG["breath_ms"]),
        "overlaps_speech": seg.get("overlaps_speech", True),
        "authored_text_policy": authored_text_policy(),
        "provider_receipt": provider_receipt,
        "processed_wav_sha256": processed_wav_sha256,
    }
    for optional_key in ("source_start", "source_end", "source_clip_id", "emotion"):
        if optional_key in seg:
            result[optional_key] = seg[optional_key]
    return result


def _tts_failure_record(index, seg, error):
    """Build a user-visible failure record for partial TTS output."""
    record = {
        "index": index,
        "start": seg["start"],
        "end": seg["end"],
        "text": _clean_narration_text(seg["narration"]),
        "error": str(error),
    }
    if authored_text_policy() == PRESERVE_APPROVED_TEXT_POLICY:
        record.update({"required": True, "policy": PRESERVE_APPROVED_TEXT_POLICY})
    if isinstance(error, ApprovedTextDurationError):
        record.update({"required": True, **error.evidence})
    return record


def _build_tts_meta(segments, engine, narration_name, failures):
    """Stable tts_meta.json payload, including partial-failure visibility."""
    return {
        "segments": segments,
        "engine": engine,
        "narration": narration_name,
        "partial": bool(failures),
        "failures": failures,
    }


def _reset_voice_reference_state():
    """Drop prepared reference bytes so a reused process re-hashes the live source."""
    for key in _VOICE_REFERENCE_STATE_KEYS:
        CONFIG.pop(key, None)


def synthesize_tts(narration, work_dir):
    """合成解说音频（并行）。Returns (segments, engine, failures)."""
    _reset_voice_reference_state()
    voice_ref = CONFIG["voice_ref"]
    tts_dir = work_dir / "tts_segments"
    tts_dir.mkdir(exist_ok=True)

    if not narration:
        raise RuntimeError("narration.json 没有可配音的解说段，已中止以避免生成无解说视频")
    validate_required_texts(
        narration, _clean_narration_text, CONFIG.get("preserve_approved_text", False)
    )

    cache_engine = _configured_tts_engine_for_cache()
    if cache_engine in {"fish-audio", "index-tts"} and voice_ref:
        if cache_engine == "fish-audio":
            raise RuntimeError("Fish Audio 不接受本地 VOICE_REF/--voice-ref；请改用 FISH_TTS_REFERENCE_ID")
        raise RuntimeError("index-tts 不支持本地 VOICE_REF/--voice-ref 克隆")
    # A fully cached narration needs no credential: probe every segment's sidecar first.
    cached_segments = []
    needs_fresh = False
    prepared_count = 0
    for i, seg in enumerate(narration):
        prepared = _prepare_tts_segment(i, seg, narration, tts_dir, cache_engine)
        if prepared is None:
            continue
        prepared_count += 1
        cached = _reuse_tts_segment_cache(i, seg, prepared[1], prepared[4], cache_engine)
        if not cached:
            needs_fresh = True
            break
        cached_segments.append(cached)
    if prepared_count == 0:
        raise RuntimeError("narration.json 没有可配音的有效文本，已中止以避免生成无解说视频")
    if not needs_fresh:
        cached_segments.sort(key=lambda x: x["index"])
        log(f"TTS 引擎: {cache_engine} (cache)")
        return cached_segments, cache_engine, []

    engine = resolve_tts_engine()

    if voice_ref:
        _cache_prepared_voice_reference(voice_ref)
        CONFIG["voice_ref_snapshot_locked"] = True

    log(f"TTS 引擎: {engine}")

    segments = []
    failures = []
    max_workers = min(len(narration), CONFIG["tts_workers"])

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_synthesize_segment, i, seg, narration, tts_dir, engine): i
                for i, seg in enumerate(narration)
            }
            for future in as_completed(futures):
                try:
                    result = future.result()
                except Exception as e:
                    i = futures[future]
                    failures.append(_tts_failure_record(i, narration[i], e))
                    log(f"  TTS 段 {i+1} 失败: {e}")
                    continue
                if result:
                    segments.append(result)
                    log(f"  段 {result['index']+1}: {result['audio_duration']:.1f}s - {result['narration'][:25]}...")
    finally:
        CONFIG.pop("voice_ref_snapshot_locked", None)

    segments.sort(key=lambda x: x["index"])
    approved_text_failures = [
        failure for failure in failures
        if failure.get("failure_kind") == "approved_text_duration_conflict"
    ]
    if approved_text_failures:
        first = approved_text_failures[0]
        evidence = {
            key: value for key, value in first.items()
            if key not in {"text", "error", "required"}
        }
        if len(approved_text_failures) > 1:
            evidence["conflict_count"] = len(approved_text_failures)
        raise ApprovedTextDurationError(evidence)
    if failures and authored_text_policy() == PRESERVE_APPROVED_TEXT_POLICY:
        sample = json.dumps(failures[:3], ensure_ascii=False, sort_keys=True)
        raise RuntimeError(
            f"批准稿严格模式有 {len(failures)}/{len(narration)} 个必需 TTS 段失败，"
            f"不能按部分成功交付: {sample}"
        )
    if failures and not CONFIG["allow_partial_tts"]:
        sample = "; ".join(f"段 {f['index']+1}: {f['error']}" for f in failures[:3])
        raise RuntimeError(
            f"TTS 失败 {len(failures)}/{len(narration)} 段，已中止以避免生成缺解说的视频。"
            f"示例: {sample}。如确需继续，可设置 ALLOW_PARTIAL_TTS=1 或 --allow-partial-tts。"
        )
    if failures:
        missing = ", ".join(str(f["index"] + 1) for f in failures[:8])
        more = "…" if len(failures) > 8 else ""
        log(
            f"警告: TTS 部分失败 {len(failures)}/{len(narration)} 段（段 {missing}{more}），"
            "成片可预览但不建议直接发布；详见 tts_meta.json failures"
        )
    if not segments:
        raise RuntimeError("TTS 没有生成任何有效解说音频，已中止以避免生成无解说视频")
    return segments, engine, failures


def _run_tts_engine(engine, text, output_wav, rate="+0%", pitch="+0Hz", emotion=None):
    """Run one TTS engine with retry and remove partial files after failures."""
    if engine not in SUPPORTED_TTS_ENGINES:
        raise RuntimeError(f"不支持的 TTS 引擎: {engine}。支持 mimo-tts、fish-audio、index-tts。")
    if engine == "index-tts":
        index_provider.validate_controls(rate, pitch, emotion)
    retries = CONFIG["tts_retries"]
    last_error = None

    for attempt in range(1, retries + 1):
        try:
            _cleanup_partial_tts_outputs(output_wav)
            receipt = None
            if engine == "mimo-tts":
                _tts_mimo(text, output_wav, rate=rate, pitch=pitch, emotion=emotion)
            elif engine == "fish-audio":
                synthesize_fish_audio(text, output_wav, rate=rate)
            else:
                receipt = index_provider.synthesize_configured(text, output_wav, CONFIG)
            if get_video_duration(output_wav) <= 0:
                raise RuntimeError(f"{engine} 输出音频时长无效")
            return receipt
        except Exception as exc:
            last_error = exc
            _cleanup_partial_tts_outputs(output_wav)
            if attempt < retries:
                wait = min(2 ** (attempt - 1), 8)
                log(f"  TTS 重试 {attempt+1}/{retries}: {exc}，等待 {wait}s")
                time.sleep(wait)

    raise RuntimeError(f"{engine} 合成失败: {last_error}") from last_error


def _cleanup_partial_tts_outputs(output_wav):
    """Remove stale partial media files before/after a failed TTS attempt."""
    wav_path = Path(output_wav)
    for path in (
        wav_path,
        wav_path.with_suffix(".mp3"),
        Path(str(wav_path) + ".part"),
        _tts_segment_cache_path(output_wav),
    ):
        path.unlink(missing_ok=True)


def _tts_segment_cache_path(output_wav):
    return Path(str(output_wav) + ".cache.json")


def _prepare_tts_segment(index, seg, narration, tts_dir, engine):
    text = _clean_narration_text(seg["narration"])
    if not text:
        return None
    output_wav = tts_dir / f"narr_{index:03d}.wav"
    if engine == "index-tts":
        rate, pitch = index_provider.default_controls(seg)
    elif CONFIG["tts_dynamic_params"]:
        rate, pitch = _compute_tts_params(text, narration, index)
    else:
        rate, pitch = "+0%", "+0Hz"
    cache_key = _tts_segment_cache_key(engine, index, seg, text, rate, pitch)
    return text, output_wav, rate, pitch, cache_key


def _reuse_tts_segment_cache(index, seg, output_wav, cache_key, engine):
    cached = _load_tts_segment_cache(output_wav, cache_key)
    if cached is None:
        return None
    if engine == "index-tts" and not index_provider.valid_cached_receipt(cached, CONFIG):
        return None
    # The sidecar's audio_fingerprint already proved these exact bytes are what produced
    # `audio_duration`; re-probing would be one ffprobe process per segment on every rerun.
    log(f"  段 {index+1}: 复用已有 ({cached['audio_duration']:.1f}s)")
    return _build_tts_segment_result(
        index,
        seg,
        cached["spoken_text"],
        output_wav,
        cached["audio_duration"],
        cached["tts_rate_offset"],
        cached["truncated"],
        cached["truncate_reason"],
        cached["normalization"],
        cached.get("provider_receipt"),
        cached.get("processed_wav_sha256"),
    )


def _tts_segment_cache_key(engine, index, seg, source_text, rate, pitch):
    """Fingerprint the exact inputs that make a cached segment safe to reuse."""
    payload = {
        "version": TTS_CACHE_VERSION,
        "engine": engine,
        "source_text": source_text,
        "segment_index": index,
        "start": round(seg["start"], 3),
        "end": round(seg["end"], 3),
        "pause_after_ms": seg.get("pause_after_ms", CONFIG["breath_ms"]),
        "rate": rate,
        "pitch": pitch,
        "emotion": seg.get("emotion", ""),
        "settings": tts_settings_fingerprint(engine),
    }
    if authored_text_policy() == PRESERVE_APPROVED_TEXT_POLICY:
        payload.update({
            "authored_text_policy": PRESERVE_APPROVED_TEXT_POLICY,
            "authored_text": seg["narration"],
            "provider_text_cleanup": "clean-narration-text-v1",
        })
    return stable_hash(payload)


def _load_tts_segment_cache(output_wav, cache_key):
    """Return cache metadata only when the sidecar proves the WAV matches narration."""
    cache_path = _tts_segment_cache_path(output_wav)
    if not output_wav.exists() or not cache_path.exists():
        return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    required = {
        "cache_key", "audio_fingerprint", "spoken_text", "audio_duration",
        "tts_rate_offset", "truncated", "truncate_reason", "normalization",
    }
    if not isinstance(data, dict) or not required <= data.keys():
        return None
    try:
        audio_fingerprint = file_fingerprint(output_wav)
    except OSError:
        return None
    if data["cache_key"] != cache_key or data["audio_fingerprint"] != audio_fingerprint:
        return None
    return data


def _write_tts_segment_cache(output_wav, cache_key, spoken_text, duration, rate_offset,
                             truncated=False, truncate_reason="none", norm_meta=None,
                             provider_receipt=None):
    """Persist non-secret provenance for safe per-segment TTS reuse."""
    _tts_segment_cache_path(output_wav).write_text(
        json.dumps({
            "version": TTS_CACHE_VERSION,
            "cache_key": cache_key,
            "audio_fingerprint": file_fingerprint(output_wav),
            "spoken_text": spoken_text,
            "audio_duration": duration,
            "tts_rate_offset": rate_offset,
            "truncated": truncated,
            "truncate_reason": truncate_reason if truncated else "none",
            "normalization": norm_meta or None,
            "provider_receipt": provider_receipt,
            "processed_wav_sha256": file_fingerprint(output_wav),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _configured_tts_engine_for_cache():
    """Resolve provider intent without requiring a live credential for cache probes."""
    provider = CONFIG["tts_provider"]
    if provider == "auto":
        if CONFIG["mimo_tts_api_key"] or not CONFIG["fish_api_key"]:
            return "mimo-tts"
        return "fish-audio"
    if provider == "index-tts":
        index_provider.configured_values(CONFIG)
        return provider
    if provider not in SUPPORTED_TTS_ENGINES:
        raise RuntimeError(
            "TTS_PROVIDER/--tts-provider 必须是 auto、mimo-tts、fish-audio 或 index-tts"
        )
    return provider


def resolve_tts_engine():
    """Resolve the selected MiMo or Fish Audio TTS engine and require its credential."""
    engine = _configured_tts_engine_for_cache()
    if engine == "index-tts":
        return engine
    if engine == "fish-audio":
        if CONFIG["fish_api_key"]:
            return engine
        raise RuntimeError("没有可用的 TTS 引擎：请设置 FISH_API_KEY（Fish Audio 需要）。")
    if CONFIG["mimo_tts_api_key"]:
        return engine
    raise RuntimeError(
        f"没有可用的 TTS 引擎：请设置 {CONFIG['mimo_tts_api_key_source']}（MiMo TTS 需要）。"
    )


def tts_settings_fingerprint(engine):
    """Return non-secret TTS settings that materially affect generated audio."""
    settings = {
        "engine": engine,
        "tts_dynamic_params": CONFIG["tts_dynamic_params"],
        "narration_speed": CONFIG["narration_speed"],
        "narration_cumulative_tempo_max": CONFIG["narration_cumulative_tempo_max"],
        "narration_cumulative_tempo_hard_max": CONFIG["narration_cumulative_tempo_hard_max"],
        "tts_segment_tempo_max": CONFIG["tts_segment_tempo_max"],
        "tts_segment_normalize": CONFIG["tts_segment_normalize"],
        "tts_segment_target_rms_dbfs": CONFIG["tts_segment_target_rms_dbfs"],
        "tts_segment_peak_limit": CONFIG["tts_segment_peak_limit"],
    }
    if engine == "index-tts":
        settings.update(index_provider.cache_settings(CONFIG))
    elif engine == "fish-audio":
        settings.update(
            {
                "fish_tts_api_url": CONFIG["fish_tts_api_url"],
                "fish_tts_model": CONFIG["fish_tts_model"],
                "fish_tts_reference_id": CONFIG["fish_tts_reference_id"],
            }
        )
    else:
        settings.update(
            {
                "mimo_tts_api_url": CONFIG["mimo_tts_api_url"],
                "mimo_tts_model": CONFIG["mimo_tts_model"],
                "mimo_tts_voice": CONFIG["mimo_tts_voice"],
                "mimo_tts_style": CONFIG["mimo_tts_style"],
            }
        )
    voice_ref = CONFIG["voice_ref"]
    if voice_ref and engine == "mimo-tts":
        ref_path = Path(voice_ref).expanduser()
        settings.pop("mimo_tts_voice", None)  # ignored by the voiceclone API
        settings["voice_ref_fingerprint"] = _voice_reference_fingerprint(ref_path)
        settings["voice_ref_preparation"] = (
            f"pcm_s16le:24000hz:mono:30s:v{VOICE_REFERENCE_PREP_VERSION}"
        )
        settings["mimo_tts_model"] = "mimo-v2.5-tts-voiceclone"
    return settings


def _mimo_tts_style_instruction(rate="+0%", pitch="+0Hz", emotion=None):
    style = CONFIG["mimo_tts_style"]
    if emotion:
        tone = f"用「{emotion}」的情绪和语气演绎这句解说，代入感强、有起伏，不要平铺直叙。"
    else:
        tone = "语气有感染力、有起伏，像在给观众讲故事，不要平淡机械。"
    rate_offset = _parse_rate_offset(rate)
    if rate_offset >= 0.06:
        speed = "语速略快，但吐字保持清楚。"
    elif rate_offset <= -0.03:
        speed = "语速略慢，适当停顿，保留收束感。"
    else:
        speed = "语速中等，节奏稳定。"
    pitch_hint = "疑问句或情绪抬升处可自然微升调。" if pitch != "+0Hz" else "音调自然。"
    return f"{style} {tone} {speed} {pitch_hint}"


def _prepare_voice_reference(ref_path):
    """Normalize arbitrary reference audio to MiMo voiceclone's 24 kHz mono WAV."""
    ref = Path(ref_path).expanduser()
    if not ref.is_file():
        raise FileNotFoundError(f"参考音频不存在或不是文件: {ref}")
    with tempfile.TemporaryDirectory(prefix="video-recap-voice-ref-") as temp_dir:
        normalized = Path(temp_dir) / "voice_ref.wav"
        result = run_cmd([
            "ffmpeg", "-y", "-i", str(ref), "-vn", "-ar", "24000", "-ac", "1",
            "-t", "30", "-acodec", "pcm_s16le", str(normalized),
        ])
        if result.returncode != 0 or not normalized.is_file() or normalized.stat().st_size <= 44:
            raise RuntimeError(f"参考音频转码失败: {result.stderr.strip() or ref}")
        return base64.b64encode(normalized.read_bytes()).decode("ascii")


def _voice_reference_signature(ref_path):
    """Cheaply identify the prepared source so a reused process cannot serve stale audio."""
    ref = Path(ref_path).expanduser().resolve()
    stat = ref.stat()
    return f"{ref}:{stat.st_size}:{stat.st_mtime_ns}"


def _voice_reference_fingerprint(ref_path):
    ref = Path(ref_path).expanduser()
    resolved = str(ref.resolve())
    if (
        CONFIG.get("voice_ref_snapshot_locked")
        and CONFIG.get("voice_ref_b64")
        and CONFIG.get("voice_ref_snapshot_path") == resolved
        and CONFIG.get("voice_ref_fingerprint")
    ):
        return CONFIG["voice_ref_fingerprint"]
    signature = _voice_reference_signature(ref)
    if (
        CONFIG.get("voice_ref_source_signature") == signature
        and CONFIG.get("voice_ref_fingerprint")
    ):
        return CONFIG["voice_ref_fingerprint"]
    fingerprint = file_fingerprint(ref)
    CONFIG["voice_ref_source_signature"] = signature
    CONFIG["voice_ref_fingerprint"] = fingerprint
    return fingerprint


def _cache_prepared_voice_reference(ref_path):
    with _VOICE_REFERENCE_LOCK:
        ref = Path(ref_path).expanduser().resolve()
        resolved = str(ref)
        if (
            CONFIG.get("voice_ref_snapshot_locked")
            and CONFIG.get("voice_ref_b64")
            and CONFIG.get("voice_ref_snapshot_path") == resolved
        ):
            return CONFIG["voice_ref_b64"]
        signature = _voice_reference_signature(ref)
        snapshot_path = CONFIG.get("voice_ref_snapshot_path")
        snapshot_signature = CONFIG.get("voice_ref_snapshot_signature")
        if (
            CONFIG.get("voice_ref_b64")
            and snapshot_path == resolved
            and snapshot_signature == signature
        ):
            return CONFIG["voice_ref_b64"]
        if not ref.is_file():
            raise FileNotFoundError(f"参考音频不存在或不是文件: {ref}")
        # ffmpeg and the cache fingerprint must consume the same immutable bytes. Copy first,
        # then derive both the normalized WAV and identity from that snapshot rather than reading
        # a caller-mutable source twice.
        with tempfile.TemporaryDirectory(prefix="video-recap-voice-ref-snapshot-") as temp_dir:
            snapshot = Path(temp_dir) / f"source{ref.suffix or '.audio'}"
            shutil.copyfile(ref, snapshot)
            fingerprint = file_fingerprint(snapshot)
            encoded = _prepare_voice_reference(snapshot)
        CONFIG["voice_ref_b64"] = encoded
        CONFIG["voice_ref_snapshot_path"] = str(ref)
        CONFIG["voice_ref_snapshot_signature"] = signature
        CONFIG["voice_ref_source_signature"] = signature
        CONFIG["voice_ref_fingerprint"] = fingerprint
        return encoded


def _tts_mimo(text, output_path, rate="+0%", pitch="+0Hz", emotion=None):
    """使用 Xiaomi MiMo-V2.5-TTS 合成，按需用参考音频克隆音色。

    MiMo-v2.5-tts 是 instruct-TTS：user 消息里的自然语言指令控制整句的情绪/语气/语速。
    每段 narration 的 `emotion` 标签即写进该指令，让解说有起伏、不机械。"""
    voice_ref = CONFIG["voice_ref"]
    voice_ref_b64 = _cache_prepared_voice_reference(voice_ref) if voice_ref else None
    payload = {
        "model": "mimo-v2.5-tts-voiceclone" if voice_ref else CONFIG["mimo_tts_model"],
        "messages": [
            {"role": "user", "content": _mimo_tts_style_instruction(rate, pitch, emotion)},
            {"role": "assistant", "content": text},
        ],
        "audio": {
            "format": "wav",
            "voice": (
                f"data:audio/wav;base64,{voice_ref_b64}"
                if voice_ref else CONFIG["mimo_tts_voice"]
            ),
        },
    }
    resp = mimo_tts_api_call(payload)
    try:
        audio_data = resp["choices"][0]["message"]["audio"]["data"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("MiMo-TTS 响应缺少 audio.data") from exc
    try:
        output_path.write_bytes(base64.b64decode(audio_data))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("MiMo-TTS 返回的 audio.data 不是有效 base64") from exc


def main():
    ap = argparse.ArgumentParser(
        description="video-voiceover: synthesize narration audio segments from narration.json.")
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--narration", default=None,
                    help="narration json (default: <work-dir>/narration.json)")
    ap.add_argument("--mimo-voice", default=None, help="MiMo TTS voice name")
    ap.add_argument(
        "--tts-provider",
        default=os.environ.get("TTS_PROVIDER", "auto"),
        choices=["auto", "mimo-tts", "fish-audio", "index-tts"],
        help="TTS provider (default: MiMo when configured, otherwise Fish Audio)",
    )
    ap.add_argument("--voice-ref", default=None,
                    help="reference audio (wav/mp3/etc.) for mimo-v2.5-tts-voiceclone")
    ap.add_argument("--allow-partial-tts", action="store_true",
                    help="allow output when some narration segments fail TTS")
    ap.add_argument(
        "--preserve-approved-text",
        action="store_true",
        help="fail closed when approved narration exceeds its window; never auto-truncate/resynthesize",
    )
    args = ap.parse_args()
    work_dir = Path(args.work_dir)
    CONFIG["tts_provider"] = args.tts_provider
    index_provider.load_private_config(CONFIG, os.environ)
    CONFIG["preserve_approved_text"] = args.preserve_approved_text
    CONFIG["voice_ref"] = (
        args.voice_ref if args.voice_ref is not None else os.environ.get("VOICE_REF", "").strip()
    )
    if args.mimo_voice:
        CONFIG["mimo_tts_voice"] = args.mimo_voice
    if args.mimo_voice and CONFIG["voice_ref"]:
        ap.error("--mimo-voice and --voice-ref are mutually exclusive")
    if args.tts_provider == "fish-audio" and (args.mimo_voice or CONFIG["voice_ref"]):
        ap.error("--mimo-voice/--voice-ref are only supported by the MiMo TTS provider")
    try:
        index_provider.validate_cli_options(args.tts_provider, args.mimo_voice, CONFIG["voice_ref"])
    except ValueError as exc:
        ap.error(str(exc))
    # Voice-reference normalization is intentionally lazy: a fully cached rerun should not
    # invoke ffmpeg. _tts_mimo uses a process-wide lock so a fresh parallel run still converts
    # the reference exactly once.
    if args.allow_partial_tts:
        CONFIG["allow_partial_tts"] = True
    if args.narration:
        narration_path = Path(args.narration)
    else:
        # Cut mode is cut-first/narrate-second: narration.json is already on the output
        # timeline, so the default needs no remapping.
        narration_path = work_dir / "narration.json"
    if CONFIG["preserve_approved_text"]:
        archive_current_meta(work_dir / "tts_meta.json")
    narration = json.loads(narration_path.read_text(encoding="utf-8"))
    tts_segments, engine_used, failures = synthesize_tts(narration, work_dir)
    meta = _build_tts_meta(tts_segments, engine_used, narration_path.name, failures)
    _write_tts_meta_atomically(work_dir / "tts_meta.json", meta)
    if failures:
        log(f"配音完成但缺 {len(failures)} 段：成片可预览但不建议直接发布")
    log(f"配音完成: {len(tts_segments)} 段, 引擎 {engine_used}")
    print(json.dumps({"status": "voiced", "segments": len(tts_segments), "engine": engine_used,
                      "partial": bool(failures), "failures": len(failures),
                      "tts_meta": str(work_dir / "tts_meta.json")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
