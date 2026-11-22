#!/usr/bin/env python3
"""Atomic TTS primitive: Fish s2.1-pro-free / local synthesis, SHA-256 cache, whole-article DP split, RMS norm."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path
from typing import Any, Sequence

from _audio import normalize_wav_rms
from _common import has_tool, load_json, probe_media, run_cmd, sanitize_api_error, save_json

__all__ = ["clean_tts_text", "main", "synthesize_segments"]

_MAX_WAV_BYTES = 64 * 1024 * 1024
_DEFAULT_FISH_MODEL = "s2.1-pro-free"
_SILENCE_TRIM_AF = "silenceremove=start_periods=1:start_threshold=-40dB:start_silence=0.04,areverse,silenceremove=start_periods=1:start_threshold=-40dB:start_silence=0.04,areverse"
_CJK_DIGIT_RE = re.compile(r"[\u3400-\u9fff0-9\uff10-\uff19]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_SILENCE_RE = re.compile(r"silence_(start|end):\s*([0-9.]+)")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def clean_tts_text(text: str, *, keep_tags: bool = False) -> str:
    """Strip markdown emphasis; preserve `[...]` inline tags only when `keep_tags=True` (Fish s2.1)."""
    out = re.sub(r"\*(.+?)\*", r"\1", re.sub(r"\*\*(.+?)\*\*", r"\1", str(text or "")))
    if not keep_tags:
        out = re.sub(r"\[[^\]]*\]", "", out)
    return re.sub(r"\s+", " ", out).strip()


def _spoken_units(text: str) -> float:
    plain = clean_tts_text(text, keep_tags=False)
    return float(len(_CJK_DIGIT_RE.findall(plain))) + 1.5 * len(_LATIN_WORD_RE.findall(plain))


def _validate_wav_bytes(audio: bytes, label: str) -> None:
    if len(audio) <= 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        raise RuntimeError(f"{label} did not return a valid WAV payload")
    riff_sz = int.from_bytes(audio[4:8], "little")
    fmt_pos = audio.find(b"fmt ", 12, min(len(audio), 1024))
    data_pos = audio.find(b"data", 12, min(len(audio), 4096))
    data_sz = int.from_bytes(audio[data_pos + 4 : data_pos + 8], "little") if data_pos != -1 and data_pos + 8 <= len(audio) else -1
    if riff_sz == 0 or riff_sz >= 0x7FFFFFF0 or data_sz == 0 or data_sz >= 0x7FFFFFF0:
        if fmt_pos == -1 or fmt_pos + 24 > len(audio) or data_pos == -1 or len(audio) <= 128 or len(audio) - (data_pos + 8) <= 84:
            raise RuntimeError(f"{label} returned empty/truncated streaming WAV")
        ch, sr, bps = int.from_bytes(audio[fmt_pos + 10 : fmt_pos + 12], "little"), int.from_bytes(audio[fmt_pos + 12 : fmt_pos + 16], "little"), int.from_bytes(audio[fmt_pos + 22 : fmt_pos + 24], "little")
        if not (1 <= ch <= 8 and 8000 <= sr <= 192000 and 1 <= bps // 8 <= 4):
            raise RuntimeError(f"{label} returned invalid streaming WAV format")
        return
    try:
        with wave.open(io.BytesIO(audio), "rb") as wf:
            ch, sw, sr, frames = wf.getnchannels(), wf.getsampwidth(), wf.getframerate(), wf.getnframes()
            decoded = wf.readframes(frames)
    except (EOFError, wave.Error) as exc:
        raise RuntimeError(f"{label} returned corrupt WAV: {exc}") from exc
    if not (1 <= ch <= 8 and 1 <= sw <= 4 and 8000 <= sr <= 192000) or frames <= 0 or len(decoded) != frames * ch * sw:
        raise RuntimeError(f"{label} returned invalid/truncated WAV frames")


def _ensure_pcm16_wav(src: Path, dst: Path) -> None:
    if not has_tool("ffmpeg"):
        with contextlib.suppress(EOFError, wave.Error):
            with wave.open(str(src), "rb") as wf:
                if wf.getsampwidth() == 2 and wf.getnframes() > 0:
                    if src != dst:
                        shutil.copy2(src, dst)
                    return
        raise RuntimeError("ffmpeg is required to transcode TTS audio to 16-bit PCM WAV")
    tmp = dst.with_name(f".{dst.name}.part.wav")
    try:
        run_cmd(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(tmp)], check=True)
        os.replace(tmp, dst)
    finally:
        tmp.unlink(missing_ok=True)


def _trim_wav_silence(src: Path, dst: Path) -> None:
    if not has_tool("ffmpeg"):
        if src != dst:
            shutil.copy2(src, dst)
        return
    tmp = dst.with_name(f".{dst.name}.trim.wav")
    try:
        res = run_cmd(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-af", _SILENCE_TRIM_AF, "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(tmp)])
        if res.returncode == 0 and tmp.is_file() and _wav_duration(tmp) > 0.05:
            os.replace(tmp, dst)
        elif src != dst:
            shutil.copy2(src, dst)
    finally:
        tmp.unlink(missing_ok=True)


def _post_json_wav(
    url: str, payload: dict[str, Any], headers: dict[str, str], label: str, *, secret: str = "", no_redirect: bool = False, retries: int = 3
) -> bytes:
    req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST")
    opener = urllib.request.build_opener(_NoRedirectHandler()) if no_redirect else None
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with (opener.open(req, timeout=90) if opener else urllib.request.urlopen(req, timeout=90)) as resp:
                ctype, audio = (resp.headers.get("Content-Type") or "").lower(), resp.read(_MAX_WAV_BYTES + 1)
            if len(audio) > _MAX_WAV_BYTES:
                raise RuntimeError(f"{label} audio exceeded 64MB limit")
            if "json" in ctype:
                detail = "JSON error response" if no_redirect else sanitize_api_error(audio.decode("utf-8", errors="replace"), (secret,))
                raise RuntimeError(f"{label} returned JSON instead of audio: {detail}")
            if "mpeg" not in ctype and "mp3" not in ctype:
                _validate_wav_bytes(audio, label)
            return audio
        except urllib.error.HTTPError as exc:
            if no_redirect:
                with contextlib.suppress(Exception):
                    exc.read(512)
                    exc.close()
                if 300 <= exc.code < 400:
                    raise RuntimeError(f"{label} redirects are forbidden") from None
                detail = ""
            else:
                detail = sanitize_api_error(exc.read().decode("utf-8", errors="replace"), (secret,))
            if exc.code in {429, 500, 502, 503, 504} and attempt < retries:
                last_err = RuntimeError(f"{label} HTTP {exc.code}{f': {detail}' if detail else ''}")
                time.sleep(0.5 * (attempt + 1))
                continue
            if not no_redirect and exc.code in {401, 402}:
                raise RuntimeError(f"{label} auth/billing error (HTTP {exc.code})") from exc
            raise RuntimeError(f"{label} HTTP {exc.code}{f': {detail}' if detail else ''}") from (None if no_redirect else exc)
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            msg = "network/timeout error" if no_redirect else sanitize_api_error(getattr(exc, "reason", exc), (secret,))
            last_err = RuntimeError(f"{label} {msg}")
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))
                continue
            raise last_err from exc
    raise last_err or RuntimeError(f"{label} request failed")


def _write_raw_as_pcm16(audio: bytes, out_wav: Path) -> None:
    raw = out_wav.with_name(f".{out_wav.name}.raw{'.wav' if audio[:4] == b'RIFF' else '.mp3'}")
    try:
        raw.write_bytes(audio)
        _ensure_pcm16_wav(raw, out_wav)
    finally:
        raw.unlink(missing_ok=True)


def _fish_model() -> str:
    return (os.environ.get("FISH_MODEL") or os.environ.get("FISH_TTS_MODEL") or _DEFAULT_FISH_MODEL).strip()


def _synth_local(text: str, out_wav: Path, *, voice: str | None, speed: float) -> str:
    """Strictly offline host TTS CLIs (zero network traffic)."""
    with tempfile.TemporaryDirectory(prefix="tts_local_") as tmp:
        tmp_dir = Path(tmp)
        if has_tool("say"):
            aiff = tmp_dir / "say.aiff"
            cmd = ["say", "-o", str(aiff), "-r", str(max(90, int(round(190 * speed))))] + (["-v", voice] if voice else [])
            run_cmd([*cmd, "--", text], check=True)
            _ensure_pcm16_wav(aiff, out_wav)
            return "local:say"
        exe = has_tool("espeak-ng") or has_tool("espeak")
        if exe:
            raw = tmp_dir / "espeak.wav"
            cmd = [exe, "-s", str(max(80, int(round(160 * speed)))), "-w", str(raw)] + (["-v", voice] if voice else [])
            run_cmd([*cmd, "--", text], check=True)
            _ensure_pcm16_wav(raw, out_wav)
            return f"local:{Path(exe).name}"
    raise RuntimeError("No strictly offline local TTS CLI found on PATH (tried say, espeak-ng, espeak)")


def _synth_remote(
    engine: str, text: str, out_wav: Path, *, voice: str | None, speed: float, temperature: float = 0.9, top_p: float = 0.9
) -> str:
    if engine == "fish":
        api_key = (os.environ.get("FISH_API_KEY") or os.environ.get("FISH_AUDIO_API_KEY") or "").strip()
        if not api_key:
            raise RuntimeError("FISH_API_KEY (or FISH_AUDIO_API_KEY) must be set for engine=fish")
        ref_id = (voice or os.environ.get("FISH_VOICE_ID") or "").strip()
        payload: dict[str, Any] = {
            "text": text, "format": "wav", "normalize": True,
            "prosody": {"speed": float(speed), "volume": 0, "normalize_loudness": True},
            "temperature": float(temperature), "top_p": float(top_p),
        }
        if ref_id:
            payload["reference_id"] = ref_id
        audio = _post_json_wav(
            os.environ.get("FISH_TTS_URL", "https://api.fish.audio/v1/tts").strip(), payload,
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "audio/wav", "model": _fish_model(), "User-Agent": "video-recap/1.0"},
            "Fish Audio", secret=api_key,
        )
    elif engine == "indextts":
        endpoint, v = os.environ.get("INDEX_TTS_ENDPOINT", "").strip(), (voice or os.environ.get("INDEX_TTS_VOICE", "")).strip()
        parsed = urllib.parse.urlsplit(endpoint)
        if not endpoint or not v or parsed.scheme not in {"http", "https"} or not parsed.hostname or "@" in parsed.netloc:
            raise RuntimeError("Invalid INDEX_TTS_ENDPOINT or voice")
        audio = _post_json_wav(endpoint, {"voice": v, "text": text}, {"Content-Type": "application/json", "Accept": "audio/wav"}, "IndexTTS", no_redirect=True)
    elif engine == "openai":
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY must be set for engine=openai")
        audio = _post_json_wav(
            os.environ.get("TTS_API_URL", "https://api.openai.com/v1/audio/speech").strip(),
            {"model": os.environ.get("TTS_MODEL", "tts-1").strip(), "input": text, "voice": voice or "alloy", "speed": float(speed), "response_format": "wav"},
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, "OpenAI TTS", secret=api_key,
        )
    else:
        raise ValueError(f"Unsupported TTS engine: {engine}")
    _write_raw_as_pcm16(audio, out_wav)
    return engine


def _resolve_engine(requested: str) -> str:
    if requested != "auto":
        return requested
    if (os.environ.get("FISH_API_KEY") or os.environ.get("FISH_AUDIO_API_KEY") or "").strip():
        return "fish"
    if os.environ.get("INDEX_TTS_ENDPOINT", "").strip():
        return "indextts"
    if os.environ.get("OPENAI_API_KEY", "").strip() and os.environ.get("TTS_API_URL", "").strip():
        return "openai"
    return "local"


def _wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as wf:
            return round(wf.getnframes() / float(wf.getframerate() or 1), 4)
    except (OSError, wave.Error):
        return round(float(probe_media(path).get("duration_s") or 0.0), 4)


def _link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.{os.getpid()}.tmp")
    try:
        try:
            os.link(src, tmp)
        except OSError:
            shutil.copy2(src, tmp)
        os.replace(tmp, dst)
    finally:
        tmp.unlink(missing_ok=True)


def _slice_wav(src: Path, dst: Path, start_s: float, end_s: float) -> None:
    with wave.open(str(src), "rb") as r:
        p = r.getparams()
        i0 = max(0, min(p.nframes, int(round(start_s * p.framerate))))
        i1 = max(i0, min(p.nframes, int(round(end_s * p.framerate))))
        r.setpos(i0)
        raw = r.readframes(i1 - i0)
    with wave.open(str(dst), "wb") as w:
        w.setparams(p)
        w.writeframes(raw)


def _detect_pauses(wav: Path) -> list[tuple[float, float]]:
    if not has_tool("ffmpeg"):
        return []
    res = run_cmd(["ffmpeg", "-hide_banner", "-i", str(wav), "-af", "silencedetect=n=-35dB:d=0.08", "-f", "null", "-"])
    pauses: list[tuple[float, float]] = []
    start: float | None = None
    for kind, val in _SILENCE_RE.findall(f"{res.stdout}\n{res.stderr}"):
        if kind == "start":
            start = float(val)
        elif start is not None:
            end = float(val)
            if pauses and start - pauses[-1][1] < 0.06:
                pauses[-1] = (pauses[-1][0], end)
            elif end > start:
                pauses.append((start, end))
            start = None
    return pauses


def _dp_split_pauses(
    pauses: Sequence[tuple[float, float]], total: float, weights: Sequence[float], *, pause_weight: float = 0.3, rate_bounds: tuple[float, float] = (0.70, 1.35)
) -> list[tuple[float, float]] | None:
    """DP pause boundary finder proportional to spoken weight; returns None when rate deviates."""
    n = len(weights)
    if n == 1:
        return [(0.0, total)]
    interior = [(a, b) for a, b in pauses if 0.05 < a and b < total - 0.05]
    if len(interior) < n - 1 or sum(weights) <= 0:
        return None
    before, acc = [], 0.0
    for a, b in interior:
        before.append(a - acc)
        acc += b - a
    speech = total - acc
    if speech <= 0.1:
        return None
    want, m, inf = [speech * w / sum(weights) for w in weights], len(interior), float("inf")
    cost, prev = [[inf] * m for _ in range(n - 1)], [[-1] * m for _ in range(n - 1)]
    err = lambda i, said: (said - want[i]) ** 2 / max(want[i], 0.1)  # noqa: E731
    for j in range(m):
        cost[0][j] = err(0, before[j]) - pause_weight * (interior[j][1] - interior[j][0])
    for i in range(1, n - 1):
        for j in range(i, m):
            for k in range(i - 1, j):
                c = cost[i - 1][k] + err(i, before[j] - before[k]) - pause_weight * (interior[j][1] - interior[j][0])
                if c < cost[i][j]:
                    cost[i][j], prev[i][j] = c, k
    j = min(range(n - 2, m), key=lambda k: cost[n - 2][k] + err(n - 1, speech - before[k]))
    if cost[n - 2][j] == inf:
        return None
    cuts: list[int] = []
    for i in range(n - 2, -1, -1):
        cuts.append(j)
        j = prev[i][j]
    cuts.reverse()
    spans, start, said, avg_rate = [], 0.0, 0.0, sum(weights) / speech
    for i in range(n):
        end = interior[cuts[i]][0] if i < n - 1 else total
        upto = before[cuts[i]] if i < n - 1 else speech
        if not (rate_bounds[0] * avg_rate <= weights[i] / max(upto - said, 0.1) <= rate_bounds[1] * avg_rate):
            return None
        spans.append((start, end))
        said = upto
        if i < n - 1:
            start = interior[cuts[i]][1]
    return spans


def _cache_key(engine: str, voice: str | None, speed: float, spoken: str, model: str, temp: float, top_p: float) -> tuple[dict[str, Any], str]:
    obj: dict[str, Any] = {"engine": engine, "voice": voice or "", "speed": round(float(speed), 3), "text": spoken}
    if engine == "fish":
        obj.update({"model": model, "temperature": round(float(temp), 3), "top_p": round(float(top_p), 3)})
    elif engine == "openai":
        obj["model"] = os.environ.get("TTS_MODEL", "tts-1").strip()
    return obj, hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()[:32]


def _finalize_wav(raw: Path, out_wav: Path, c_wav: Path, c_meta: Path, key_obj: dict[str, Any], engine_tag: str) -> dict[str, Any]:
    norm = out_wav.with_name(f".{out_wav.stem}.{os.getpid()}.norm.wav")
    try:
        _trim_wav_silence(raw, raw)
        rms_meta = normalize_wav_rms(raw, norm, target_rms_dbfs=-20.0, peak_limit=0.98)
        _link_or_copy(norm, c_wav)
        save_json(c_meta, {"key": key_obj, "engine": engine_tag, "rms": rms_meta})
        os.replace(norm, out_wav)
        return rms_meta
    finally:
        norm.unlink(missing_ok=True)


def synthesize_segments(
    segments: Sequence[dict[str, Any]],
    out_dir: str | Path,
    *,
    engine: str = "auto",
    voice: str | None = None,
    speed: float = 1.0,
    temperature: float = 0.9,
    top_p: float = 0.9,
    whole_article: bool = False,
    cache_dir: str | Path | None = None,
    measure_only: bool = False,
    base_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Synthesize (SHA-256 cached + silence-trimmed + RMS normalized) and measure exact durations."""
    out, cache_root = Path(out_dir), Path(cache_dir) if cache_dir else Path(out_dir) / "cache"
    out.mkdir(parents=True, exist_ok=True)
    cache_root.mkdir(parents=True, exist_ok=True)
    chosen = _resolve_engine(engine)
    eff_voice = (voice or (os.environ.get("FISH_VOICE_ID") if chosen == "fish" else os.environ.get("INDEX_TTS_VOICE") if chosen == "indextts" else "") or "").strip() or None
    fish_model, ordered, prepared = _fish_model() if chosen == "fish" else "", list(segments), []
    for idx, seg in enumerate(ordered):
        seg_id = str(seg.get("id") or f"n{idx + 1}")
        display = clean_tts_text(str(seg.get("text") or ""), keep_tags=False)
        spoken = clean_tts_text(str(seg.get("spoken_text") or seg.get("text") or ""), keep_tags=(chosen == "fish"))
        if not spoken and not measure_only:
            raise ValueError(f"Segment {seg_id} has empty text")
        key_obj, digest = _cache_key(chosen, eff_voice, speed, spoken, fish_model, temperature, top_p)
        prepared.append({"seg": seg, "id": seg_id, "display": display or clean_tts_text(spoken, keep_tags=False), "spoken": spoken, "key_obj": key_obj, "digest": digest})

    article_slices: dict[str, tuple[Path, dict[str, Any]]] = {}
    article_mode: str | None = None
    if whole_article and chosen == "fish" and len(prepared) > 1 and not measure_only:
        if not all((cache_root / f"{p['digest']}.wav").is_file() and (cache_root / f"{p['digest']}.json").is_file() for p in prepared):
            master = out / f".article.{os.getpid()}.raw.wav"
            try:
                _synth_remote("fish", "\n".join(p["spoken"] for p in prepared), master, voice=voice, speed=speed, temperature=temperature, top_p=top_p)
                _trim_wav_silence(master, master)
                spans = _dp_split_pauses(_detect_pauses(master), _wav_duration(master), [max(1.0, _spoken_units(p["spoken"])) for p in prepared])
                if spans:
                    for p, (a, b) in zip(prepared, spans):
                        seg_raw, out_wav = out / f".{p['id']}.{os.getpid()}.slice.wav", out / f"{p['id']}.wav"
                        try:
                            _slice_wav(master, seg_raw, a, b)
                            rms = _finalize_wav(seg_raw, out_wav, cache_root / f"{p['digest']}.wav", cache_root / f"{p['digest']}.json", p["key_obj"], "fish:whole_article")
                            article_slices[p["id"]] = (out_wav, rms)
                        finally:
                            seg_raw.unlink(missing_ok=True)
                    article_mode = "whole_article"
                else:
                    article_mode = "per_segment_fallback"
            finally:
                master.unlink(missing_ok=True)

    results: list[dict[str, Any]] = []
    for idx, p in enumerate(prepared):
        seg, seg_id, display, spoken = p["seg"], p["id"], p["display"], p["spoken"]
        at_s, slot_s = float(seg.get("at", 0.0)), seg.get("slot_s")
        if slot_s is None and "end" in seg:
            slot_s = float(seg["end"]) - at_s
        elif slot_s is None and idx + 1 < len(ordered) and "at" in ordered[idx + 1]:
            slot_s = max(0.0, float(ordered[idx + 1]["at"]) - at_s - 0.1)

        out_wav, c_wav, c_meta = out / f"{seg_id}.wav", cache_root / f"{p['digest']}.wav", cache_root / f"{p['digest']}.json"
        cached_hit, rms_meta = False, {}
        if seg_id in article_slices:
            out_wav, rms_meta = article_slices[seg_id]
        elif measure_only:
            cands: list[Path] = []
            if seg.get("file"):
                fp = Path(str(seg["file"]))
                cands.append(fp)
                if base_dir is not None and not fp.is_absolute():
                    cands.append(Path(base_dir) / fp)
            cands.append(out / f"{seg_id}.wav")
            found = next((c for c in cands if c.is_file()), None)
            if found is None:
                raise FileNotFoundError(f"--measure-only WAV not found for {seg_id}: checked {[str(c) for c in cands]}")
            out_wav, cached_hit = found, True
        elif c_wav.is_file() and c_meta.is_file():
            rms_meta = load_json(c_meta).get("rms") or {}
            _link_or_copy(c_wav, out_wav)
            cached_hit = True
        else:
            raw_tmp = out / f".{seg_id}.{os.getpid()}.raw.wav"
            try:
                used = _synth_local(spoken, raw_tmp, voice=voice, speed=speed) if chosen == "local" else _synth_remote(chosen, spoken, raw_tmp, voice=voice, speed=speed, temperature=temperature, top_p=top_p)
                rms_meta = _finalize_wav(raw_tmp, out_wav, c_wav, c_meta, p["key_obj"], used)
            finally:
                raw_tmp.unlink(missing_ok=True)

        actual_s = _wav_duration(out_wav)
        entry: dict[str, Any] = {
            "id": seg_id, "text": display, "file": str(out_wav), "at": round(at_s, 4),
            "slot_s": round(float(slot_s), 4) if slot_s is not None else None,
            "actual_s": actual_s, "overflow_s": round(max(0.0, actual_s - float(slot_s)), 4) if slot_s is not None else 0.0,
            "rms_dbfs": rms_meta.get("rms_dbfs_after"), "cached": cached_hit,
        }
        if spoken != display:
            entry["spoken_text"] = spoken
        units = _spoken_units(display)
        if units > 0 and actual_s > 2.0 + units / 2.0:
            entry["warning"] = f"Audio {actual_s:.2f}s looks unusually long for text; listen to verify."
        elif chosen == "local" and units >= 3 and actual_s < 0.15:
            entry["warning"] = f"Local TTS produced {actual_s:.3f}s audio for {units:.0f} units (e.g. macOS 'say' skipping CJK); pass a matching --voice."
        results.append(entry)

    report: dict[str, Any] = {"engine": chosen, "voice": voice, "speed": float(speed), "segments": results}
    if chosen == "fish":
        report.update({"model": fish_model, "temperature": float(temperature), "top_p": float(top_p)})
    if article_mode:
        report["synthesis_mode"] = article_mode
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Synthesize and measure TTS narration segments (no speed-warping).")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--timeline", help="Path to timeline.json (reads audio.narration[]).")
    src.add_argument("--segments", help="Path to JSON list or {segments: [...]} file.")
    parser.add_argument("--out-dir", required=True, help="Directory for output WAV files.")
    parser.add_argument("--engine", default="auto", choices=("auto", "local", "openai", "fish", "indextts"))
    parser.add_argument("--voice", help="Voice ID / reference_id / system voice name.")
    parser.add_argument("--speed", type=float, default=1.0, help="Synthesis speed multiplier.")
    parser.add_argument("--temperature", type=float, default=0.9, help="Fish Audio sampling temperature.")
    parser.add_argument("--top-p", type=float, default=0.9, help="Fish Audio nucleus sampling top_p.")
    parser.add_argument("--whole-article", action="store_true", help="Synthesize all lines in one Fish call and DP-split at pauses.")
    parser.add_argument("--cache-dir", help="Optional content-addressed WAV cache directory.")
    parser.add_argument("--measure-only", action="store_true", help="Measure existing WAV files without synthesizing.")
    parser.add_argument("--update-timeline", action="store_true", help="Write measured file & duration back to --timeline.")
    parser.add_argument("--sync-subtitles", action="store_true", help="Sync narration subtitle cues to measured [at, at+duration].")
    parser.add_argument("--out", help="Output measurement JSON path (stdout if omitted).")
    args = parser.parse_args(argv)

    tl_obj = load_json(args.timeline) if args.timeline else None
    raw_segs = ((tl_obj.get("audio") or {}).get("narration") or []) if tl_obj is not None else ((lambda d: d["segments"] if isinstance(d, dict) else d)(load_json(args.segments)))
    base_d = Path(args.timeline).resolve().parent if args.timeline else Path(args.segments).resolve().parent
    report = synthesize_segments(
        raw_segs, args.out_dir, engine=args.engine, voice=args.voice, speed=args.speed,
        temperature=args.temperature, top_p=args.top_p, whole_article=args.whole_article,
        cache_dir=args.cache_dir, measure_only=args.measure_only, base_dir=base_d,
    )
    if args.update_timeline and tl_obj is not None:
        narr_list, by_id, tl_dir = tl_obj.setdefault("audio", {}).setdefault("narration", []), {r["id"]: r for r in report["segments"]}, Path(args.timeline).resolve().parent
        for idx, seg in enumerate(narr_list):
            sid = str(seg.get("id") or f"n{idx + 1}")
            if sid in by_id:
                wav_p = Path(by_id[sid]["file"]).resolve()
                seg["file"] = wav_p.relative_to(tl_dir).as_posix() if wav_p.is_relative_to(tl_dir) else str(wav_p)
                seg["duration"] = by_id[sid]["actual_s"]
        if args.sync_subtitles:
            keep_other = [s for s in (tl_obj.get("subtitles") or []) if s.get("style") != "narration"]
            synced = [{"id": r["id"], "start": r["at"], "end": round(r["at"] + r["actual_s"], 4), "text": r["text"], "style": "narration"} for r in report["segments"]]
            tl_obj["subtitles"] = sorted([*keep_other, *synced], key=lambda s: float(s["start"]))
        save_json(args.timeline, tl_obj)
    save_json(args.out, report) if args.out else sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
