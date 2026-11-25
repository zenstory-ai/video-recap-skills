"""Shared audio automation, RMS normalization, and two-pass linear loudness helpers."""

from __future__ import annotations

import array
import functools
import json
import math
import re
import sys
import wave
from pathlib import Path
from typing import Any, Callable, Sequence

from _common import filter_script_args, has_tool, resolve_source, run_cmd, timeline_video_spans

__all__ = [
    "CODEC_PEAK_HEADROOM_DB",
    "build_loudnorm_filter",
    "coalesce_duck_windows",
    "correct_aac_true_peak",
    "default_bridge",
    "ducking_expression",
    "ducking_filters",
    "ducking_keyframes",
    "measure_loudness",
    "normalize_wav_rms",
]

LIMITER_HEADROOM_DB = 1.0
CODEC_PEAK_HEADROOM_DB = 0.5
_CODEC_PEAK_MARGIN_DB = 0.1
_LOUDNORM_MAX_LRA = 20.0
_LIMITER_OVERSAMPLE_RATE = 192000


def default_bridge(fade_s: float) -> float:
    return 2.0 * float(fade_s)


def coalesce_duck_windows(windows: Sequence[tuple[float, float, float]], bridge_s: float) -> list[list[float]]:
    """Merge [(start, end, gain)] windows separated by gaps shorter than `bridge_s`."""
    rel = sorted(([float(s), float(e), float(g)] for s, e, g in windows if float(e) > float(s)), key=lambda r: r[0])
    if not rel:
        return []
    merged = [rel[0][:]]
    for start, end, gain in rel[1:]:
        if start - merged[-1][1] < bridge_s:
            merged[-1][1] = max(merged[-1][1], end)
            merged[-1][2] = min(merged[-1][2], gain)
        else:
            merged.append([start, end, gain])
    return merged


def _t_minus(value: float) -> str:
    return f"t+{abs(value):.4f}" if value < 0 else f"t-{value:.4f}"


def ducking_expression(
    windows: Sequence[tuple[float, float, float]], idle_gain: float, fade_s: float, bridge_s: float | None = None
) -> str:
    """Build ffmpeg `volume=` expression matching `ducking_keyframes_for_span` 1:1."""
    idle, fade = float(idle_gain), max(0.0, float(fade_s))
    merged = coalesce_duck_windows(windows, default_bridge(fade) if bridge_s is None else float(bridge_s))
    if not merged:
        return f"{idle:.4f}"
    terms = [
        f"+({level - idle:.4f})*"
        + (
            f"between(t,{start:.4f},{end:.4f})"
            if fade <= 0
            else f"min(1,max(0,min({_t_minus(start - fade)},{end + fade:.4f}-t)/{fade:.4f}))"
        )
        for start, end, level in merged
    ]
    return f"max(0,min(1,{idle:.4f}{''.join(terms)}))"


def ducking_filters(
    windows: Sequence[tuple[float, float, float]], idle_gain: float, fade_s: float, bridge_s: float | None = None, chunk: int = 40
) -> list[str]:
    """`volume=` filters equal to `ducking_expression`, split so no single expression exceeds ffmpeg's parser limits.

    Coalesced windows (fades included) never overlap, so a chain of per-group gains multiplies to the same curve."""
    fade = max(0.0, float(fade_s))
    merged = coalesce_duck_windows(windows, default_bridge(fade) if bridge_s is None else float(bridge_s))
    idle = float(idle_gain)
    if len(merged) <= chunk or idle <= 0:
        return [f"volume='{ducking_expression(windows, idle, fade, bridge_s)}':eval=frame"]
    groups = [merged[i:i + chunk] for i in range(0, len(merged), chunk)]
    out = [f"volume='{ducking_expression([tuple(w) for w in groups[0]], idle, fade, 0.0)}':eval=frame"]
    out += [f"volume='{ducking_expression([(s, e, lv / idle) for s, e, lv in g], 1.0, fade, 0.0)}':eval=frame" for g in groups[1:]]
    return out


def ducking_keyframes_for_span(
    windows: Sequence[tuple[float, float, float]],
    idle_gain: float,
    fade_s: float,
    span_start: float,
    span_end: float,
    bridge_s: float | None = None,
) -> list[dict[str, float]]:
    """Return `[{"t": seconds, "gain": linear}]` keyframes matching `ducking_expression`."""
    idle, fade = float(idle_gain), max(0.0, float(fade_s))
    s0, s1 = float(span_start), float(span_end)
    clipped = [
        (max(s0, float(s)), min(s1, float(e)), float(g))
        for s, e, g in windows
        if float(e) > s0 and float(s) < s1 and float(e) > float(s)
    ]
    merged = coalesce_duck_windows(clipped, default_bridge(fade) if bridge_s is None else float(bridge_s))
    if not merged:
        return []
    pts: list[tuple[float, float]] = [(s0, idle)]
    for start, end, level in merged:
        pts.extend([(max(s0, start - fade), idle), (start, level), (end, level), (min(s1, end + fade), idle)])
    pts.append((s1, idle))
    pts.sort(key=lambda item: item[0])
    out: list[tuple[float, float]] = []
    for t_s, gain in pts:
        if out and abs(out[-1][0] - t_s) < 1e-4:
            out[-1] = (t_s, min(out[-1][1], gain))
        else:
            out.append((t_s, gain))
    return [{"t": round(t_s, 4), "gain": round(gain, 4)} for t_s, gain in out]


def _narration_windows(timeline: dict[str, Any], base_dir: Path | None = None) -> list[tuple[float, float]]:
    windows: list[tuple[float, float]] = []
    for seg in (timeline.get("audio") or {}).get("narration") or ():
        start, dur = float(seg.get("at", 0.0)), seg.get("duration")
        if dur is None and seg.get("file"):
            wav_path = resolve_source(timeline, seg["file"], base_dir)
            if wav_path.is_file():
                try:
                    with wave.open(str(wav_path), "rb") as wf:
                        dur = wf.getnframes() / float(wf.getframerate() or 1)
                except (OSError, wave.Error):
                    dur = 0.0
        dur_f = float(dur or 0.0)
        if dur_f > 0:
            windows.append((start, start + dur_f))
    return windows


def ducking_keyframes(timeline: dict[str, Any], *, base_dir: Path | None = None) -> dict[str, list[dict[str, float]]]:
    """Derive canonical `{"original": [...], "bgm": [...]}` keyframes from `timeline.json` v3."""
    _spans, total_dur = timeline_video_spans(timeline)
    if total_dur <= 0:
        return {"original": [], "bgm": []}
    duck = (timeline.get("mix") or {}).get("duck") or {}
    fade_s = float(duck.get("fade_s", 0.25))
    bridge_s = float(duck.get("bridge_s", default_bridge(fade_s)))
    orig_under = float(duck.get("under_narration", 0.18))
    bgm_under = float(duck.get("bgm_under_narration", 0.12))
    narr_spans = _narration_windows(timeline, base_dir=base_dir)
    bgm_tracks = (timeline.get("audio") or {}).get("bgm") or []
    bgm_idle = float(bgm_tracks[0].get("gain", 0.25)) if bgm_tracks else 0.25
    return {
        "original": ducking_keyframes_for_span([(s, e, orig_under) for s, e in narr_spans], 1.0, fade_s, 0.0, total_dur, bridge_s),
        "bgm": ducking_keyframes_for_span([(s, e, min(bgm_idle, bgm_under)) for s, e in narr_spans], bgm_idle, fade_s, 0.0, total_dur, bridge_s),
    }


def normalize_wav_rms(
    src_wav: str | Path, dst_wav: str | Path, *, target_rms_dbfs: float = -20.0, peak_limit: float = 0.98
) -> dict[str, float | None]:
    """Normalize a 16-bit PCM WAV to `target_rms_dbfs` with peak guard (stdlib only)."""
    src, dst = Path(src_wav), Path(dst_wav)
    with wave.open(str(src), "rb") as wf:
        ch, sw, sr, frames = wf.getnchannels(), wf.getsampwidth(), wf.getframerate(), wf.getnframes()
        data = wf.readframes(frames)
    if sw != 2:
        raise ValueError(f"Only 16-bit PCM WAV is supported: {src}")
    samples = array.array("h")
    samples.frombytes(data)
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        dst.write_bytes(src.read_bytes())
        return {"rms_dbfs_before": None, "rms_dbfs_after": None, "peak_after": 0.0, "gain_db": 0.0}
    count = len(samples)
    rms = math.sqrt(sum(s * s for s in samples) / count)
    peak = max(max(samples), -min(samples)) / 32768.0
    rms_before = 20.0 * math.log10(max(rms, 1e-9) / 32768.0)
    gain = ((10.0 ** (target_rms_dbfs / 20.0)) * 32768.0) / max(rms, 1e-9)
    if peak > 0:
        gain = min(gain, peak_limit / peak)
    out_samples = array.array("h", bytes(2 * count))
    sq_sum, out_peak = 0, 0
    for idx in range(count):
        val = max(-32768, min(32767, int(round(samples[idx] * gain))))
        out_samples[idx] = val
        sq_sum += val * val
        out_peak = max(out_peak, abs(val))
    if sys.byteorder != "little":
        out_samples.byteswap()
    with wave.open(str(dst), "wb") as wf:
        wf.setnchannels(ch)
        wf.setsampwidth(sw)
        wf.setframerate(sr)
        wf.writeframes(out_samples.tobytes())
    out_rms = math.sqrt(sq_sum / count)
    return {
        "rms_dbfs_before": round(rms_before, 3),
        "rms_dbfs_after": round(20.0 * math.log10(max(out_rms, 1e-9) / 32768.0), 3),
        "peak_after": round(out_peak / 32768.0, 4),
        "gain_db": round(20.0 * math.log10(max(gain, 1e-9)), 3),
    }


@functools.lru_cache(maxsize=1)
def _alimiter_has_latency() -> bool:
    return bool(has_tool("ffmpeg")) and "latency" in (run_cmd(["ffmpeg", "-hide_banner", "-h", "filter=alimiter"], timeout=20).stdout or "")


def _parse_loudnorm_json(text: str) -> dict[str, Any] | None:
    req = {"input_i", "input_tp", "input_lra", "input_thresh", "target_offset"}
    for match in reversed(list(re.finditer(r"\{[\s\S]*?\}", text))):
        try:
            data = json.loads(match.group(0))
        except ValueError:
            continue
        if req <= set(data):
            return data
    return None


def _summary_peak(text: str) -> float | None:
    matches = re.findall(r"Peak:\s*([+-]?\d+(?:\.\d+)?)\b", text.split("Summary:")[-1])
    return float(matches[-1]) if matches else None


def measure_loudness(
    path: str | Path | None = None,
    *,
    inputs: Sequence[str] = (),
    filter_complex: str | None = None,
    audio_map: str = "0:a:0",
    work_dir: Path | None = None,
    target_lufs: float = -14.0,
    true_peak: float = -1.5,
    target_lra: float = 11.0,
) -> dict[str, float] | None:
    """Measure integrated LUFS, true peak (max of loudnorm & ebur128), LRA, and offset."""
    if not has_tool("ffmpeg"):
        return None
    ln_probe = f"loudnorm=I={target_lufs}:TP={true_peak}:LRA={target_lra}:print_format=json"
    script_path: Path | None = None
    try:
        if filter_complex is not None:
            probe_fc = f"{filter_complex};[{audio_map.strip('[]')}]ebur128=peak=true:framelog=quiet,{ln_probe}[lnprobe]"
            f_args, script_path = filter_script_args("filter_complex", probe_fc, work_dir or Path("."))
            cmd = ["ffmpeg", "-hide_banner", "-nostats", *inputs, *f_args, "-map", "[lnprobe]", "-f", "null", "-"]
        else:
            if path is None:
                return None
            cmd = [
                "ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-map", audio_map,
                "-af", f"ebur128=peak=true:framelog=quiet,{ln_probe}", "-f", "null", "-",
            ]
        res = run_cmd(cmd)
    finally:
        if script_path is not None:
            script_path.unlink(missing_ok=True)
    text = f"{res.stdout}\n{res.stderr}"
    data = _parse_loudnorm_json(text) if res.returncode == 0 else None
    if not data:
        return None
    try:
        vals = {
            "integrated": float(data["input_i"]), "true_peak": float(data["input_tp"]),
            "lra": float(data["input_lra"]), "threshold": float(data["input_thresh"]),
            "target_offset": float(data["target_offset"]),
        }
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in vals.values()) or vals["threshold"] <= -70.0:
        return None
    ebur_tp = _summary_peak(text)
    if ebur_tp is not None and math.isfinite(ebur_tp):
        vals["true_peak"] = max(vals["true_peak"], ebur_tp)
    return vals


def build_loudnorm_filter(
    measured: dict[str, float] | None,
    *,
    target_lufs: float = -14.0,
    true_peak: float = -1.5,
    target_lra: float = 11.0,
    max_limiter_db: float = 6.0,
) -> tuple[str, dict[str, Any]]:
    """Return `(ffmpeg_filter_str, plan_meta)` for linear two-pass loudnorm + oversampled limiter."""
    tail_limiter = f"alimiter=limit={10.0 ** (true_peak / 20.0):.6f}:level=false"
    if not measured:
        return (
            f"loudnorm=I={target_lufs}:TP={true_peak}:LRA={target_lra}:linear=true,{tail_limiter}",
            {"mode": "single_pass", "target_lufs": target_lufs, "true_peak": true_peak},
        )
    m_i, m_tp, m_lra = measured["integrated"], measured["true_peak"], measured["lra"]
    lra, gain_needed = max(target_lra, m_lra), target_lufs - m_i
    pre_chain, effective_tp, limiter_info = "", m_tp, None
    if m_tp + gain_needed > true_peak and max_limiter_db > 0 and lra <= _LOUDNORM_MAX_LRA:
        ceiling = round(true_peak - LIMITER_HEADROOM_DB, 2)
        pre_gain = min(gain_needed, ceiling + max_limiter_db - m_tp)
        if pre_gain > 0.1:
            lim = 10.0 ** (ceiling / 20.0)
            lat = ":latency=true" if _alimiter_has_latency() else ""
            pre_chain = f"volume={pre_gain:.2f}dB,aresample={_LIMITER_OVERSAMPLE_RATE},alimiter=limit={lim:.6f}:attack=5:release=100:level=false{lat},aresample=48000"
            effective_tp, m_i = ceiling, m_i + pre_gain
            limiter_info = {"pre_gain_db": round(pre_gain, 2), "ceiling_dbtp": ceiling}
    peak_limited = math.floor((true_peak - effective_tp + m_i) * 100) / 100.0 - 0.01
    integrated = round(min(target_lufs, peak_limited), 2)
    if integrated < -70.0 or lra > _LOUDNORM_MAX_LRA:
        return (
            f"loudnorm=I={target_lufs}:TP={true_peak}:LRA={min(_LOUDNORM_MAX_LRA, target_lra)},{tail_limiter}",
            {"mode": "dynamic_fallback", "target_lufs": target_lufs, "true_peak": true_peak},
        )
    ln = (
        f"loudnorm=I={integrated}:TP={true_peak}:LRA={lra:.2f}:linear=true"
        f":measured_I={m_i:.2f}:measured_TP={effective_tp:.2f}:measured_LRA={m_lra:.2f}"
        f":measured_thresh={measured['threshold']:.2f}:offset={measured['target_offset']:.2f}:print_format=summary"
    )
    return f"{pre_chain},{ln},{tail_limiter}" if pre_chain else f"{ln},{tail_limiter}", {
        "mode": "two_pass_linear_limited" if limiter_info else "two_pass_linear",
        "integrated_target": integrated, "true_peak_target": true_peak, "limiter": limiter_info,
    }


def correct_aac_true_peak(
    mp4_path: str | Path,
    reencode_fn: Callable[[float], None],
    *,
    target_tp: float = -1.5,
    initial_target: float | None = None,
    max_tries: int = 3,
) -> dict[str, Any]:
    """Measure delivered AAC true peak; if over `target_tp`, lower pre-encode target and re-encode."""
    peak_target = round(target_tp - CODEC_PEAK_HEADROOM_DB, 2) if initial_target is None else initial_target
    delivered, corrections = measure_loudness(mp4_path), 0
    while delivered is not None and delivered["true_peak"] > target_tp and corrections < max_tries:
        peak_target = round(peak_target - (delivered["true_peak"] - target_tp) - _CODEC_PEAK_MARGIN_DB, 2)
        reencode_fn(peak_target)
        corrections += 1
        delivered = measure_loudness(mp4_path)
    return {
        "integrated": round(delivered["integrated"], 2) if delivered else None,
        "true_peak": round(delivered["true_peak"], 2) if delivered else None,
        "peak_target_dbtp": peak_target, "corrections": corrections,
    }
