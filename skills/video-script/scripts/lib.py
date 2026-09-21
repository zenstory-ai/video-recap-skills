"""Self-contained config + utilities for this skill (no cross-skill imports).
Merged from the shared core; reads the same env vars as the rest of the bundle."""
import json
import math
import os
import re
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
DEFAULT_MIMO_MODEL = "mimo-v2.5"          # VLM / chat (vision understanding)


def normalize_api_url(raw_url):
    """Normalize a MiMo (OpenAI-compatible) base URL or chat/completions endpoint."""
    url = (raw_url or DEFAULT_MIMO_API_URL).rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    return f"{url}/chat/completions"


def is_mimo_token_plan_key(api_key):
    """Return True for Xiaomi MiMo Token Plan keys, which use token-plan base URLs."""
    return str(api_key or "").strip().startswith("tp-")


def default_mimo_api_url(is_token_plan, cluster=None):
    """Pick the correct MiMo base URL for pay-as-you-go vs Token Plan keys.

    MiMo uses independent credentials for pay-as-you-go (`sk-*`) and Token Plan
    (`tp-*`). Token Plan keys must be sent to the Token Plan cluster base URL,
    not the pay-as-you-go `api.xiaomimimo.com` endpoint.

    The caller classifies its own key with `is_mimo_token_plan_key` and passes only
    that bit: a credential never reaches a function whose return value is logged.
    """
    if is_token_plan:
        cluster_name = (cluster or os.environ.get("MIMO_TOKEN_PLAN_CLUSTER") or DEFAULT_MIMO_TOKEN_PLAN_CLUSTER)
        cluster_name = str(cluster_name).strip().lower()
        if cluster_name not in MIMO_TOKEN_PLAN_API_URLS:
            raise ValueError(
                f"MiMo token-plan cluster must be one of {sorted(MIMO_TOKEN_PLAN_API_URLS)}; "
                f"got {cluster_name!r}"
            )
        return MIMO_TOKEN_PLAN_API_URLS[cluster_name]
    return DEFAULT_MIMO_API_URL


def _env_number(name, default, cast, minimum):
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = cast(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a {cast.__name__}; got {raw!r}") from exc
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{name} must be finite; got {raw!r}")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}; got {value}")
    return value


def env_int(name, default, *, minimum=None):
    """Read an integer env var, rejecting malformed or below-minimum values."""
    return _env_number(name, default, int, minimum)


def env_bool(name, default=False):
    """Read common boolean env var forms."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def env_float(name, default, *, minimum=None):
    """Read a float env var, rejecting malformed or below-minimum values."""
    return _env_number(name, default, float, minimum)


# Single MiMo credential powers ASR + VLM + TTS. Per-capability overrides
# (MIMO_VIDEO_API_KEY / MIMO_TTS_API_KEY / MIMO_ASR_API_KEY and their *_API_URL forms)
# are optional and fall back to MIMO_API_KEY / MIMO_API_URL. Token-Plan keys (tp-*) auto-
# route to the Token-Plan cluster base URL; pay-as-you-go keys use api.xiaomimimo.com.
_mimo_api_key = os.environ.get("MIMO_API_KEY", "")
_raw_api_url = os.environ.get("MIMO_API_URL") or default_mimo_api_url(is_mimo_token_plan_key(_mimo_api_key))

CONFIG = {
    "api_url": normalize_api_url(_raw_api_url),
    "api_key": _mimo_api_key,
    "api_env_var": "MIMO_API_KEY",
    "vlm_model": os.environ.get("MIMO_MODEL", DEFAULT_MIMO_MODEL),
    "mimo_disable_thinking": env_bool("MIMO_DISABLE_THINKING", True),
    # TTS 语速（字符/秒）。实测 mimo-tts 冰糖音色中位 ~3.9 字/秒，可用 SPEECH_RATE 覆盖
    # 生成解说时使用 speech_rate * safety_margin 作为约束
    "speech_rate": env_float("SPEECH_RATE", 3.9, minimum=0.5),  # 旧值 3.5 系统性偏低 ~10-17%
    "speech_safety_margin": env_float("SPEECH_SAFETY_MARGIN", 0.85, minimum=0.1),  # 保守系数：TTS 实际语速有 ±20% 波动
    # Block-coverage lint thresholds — promoted from inline .get() literals to real CONFIG keys (tunable; defaults unchanged)
    "narration_coverage_target": 0.7,   # rough first-draft/diagnostic fallback; content-led audio decisions may differ (not a quota)
    "narration_coverage_max": 0.85,     # above this coverage → no_original_blocks (narration is wall-to-wall)
    "narration_coverage_min": 0.5,      # below this coverage → under_narrated
    "original_block_min_seconds": 2.5,  # a deliberate original-audio gap must be at least this long
    "narration_block_min_chars": 16,    # below this avg block size → fragmented_beats
    "breath_ms": 250,  # 段间呼吸空间(ms)；block recap 块内连贯、块间留原声呼吸
    "narration_speed": env_float("NARRATION_SPEED", 1.15, minimum=0.5),  # 解说整体提速(atempo)，默认回到可懂区间；长片可设 1.0
    "narration_tail_pad_seconds": 0.1,  # 解说尾部最少留白；短 slot 会自动压低 delay 避免截断
    "quiet_overlap_min_ratio": 0.8,  # 解说段至少多少比例落在安静窗口内才标记为非对白重叠
    "visual_beat_max_seconds": 18.0,  # 单段解说超过该时长且跨多个帧锚点时给 lint 提醒
    "visual_beat_max_facts": 3,  # 单段解说最多建议覆盖的 frame_facts 锚点数量
    "asr_chunk_min_chars": env_int("ASR_CHUNK_MIN_CHARS", 500, minimum=1),  # brief 中 ASR 写作分块最小字数/词数
    "asr_chunk_max_chars": env_int("ASR_CHUNK_MAX_CHARS", 800, minimum=1),  # brief 中 ASR 写作分块最大字数/词数
    "edit_mode": os.environ.get("EDIT_MODE", "full"),  # full | cut
}

def log(msg):
    print(f"[video-recap] {msg}", flush=True)

def file_identity(path):
    """{size, mtime_ns} of an input file: the identity brief consumers compare against
    the provenance a producer stored, instead of re-hashing bytes."""
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


def _sanitize_api_error(value, limit=500):
    """Bound transport diagnostics without echoing request media or credentials."""
    text = _ERROR_DATA_URL_RE.sub("<redacted-data-url>", str(value or ""))
    text = _ERROR_KEY_RE.sub("<redacted-key>", text)
    return text[:limit]

def _api_headers(api_provider=None, api_url=None, api_key=None):
    """Build MiMo auth headers (OpenAI-compatible chat/completions with an api-key header)."""
    del api_provider, api_url  # MiMo is the only provider; signature kept for call sites
    key = CONFIG["api_key"] if api_key is None else api_key
    return {
        "Content-Type": "application/json",
        "User-Agent": "video-recap/1.0",
        "api-key": key,
    }

def _prepare_api_payload(payload, api_provider=None, api_url=None):
    """Normalize payload fields for MiMo's OpenAI-compatible chat/completions API."""
    del api_provider, api_url
    normalized = dict(payload)
    if "max_tokens" in normalized and "max_completion_tokens" not in normalized:
        normalized["max_completion_tokens"] = normalized.pop("max_tokens")
    model = normalized["model"]
    if (
        CONFIG["mimo_disable_thinking"]
        and not model.endswith(("-tts", "-asr"))
        and "thinking" not in normalized
    ):
        # MiMo V2.5 may spend small max_completion_tokens budgets on reasoning_content.
        # The recap pipeline needs visible text, so disable thinking unless set explicitly.
        normalized["thinking"] = {"type": "disabled"}
    return normalized

def api_call(payload, max_retries=8, *, api_provider=None, api_url=None, api_key=None, api_env_var=None):
    """调用 OpenAI-compatible API，带重试。

    集群的 429 限流是常态而非错误，所以重试更耐心（更多次数 + 退避封顶 60s + 遵从 Retry-After），
    避免一次瞬时限流就中止整个阶段。配额窗口常以分钟计，所以 429 在没有 Retry-After 时也至少等 10s。
    """
    endpoint = normalize_api_url(api_url if api_url is not None else CONFIG["api_url"])
    headers = _api_headers(api_provider=api_provider, api_url=endpoint, api_key=api_key)
    data = json.dumps(_prepare_api_payload(payload, api_provider=api_provider, api_url=endpoint)).encode("utf-8")

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
                key_name = api_env_var or CONFIG["api_env_var"]
                raise RuntimeError(f"API 认证失败 (401)。请检查 {key_name} 和 API URL 是否匹配。")
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
            # (Deliberately broad, but no longer written as `(URLError, Exception)`, which
            # read as a tuple while `Exception` already subsumed the first member.)
            wait = min(2 ** attempt, 60)
            safe_error = _sanitize_api_error(e)
            log(f"API 调用失败 (尝试 {attempt+1}/{max_retries}): {safe_error}")
            if attempt < max_retries - 1:
                log(f"等待 {wait}s 后重试...")
                time.sleep(wait)
            else:
                raise RuntimeError(f"API 调用失败 {max_retries} 次: {safe_error}")
    # Unreachable for max_retries >= 1; guards against a silent `None` return (and the
    # TypeError it would cause at the caller's resp["choices"]) if a caller passes 0.
    raise ValueError(f"max_retries must be >= 1, got {max_retries}")
