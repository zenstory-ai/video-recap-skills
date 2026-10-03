#!/usr/bin/env python3
"""Environment doctor for the video-recap skill bundle.

The pipeline runs on ffmpeg + MiMo for understanding; voiceover may explicitly use
MiMo, Fish Audio, or a privately configured Index TTS endpoint.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import urllib.parse
from pathlib import Path

from lib import CONFIG, TTS_PROVIDERS, env_bool, ffmpeg_filters


SCRIPT_DIR = Path(__file__).resolve().parent
# index-tts is checked as hard failures below; these providers only need a key.
TTS_KEY_HINTS = {
    "mimo-tts": "MIMO_TTS_API_KEY or MIMO_API_KEY",
    "fish-audio": "FISH_API_KEY",
}


def _command_path(name: str) -> str | None:
    return shutil.which(name)


def _asr_status() -> dict[str, object]:
    configured = bool(CONFIG["mimo_asr_api_key"])
    return {
        "configured": configured,
        "available": configured,
        "mimo_asr_model": CONFIG["mimo_asr_model"],
        "mimo_asr_api_url": CONFIG["mimo_asr_api_url"],
        "mimo_asr_api_url_source": CONFIG["mimo_asr_api_url_source"],
        "mimo_asr_language": CONFIG["mimo_asr_language"],
        "mimo_asr_env_var": CONFIG["mimo_asr_env_var"],
        "note": "ASR uses MiMo (mimo-v2.5-asr); set MIMO_API_KEY, or run with --skip-asr.",
    }


def _index_tts_status() -> dict[str, object]:
    """Validate private Index settings locally without exposing or contacting them."""
    endpoint = os.environ.get("INDEX_TTS_ENDPOINT", "")
    voice = os.environ.get("INDEX_TTS_VOICE", "").strip()
    has_forbidden_control = any(
        ord(char) < 32 or ord(char) == 127 for char in endpoint
    )
    try:
        parsed = urllib.parse.urlsplit(endpoint) if endpoint else None
        port = parsed.port if parsed else None
    except ValueError:
        parsed = None
        port = None
    endpoint_format_valid = bool(
        parsed
        and not has_forbidden_control
        and parsed.scheme in {"http", "https"}
        and parsed.hostname
        and "@" not in parsed.netloc
        and not parsed.query
        and not parsed.fragment
        and (port is None or 1 <= port <= 65535)
    )
    return {
        "index_tts_endpoint_set": bool(endpoint),
        "index_tts_endpoint_format_valid": endpoint_format_valid,
        "index_tts_voice_set": bool(voice),
        "index_tts_configured": endpoint_format_valid and bool(voice),
        "connectivity_checked": False,
        "validation_scope": "offline configuration only; connectivity and acoustic quality not checked",
    }


def build_report(*, tts_provider: str | None = None) -> dict[str, object]:
    filters = ffmpeg_filters()
    ffmpeg_path = _command_path("ffmpeg") or ""
    ffprobe_path = _command_path("ffprobe") or ""
    mimo_video_configured = bool(CONFIG["mimo_video_api_key"])
    mimo_tts_configured = bool(CONFIG["mimo_tts_api_key"])
    fish_tts_configured = bool(CONFIG["fish_api_key"])
    index_tts = _index_tts_status()
    requested_tts_provider = tts_provider or CONFIG["tts_provider"]
    effective_tts_provider = requested_tts_provider
    if requested_tts_provider == "auto":
        effective_tts_provider = (
            "mimo-tts" if mimo_tts_configured or not fish_tts_configured else "fish-audio"
        )
    if effective_tts_provider == "fish-audio":
        tts_configured = fish_tts_configured
    elif effective_tts_provider == "index-tts":
        tts_configured = index_tts["index_tts_configured"]
    else:
        tts_configured = mimo_tts_configured
    if effective_tts_provider == "fish-audio":
        tts_model = CONFIG["fish_tts_model"]
    elif effective_tts_provider == "index-tts":
        tts_model = "provider-managed"
    else:
        tts_model = CONFIG["mimo_tts_model"]
    subtitle_filter = "subtitles" in filters
    drawtext_filter = "drawtext" in filters
    burn_explicit = "BURN_SUBTITLES" in os.environ and env_bool("BURN_SUBTITLES", True)
    if not ffmpeg_path:
        subtitle_delivery = "unavailable"
    elif subtitle_filter:
        subtitle_delivery = "burned"
    else:
        # The default burn degrades to the .srt sidecar; an explicit request fails fast.
        subtitle_delivery = "fails_explicit_burn" if burn_explicit else "sidecar_srt"
    checks = {
        "system_tools": {
            "ffmpeg": bool(ffmpeg_path),
            "ffmpeg_path": ffmpeg_path,
            "ffprobe": bool(ffprobe_path),
            "ffprobe_path": ffprobe_path,
            "ffmpeg_subtitles_filter": subtitle_filter,
            "ffmpeg_ass_filter": "ass" in filters,
            "burn_subtitles_ready": bool(ffmpeg_path and subtitle_filter),
            "subtitle_delivery": subtitle_delivery,
            "ffmpeg_drawtext_filter": drawtext_filter,
            "visual_overlays_ready": bool(ffmpeg_path and drawtext_filter),
        },
        "tts": {
            "provider": effective_tts_provider,
            "requested_provider": requested_tts_provider,
            "mimo_tts_configured": mimo_tts_configured,
            "mimo_tts_api_url": CONFIG["mimo_tts_api_url"],
            "mimo_tts_api_url_source": CONFIG["mimo_tts_api_url_source"],
            "mimo_tts_model": CONFIG["mimo_tts_model"],
            "mimo_tts_model_source": CONFIG["mimo_tts_model_source"],
            "mimo_tts_voice": CONFIG["mimo_tts_voice"],
            "mimo_tts_voice_source": CONFIG["mimo_tts_voice_source"],
            "fish_tts_configured": fish_tts_configured,
            "fish_tts_api_url": CONFIG["fish_tts_api_url"],
            "fish_tts_model": CONFIG["fish_tts_model"],
            "fish_tts_reference_id_set": bool(CONFIG["fish_tts_reference_id"]),
            "fish_tts_reference_id_source": CONFIG["fish_tts_reference_id_source"],
            **index_tts,
            "model": tts_model,
            "available": tts_configured,
        },
        "asr": _asr_status(),
        "api_config": {
            "api_provider": CONFIG["api_provider"],
            "api_url": CONFIG["api_url"],
            "api_url_source": CONFIG["api_url_source"],
            "api_env_var": CONFIG["api_env_var"],
            "api_key_set": bool(CONFIG["api_key"]),
            "vlm_model": CONFIG["vlm_model"],
            "vlm_model_source": CONFIG["vlm_model_source"],
            "vlm_workers": CONFIG["vlm_workers"],
            "mimo_video_configured": mimo_video_configured,
            "mimo_video_api_url": CONFIG["mimo_video_api_url"],
            "mimo_video_model": CONFIG["mimo_video_model"],
            "mimo_video_model_source": CONFIG["mimo_video_model_source"],
        },
        "python": {
            "executable": sys.executable,
            "version": sys.version.split()[0],
        },
    }

    failures: list[str] = []
    warnings: list[str] = []
    tools = checks["system_tools"]
    for name in ("ffmpeg", "ffprobe"):
        if not tools[name]:
            failures.append(f"Missing system tool: {name}")
    if requested_tts_provider not in TTS_PROVIDERS:
        failures.append(
            "TTS_PROVIDER must be one of: " + ", ".join(TTS_PROVIDERS)
        )
    elif requested_tts_provider == "index-tts":
        if not index_tts["index_tts_endpoint_set"]:
            failures.append("INDEX_TTS_ENDPOINT is not set")
        elif not index_tts["index_tts_endpoint_format_valid"]:
            failures.append(
                "INDEX_TTS_ENDPOINT must be an http/https URL with a hostname and no credentials, query, or fragment"
            )
        if not index_tts["index_tts_voice_set"]:
            failures.append("INDEX_TTS_VOICE is not set")
    if subtitle_delivery == "sidecar_srt":
        warnings.append(
            "ffmpeg lacks subtitles/libass filter; the default run will not burn subtitles and "
            "delivers a .srt sidecar instead (explicit --burn-subtitles fails): "
            "install an ffmpeg build with libass to burn them"
        )
    elif subtitle_delivery == "fails_explicit_burn":
        warnings.append(
            "ffmpeg lacks subtitles/libass filter and BURN_SUBTITLES asks for burn-in, so runs "
            "will stop at preflight: unset BURN_SUBTITLES to get a .srt sidecar, pass "
            "--no-burn-subtitles, or install an ffmpeg build with libass"
        )
    if tools["ffmpeg"] and not drawtext_filter:
        warnings.append(
            "ffmpeg lacks the drawtext filter (libfreetype); narration visual_overlays will stop "
            "the run before TTS: remove them or install an ffmpeg build with drawtext"
        )
    if not checks["api_config"]["api_key_set"]:
        failures.append("MIMO_API_KEY is not set; the default ASR / VLM path requires MiMo")
    if not mimo_video_configured:
        warnings.append(
            "MiMo VLM not configured: set MIMO_VIDEO_API_KEY or MIMO_API_KEY before video understanding"
        )
    if not tts_configured and effective_tts_provider in TTS_KEY_HINTS:
        warnings.append(
            f"TTS provider {effective_tts_provider} not configured: "
            f"set {TTS_KEY_HINTS[effective_tts_provider]} before voiceover"
        )
    if not checks["asr"]["available"]:
        warnings.append("ASR not configured (MIMO_API_KEY); pipeline can run with --skip-asr")
    return {
        "ok": not failures,
        "repo_root": str(SCRIPT_DIR.parents[2]),
        "checks": checks,
        "failures": failures,
        "warnings": warnings,
    }


def _status_icon(ok: bool, *, warning: bool = False) -> str:
    if ok:
        return "✓"
    return "!" if warning else "✗"


def _print_human(report: dict) -> None:
    checks = report["checks"]
    print("video-recap doctor")
    print(f"Repo root: {report['repo_root']}")

    system = checks["system_tools"]
    print("\n[system]")
    print(f"{_status_icon(system['ffmpeg'])} ffmpeg: {system['ffmpeg_path'] or 'not found'}")
    print(f"{_status_icon(system['ffprobe'])} ffprobe: {system['ffprobe_path'] or 'not found'}")
    print(
        f"{_status_icon(system['ffmpeg_subtitles_filter'], warning=True)} "
        f"ffmpeg subtitles/libass filter: "
        f"{'available' if system['ffmpeg_subtitles_filter'] else 'missing'}"
        f" (subtitles: {system['subtitle_delivery']})"
    )
    print(
        f"{_status_icon(system['ffmpeg_drawtext_filter'], warning=True)} "
        f"ffmpeg drawtext filter (visual_overlays): "
        f"{'available' if system['ffmpeg_drawtext_filter'] else 'missing'}"
    )

    api = checks["api_config"]
    print("\n[api]")
    print(f"✓ API provider: {api['api_provider']}")
    print(f"✓ API URL: {api['api_url']} (source: {api['api_url_source']})")
    print(
        f"{_status_icon(api['api_key_set'])} "
        f"{api['api_env_var']}: {'set' if api['api_key_set'] else 'not set'}"
    )
    print(f"✓ VLM model: {api['vlm_model']} (source: {api['vlm_model_source']})")
    print(f"✓ VLM_WORKERS: {api['vlm_workers']}")

    asr = checks["asr"]
    print("\n[asr]")
    print(
        f"{_status_icon(asr['available'], warning=True)} "
        f"MiMo ASR: {'configured' if asr['available'] else 'not configured'} "
        f"(key: {asr['mimo_asr_env_var']})"
    )
    print(f"✓ ASR model: {asr['mimo_asr_model']}")
    print(f"✓ ASR API URL: {asr['mimo_asr_api_url']} (source: {asr['mimo_asr_api_url_source']})")
    print(f"✓ ASR language: {asr['mimo_asr_language']}")
    if not asr["available"]:
        print(f"  note: {asr['note']}")

    tts = checks["tts"]
    print("\n[tts]")
    print(
        f"{_status_icon(tts['available'])} {tts['provider']}: "
        f"{'configured' if tts['available'] else 'not configured'}"
    )
    print(f"✓ TTS model: {tts['model']}")
    if tts["provider"] == "fish-audio":
        print(
            "✓ TTS voice reference ID: "
            f"{'set' if tts['fish_tts_reference_id_set'] else 'not set'} "
            f"(source: {tts['fish_tts_reference_id_source']})"
        )
        print(f"✓ TTS API URL: {tts['fish_tts_api_url']}")
    elif tts["provider"] == "index-tts":
        print(
            f"{_status_icon(tts['index_tts_endpoint_format_valid'])} "
            "Index TTS endpoint: "
            f"{'configured with valid format' if tts['index_tts_endpoint_format_valid'] else 'missing or invalid'}"
        )
        print(
            f"{_status_icon(tts['index_tts_voice_set'])} Index TTS voice: "
            f"{'configured' if tts['index_tts_voice_set'] else 'not set'}"
        )
        print(f"! Validation scope: {tts['validation_scope']}")
    else:
        print(f"✓ TTS voice: {tts['mimo_tts_voice']} (source: {tts['mimo_tts_voice_source']})")
        print(f"✓ TTS API URL: {tts['mimo_tts_api_url']} (source: {tts['mimo_tts_api_url_source']})")

    if report["warnings"]:
        print("\nWarnings:")
        for warning in report["warnings"]:
            print(f"- {warning}")
    if report["failures"]:
        print("\nStatus: FAILED")
        for failure in report["failures"]:
            print(f"- {failure}")
    else:
        print("\nStatus: OK")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check video-recap runtime prerequisites.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument(
        "--tts-provider",
        choices=TTS_PROVIDERS,
        default=None,
        help="override the TTS provider for this preflight report",
    )
    args = parser.parse_args()

    report = build_report(tts_provider=args.tts_provider)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    _print_human(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
