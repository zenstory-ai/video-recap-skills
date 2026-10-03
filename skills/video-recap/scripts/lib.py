"""Self-contained config, JSON, file-identity and ffmpeg-capability helpers for the video-recap orchestrator."""
import json
import os
import re
import shutil
import subprocess
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
# Accepted values of TTS_PROVIDER / --tts-provider, shared by recap.py and doctor.py.
TTS_PROVIDERS = ("auto", "mimo-tts", "fish-audio", "index-tts")


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


def read_json_object(path):
    """A JSON object file as a dict, or None when it is unreadable, malformed or not an object."""
    try:
        data = load_json(path)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# ── 文件身份与 id ─────────────────────────────────────────────────────

def file_identity(path):
    """``{size, mtime_ns}`` of a file: the identity recap records and compares for a source
    video or adopted artifact. A file rewritten in place gets a new mtime_ns."""
    st = os.stat(os.fspath(path))
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


def _slug(text, max_len=48):
    raw = Path(text).stem.lower()
    raw = re.sub(r"[^a-z0-9\u4e00-\u9fff._-]+", "-", raw).strip("-._")
    return (raw or "material")[:max_len].strip("-._") or "material"


def _id_stem(source_path, max_len=32):
    raw = re.sub(r"[^a-z0-9]+", "-", Path(source_path).stem.lower()).strip("-")
    return (raw or "source")[:max_len].strip("-") or "source"


def source_id_for(source_path):
    """``src_<stem>_<size>``: readable, stable across runs, and distinct for a different cut
    of the same title (the size changes)."""
    return f"src_{_id_stem(source_path)}_{os.stat(os.fspath(source_path)).st_size}"


def material_id_for(source_path, source_identity):
    return f"{_slug(str(source_path))}-{source_identity['size']}"


# ── ffmpeg 能力 ───────────────────────────────────────────────────────

def ffmpeg_filters():
    """Filters the installed ffmpeg lists; empty when ffmpeg is absent.

    A present ffmpeg whose `-filters` fails or hangs is an environment fault and raises,
    so it is never misreported downstream as "filter absent"."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return set()
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-filters"], text=True, capture_output=True, timeout=20
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"`ffmpeg -filters` failed or hung: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[:300]
        raise RuntimeError(f"`ffmpeg -filters` failed (exit {result.returncode}): {detail}")
    filters = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] and parts[0][0] in ".TSCAPN|":
            filters.add(parts[1])
    return filters


def ffmpeg_has_subtitles_filter():
    """True when this ffmpeg can burn subtitles — its filter list includes the libass
    `subtitles` filter. The render burns even the .ass file through `subtitles=` (see
    video-assemble assemble.py:_subtitle_burn_filter), so this — not the `ass` filter — is
    the exact capability `--burn-subtitles` needs. The orchestrator preflight
    (recap_runtime.py) uses it to fail fast before any API spend; doctor.py reports it."""
    return "subtitles" in ffmpeg_filters()


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
