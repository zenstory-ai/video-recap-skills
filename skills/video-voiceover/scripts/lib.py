"""Self-contained config + utilities for this skill (no cross-skill imports).
Merged from the shared core; reads the same env vars as the rest of the bundle."""
import json
import math
import os
import re
import subprocess
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


# ── 配置 ──────────────────────────────────────────────────────────────
DEFAULT_MIMO_API_URL = "https://api.xiaomimimo.com/v1"
DEFAULT_MIMO_TOKEN_PLAN_CLUSTER = "cn"
MIMO_TOKEN_PLAN_API_URLS = {
    "cn": "https://token-plan-cn.xiaomimimo.com/v1",
    "sgp": "https://token-plan-sgp.xiaomimimo.com/v1",
    "ams": "https://token-plan-ams.xiaomimimo.com/v1",
}
DEFAULT_MIMO_ASR_MODEL = "mimo-v2.5-asr"  # speech-to-text
DEFAULT_MIMO_TTS_MODEL = "mimo-v2.5-tts"  # text-to-speech
DEFAULT_FISH_TTS_API_URL = "https://api.fish.audio/v1/tts"
DEFAULT_FISH_TTS_MODEL = "s2.1-pro-free"
DEFAULT_FISH_TTS_REFERENCE_ID = "5653cea4ac83480aaf2bf45406556185"


def normalize_api_url(raw_url):
    """Normalize a MiMo (OpenAI-compatible) base URL or chat/completions endpoint."""
    url = raw_url.rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    return f"{url}/chat/completions"


def is_mimo_token_plan_key(api_key):
    """Return True for Xiaomi MiMo Token Plan keys, which use token-plan base URLs."""
    return api_key.startswith("tp-")


def default_mimo_api_url(is_token_plan):
    """Pick the correct MiMo base URL for pay-as-you-go vs Token Plan keys.

    MiMo uses independent credentials for pay-as-you-go (`sk-*`) and Token Plan
    (`tp-*`). Token Plan keys must be sent to the Token Plan cluster base URL,
    not the pay-as-you-go `api.xiaomimimo.com` endpoint.

    The caller classifies its own key with `is_mimo_token_plan_key` and passes only
    that bit: a credential never reaches a function whose return value is logged.
    """
    if not is_token_plan:
        return DEFAULT_MIMO_API_URL
    cluster = os.environ.get("MIMO_TOKEN_PLAN_CLUSTER", DEFAULT_MIMO_TOKEN_PLAN_CLUSTER).strip().lower()
    if cluster not in MIMO_TOKEN_PLAN_API_URLS:
        raise ValueError(
            f"MIMO_TOKEN_PLAN_CLUSTER must be one of {sorted(MIMO_TOKEN_PLAN_API_URLS)}, got {cluster!r}"
        )
    return MIMO_TOKEN_PLAN_API_URLS[cluster]


def _env_number(name, default, cast, minimum):
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = cast(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a {cast.__name__}, got {raw!r}") from exc
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {raw!r}")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {raw!r}")
    return value


def env_int(name, default, *, minimum=None):
    """Read an integer env var, rejecting malformed or below-minimum values."""
    return _env_number(name, default, int, minimum)


def env_float(name, default, *, minimum=None):
    """Read a float env var, rejecting malformed or below-minimum values."""
    return _env_number(name, default, float, minimum)


def env_bool(name, default=False):
    """Read common boolean env var forms (1/true/yes/y/on, 0/false/no/n/off)."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    text = raw.strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean (1/true/yes/on or 0/false/no/off), got {raw!r}")


# Single MiMo credential powers ASR + TTS. The TTS override (MIMO_TTS_API_KEY / MIMO_TTS_API_URL)
# is optional and falls back to MIMO_API_KEY / MIMO_API_URL. Token-Plan keys (tp-*) auto-route to
# the Token-Plan cluster base URL; pay-as-you-go keys use api.xiaomimimo.com.
_mimo_api_key = os.environ.get("MIMO_API_KEY", "")
_mimo_tts_api_key = os.environ.get("MIMO_TTS_API_KEY", "") or _mimo_api_key
_raw_api_url = os.environ.get("MIMO_API_URL") or default_mimo_api_url(is_mimo_token_plan_key(_mimo_api_key))
_raw_mimo_tts_api_url = (
    os.environ.get("MIMO_TTS_API_URL")
    or os.environ.get("MIMO_API_URL")
    or default_mimo_api_url(is_mimo_token_plan_key(_mimo_tts_api_key))
)

CONFIG = {
    "mimo_api_url": normalize_api_url(_raw_api_url),
    "mimo_api_key": _mimo_api_key,
    "mimo_tts_api_url": normalize_api_url(_raw_mimo_tts_api_url),
    "mimo_tts_api_key": _mimo_tts_api_key,
    "mimo_tts_env_var": "MIMO_TTS_API_KEY" if os.environ.get("MIMO_TTS_API_KEY") else "MIMO_API_KEY",
    "mimo_asr_model": os.environ.get("MIMO_ASR_MODEL", DEFAULT_MIMO_ASR_MODEL),
    "mimo_tts_model": os.environ.get("MIMO_TTS_MODEL", DEFAULT_MIMO_TTS_MODEL),
    "mimo_tts_voice": os.environ.get("MIMO_TTS_VOICE", "冰糖"),
    "voice_ref": os.environ.get("VOICE_REF", "").strip(),  # optional arbitrary reference audio for narration voice clone
    "mimo_tts_style": os.environ.get(
        "MIMO_TTS_STYLE",
        "自然、清晰、有感染力，像在给观众讲故事；随剧情起伏，该紧张时紧张、该动情时动情，不平铺直叙。",
    ),
    "tts_provider": os.environ.get("TTS_PROVIDER", "auto").strip().lower(),
    "tts_timeout": env_int("TTS_TIMEOUT", 300, minimum=1),
    "fish_api_key": os.environ.get("FISH_API_KEY", ""),
    "fish_tts_api_url": os.environ.get("FISH_TTS_API_URL", DEFAULT_FISH_TTS_API_URL),
    "fish_tts_model": os.environ.get("FISH_TTS_MODEL", DEFAULT_FISH_TTS_MODEL),
    "fish_tts_reference_id": os.environ.get(
        "FISH_TTS_REFERENCE_ID", DEFAULT_FISH_TTS_REFERENCE_ID
    ).strip(),
    "mimo_disable_thinking": env_bool("MIMO_DISABLE_THINKING", True),
    "breath_ms": 250,  # 段间呼吸空间(ms)；block recap 块内连贯、块间留原声呼吸
    "narration_speed": env_float("NARRATION_SPEED", 1.15, minimum=0.5),  # 解说整体提速(atempo)，默认回到可懂区间；长片可设 1.0
    "narration_cumulative_tempo_max": env_float("NARRATION_CUMULATIVE_TEMPO_MAX", 1.35, minimum=1.0),  # TTS rate × 全局 atempo × 段内 atempo 的累计上限
    "narration_cumulative_tempo_hard_max": env_float("NARRATION_CUMULATIVE_TEMPO_HARD_MAX", 1.40, minimum=1.0),  # QC/阻断硬上限
    "tts_segment_tempo_max": env_float("TTS_SEGMENT_TEMPO_MAX", 1.20, minimum=1.0),  # 兼容旧段内 atempo 上限；实际会被累计预算收紧
    "tts_dynamic_params": True,  # 启用动态语速调节
    "tts_workers": env_int("TTS_WORKERS", 4, minimum=1),  # TTS 并行合成线程数
    "tts_retries": env_int("TTS_RETRIES", 3, minimum=1),  # 单段 TTS 失败重试次数
    "allow_partial_tts": env_bool("ALLOW_PARTIAL_TTS", False),
    "tts_segment_normalize": env_bool("TTS_SEGMENT_NORMALIZE", True),  # 单段 TTS RMS 归一，降低段间忽大忽小
    "tts_segment_target_rms_dbfs": env_float("TTS_SEGMENT_TARGET_RMS_DBFS", -20.0),
    "tts_segment_peak_limit": env_float("TTS_SEGMENT_PEAK_LIMIT", 0.98, minimum=0.1),
}


def narration_tempo_budget(tts_rate_offset=0.0):
    """Return the canonical tempo budget shared by voiceover and assemble."""
    global_speed = CONFIG["narration_speed"]
    rate_factor = 1.0 + tts_rate_offset
    cumulative_max = CONFIG["narration_cumulative_tempo_max"]
    hard_max = max(cumulative_max, CONFIG["narration_cumulative_tempo_hard_max"])
    legacy_segment_cap = CONFIG["tts_segment_tempo_max"]
    segment_tempo_max = max(1.0, min(legacy_segment_cap, cumulative_max / (global_speed * rate_factor)))
    return {
        "global_narration_speed": global_speed,
        "tts_rate_factor": rate_factor,
        "cumulative_tempo_max": cumulative_max,
        "cumulative_tempo_hard_max": hard_max,
        "segment_tempo_max": segment_tempo_max,
        "max_raw_duration_factor": global_speed * segment_tempo_max,
    }


def log(msg):
    print(f"[video-recap] {msg}", flush=True)


def run_cmd(cmd, **kwargs):
    """运行命令列表，返回 CompletedProcess"""
    display = " ".join(
        str(part) if len(str(part)) <= 240 else str(part)[:237] + "..." for part in cmd
    )
    log(f"运行: {display}")
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def get_video_duration(video_path):
    """获取媒体时长（秒）"""
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "csv=p=0", str(video_path)]
    result = run_cmd(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe 无法读取时长: {video_path}: {result.stderr.strip()}")
    return float(result.stdout.strip())


def file_identity(path):
    """{size, mtime_ns} — the cache identity of an input file (no content read)."""
    st = os.stat(os.fspath(path))
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


def _retry_after_seconds(value, fallback):
    """Parse Retry-After seconds or HTTP-date; return fallback on malformed input."""
    if not value:
        return fallback
    try:
        return max(fallback, max(0, int(value)))
    except (TypeError, ValueError):
        pass
    try:
        retry_at = parsedate_to_datetime(value)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(fallback, max(0, int((retry_at - datetime.now(timezone.utc)).total_seconds())))
    except (TypeError, ValueError, IndexError, OverflowError):
        return fallback


_ERROR_DATA_URL_RE = re.compile(
    r"data:(?:audio|video|image)/[^;,\s\"'<>]+;base64,[A-Za-z0-9+/=]+",
    re.IGNORECASE,
)
_ERROR_KEY_RE = re.compile(r"\b(?:tp|sk)-[A-Za-z0-9_-]{8,}\b")


def _sanitize_api_error(value, limit=500, *, extra_secrets=()):
    """Bound transport diagnostics without echoing request media or credentials."""
    text = _ERROR_DATA_URL_RE.sub("<redacted-data-url>", str(value))
    text = _ERROR_KEY_RE.sub("<redacted-key>", text)
    for secret in extra_secrets:
        if secret:
            text = text.replace(str(secret), "<redacted-key>")
    return text[:limit]


def _prepare_api_payload(payload):
    """Normalize payload fields for MiMo's OpenAI-compatible chat/completions API."""
    normalized = dict(payload)
    if "max_tokens" in normalized and "max_completion_tokens" not in normalized:
        normalized["max_completion_tokens"] = normalized.pop("max_tokens")
    if (
        CONFIG["mimo_disable_thinking"]
        and not normalized["model"].endswith(("-tts", "-asr"))
        and "thinking" not in normalized
    ):
        # MiMo V2.5 may spend small max_completion_tokens budgets on reasoning_content.
        # The recap pipeline needs visible text, so disable thinking unless set explicitly.
        normalized["thinking"] = {"type": "disabled"}
    return normalized


def mimo_tts_api_call(payload):
    """Call the MiMo TTS endpoint."""
    return api_call(
        payload,
        max_retries=10,
        api_url=CONFIG["mimo_tts_api_url"],
        api_key=CONFIG["mimo_tts_api_key"],
        api_env_var=CONFIG["mimo_tts_env_var"],
    )


def mimo_asr_api_call(payload):
    """Call the MiMo speech-recognition (ASR) endpoint."""
    return api_call(
        payload,
        max_retries=10,
        api_url=CONFIG["mimo_api_url"],
        api_key=CONFIG["mimo_api_key"],
        api_env_var="MIMO_API_KEY",
    )


def api_call(payload, *, api_url, api_key, api_env_var, max_retries=8):
    """调用 OpenAI-compatible API，带重试。

    集群的 429 限流是常态而非错误，所以重试更耐心（更多次数 + 退避封顶 60s + 遵从 Retry-After），
    避免一次瞬时限流就中止整个阶段。配额窗口常以分钟计，所以 429 在没有 Retry-After 时也至少等 10s。
    """
    endpoint = normalize_api_url(api_url)
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "video-recap/1.0",
        "api-key": api_key,
    }
    data = json.dumps(_prepare_api_payload(payload)).encode("utf-8")

    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(endpoint, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=300) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = _sanitize_api_error(e.read().decode("utf-8", errors="replace"))
            wait = min(2 ** attempt, 60)
            if e.code == 429:
                retry_after = e.headers.get("Retry-After")
                wait = _retry_after_seconds(retry_after, max(wait, 10))
                log(f"API 速率限制 (尝试 {attempt+1}/{max_retries}), 等待 {wait}s")
            elif e.code == 401:
                raise RuntimeError(f"API 认证失败 (401)。请检查 {api_env_var} 和 API URL 是否匹配。")
            elif e.code == 403:
                hint = "API 访问被拒绝 (403)。"
                if "1010" in body or "cloudflare" in body.lower():
                    hint += "IP 被 Cloudflare 限流，请等待几分钟后重试。"
                    raise RuntimeError(hint)
                hint += "请检查 API key 权限和 API URL 设置。"
                raise RuntimeError(hint)
            elif e.code == 405:
                raise RuntimeError("API 端点不可用 (405)，可能被 WAF 拦截。请检查 MIMO_API_URL 或稍后重试。")
            elif e.code == 503:
                log(f"API 服务暂不可用 (503)，等待 {wait}s (尝试 {attempt+1}/{max_retries})")
            elif e.code == 524:
                # Cloudflare 超时：服务端处理超时，需要更长退避
                wait = max(wait, 4 * (attempt + 1))
                log(f"API 超时 (524)，等待 {wait}s (尝试 {attempt+1}/{max_retries})")
            else:
                log(f"API 调用失败 (尝试 {attempt+1}/{max_retries}): HTTP {e.code} — {body}")
            if attempt < max_retries - 1:
                time.sleep(wait)
            else:
                raise RuntimeError(f"API 调用失败 {max_retries} 次: HTTP {e.code} — {body}")
        except Exception as e:  # noqa: BLE001 - transport/decode faults are all retryable here
            wait = min(2 ** attempt, 60)
            safe_error = _sanitize_api_error(e)
            log(f"API 调用失败 (尝试 {attempt+1}/{max_retries}): {safe_error}")
            if attempt < max_retries - 1:
                log(f"等待 {wait}s 后重试...")
                time.sleep(wait)
            else:
                raise RuntimeError(f"API 调用失败 {max_retries} 次: {safe_error}")


def _text_char_count(text):
    """计算文本的有效字数（去除标点和空白，这些不占 TTS 朗读时间）。"""
    return len(re.sub(r'[，。！？、；：…“”‘’《》〈〉\s"\'「」『』（）()【】\[\]—～·,.!?;:\\-]', '', text))


def _truncate_at_sentence(text, max_chars):
    """在句子边界截断，不产生残句。max_chars 按有效字符计（不含标点空白）。"""
    if _text_char_count(text) <= max_chars:
        return text
    eff = 0
    cutoff = len(text)
    for i, ch in enumerate(text):
        eff += 1 if _text_char_count(ch) else 0
        if eff > max_chars:
            cutoff = i + 1
            break
    idx = max(text[:cutoff].rfind(sep) for sep in ['。', '！', '？', '!', '?'])
    if idx > 0:
        return text[:idx + 1]
    idx = max(text[:cutoff].rfind(sep) for sep in ['，', '、', '；', ','])
    if idx > 3:
        return text[:idx] + '。'
    return ""
