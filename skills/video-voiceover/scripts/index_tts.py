"""Self-hosted index-tts JSON-to-WAV transport with no redirect forwarding."""

import contextlib
import hashlib
import io
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path


MAX_WAV_BYTES = 64 * 1024 * 1024
MAX_ERROR_BYTES = 512
SPEED_POLICY = "provider-default-no-rate-control"
RECEIPT_SCHEMA = "index-tts-request-receipt"
RECEIPT_VERSION = 1


class _SafeResponseError(RuntimeError):
    pass


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open_without_redirects(request, timeout):
    return urllib.request.build_opener(_NoRedirectHandler()).open(request, timeout=timeout)


def validate_index_tts_config(endpoint, voice):
    """Validate explicit private settings without returning a credential-bearing URL."""
    endpoint = str(endpoint or "")
    voice = str(voice or "").strip()
    if not endpoint:
        raise ValueError("INDEX_TTS_ENDPOINT 必须显式配置")
    if any(ord(char) < 32 or ord(char) == 127 for char in endpoint):
        raise ValueError("INDEX_TTS_ENDPOINT 配置无效或包含禁止字符")
    try:
        parsed = urllib.parse.urlsplit(endpoint)
        port = parsed.port
    except ValueError:
        raise ValueError("INDEX_TTS_ENDPOINT 配置无效") from None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("INDEX_TTS_ENDPOINT 必须是带主机名的 http/https URL")
    if "@" in parsed.netloc or "?" in endpoint or "#" in endpoint:
        raise ValueError("INDEX_TTS_ENDPOINT 禁止 userinfo、query 或 fragment 携带凭证")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("INDEX_TTS_ENDPOINT 配置无效")
    if not voice:
        raise ValueError("INDEX_TTS_VOICE 必须显式配置")
    return endpoint, voice


def endpoint_fingerprint(endpoint):
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()


def load_private_config(config, environ):
    """Read the private index-tts settings; validate them once, only when that provider is selected."""
    endpoint = environ.get("INDEX_TTS_ENDPOINT", "").strip()
    voice = environ.get("INDEX_TTS_VOICE", "").strip()
    if config["tts_provider"] == "index-tts":
        endpoint, voice = validate_index_tts_config(endpoint, voice)
    config["index_tts_endpoint"] = endpoint
    config["index_tts_voice"] = voice
    config["index_tts_cache_revision"] = environ.get("INDEX_TTS_CACHE_REVISION", "").strip()


def cache_settings(config):
    return {
        "index_tts_endpoint_sha256": endpoint_fingerprint(config["index_tts_endpoint"]),
        "index_tts_voice": config["index_tts_voice"],
        "index_tts_speed_policy": SPEED_POLICY,
        "index_tts_cache_revision": config.get("index_tts_cache_revision", ""),
    }


def default_controls(segment):
    unsupported = []
    for key, default in (("emotion", None), ("style", None), ("rate", "+0%"), ("pitch", "+0Hz")):
        if segment.get(key) not in (None, "", default):
            unsupported.append(key)
    if unsupported:
        raise RuntimeError(f"已配置的端点不接受段级控制字段: {', '.join(unsupported)}")
    return "+0%", "+0Hz"


def synthesize_configured(text, output_path, config):
    return synthesize_index_tts(
        text, output_path, endpoint=config["index_tts_endpoint"],
        voice=config["index_tts_voice"], timeout=config["tts_timeout"],
    )


def finalize_receipt(receipt, processed_hash):
    if not receipt:
        return receipt
    receipt["processed_wav_sha256"] = processed_hash
    receipt["raw_wav_retained_as_processed"] = receipt["returned_wav_sha256"] == processed_hash
    receipt["raw_wav_reconstructable_from_receipt"] = False
    return receipt


def valid_cached_receipt(cache_data, config):
    receipt = cache_data.get("provider_receipt")
    if not isinstance(receipt, dict):
        return False
    expected = {
        "receipt_schema": RECEIPT_SCHEMA,
        "receipt_version": RECEIPT_VERSION,
        "provider": "index-tts",
        "requested_voice": config["index_tts_voice"],
        "speed_policy": SPEED_POLICY,
        "processed_wav_sha256": cache_data.get("audio_fingerprint"),
    }
    raw_hash = receipt.get("returned_wav_sha256")
    processed_hash = receipt.get("processed_wav_sha256")
    retained = receipt.get("raw_wav_retained_as_processed")
    if not all(receipt.get(key) == value for key, value in expected.items()):
        return False
    if cache_data.get("processed_wav_sha256") != processed_hash:
        return False
    if not all(
        isinstance(value, str) and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
        for value in (raw_hash, processed_hash)
    ):
        return False
    if not isinstance(retained, bool) or receipt.get("raw_wav_reconstructable_from_receipt") is not False:
        return False
    return not retained or raw_hash == processed_hash


def _discard_http_error_body(error):
    """Drain a bounded slice and close; the body is never surfaced (it may echo private URLs)."""
    with contextlib.suppress(Exception):
        try:
            error.read(MAX_ERROR_BYTES)
        finally:
            error.close()


def _validate_wav(audio):
    if len(audio) <= 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        raise RuntimeError("IndexTTS 返回的内容不是有效 WAV")
    if int.from_bytes(audio[4:8], "little") + 8 > len(audio):
        raise RuntimeError("IndexTTS 返回的 RIFF 声明长度超过响应，音频被截断")
    try:
        with wave.open(io.BytesIO(audio), "rb") as wav:
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            sample_rate = wav.getframerate()
            frames = wav.getnframes()
            decoded = wav.readframes(frames)
    except (EOFError, wave.Error) as exc:
        raise RuntimeError("IndexTTS 返回的 WAV 结构损坏或不受支持") from exc
    if not (1 <= channels <= 8 and 1 <= sample_width <= 4 and 8000 <= sample_rate <= 192000):
        raise RuntimeError("IndexTTS 返回的 WAV 音频参数越界")
    if frames <= 0:
        raise RuntimeError("IndexTTS 返回的 WAV 没有音频帧")
    if len(decoded) != frames * channels * sample_width:
        raise RuntimeError("IndexTTS 返回的 WAV 音频数据被截断")


def synthesize_index_tts(text, output_path, *, endpoint, voice, timeout):
    """POST the exact {voice,text} contract and atomically persist an authentic WAV.

    endpoint/voice were validated by load_private_config; timeout by lib's env_int."""
    body = json.dumps({"voice": voice, "text": text}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "audio/wav",
            "User-Agent": "video-recap/1.0",
        },
        method="POST",
    )
    try:
        with _open_without_redirects(request, timeout) as response:
            content_type = response.headers.get("Content-Type", "").lower()
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_WAV_BYTES:
                raise _SafeResponseError("IndexTTS 返回 WAV 过大，已拒绝读取")
            audio = response.read(MAX_WAV_BYTES + 1)
    except urllib.error.HTTPError as exc:
        _discard_http_error_body(exc)
        if 300 <= exc.code < 400:
            raise RuntimeError("IndexTTS 禁止 HTTP 重定向，未向其他主机转发批准文本") from None
        raise RuntimeError(f"IndexTTS 请求失败 (HTTP {exc.code})，远端返回错误状态") from None
    except urllib.error.URLError as exc:
        kind = "超时" if isinstance(exc.reason, (TimeoutError, socket.timeout)) else "网络"
        raise RuntimeError(f"IndexTTS {kind}错误，未返回音频") from None
    except (TimeoutError, socket.timeout):
        raise RuntimeError("IndexTTS 超时错误，未返回音频") from None
    except ValueError:
        raise RuntimeError("IndexTTS 返回了无效的 Content-Length") from None
    except _SafeResponseError as exc:
        raise RuntimeError(str(exc)) from None
    except Exception:
        raise RuntimeError("IndexTTS 响应读取或关闭失败，未保存音频") from None

    if len(audio) > MAX_WAV_BYTES:
        raise RuntimeError("IndexTTS 返回 WAV 过大，已拒绝保存")
    if "json" in content_type:
        raise RuntimeError("IndexTTS 返回了 JSON 错误响应而不是音频；正文已隐藏")
    _validate_wav(audio)

    output = Path(output_path)
    partial = Path(str(output) + ".part")
    try:
        partial.write_bytes(audio)
        os.replace(partial, output)
    finally:
        partial.unlink(missing_ok=True)
    return {
        "receipt_schema": RECEIPT_SCHEMA,
        "receipt_version": RECEIPT_VERSION,
        "provider": "index-tts",
        "requested_voice": voice,
        "returned_wav_sha256": hashlib.sha256(audio).hexdigest(),
        "speed_policy": SPEED_POLICY,
    }
