"""Self-contained config and MiMo client for the video-recap orchestrator."""
import json
import os
import socket
import urllib.error
import urllib.request
from pathlib import Path


# ── 配置 ──────────────────────────────────────────────────────────────

DEFAULT_MIMO_API_URL = "https://api.xiaomimimo.com/v1"
DEFAULT_MIMO_TOKEN_PLAN_CLUSTER = "cn"
MIMO_TOKEN_PLAN_API_URLS = {
    "cn": "https://token-plan-cn.xiaomimimo.com/v1",
    "sgp": "https://token-plan-sgp.xiaomimimo.com/v1",
    "ams": "https://token-plan-ams.xiaomimimo.com/v1",
}
DEFAULT_MIMO_MODEL = "mimo-v2.5"          # VLM / chat (vision understanding)
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
    cluster = (os.environ.get("MIMO_TOKEN_PLAN_CLUSTER") or DEFAULT_MIMO_TOKEN_PLAN_CLUSTER).strip().lower()
    if cluster not in MIMO_TOKEN_PLAN_API_URLS:
        raise ValueError(
            f"MIMO_TOKEN_PLAN_CLUSTER must be one of {sorted(MIMO_TOKEN_PLAN_API_URLS)}; got {cluster!r}"
        )
    return MIMO_TOKEN_PLAN_API_URLS[cluster]


def env_int(name, default, *, minimum=None):
    """Read an integer env var; a malformed or out-of-range value is a clear error."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer; got {raw!r}") from None
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}; got {value}")
    return value


def env_bool(name, default=False):
    """Read common boolean env var forms."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


# Single MiMo credential powers ASR + VLM + TTS. Per-capability overrides
# (MIMO_VIDEO_API_KEY / MIMO_TTS_API_KEY / MIMO_ASR_API_KEY and their *_API_URL forms)
# are optional and fall back to MIMO_API_KEY / MIMO_API_URL. Token-Plan keys (tp-*) auto-
# route to the Token-Plan cluster base URL; pay-as-you-go keys use api.xiaomimimo.com.
_mimo_api_key = os.environ.get("MIMO_API_KEY", "")
_mimo_video_api_key = os.environ.get("MIMO_VIDEO_API_KEY", "") or _mimo_api_key
_mimo_tts_api_key = os.environ.get("MIMO_TTS_API_KEY", "") or _mimo_api_key
_mimo_asr_api_key = os.environ.get("MIMO_ASR_API_KEY", "") or _mimo_api_key
_raw_api_url = os.environ.get("MIMO_API_URL") or default_mimo_api_url(is_mimo_token_plan_key(_mimo_api_key))
_raw_mimo_video_api_url = (
    os.environ.get("MIMO_VIDEO_API_URL")
    or os.environ.get("MIMO_API_URL")
    or default_mimo_api_url(is_mimo_token_plan_key(_mimo_video_api_key))
)
_raw_mimo_tts_api_url = (
    os.environ.get("MIMO_TTS_API_URL")
    or os.environ.get("MIMO_API_URL")
    or default_mimo_api_url(is_mimo_token_plan_key(_mimo_tts_api_key))
)
_raw_mimo_asr_api_url = (
    os.environ.get("MIMO_ASR_API_URL")
    or os.environ.get("MIMO_API_URL")
    or default_mimo_api_url(is_mimo_token_plan_key(_mimo_asr_api_key))
)

CONFIG = {
    "api_provider": "mimo",
    "api_url": normalize_api_url(_raw_api_url),
    "api_url_source": "env" if os.environ.get("MIMO_API_URL") else "default",
    "api_key": _mimo_api_key,
    "api_env_var": "MIMO_API_KEY",
    # Read through a COPY of CONFIG by qc.mimo_evidence._effective_config /
    # safe_mimo_config, which is why neither a `CONFIG.get(...)` grep nor live-dict
    # instrumentation sees them. They drive the QC model fallback chain and the
    # provenance recorded in the QC report.
    "mimo_qc_model": os.environ.get("MIMO_QC_MODEL") or os.environ.get("MIMO_VIDEO_MODEL")
    or os.environ.get("MIMO_MODEL", DEFAULT_MIMO_MODEL),
    "mimo_qc_model_source": "env" if os.environ.get("MIMO_QC_MODEL") else "fallback",
    "mimo_model": os.environ.get("MIMO_MODEL", DEFAULT_MIMO_MODEL),
    "mimo_model_source": "env" if os.environ.get("MIMO_MODEL") else "default",
    "mimo_api_url": normalize_api_url(_raw_api_url),
    "mimo_api_url_source": "env" if os.environ.get("MIMO_API_URL") else "default",
    "mimo_video_api_url_source": "env" if (
        os.environ.get("MIMO_VIDEO_API_URL") or os.environ.get("MIMO_API_URL")
    ) else "default",
    "mimo_disable_thinking": env_bool("MIMO_DISABLE_THINKING", True),
    "mimo_disable_thinking_source": "env" if os.environ.get("MIMO_DISABLE_THINKING") else "default",
    "mimo_media_resolution": os.environ.get("MIMO_MEDIA_RESOLUTION", "default"),
    "mimo_media_resolution_source": "env" if os.environ.get("MIMO_MEDIA_RESOLUTION") else "default",
    "mimo_api_key": _mimo_api_key,
    "mimo_video_api_url": normalize_api_url(_raw_mimo_video_api_url),
    "mimo_video_api_key": _mimo_video_api_key,
    "mimo_tts_api_url": normalize_api_url(_raw_mimo_tts_api_url),
    "mimo_tts_api_url_source": "env" if (
        os.environ.get("MIMO_TTS_API_URL") or os.environ.get("MIMO_API_URL")
    ) else "default",
    "mimo_tts_api_key": _mimo_tts_api_key,
    "mimo_asr_api_url": normalize_api_url(_raw_mimo_asr_api_url),
    "mimo_asr_api_url_source": "env" if (
        os.environ.get("MIMO_ASR_API_URL") or os.environ.get("MIMO_API_URL")
    ) else "default",
    "mimo_asr_api_key": _mimo_asr_api_key,
    "mimo_asr_env_var": "MIMO_ASR_API_KEY" if os.environ.get("MIMO_ASR_API_KEY") else "MIMO_API_KEY",
    "mimo_video_model": os.environ.get("MIMO_VIDEO_MODEL") or os.environ.get("MIMO_MODEL", DEFAULT_MIMO_MODEL),
    "mimo_video_model_source": "env" if (
        os.environ.get("MIMO_VIDEO_MODEL") or os.environ.get("MIMO_MODEL")
    ) else "default",
    "vlm_model": os.environ.get("MIMO_MODEL", DEFAULT_MIMO_MODEL),
    "vlm_model_source": "env" if os.environ.get("MIMO_MODEL") else "default",
    "mimo_asr_model": os.environ.get("MIMO_ASR_MODEL", DEFAULT_MIMO_ASR_MODEL),
    "mimo_asr_language": os.environ.get("MIMO_ASR_LANGUAGE", "auto"),  # auto | zh | en
    "mimo_tts_model": os.environ.get("MIMO_TTS_MODEL", DEFAULT_MIMO_TTS_MODEL),
    "mimo_tts_model_source": "env" if os.environ.get("MIMO_TTS_MODEL") else "default",
    "mimo_tts_voice": os.environ.get("MIMO_TTS_VOICE", "冰糖"),
    "mimo_tts_voice_source": "env" if os.environ.get("MIMO_TTS_VOICE") else "default",
    "tts_provider": os.environ.get("TTS_PROVIDER", "auto").strip().lower(),
    "fish_api_key": os.environ.get("FISH_API_KEY", ""),
    "fish_tts_api_url": os.environ.get("FISH_TTS_API_URL", DEFAULT_FISH_TTS_API_URL),
    "fish_tts_model": os.environ.get("FISH_TTS_MODEL", DEFAULT_FISH_TTS_MODEL),
    "fish_tts_reference_id": os.environ.get(
        "FISH_TTS_REFERENCE_ID", DEFAULT_FISH_TTS_REFERENCE_ID
    ).strip(),
    "fish_tts_reference_id_source": (
        "env" if os.environ.get("FISH_TTS_REFERENCE_ID") else "default"
    ),
    "vlm_workers": env_int("VLM_WORKERS", 8, minimum=1),  # VLM 并行分析线程数
}


class MiMoQCRequestError(RuntimeError):
    """Sanitized, fail-open transport error for the advisory QC request."""


def mimo_qc_api_call(payload, *, config=None, timeout=60):
    """Send exactly one OpenAI-compatible MiMo request for one QC stage.

    Deliberately no retries: the QC feature is advisory, and the orchestrator's
    one-request-per-stage contract is more important than hiding 429/timeout
    behavior. Callers turn every failure into a non-blocking status report.
    """
    cfg = dict(CONFIG)
    if config:
        cfg.update(config)
    api_key = cfg.get("mimo_video_api_key") or cfg.get("mimo_api_key") or cfg.get("api_key")
    if not api_key:
        raise MiMoQCRequestError("missing_key")
    endpoint = normalize_api_url(
        cfg.get("mimo_video_api_url") or cfg.get("mimo_api_url") or cfg.get("api_url")
    )
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "video-recap/mimo-qc",
            "api-key": api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise MiMoQCRequestError(f"http_{exc.code}") from None
    except (TimeoutError, socket.timeout):
        raise MiMoQCRequestError("timeout") from None
    except (urllib.error.URLError, OSError):
        raise MiMoQCRequestError("network_error") from None
    try:
        result = json.loads(raw)
    except ValueError:
        raise MiMoQCRequestError("invalid_json") from None
    if not isinstance(result, dict):
        raise MiMoQCRequestError("invalid_response")
    return result
