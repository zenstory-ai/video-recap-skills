"""Self-contained config and utilities for the unified video-recap production skill."""
import functools
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
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
TTS_PROVIDERS = ("auto", "mimo-tts", "fish-audio", "index-tts")


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
    """Pick the correct MiMo base URL for pay-as-you-go vs Token Plan keys."""
    if is_token_plan:
        cluster_name = cluster or os.environ.get("MIMO_TOKEN_PLAN_CLUSTER") or DEFAULT_MIMO_TOKEN_PLAN_CLUSTER
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


def env_float(name, default, min_val=None, *, minimum=None):
    """Read a float env var, rejecting malformed or below-minimum values."""
    bound = minimum if minimum is not None else min_val
    return _env_number(name, default, float, bound)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_json_object(path):
    """A JSON object file as a dict, or None when it is unreadable, malformed or not an object."""
    try:
        data = load_json(path)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def file_identity(path):
    """``{size, mtime_ns}`` of a file: the identity recorded instead of content hashes."""
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
    """``src_<stem>_<size>``: readable, stable across runs, and distinct for a different cut."""
    return f"src_{_id_stem(source_path)}_{os.stat(os.fspath(source_path)).st_size}"


def material_id_for(source_path, source_identity):
    return f"{_slug(str(source_path))}-{source_identity['size']}"


def ffmpeg_filters():
    """Filters the installed ffmpeg lists; empty when ffmpeg is absent."""
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-filters"], text=True, capture_output=True, timeout=20
        )
    except FileNotFoundError:
        return set()
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"`ffmpeg -filters` failed or hung: {exc}") from exc
    if result.returncode != 0:
        detail = (
            getattr(result, "stderr", None) or getattr(result, "stdout", None) or ""
        ).strip()[:300]
        raise RuntimeError(f"`ffmpeg -filters` failed (exit {result.returncode}): {detail}")
    filters = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] and parts[0][0] in ".TSCAPN|":
            filters.add(parts[1])
    return filters


def ffmpeg_has_subtitles_filter():
    """True when this ffmpeg can burn subtitles — its filter list includes `subtitles`."""
    return "subtitles" in ffmpeg_filters()


_mimo_api_key = os.environ.get("MIMO_API_KEY", "")
_mimo_tts_api_key = os.environ.get("MIMO_TTS_API_KEY", "") or _mimo_api_key
_mimo_asr_api_key = os.environ.get("MIMO_ASR_API_KEY", "") or _mimo_api_key
_raw_api_url = os.environ.get("MIMO_API_URL") or default_mimo_api_url(is_mimo_token_plan_key(_mimo_api_key))
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

_foreign_source_audio = env_bool("FOREIGN_SOURCE_AUDIO", False)
_foreign_under_narration_volume = 0.05


def _default_subtitle_font_name():
    """Return a clean CJK-capable system sans-serif family for burned subtitles."""
    if sys.platform == "darwin":
        return "PingFang SC"
    if sys.platform == "win32":
        return "Microsoft YaHei"
    return "Noto Sans CJK SC"

CONFIG = {
    "api_provider": "mimo",
    "api_url": normalize_api_url(_raw_api_url),
    "api_url_source": "env" if os.environ.get("MIMO_API_URL") else "default",
    "api_key": _mimo_api_key,
    "api_env_var": "MIMO_API_KEY",
    "mimo_api_url": normalize_api_url(_raw_api_url),
    "mimo_api_key": _mimo_api_key,
    "mimo_tts_api_url": normalize_api_url(_raw_mimo_tts_api_url),
    "mimo_tts_api_url_source": "env" if (
        os.environ.get("MIMO_TTS_API_URL") or os.environ.get("MIMO_API_URL")
    ) else "default",
    "mimo_tts_api_key": _mimo_tts_api_key,
    "mimo_tts_env_var": "MIMO_TTS_API_KEY" if os.environ.get("MIMO_TTS_API_KEY") else "MIMO_API_KEY",
    "mimo_asr_api_url": normalize_api_url(_raw_mimo_asr_api_url),
    "mimo_asr_api_url_source": "env" if (
        os.environ.get("MIMO_ASR_API_URL") or os.environ.get("MIMO_API_URL")
    ) else "default",
    "mimo_asr_api_key": _mimo_asr_api_key,
    "mimo_asr_env_var": "MIMO_ASR_API_KEY" if os.environ.get("MIMO_ASR_API_KEY") else "MIMO_API_KEY",
    "vlm_model": os.environ.get("MIMO_MODEL", DEFAULT_MIMO_MODEL),
    "vlm_model_source": "env" if os.environ.get("MIMO_MODEL") else "default",
    "mimo_asr_model": os.environ.get("MIMO_ASR_MODEL", DEFAULT_MIMO_ASR_MODEL),
    "mimo_asr_language": os.environ.get("MIMO_ASR_LANGUAGE", "auto"),
    "mimo_asr_base64_max_mb": 10.0,
    "asr_segment_seconds": env_float("ASR_SEGMENT_SECONDS", 15.0, minimum=5.0),
    "scene_threshold": 0.1,
    "mimo_media_resolution": "default",
    "vlm_seconds_per_frame": env_float("VLM_SECONDS_PER_FRAME", 4.0, minimum=0.5),
    "vlm_max_frames": env_int("VLM_MAX_FRAMES", 16, minimum=3),
    "vlm_max_tokens": env_int("VLM_MAX_TOKENS", 1500, minimum=200),
    "mimo_disable_thinking": True,
    "fps": 0,
    "storyboard": env_bool("STORYBOARD", True),
    "storyboard_max_tiles": env_int("STORYBOARD_MAX_TILES", 30, minimum=1),
    "storyboard_columns": env_int("STORYBOARD_COLUMNS", 6, minimum=1),
    "storyboard_rows_per_page": env_int("STORYBOARD_ROWS_PER_PAGE", 5, minimum=1),
    "storyboard_long_scene_seconds": env_float("STORYBOARD_LONG_SCENE_SECONDS", 6.0, minimum=0.1),
    "speech_rate": env_float("SPEECH_RATE", 3.9, minimum=0.5),
    "speech_safety_margin": 0.85,
    "breath_ms": 250,
    "narration_speed": env_float("NARRATION_SPEED", 1.15, minimum=0.5),
    "narration_tail_pad_seconds": 0.1,
    "silence_noise_threshold": "-25dB",
    "source_boundary_noise_threshold": os.environ.get("SOURCE_BOUNDARY_NOISE_THRESHOLD", "-18dB"),
    "source_boundary_min_pause": env_float("SOURCE_BOUNDARY_MIN_PAUSE", 0.12, minimum=0.05),
    "source_boundary_max_alignment_error": env_float(
        "SOURCE_BOUNDARY_MAX_ALIGNMENT_ERROR", 2.1, minimum=0.2
    ),
    "silence_min_duration": 0.3,
    "quiet_window_min": 1.0,
    "silence_merge_gap": 0.5,
    "scene_junk_filter": env_bool("SCENE_JUNK_FILTER", True),
    "scene_junk_workers": env_int("SCENE_JUNK_WORKERS", 8, minimum=1),
    "scene_junk_dark_luma": env_float("SCENE_JUNK_DARK_LUMA", 8.0, minimum=0.0),
    "scene_junk_bright_luma": env_float("SCENE_JUNK_BRIGHT_LUMA", 245.0, minimum=0.0),
    "scene_junk_pixel_ratio": env_float("SCENE_JUNK_PIXEL_RATIO", 0.995, minimum=0.0),
    "context_info": "",
    "vlm_workers": env_int("VLM_WORKERS", 8, minimum=1),
    "edit_mode": "full",
    "target_duration": "",
    "mimo_tts_model": os.environ.get("MIMO_TTS_MODEL", DEFAULT_MIMO_TTS_MODEL),
    "mimo_tts_model_source": "env" if os.environ.get("MIMO_TTS_MODEL") else "default",
    "mimo_tts_voice": os.environ.get("MIMO_TTS_VOICE", "冰糖"),
    "mimo_tts_voice_source": "env" if os.environ.get("MIMO_TTS_VOICE") else "default",
    "voice_ref": os.environ.get("VOICE_REF", "").strip(),
    "mimo_tts_style": "自然、清晰、有感染力，像在给观众讲故事；随剧情起伏，该紧张时紧张、该动情时动情，不平铺直叙。",
    "tts_provider": os.environ.get("TTS_PROVIDER", "auto").strip().lower(),
    "tts_timeout": env_int("TTS_TIMEOUT", 300, minimum=1),
    "fish_api_key": os.environ.get("FISH_API_KEY", ""),
    "fish_tts_api_url": os.environ.get("FISH_TTS_API_URL", DEFAULT_FISH_TTS_API_URL),
    "fish_tts_model": os.environ.get("FISH_TTS_MODEL", DEFAULT_FISH_TTS_MODEL),
    "fish_tts_reference_id": os.environ.get(
        "FISH_TTS_REFERENCE_ID", DEFAULT_FISH_TTS_REFERENCE_ID
    ).strip(),
    "fish_tts_reference_id_source": (
        "env" if os.environ.get("FISH_TTS_REFERENCE_ID") else "default"
    ),
    "narration_cumulative_tempo_max": 1.35,
    "narration_cumulative_tempo_hard_max": 1.40,
    "tts_segment_tempo_max": 1.20,
    "tts_workers": env_int("TTS_WORKERS", 4, minimum=1),
    "tts_retries": env_int("TTS_RETRIES", 3, minimum=1),
    "tts_min_speech_rate": env_float("TTS_MIN_SPEECH_RATE", 2.5, minimum=0.0),
    "allow_partial_tts": False,
    "tts_segment_normalize": True,
    "tts_segment_target_rms_dbfs": -20.0,
    "tts_segment_peak_limit": 0.98,
    "snap_clip_line_end": env_bool("SNAP_CLIP_LINE_END", True),
    "clip_snap_max_extend": env_float("CLIP_SNAP_MAX_EXTEND", 2.0, minimum=0.0),
    "clip_end_pause_pad": env_float("CLIP_END_PAUSE_PAD", 0.30, minimum=0.0),
    "clip_start_snap_max_prepend": env_float("CLIP_START_SNAP_MAX_PREPEND", 1.8, minimum=0.0),
    "clip_start_snap_max_trim": env_float("CLIP_START_SNAP_MAX_TRIM", 0.35, minimum=0.0),
    "min_clip_duration": env_float("MIN_CLIP_DURATION", 0.8, minimum=0.05),
    "clip_join_audio_fade_ms": 30.0,
    "scene_cut_snap": env_bool("SCENE_CUT_SNAP", True),
    "scene_cut_snap_margin": env_float("SCENE_CUT_SNAP_MARGIN", 0.5, minimum=0.0),
    "scene_cut_detect_threshold": env_float("SCENE_CUT_DETECT_THRESHOLD", 0.32, minimum=0.0),
    "cut_shot_review": env_bool("CUT_SHOT_REVIEW", True),
    "shot_review_threshold": env_float("SHOT_REVIEW_THRESHOLD", 0.35, minimum=0.0),
    "flash_sliver_max_seconds": env_float("FLASH_SLIVER_MAX_SECONDS", 0.20, minimum=0.0),
    "max_dead_air_seconds": env_float("MAX_DEAD_AIR_SECONDS", 5.0, minimum=0.0),
    "max_cold_open_silence_seconds": env_float("MAX_COLD_OPEN_SILENCE_SECONDS", 2.5, minimum=0.0),
    "ending_audio_fade_ms": env_float("ENDING_AUDIO_FADE_MS", 350.0, minimum=0.0),
    "studio_vocal_polish": env_bool("STUDIO_VOCAL_POLISH", True),
    "fade_ms": 120,
    "ducking_narr_weight": 1.6,
    "zone_ducking_volume": env_float(
        "ZONE_DUCKING_VOLUME",
        _foreign_under_narration_volume if _foreign_source_audio else 0.12,
        minimum=0.0,
    ),
    "idle_orig_volume": env_float("IDLE_ORIG_VOLUME", 1.0, minimum=0.0),
    "duck_fade_seconds": env_float("DUCK_FADE_SECONDS", 0.3, minimum=0.0),
    "duck_bridge_seconds": env_float("DUCK_BRIDGE_SECONDS", 1.5, minimum=0.0),
    "bgm_path": os.environ.get("BGM_PATH", "").strip(),
    "source_video": "",
    "export_jianying": False,
    "jianying_draft_dir": os.environ.get("JIANYING_DRAFT_DIR", "").strip(),
    "jianying_bundle_media": env_bool("JIANYING_BUNDLE_MEDIA", True),
    "bgm_volume": env_float("BGM_VOLUME", 0.18, minimum=0.0),
    "bgm_ducking_volume": env_float("BGM_DUCKING_VOLUME", 0.10, minimum=0.0),
    "mask_source_subtitles": env_bool("MASK_SOURCE_SUBTITLES", False),
    "source_subtitle_mask_ratio": env_float("SOURCE_SUBTITLE_MASK_RATIO", 0.14, minimum=0.0),
    "source_subtitle_mask_timing": os.environ.get("SOURCE_SUBTITLE_MASK_TIMING", "narration").strip().lower(),
    "subtitle_mask_opacity": min(1.0, env_float("SUBTITLE_MASK_OPACITY", 0.6, minimum=0.0)),
    "subtitle_mask_padding": env_int("SUBTITLE_MASK_PADDING", 4, minimum=0),
    "subtitle_y_top": -1,
    "subtitle_y_bot": -1,
    "quiet_overlap_min_ratio": 0.8,
    "speech_ducking_volume": env_float(
        "SPEECH_DUCKING_VOLUME",
        _foreign_under_narration_volume if _foreign_source_audio else 0.12,
        minimum=0.0,
    ),
    "burn_subtitles": env_bool("BURN_SUBTITLES", True),
    "burn_subtitles_explicit": "BURN_SUBTITLES" in os.environ,
    "burn_subtitles_degraded": None,
    "subtitle_original_in_gaps": env_bool("SUBTITLE_ORIGINAL_IN_GAPS", True),
    "force_video_reencode": env_bool("FORCE_VIDEO_REENCODE", False),
    "output_crf": env_int("OUTPUT_CRF", 18, minimum=0),
    "output_preset": os.environ.get("OUTPUT_PRESET", "veryfast"),
    "output_max_height": env_int("OUTPUT_MAX_HEIGHT", 0, minimum=0),
    "final_loudnorm": env_bool("FINAL_LOUDNORM", True),
    "target_lufs": env_float("TARGET_LUFS", -14.0),
    "target_true_peak": -1.0,
    "target_lra": 11.0,
    "final_limiter_peak": 0.98,
    "loudness_limiter_max_db": env_float("LOUDNESS_LIMITER_MAX_DB", 6.0, minimum=0.0),
    "subtitle_font_name": os.environ.get("SUBTITLE_FONT_NAME") or _default_subtitle_font_name(),
    "subtitle_font_file": os.environ.get("SUBTITLE_FONT_FILE", "").strip(),
    "subtitle_font_size": env_int("SUBTITLE_FONT_SIZE", 42, minimum=8),
    "subtitle_primary_color": os.environ.get("SUBTITLE_PRIMARY_COLOR", "&H008AE0FF"),
    "subtitle_outline_color": os.environ.get("SUBTITLE_OUTLINE_COLOR", "&H00000000"),
    "subtitle_outline": env_float("SUBTITLE_OUTLINE", 2.5, minimum=0.0),
    "subtitle_shadow": env_float("SUBTITLE_SHADOW", 1.0, minimum=0.0),
    "subtitle_margin_v": env_int("SUBTITLE_MARGIN_V", 48, minimum=0),
    "subtitle_margin_l": 40,
    "subtitle_margin_r": 40,
    "subtitle_alignment": env_int("SUBTITLE_ALIGNMENT", 2, minimum=1),
    "subtitle_max_chars": env_int("SUBTITLE_MAX_CHARS", 18, minimum=6),
    "subtitle_max_lines": env_int("SUBTITLE_MAX_LINES", 2, minimum=1),
    "subtitle_play_res_x": env_int("SUBTITLE_PLAY_RES_X", 1280, minimum=1),
    "subtitle_play_res_y": env_int("SUBTITLE_PLAY_RES_Y", 720, minimum=1),
    "subtitle_glow": env_float("SUBTITLE_GLOW", 0.0, minimum=0.0),
    "subtitle_fade_ms": env_int("SUBTITLE_FADE_MS", 0, minimum=0),
    "subtitle_highlight_color": os.environ.get("SUBTITLE_HIGHLIGHT_COLOR", "&H004CD2FF"),
    "subtitle_bridge_gap_seconds": env_float("SUBTITLE_BRIDGE_GAP_SECONDS", 0.0, minimum=0.0),
}

SCRIPT_DIR = Path(__file__).parent
PROMPTS_DIR = SCRIPT_DIR.parent / "references"


def narration_tempo_budget(tts_rate_offset=0.0):
    """Return the canonical tempo budget shared by voiceover and assemble."""
    global_speed = CONFIG["narration_speed"]
    rate_factor = max(0.01, 1.0 + float(tts_rate_offset))
    cumulative_max = CONFIG["narration_cumulative_tempo_max"]
    hard_max = max(cumulative_max, CONFIG["narration_cumulative_tempo_hard_max"])
    segment_tempo_max = max(
        1.0, min(CONFIG["tts_segment_tempo_max"], cumulative_max / (global_speed * rate_factor))
    )
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
    """运行命令，返回 CompletedProcess"""
    if isinstance(cmd, list):
        display_parts = []
        for part in cmd:
            text = str(part)
            display_parts.append(text if len(text) <= 240 else text[:237] + "...")
        display = " ".join(display_parts)
    else:
        display = str(cmd)
        if len(display) > 2000:
            display = display[:1997] + "..."
    log(f"运行: {display}")
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


_LEGACY_FILTER_FILE_OPTIONS = {
    "filter_complex": "-filter_complex_script",
    "filter:v:0": "-filter_script:v:0",
}


@functools.lru_cache(maxsize=None)
def _ffmpeg_reads_option_files():
    """Whether the ffmpeg on PATH accepts `-/option path` (asked once per process)."""
    if shutil.which("ffmpeg") is None:
        return False
    with tempfile.TemporaryDirectory() as tmp:
        graph = os.path.join(tmp, "probe_filter.txt")
        with open(graph, "w", encoding="utf-8") as fh:
            fh.write("null")
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-/filter_complex", graph],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=20,
        )
    return "Unrecognized option" not in result.stderr


def filter_file_args(option, path):
    """ffmpeg arguments that load `option`'s filtergraph from `path`, spelled for this ffmpeg."""
    if _ffmpeg_reads_option_files():
        return [f"-/{option}", str(path)]
    return [_LEGACY_FILTER_FILE_OPTIONS[option], str(path)]


def get_video_duration(video_path):
    """获取视频时长（秒）"""
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-show_entries",
        "format=duration",
        "-of",
        "csv=p=0",
        str(video_path),
    ]
    result = run_cmd(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe 无法读取时长 {video_path}: {result.stderr.strip()[-500:]}")
    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise RuntimeError(
            f"ffprobe 时长输出无法解析 {video_path}: {result.stdout.strip()[:100]!r}"
        ) from exc


def load_background_research(work_dir):
    """Load the agent-authored background_research.json: missing → {}, malformed → raise."""
    path = Path(work_dir) / "background_research.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"background_research.json 不是合法 JSON，请修复后重试: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("background_research.json 顶层必须是 JSON 对象（{...}）")
    return data


MODERATION_REFUSAL_MARKERS = ("request was rejected", "considered high risk")


def is_moderation_refusal(text):
    """True when a MiMo reply is a moderation refusal, not a transcript or description."""
    low = " ".join(str(text or "").lower().split())
    return len(low) <= 200 and all(marker in low for marker in MODERATION_REFUSAL_MARKERS)


def offline_ignored_settings(api_key, endpoint_key):
    """Cache-settings keys not compared when no credential is set."""
    return () if api_key else (endpoint_key,)


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
    text = _ERROR_DATA_URL_RE.sub("<redacted-data-url>", str(value or ""))
    text = _ERROR_KEY_RE.sub("<redacted-key>", text)
    for secret in extra_secrets:
        if secret:
            text = text.replace(str(secret), "<redacted-key>")
    return text[:limit]


def _api_headers(api_key=None):
    """Build MiMo auth headers (OpenAI-compatible chat/completions with an api-key header)."""
    key = CONFIG["api_key"] if api_key is None else api_key
    return {
        "Content-Type": "application/json",
        "User-Agent": "video-recap/1.0",
        "api-key": key,
    }


def _prepare_api_payload(payload):
    """Normalize payload fields for MiMo's OpenAI-compatible chat/completions API."""
    normalized = dict(payload)
    if "max_tokens" in normalized and "max_completion_tokens" not in normalized:
        normalized["max_completion_tokens"] = normalized.pop("max_tokens")
    model = str(normalized.get("model") or "")
    if (
        CONFIG["mimo_disable_thinking"]
        and not model.endswith(("-tts", "-asr"))
        and "thinking" not in normalized
    ):
        normalized["thinking"] = {"type": "disabled"}
    return normalized


def _mimo_endpoint(kind):
    """Return per-capability MiMo endpoint settings (ASR / TTS)."""
    by_kind = {
        "asr": ("mimo_asr_api_url", "mimo_asr_api_key", "mimo_asr_env_var"),
        "tts": ("mimo_tts_api_url", "mimo_tts_api_key", "mimo_tts_env_var"),
    }
    if kind not in by_kind:
        raise ValueError(f"Unsupported MiMo endpoint kind: {kind}")
    url_key, key_key, src_key = by_kind[kind]
    return {
        "api_url": CONFIG.get(url_key) or CONFIG.get("mimo_api_url"),
        "api_key": CONFIG.get(key_key) or CONFIG.get("mimo_api_key"),
        "api_env_var": CONFIG[src_key],
    }


def _call_mimo_endpoint(kind, payload, max_retries=10, *, timeout=300):
    settings = _mimo_endpoint(kind)
    return api_call(
        payload,
        max_retries=max_retries,
        api_url=settings["api_url"],
        api_key=settings["api_key"],
        api_env_var=settings["api_env_var"],
        timeout=timeout,
    )


def mimo_asr_api_call(payload, max_retries=10):
    """Call the MiMo speech-recognition (ASR) endpoint."""
    return _call_mimo_endpoint("asr", payload, max_retries=max_retries)


def mimo_tts_api_call(payload, max_retries=10):
    """Call the MiMo TTS endpoint."""
    return _call_mimo_endpoint(
        "tts", payload, max_retries=max_retries, timeout=CONFIG["tts_timeout"]
    )


def api_call(
    payload, max_retries=8, *, api_url=None, api_key=None, api_env_var=None, timeout=300
):
    """调用 OpenAI-compatible API，带重试。"""
    endpoint = normalize_api_url(api_url if api_url is not None else CONFIG["api_url"])
    headers = _api_headers(api_key=api_key)
    data = json.dumps(_prepare_api_payload(payload)).encode("utf-8")

    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(endpoint, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
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
                wait = max(wait, 4 * (attempt + 1))
                log(f"API 超时 (524)，等待 {wait}s (尝试 {attempt+1}/{max_retries})")
            else:
                log(f"API 调用失败 (尝试 {attempt+1}/{max_retries}): HTTP {e.code} — {body}")
            if attempt < max_retries - 1:
                time.sleep(wait)
            else:
                raise RuntimeError(f"API 调用失败 {max_retries} 次: HTTP {e.code} — {body}")
        except Exception as e:  # noqa: BLE001
            wait = min(2 ** attempt, 60)
            safe_error = _sanitize_api_error(e)
            log(f"API 调用失败 (尝试 {attempt+1}/{max_retries}): {safe_error}")
            if attempt < max_retries - 1:
                log(f"等待 {wait}s 后重试...")
                time.sleep(wait)
            else:
                raise RuntimeError(f"API 调用失败 {max_retries} 次: {safe_error}")
    raise ValueError(f"max_retries must be >= 1, got {max_retries}")


def load_prompt(name):
    """加载 prompt 模板"""
    path = PROMPTS_DIR / "prompt-templates.md"
    if not path.exists():
        return None
    content = path.read_text(encoding="utf-8")
    pattern = rf"### {name}\s*\n(.*?)(?=\n### |\Z)"
    m = re.search(pattern, content, re.DOTALL)
    return m.group(1).strip() if m else None
