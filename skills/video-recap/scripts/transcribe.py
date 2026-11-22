#!/usr/bin/env python3
"""Host-first speech transcription primitive: SRT -> embedded subtitles -> local Whisper -> optional HTTP ASR."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Sequence

from _common import has_tool, probe_media, run_cmd, sanitize_api_error, save_json
from _subtitles import parse_srt, write_srt

__all__ = ["main", "transcribe_media"]


def _format_segments(raw_cues: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for idx, cue in enumerate(raw_cues, start=1):
        text = str(cue.get("text") or "").strip()
        start = round(float(cue["start"]), 4)
        end = round(float(cue["end"]), 4)
        if not text or end <= start:
            continue
        seg: dict[str, Any] = {"id": idx, "start": start, "end": end, "text": text}
        if cue.get("words"):
            seg["words"] = cue["words"]
        segments.append(seg)
    return segments


def _try_sidecar_srt(media_path: Path, explicit_srt: str | Path | None) -> tuple[str, list[dict[str, Any]]] | None:
    candidates: list[Path] = []
    if explicit_srt:
        p = Path(explicit_srt)
        if not p.is_file():
            raise FileNotFoundError(f"Subtitle file not found: {p}")
        candidates.append(p)
    else:
        for ext in (".srt", ".vtt"):
            cand = media_path.with_suffix(ext)
            if cand.is_file():
                candidates.append(cand)
    for path in candidates:
        cues = parse_srt(path)
        if cues:
            return "srt", _format_segments(cues)
    return None


def _try_embedded_subtitles(media_path: Path, info: dict[str, Any]) -> tuple[str, list[dict[str, Any]]] | None:
    if not info.get("subtitle_streams") or not has_tool("ffmpeg"):
        return None
    with tempfile.TemporaryDirectory(prefix="transcribe_sub_") as tmp:
        out_srt = Path(tmp) / "embedded.srt"
        res = run_cmd([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(media_path), "-map", "0:s:0", str(out_srt),
        ])
        if res.returncode == 0 and out_srt.is_file():
            cues = parse_srt(out_srt)
            if cues:
                return "embedded", _format_segments(cues)
    return None


def _extract_mono_wav(media_path: Path, out_wav: Path) -> bool:
    if not has_tool("ffmpeg"):
        return False
    res = run_cmd([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(media_path), "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(out_wav),
    ])
    return res.returncode == 0 and out_wav.is_file() and out_wav.stat().st_size > 44


def _try_local_whisper(media_path: Path, lang: str | None) -> tuple[str, list[dict[str, Any]]] | None:
    """Probe local Whisper CLIs on PATH without requiring pip packages."""
    with tempfile.TemporaryDirectory(prefix="transcribe_whisper_") as tmp:
        tmp_dir = Path(tmp)
        wav = tmp_dir / "audio.wav"
        if not _extract_mono_wav(media_path, wav):
            return None

        for cli_name in ("whisper", "faster-whisper", "mlx_whisper"):
            exe = has_tool(cli_name)
            if not exe:
                continue
            cmd = [exe, str(wav), "--output_format", "json", "--output_dir", str(tmp_dir)]
            if lang:
                cmd.extend(["--language", lang])
            res = run_cmd(cmd, timeout=600)
            json_out = tmp_dir / "audio.json"
            if res.returncode == 0 and json_out.is_file():
                data = json.loads(json_out.read_text(encoding="utf-8"))
                segs = _format_segments(data.get("segments") or [])
                if segs:
                    return f"local:{cli_name}", segs

        whisper_cpp = has_tool("whisper-cli")
        model_path = os.environ.get("WHISPER_MODEL_PATH", "").strip()
        if whisper_cpp and model_path and Path(model_path).is_file():
            out_prefix = tmp_dir / "wcpp"
            cmd = [whisper_cpp, "-m", model_path, "-f", str(wav), "-osrt", "-of", str(out_prefix)]
            if lang:
                cmd.extend(["-l", lang])
            res = run_cmd(cmd, timeout=600)
            srt_out = Path(f"{out_prefix}.srt")
            if res.returncode == 0 and srt_out.is_file():
                cues = parse_srt(srt_out)
                if cues:
                    return "local:whisper-cli", _format_segments(cues)
    return None


def _multipart_body(fields: dict[str, str], file_field: str, filename: str, file_bytes: bytes) -> tuple[bytes, str]:
    boundary = f"----VideoRecapBoundary{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for key, val in fields.items():
        chunks.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{val}\r\n".encode("utf-8")
        )
    chunks.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; filename=\"{filename}\"\r\n"
        f"Content-Type: audio/wav\r\n\r\n".encode("utf-8")
    )
    chunks.append(file_bytes)
    chunks.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _try_http_asr(media_path: Path, lang: str | None) -> tuple[str, list[dict[str, Any]]] | None:
    """Optional OpenAI-compatible /audio/transcriptions HTTP call (only when explicitly configured)."""
    url = os.environ.get("ASR_API_URL", "").strip()
    api_key = os.environ.get("ASR_API_KEY", "").strip()
    if not url or not api_key:
        return None
    model = os.environ.get("ASR_MODEL", "whisper-1").strip() or "whisper-1"
    with tempfile.TemporaryDirectory(prefix="transcribe_api_") as tmp:
        wav = Path(tmp) / "audio.wav"
        if not _extract_mono_wav(media_path, wav):
            return None
        fields = {"model": model, "response_format": "verbose_json"}
        if lang:
            fields["language"] = lang
        body, content_type = _multipart_body(fields, "file", "audio.wav", wav.read_bytes())
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": content_type},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            detail = sanitize_api_error(exc.read().decode("utf-8", errors="replace"), (api_key,))
            raise RuntimeError(f"ASR HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"ASR network error: {sanitize_api_error(exc.reason, (api_key,))}") from exc
        raw_segs = data.get("segments") or []
        if not raw_segs and data.get("text"):
            dur = float(data.get("duration") or probe_media(media_path).get("duration_s") or 1.0)
            raw_segs = [{"start": 0.0, "end": dur, "text": data["text"]}]
        return "api", _format_segments(raw_segs)


def transcribe_media(
    media_path: str | Path,
    *,
    srt_path: str | Path | None = None,
    provider: str = "auto",
    lang: str | None = None,
) -> dict[str, Any]:
    """Transcribe speech using host-first order; returns clean `unavailable` if no backend exists."""
    media = Path(media_path)
    info = probe_media(media)
    if provider not in {"auto", "srt", "embedded", "whisper", "api"}:
        raise ValueError(f"Unsupported provider: {provider}")

    if not info.get("has_audio") and not srt_path and not info.get("subtitle_streams"):
        return {
            "status": "unavailable",
            "provider": "none",
            "language": lang,
            "media": str(media),
            "segments": [],
            "note": "Media has no audio or subtitle stream.",
        }

    order = [provider] if provider != "auto" else ["srt", "embedded", "whisper", "api"]
    for step in order:
        found = None
        if step == "srt":
            found = _try_sidecar_srt(media, srt_path)
        elif step == "embedded":
            found = _try_embedded_subtitles(media, info)
        elif step == "whisper":
            found = _try_local_whisper(media, lang)
        elif step == "api":
            found = _try_http_asr(media, lang)
        if found is not None:
            used_provider, segments = found
            return {
                "status": "ok",
                "provider": used_provider,
                "language": lang,
                "media": str(media),
                "segments": segments,
            }

    return {
        "status": "unavailable",
        "provider": "none",
        "language": lang,
        "media": str(media),
        "segments": [],
        "note": "No sidecar SRT, embedded subtitle, local whisper CLI, or ASR_API_URL configured.",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract speech transcript (SRT -> local Whisper -> optional API).")
    parser.add_argument("media", help="Input media path.")
    parser.add_argument("--srt", help="Explicit existing SRT/VTT path.")
    parser.add_argument("--provider", default="auto", choices=("auto", "srt", "embedded", "whisper", "api"))
    parser.add_argument("--lang", help="Optional ISO language code (e.g. zh, en, ja).")
    parser.add_argument("--out", help="Output JSON path (stdout if omitted).")
    parser.add_argument("--out-srt", help="Optional output SRT path.")
    args = parser.parse_args(argv)

    result = transcribe_media(args.media, srt_path=args.srt, provider=args.provider, lang=args.lang)
    if args.out_srt and result["segments"]:
        write_srt(result["segments"], args.out_srt)
    if args.out:
        save_json(args.out, result)
    else:
        sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
