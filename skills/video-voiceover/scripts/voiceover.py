import argparse
import base64
import json
import os
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from approved_text_policy import (
    PRESERVE_APPROVED_TEXT_POLICY,
    ApprovedTextDurationError,
    archive_current_meta,
    enforce_duration,
    policy_name,
    validate_required_texts,
    write_json_atomically as _write_tts_meta_atomically,
)
from providers.fish_audio import fish_speed, synthesize_fish_audio
import providers.index_tts as index_provider
from lib import (
    CONFIG,
    file_identity,
    get_video_duration,
    log,
    mimo_tts_api_call,
    narration_tempo_budget,
    run_cmd,
)
import tts_cache
from tts_audio import (
    _maybe_normalize_tts_wav,
    implausible_tts_duration,
    rejected_take_hint,
    rejected_take_path,
)

SUPPORTED_TTS_ENGINES = {"mimo-tts", "fish-audio", "index-tts"}
SEGMENT_AUDIO_SCHEMA_VERSION = 1
VOICE_REFERENCE_PREP_VERSION = 1


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


def _synthesize_segment(i, seg, narration, tts_dir, engine, prepared=None, voice_ref_b64=None):
    """合成单个 TTS 段（线程安全），支持 resume 跳过已有文件。

    `prepared` is the `_prepare_tts_segment` tuple of a segment synthesize_tts already probed
    as a cache miss; passing it skips a second sidecar read + settings resolution.
    `voice_ref_b64` is the reference audio synthesize_tts transcoded once for this run."""
    if prepared is None:
        prepared = _prepare_tts_segment(i, seg, narration, tts_dir, engine)
        if prepared is None:
            return None
        cached = _reuse_tts_segment_cache(
            i, seg, prepared[1], prepared[4], engine, _parse_rate_offset(prepared[2]))
        if cached:
            return cached
    text, output_wav, rate, pitch, cache_inputs = prepared

    # output_wav may be a hard link into the cache (another block's audio until now): unlink it
    # so the provider writes a new file instead of overwriting that shared audio in place.
    _cleanup_partial_tts_outputs(output_wav)
    provider_receipt = _run_tts_engine(
        engine, text, output_wav, rate=rate, pitch=pitch, emotion=seg.get("emotion"),
        voice_ref_b64=voice_ref_b64, segment_number=i + 1,
    )

    dur = get_video_duration(output_wav)
    rate_offset = _parse_rate_offset(rate)
    try:
        _check_segment_window(i, seg, text, dur, rate_offset)
    except ApprovedTextDurationError:
        _cleanup_partial_tts_outputs(output_wav)
        raise

    norm_meta = _maybe_normalize_tts_wav(output_wav)
    if norm_meta:
        dur = get_video_duration(output_wav)
    _write_tts_segment_cache(output_wav, cache_inputs, text, dur, norm_meta, provider_receipt)
    return _build_tts_segment_result(
        i, seg, text, output_wav, dur, rate_offset, norm_meta, provider_receipt)


def _check_segment_window(index, seg, spoken_text, duration, rate_offset):
    """Fail an approved-text block that overflows its window; log any other overflow."""
    seg_slot = seg["end"] - seg["start"]
    seg_pause = seg.get("pause_after_ms", CONFIG["breath_ms"]) / 1000
    available = max(0.5, seg_slot - seg_pause)
    raw_budget = available * narration_tempo_budget(rate_offset)["max_raw_duration_factor"]
    enforce_approved_text_policy(index, seg, seg["narration"], spoken_text, duration,
                                 available, raw_budget)
    if duration > raw_budget:
        # Never shorten authored text here: a truncated segment always blocked later as
        # `truncated_speech`. assemble either fits it with bounded tempo or blocks it as
        # `no_safe_fit` before the video encode.
        log(
            f"  段 {index+1}: 超出预算 {duration:.1f}s > {raw_budget:.1f}s，保留原稿，"
            "交由 assemble 有界提速或在渲染前阻断"
        )


def _build_tts_segment_result(index, seg, text, output_wav, duration, rate_offset,
                              norm_meta=None, provider_receipt=None):
    budget = narration_tempo_budget(rate_offset)
    authored_text = _clean_narration_text(seg["narration"])
    # voiceover never shortens text; still flag any spoken/authored mismatch (e.g. a reused sidecar).
    resolved_truncated = text != authored_text
    result = {
        "segment_audio_schema_version": SEGMENT_AUDIO_SCHEMA_VERSION,
        "index": index,
        "start": seg["start"],
        "end": seg["end"],
        "narration": authored_text,
        "authored_text": seg["narration"],
        "spoken_text": text,
        "truncated": resolved_truncated,
        "truncate_reason": "sentence_boundary" if resolved_truncated else "none",
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


def _voice_record(engine):
    """Which voice this run actually used: provider, model, voice id or reference audio."""
    settings = tts_settings_payload(engine)
    if engine == "fish-audio":
        return {"provider": engine, "model": settings["fish_tts_model"],
                "voice_id": settings["fish_tts_reference_id"], "reference": None}
    if engine == "index-tts":
        return {"provider": engine, "model": None,
                "voice_id": settings["index_tts_voice"], "reference": None}
    reference = settings.get("voice_ref_identity")
    return {"provider": engine, "model": settings["mimo_tts_model"],
            "voice_id": None if reference else settings["mimo_tts_voice"],
            "reference": reference}


def _build_tts_meta(segments, engine, narration_name, failures):
    """Stable tts_meta.json payload, including partial-failure visibility."""
    return {
        "segments": segments,
        "engine": engine,
        "voice": _voice_record(engine),
        "narration": narration_name,
        "partial": bool(failures),
        "failures": failures,
    }


def synthesize_tts(narration, work_dir):
    """合成解说音频（并行）。Returns (segments, engine, failures)."""
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
    # A fully cached narration needs no credential: probe every segment's cache once here;
    # hits are final and only misses reach the workers (with their prepared inputs).
    segments = []
    misses = []
    failures = []
    for i, seg in enumerate(narration):
        prepared = _prepare_tts_segment(i, seg, narration, tts_dir, cache_engine)
        if prepared is None:
            continue
        try:
            cached = _reuse_tts_segment_cache(
                i, seg, prepared[1], prepared[4], cache_engine, _parse_rate_offset(prepared[2]))
        except ApprovedTextDurationError as e:
            failures.append(_tts_failure_record(i, seg, e))
            continue
        if cached:
            segments.append(cached)
        else:
            misses.append((i, seg, prepared))
    if not segments and not misses and not failures:
        raise RuntimeError("narration.json 没有可配音的有效文本，已中止以避免生成无解说视频")
    if misses:
        engine = _synthesize_misses(misses, narration, tts_dir, segments, failures)
    else:
        engine = cache_engine
        log(f"TTS 引擎: {cache_engine} (cache)")
    return _finish_tts(narration, segments, engine, failures)


def _synthesize_misses(misses, narration, tts_dir, segments, failures):
    """Synthesize the cache misses in parallel into `segments` / `failures`; returns the engine."""
    engine = resolve_tts_engine()
    voice_ref = CONFIG["voice_ref"]

    # Transcode the reference once per run, and only now: a fully cached rerun never runs
    # ffmpeg. The misses' cache keys hold the identity probed above, so a reference edited
    # meanwhile would label new audio with the old identity; fail instead.
    voice_ref_b64 = None
    if voice_ref and engine == "mimo-tts":
        probed_identity = misses[0][2][4]["settings"]["voice_ref_identity"]  # [4] = cache inputs
        voice_ref_b64 = _prepare_voice_reference(voice_ref)
        if _voice_reference_signature(voice_ref) != probed_identity:
            raise RuntimeError(f"参考音频在配音期间被修改: {voice_ref}；请重新运行")

    log(f"TTS 引擎: {engine}")

    max_workers = min(len(misses), CONFIG["tts_workers"])

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _synthesize_segment, i, seg, narration, tts_dir, engine, prepared,
                voice_ref_b64=voice_ref_b64,
            ): i
            for i, seg, prepared in misses
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
    return engine


def _finish_tts(narration, segments, engine, failures):
    """Apply the failure policy to one run's results; returns (segments, engine, failures)."""
    segments.sort(key=lambda x: x["index"])
    failures.sort(key=lambda x: x["index"])
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
        missing = "、".join(f"段 {f['index'] + 1}" for f in failures[:8])
        more = "…" if len(failures) > 8 else ""
        log(
            f"警告: TTS 部分失败 {len(failures)}/{len(narration)} 段（{missing}{more}），"
            "成片可预览但不建议直接发布；详见 tts_meta.json failures"
        )
    if not segments:
        raise RuntimeError("TTS 没有生成任何有效解说音频，已中止以避免生成无解说视频")
    return segments, engine, failures


def _run_tts_engine(engine, text, output_wav, rate="+0%", pitch="+0Hz", emotion=None,
                    voice_ref_b64=None, segment_number=None):
    """Run one TTS engine with retry and remove partial files after failures.

    `engine` comes from resolve_tts_engine; index-tts controls were already forced to the
    provider defaults by _prepare_tts_segment. A take rejected as implausibly long is kept as
    `<name>.rejected.wav` (the latest one) so the user can listen before relaxing the bound."""
    retries = CONFIG["tts_retries"]
    last_error = None
    rejected = rejected_take_path(output_wav)
    rejected.unlink(missing_ok=True)
    label = f"段 {segment_number} " if segment_number is not None else ""

    for attempt in range(1, retries + 1):
        try:
            _cleanup_partial_tts_outputs(output_wav)
            receipt = None
            if engine == "mimo-tts":
                _tts_mimo(text, output_wav, rate=rate, pitch=pitch, emotion=emotion,
                          voice_ref_b64=voice_ref_b64)
            elif engine == "fish-audio":
                synthesize_fish_audio(text, output_wav, rate=rate)
            else:
                receipt = index_provider.synthesize_configured(text, output_wav, CONFIG)
            duration = get_video_duration(output_wav)
            if duration <= 0:
                raise RuntimeError(f"{engine} 输出音频时长无效")
            # A hallucinated reading (the text plus invented speech) is a failed attempt: it is
            # retried, never cached, and never reaches assemble as an over-budget block.
            implausible = implausible_tts_duration(text, duration)
            if implausible:
                os.replace(output_wav, rejected)
                raise RuntimeError(implausible)
            rejected.unlink(missing_ok=True)
            return receipt
        except Exception as exc:
            last_error = exc
            _cleanup_partial_tts_outputs(output_wav)
            if attempt < retries:
                wait = min(2 ** (attempt - 1), 8)
                log(f"  {label}TTS 重试 {attempt+1}/{retries}: {exc}，等待 {wait}s")
                time.sleep(wait)

    hint = rejected_take_hint(rejected)
    raise RuntimeError(f"{engine} 合成失败: {last_error}{hint}") from last_error


def _cleanup_partial_tts_outputs(output_wav):
    """Remove stale partial media files before/after a failed TTS attempt."""
    wav_path = Path(output_wav)
    for path in (
        wav_path,
        wav_path.with_suffix(".mp3"),
        Path(str(wav_path) + ".part"),
        tts_cache.legacy_sidecar_path(output_wav),
    ):
        path.unlink(missing_ok=True)


def _prepare_tts_segment(index, seg, narration, tts_dir, engine):
    text = _clean_narration_text(seg["narration"])
    if not text:
        return None
    output_wav = tts_dir / f"narr_{index:03d}.wav"
    if engine == "index-tts":
        rate, pitch = index_provider.default_controls(seg)
    else:
        rate, pitch = _compute_tts_params(text, narration, index)
    cache_inputs = _tts_segment_cache_inputs(engine, seg, text, rate, pitch)
    return text, output_wav, rate, pitch, cache_inputs


def _reuse_tts_segment_cache(index, seg, output_wav, cache_inputs, engine, rate_offset):
    """A result from the content-addressed cache, with `output_wav` pointing at its audio.

    `rate_offset` is this block's own nominal rate. The key holds only what the provider
    receives, so a take synthesized at another nominal rate that produced the same request
    is reused, and reports this block's rate exactly as a fresh synthesis would."""
    cached = tts_cache.load(output_wav.parent, cache_inputs)
    if cached is None:
        return None
    if engine == "index-tts" and not index_provider.valid_cached_receipt(cached, CONFIG):
        return None
    implausible = implausible_tts_duration(cached["spoken_text"], cached["audio_duration"])
    if implausible:
        # Written before this bound existed: re-synthesize instead of blocking every rerun.
        log(f"  段 {index+1}: 不复用缓存，重新合成：{implausible}")
        return None
    # The window is not part of the key (it never changes the audio), so an approved-text
    # block moved into a shorter window is re-checked here instead of re-synthesized.
    _check_segment_window(index, seg, cached["spoken_text"], cached["audio_duration"],
                          rate_offset)
    tts_cache.materialize(output_wav.parent, cache_inputs, output_wav)
    # The sidecar's audio identity (size, mtime_ns) still matches the WAV that produced
    # `audio_duration`; re-probing would be one ffprobe process per segment on every rerun.
    log(f"  段 {index+1}: 复用已有 ({cached['audio_duration']:.1f}s)")
    return _build_tts_segment_result(
        index,
        seg,
        cached["spoken_text"],
        output_wav,
        cached["audio_duration"],
        rate_offset,
        cached["normalization"],
        cached.get("provider_receipt"),
    )


def _provider_prosody_request(engine, rate, pitch, emotion):
    """The prosody part of the request the provider actually receives for one block.

    MiMo gets a natural-language instruction in which rate offsets only change the wording
    at >= +6% or <= -3% (pitch only as zero/non-zero); Fish Audio gets a numeric speed and
    ignores pitch and emotion; index-tts takes no per-block controls."""
    if engine == "mimo-tts":
        return {"instruction": _mimo_tts_style_instruction(rate, pitch, emotion)}
    if engine == "fish-audio":
        return {"speed": fish_speed(rate)}
    return {}


def _tts_segment_cache_inputs(engine, seg, source_text, rate, pitch):
    """The exact inputs that make cached audio safe to reuse (compared by equality).

    Only what changes the audio: text, the prosody request the provider receives, and
    provider/voice settings. The block's position and window are left out, so deleting or
    inserting a block reuses every other block's audio; a block whose position changes its
    nominal rate is re-synthesized only if the provider would receive a different request."""
    payload = {
        "engine": engine,
        "source_text": source_text,
        "provider_request": _provider_prosody_request(engine, rate, pitch, seg.get("emotion")),
        "settings": tts_settings_payload(engine),
    }
    if authored_text_policy() == PRESERVE_APPROVED_TEXT_POLICY:
        payload.update({
            "authored_text_policy": PRESERVE_APPROVED_TEXT_POLICY,
            "authored_text": seg["narration"],
            "provider_text_cleanup": "clean-narration-text-v1",
        })
    return payload


def _write_tts_segment_cache(output_wav, cache_inputs, spoken_text, duration,
                             norm_meta=None, provider_receipt=None):
    """Store the finished block WAV under its non-secret synthesis inputs for reuse."""
    tts_cache.store(output_wav, cache_inputs, {
        "spoken_text": spoken_text,
        "audio_duration": duration,
        "normalization": norm_meta or None,
        "provider_receipt": provider_receipt,
    })


def _configured_tts_engine_for_cache():
    """Resolve provider intent without requiring a live credential for cache probes."""
    provider = CONFIG["tts_provider"]
    if provider == "auto":
        if CONFIG["mimo_tts_api_key"] or not CONFIG["fish_api_key"]:
            return "mimo-tts"
        return "fish-audio"
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
        f"没有可用的 TTS 引擎：请设置 {CONFIG['mimo_tts_env_var']}（MiMo TTS 需要）。"
    )


def tts_settings_payload(engine):
    """Return non-secret TTS settings that materially affect generated audio."""
    settings = {
        "engine": engine,
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
        settings["voice_ref_identity"] = _voice_reference_signature(ref_path)
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
    """Identity of the reference source: resolved path plus size and mtime_ns."""
    ref = Path(ref_path).expanduser().resolve()
    return {"path": str(ref), **file_identity(ref)}


def _tts_mimo(text, output_path, rate="+0%", pitch="+0Hz", emotion=None, voice_ref_b64=None):
    """使用 Xiaomi MiMo-V2.5-TTS 合成，按需用参考音频克隆音色。

    MiMo-v2.5-tts 是 instruct-TTS：user 消息里的自然语言指令控制整句的情绪/语气/语速。
    每段 narration 的 `emotion` 标签即写进该指令，让解说有起伏、不机械。
    `voice_ref_b64` is the run's prepared reference; a direct call transcodes the live source."""
    voice_ref = CONFIG["voice_ref"]
    if voice_ref and voice_ref_b64 is None:
        voice_ref_b64 = _prepare_voice_reference(voice_ref)
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
    if args.mimo_voice and args.tts_provider in {"fish-audio", "index-tts"}:
        ap.error("--mimo-voice is only supported by the MiMo TTS provider")
    # A local --voice-ref with fish-audio/index-tts is rejected by synthesize_tts, which also
    # transcodes the reference lazily: once per run, and never for a fully cached rerun.
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
