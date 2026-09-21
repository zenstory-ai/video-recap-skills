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
import subprocess
import sys
import urllib.parse
from pathlib import Path

from lib import CONFIG


SCRIPT_DIR = Path(__file__).resolve().parent
DEGRADED_GROUP = "warnings/degraded"
TTS_PROVIDERS = ("auto", "mimo-tts", "fish-audio", "index-tts")


def _command_path(name: str) -> str | None:
    return shutil.which(name)


def _ffmpeg_filters() -> set[str]:
    """Filters the installed ffmpeg lists; empty when ffmpeg is absent.

    A present ffmpeg whose `-filters` fails or hangs is an environment fault and raises,
    so it is never misreported downstream as "filter absent"."""
    ffmpeg = _command_path("ffmpeg")
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


def ffmpeg_has_subtitles_filter() -> bool:
    """True when this ffmpeg can burn subtitles — its filter list includes the libass
    `subtitles` filter. The render burns even the .ass file through `subtitles=` (see
    video-assemble assemble.py:_subtitle_burn_filter), so this — not the `ass` filter — is
    the exact capability `--burn-subtitles` needs. Reused by the orchestrator preflight
    (recap.py) to fail fast before any API spend."""
    return "subtitles" in _ffmpeg_filters()


def _asr_status() -> dict[str, object]:
    configured = bool(CONFIG["mimo_asr_api_key"])
    return {
        "configured": configured,
        "available": configured,
        "mimo_asr_model": CONFIG["mimo_asr_model"],
        "mimo_asr_api_url": CONFIG["mimo_asr_api_url"],
        "mimo_asr_api_url_source": CONFIG["mimo_asr_api_url_source"],
        "mimo_asr_language": CONFIG["mimo_asr_language"],
        "mimo_asr_api_key_source": CONFIG["mimo_asr_api_key_source"],
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


def _capability(name: str, summary: str, *, detail: str = "", action: str = "") -> dict[str, str]:
    item = {"name": name, "summary": summary}
    if detail:
        item["detail"] = detail
    if action:
        item["action"] = action
    return item


def _build_capability_menu(checks: dict) -> dict[str, list[dict[str, str]]]:
    """Human-ready preflight summary grouped by what can run, what blocks, and what degrades.

    This is intentionally a small rollup over the existing `checks` tree. It does not replace
    the raw machine checks, install anything, or introduce provider ranking.
    """
    system = checks["system_tools"]
    api = checks["api_config"]
    asr = checks["asr"]
    tts = checks["tts"]

    menu: dict[str, list[dict[str, str]]] = {
        "ready": [],
        "blocked": [],
        DEGRADED_GROUP: [],
        "optional_upgrades": [],
    }

    ffmpeg_ready = system["ffmpeg"]
    ffprobe_ready = system["ffprobe"]
    subtitles_ready = system["burn_subtitles_ready"]
    api_key_set = api["api_key_set"]
    asr_ready = asr["available"]
    tts_ready = tts["available"]
    vlm_ready = api["mimo_video_configured"]
    normal_core_ready = ffmpeg_ready and ffprobe_ready and api_key_set and vlm_ready and tts_ready

    if ffmpeg_ready and ffprobe_ready:
        menu["ready"].append(
            _capability(
                "core_media_tools",
                "ffmpeg and ffprobe are available",
                detail="Local probing, cutting, rendering, and duration checks can run.",
            )
        )
    else:
        if not ffmpeg_ready:
            menu["blocked"].append(
                _capability("ffmpeg", "Missing ffmpeg", action="Install ffmpeg before running the recap pipeline.")
            )
        if not ffprobe_ready:
            menu["blocked"].append(
                _capability("ffprobe", "Missing ffprobe", action="Install ffprobe before running media probing/export.")
            )

    if api_key_set:
        menu["ready"].append(
            _capability(
                "mimo_credentials",
                "MiMo API key is configured",
                detail=f"Source: {api['api_key_source']}",
            )
        )
    else:
        menu["blocked"].append(
            _capability(
                "mimo_credentials",
                "Missing MIMO_API_KEY",
                action="Set MIMO_API_KEY; the default ASR / VLM / TTS path depends on it.",
            )
        )

    if vlm_ready:
        menu["ready"].append(
            _capability(
                "mimo_vlm",
                "MiMo VLM/video understanding is configured",
                detail=f"Model: {api['vlm_model']}",
            )
        )
    elif api_key_set:
        menu["blocked"].append(
            _capability(
                "mimo_vlm",
                "MiMo VLM/video understanding is not configured",
                action="Set MIMO_VIDEO_API_KEY or the shared MIMO_API_KEY before video understanding.",
            )
        )

    tts_provider = tts["provider"]
    if tts_provider == "index-tts":
        capability_name = "index_tts_configuration"
        action = "Set valid INDEX_TTS_ENDPOINT and INDEX_TTS_VOICE values before voiceover."
    elif tts_provider == "fish-audio":
        capability_name = "fish_audio_tts"
        action = "Set FISH_API_KEY before voiceover."
    else:
        capability_name = "mimo_tts"
        action = "Set MIMO_TTS_API_KEY or the shared MIMO_API_KEY before voiceover."
    if tts_ready:
        menu["ready"].append(
            _capability(
                capability_name,
                (
                    "index-tts configuration is present"
                    if tts_provider == "index-tts"
                    else f"{tts_provider} is configured"
                ),
                detail=(
                    tts["validation_scope"]
                    if tts_provider == "index-tts"
                    else f"Model: {tts['model']}"
                ),
            )
        )
    elif api_key_set:
        menu["blocked"].append(
            _capability(
                capability_name,
                f"{tts_provider} is not configured",
                action=action,
            )
        )

    if asr_ready:
        menu["ready"].append(
            _capability(
                "mimo_asr",
                "MiMo ASR is configured",
                detail=f"Language: {asr['mimo_asr_language']}; model: {asr['mimo_asr_model']}",
            )
        )
    else:
        menu[DEGRADED_GROUP].append(
            _capability(
                "mimo_asr",
                "ASR is unavailable; run only with --skip-asr",
                action=asr["note"],
            )
        )

    if subtitles_ready:
        menu["ready"].append(
            _capability("subtitle_burn", "Subtitle burn-in is available", detail="ffmpeg has the subtitles/libass filter.")
        )
    elif ffmpeg_ready:
        menu[DEGRADED_GROUP].append(
            _capability(
                "subtitle_burn",
                "Subtitle burn-in is unavailable",
                action="Use --no-burn-subtitles or install an ffmpeg build with the subtitles/libass filter.",
            )
        )

    if not normal_core_ready:
        menu["blocked"].append(
            _capability(
                "default_recap_pipeline",
                "Default recap run is blocked",
                detail="Resolve the blocking items above before a normal run.",
            )
        )
    elif asr_ready and subtitles_ready:
        menu["ready"].append(
            _capability(
                "default_recap_pipeline",
                (
                    "Recap prerequisites are configured"
                    if tts_provider == "index-tts"
                    else "Default recap run is ready"
                ),
                detail=(
                    "ASR, VLM, and media tools are configured; Index TTS passed offline configuration checks only."
                    if tts_provider == "index-tts"
                    else "ASR, VLM, TTS, and media tools are configured."
                ),
            )
        )
    else:
        actions = []
        if not asr_ready:
            actions.append("run with --skip-asr")
        if not subtitles_ready:
            actions.append("run with --no-burn-subtitles")
        menu[DEGRADED_GROUP].append(
            _capability(
                "recap_degraded_mode",
                "Recap can run only in an explicit degraded mode",
                detail="; ".join(actions),
            )
        )

    menu["optional_upgrades"].append(
        _capability(
            "jianying_export",
            "Editable JianYing draft export can be requested with --export-jianying",
            detail="No JianYing install is required to write the draft; ffprobe improves media metadata.",
        )
    )
    if subtitles_ready:
        menu["optional_upgrades"].append(
            _capability(
                "burned_subtitles",
                "Burned subtitles are available and enabled by default",
                action="Use --no-burn-subtitles if you prefer external subtitle files.",
            )
        )

    return menu


def build_report(*, tts_provider: str | None = None) -> dict[str, object]:
    filters = _ffmpeg_filters()
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
    checks = {
        "system_tools": {
            "ffmpeg": bool(ffmpeg_path),
            "ffmpeg_path": ffmpeg_path,
            "ffprobe": bool(ffprobe_path),
            "ffprobe_path": ffprobe_path,
            "ffmpeg_subtitles_filter": subtitle_filter,
            "ffmpeg_ass_filter": "ass" in filters,
            "burn_subtitles_ready": bool(ffmpeg_path and subtitle_filter),
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
            "api_key_source": CONFIG["api_key_source"],
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
    if tools["ffmpeg"] and not tools["ffmpeg_subtitles_filter"]:
        warnings.append("ffmpeg lacks subtitles/libass filter; --burn-subtitles will fail")
    if not checks["api_config"]["api_key_set"]:
        failures.append("MIMO_API_KEY is not set; the default ASR / VLM path requires MiMo")
    if not checks["asr"]["available"]:
        warnings.append("ASR not configured (MIMO_API_KEY); pipeline can run with --skip-asr")
    return {
        "ok": not failures,
        "repo_root": str(SCRIPT_DIR.parents[2]),
        "checks": checks,
        "capability_menu": _build_capability_menu(checks),
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
    )

    api = checks["api_config"]
    print("\n[api]")
    print(f"✓ API provider: {api['api_provider']}")
    print(f"✓ API URL: {api['api_url']} (source: {api['api_url_source']})")
    print(
        f"{_status_icon(api['api_key_set'])} "
        f"{api['api_key_source']}: {'set' if api['api_key_set'] else 'not set'}"
    )
    print(f"✓ VLM model: {api['vlm_model']} (source: {api['vlm_model_source']})")
    print(f"✓ VLM_WORKERS: {api['vlm_workers']}")

    asr = checks["asr"]
    print("\n[asr]")
    print(
        f"{_status_icon(asr['available'], warning=True)} "
        f"MiMo ASR: {'configured' if asr['available'] else 'not configured'} "
        f"(key: {asr['mimo_asr_api_key_source']})"
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

    menu = report["capability_menu"]
    print("\n[capability menu]")
    for group in ("ready", "blocked", DEGRADED_GROUP, "optional_upgrades"):
        print(f"{group}:")
        items = menu[group]
        if not items:
            print("  - none")
            continue
        for item in items:
            line = f"  - {item['name']}: {item['summary']}"
            if "detail" in item:
                line += f" ({item['detail']})"
            print(line)
            if "action" in item:
                print(f"    action: {item['action']}")

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
