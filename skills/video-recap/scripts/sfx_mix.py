"""Studio Foley / transition SFX synthesis, speech-safe placement, and stem rendering."""

from __future__ import annotations

import array
import math
import random
import struct
import subprocess
import wave
from pathlib import Path

import lib
from artifacts import _load_work_json, file_identity
from source_subtitles import _load_agent_original_subtitles, _narration_gap_windows

SFX_SAMPLE_RATE = 48000
SFX_PRESETS = ("whoosh_soft", "whoosh_crisp", "impact_warm", "riser_subtle")


def synthesize_studio_sfx(preset: str, output_path: str | Path, *, seed: int = 42) -> Path:
    """Synthesize a clean, cinema-grade stereo Foley WAV (48 kHz, 16-bit PCM)."""
    if preset not in SFX_PRESETS:
        raise ValueError(f"未知音效预设 {preset!r}；可选: {', '.join(SFX_PRESETS)}")
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    sr = SFX_SAMPLE_RATE

    if preset in {"whoosh_soft", "whoosh_crisp"}:
        dur = 0.34 if preset == "whoosh_soft" else 0.25
        peak_pos = 0.46
        n = int(sr * dur)
        lp_l = lp_r = bp_l = bp_r = 0.0
        phase = 0.0
        samples = array.array("h")
        for i in range(n):
            t = i / sr
            u = i / max(1, n - 1)
            if u < peak_pos:
                env = math.sin(0.5 * math.pi * (u / peak_pos)) ** 1.7
            else:
                env = math.cos(0.5 * math.pi * ((u - peak_pos) / (1.0 - peak_pos))) ** 2.1
            cutoff = (700.0 if preset == "whoosh_soft" else 950.0) + (
                2600.0 if preset == "whoosh_soft" else 3600.0
            ) * (env ** 1.3)
            alpha = min(0.82, 2.0 * math.pi * cutoff / sr)
            hp_alpha = min(0.25, 2.0 * math.pi * 220.0 / sr)
            wn_l = rng.uniform(-1.0, 1.0)
            wn_r = rng.uniform(-1.0, 1.0) * 0.72 + wn_l * 0.28
            lp_l += alpha * (wn_l - lp_l)
            lp_r += alpha * (wn_r - lp_r)
            bp_l += hp_alpha * (lp_l - bp_l)
            bp_r += hp_alpha * (lp_r - bp_r)
            air_l = (lp_l - bp_l) * env * 0.62
            air_r = (lp_r - bp_r) * env * 0.62
            freq = 105.0 - 48.0 * u
            phase += 2.0 * math.pi * freq / sr
            body = math.sin(phase) * (env ** 1.8) * 0.28
            pan_l = 0.85 + 0.15 * math.cos(math.pi * u)
            pan_r = 0.85 - 0.15 * math.cos(math.pi * u)
            val_l = max(-0.92, min(0.92, (air_l + body) * pan_l))
            val_r = max(-0.92, min(0.92, (air_r + body) * pan_r))
            samples.append(int(val_l * 32767))
            samples.append(int(val_r * 32767))
    elif preset == "impact_warm":
        dur = 0.64
        n = int(sr * dur)
        phase1 = phase2 = lp_l = lp_r = 0.0
        samples = array.array("h")
        for i in range(n):
            t = i / sr
            u = i / max(1, n - 1)
            attack = min(1.0, t / 0.010)
            sub_env = attack * math.exp(-5.2 * u)
            transient_env = attack * math.exp(-26.0 * u)
            freq = 42.0 + 86.0 * math.exp(-16.0 * u)
            phase1 += 2.0 * math.pi * freq / sr
            phase2 += 2.0 * math.pi * (freq * 1.98) / sr
            sub = (math.sin(phase1) * 0.68 + math.sin(phase2) * 0.18) * sub_env
            wn_l = rng.uniform(-1.0, 1.0)
            wn_r = rng.uniform(-1.0, 1.0)
            alpha = min(0.45, 2.0 * math.pi * 900.0 / sr)
            lp_l += alpha * (wn_l - lp_l)
            lp_r += alpha * (wn_r - lp_r)
            val_l = math.tanh((sub + lp_l * transient_env * 0.24) * 1.15) * 0.86
            val_r = math.tanh((sub + lp_r * transient_env * 0.24) * 1.15) * 0.86
            samples.append(int(val_l * 32767))
            samples.append(int(val_r * 32767))
    else:  # riser_subtle
        dur = 0.82
        n = int(sr * dur)
        lp_l = lp_r = phase = 0.0
        samples = array.array("h")
        for i in range(n):
            u = i / max(1, n - 1)
            tail = min(1.0, (1.0 - u) / 0.06)
            env = (u ** 2.4) * tail
            cutoff = 420.0 + 3100.0 * (u ** 2.0)
            alpha = min(0.85, 2.0 * math.pi * cutoff / sr)
            lp_l += alpha * (rng.uniform(-1.0, 1.0) - lp_l)
            lp_r += alpha * (rng.uniform(-1.0, 1.0) - lp_r)
            freq = 78.0 + 110.0 * (u ** 1.6)
            phase += 2.0 * math.pi * freq / sr
            shimmer = math.sin(phase) * 0.20 * env
            val_l = max(-0.88, min(0.88, lp_l * env * 0.48 + shimmer))
            val_r = max(-0.88, min(0.88, lp_r * env * 0.48 + shimmer))
            samples.append(int(val_l * 32767))
            samples.append(int(val_r * 32767))

    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(samples.tobytes())
    return out


def _load_raw_sfx_events(work_dir: Path) -> list[dict]:
    plan = _load_work_json(work_dir, "packaging_plan.json")
    if isinstance(plan, dict) and isinstance(plan.get("sfx"), list):
        return list(plan["sfx"])
    standalone = _load_work_json(work_dir, "sfx_plan.json")
    if isinstance(standalone, dict) and isinstance(standalone.get("events"), list):
        return list(standalone["events"])
    if isinstance(standalone, list):
        return list(standalone)
    return []


def _kept_dialogue_windows(work_dir: Path, tts_segments: list[dict], video_duration: float) -> list[tuple[float, float]]:
    """Return intervals where original dialogue is audible in narration gaps."""
    gaps = _narration_gap_windows(tts_segments, video_duration, min_gap=0.45)
    if not gaps:
        return []
    protected: list[tuple[float, float]] = []
    orig_subs = _load_agent_original_subtitles(work_dir) or []
    for row in orig_subs:
        s, e = float(row["start"]), float(row["end"])
        for gs, ge in gaps:
            cs, ce = max(s, gs), min(e, ge)
            if ce - cs >= 0.2:
                protected.append((cs, ce))
    anchors = _load_work_json(work_dir, "speech_boundary_anchors_output.json") or _load_work_json(
        work_dir, "speech_boundary_anchors.json"
    )
    if isinstance(anchors, dict):
        for span in anchors.get("speech_spans") or ():
            s, e = float(span.get("start", 0.0)), float(span.get("end", 0.0))
            for gs, ge in gaps:
                cs, ce = max(s, gs), min(e, ge)
                if ce - cs >= 0.25:
                    protected.append((cs, ce))
    return sorted(protected)


def _decode_sfx_pcm(audio_path: Path) -> array.array:
    """Decode any audio file to 48 kHz stereo signed 16-bit PCM array."""
    if audio_path.suffix.lower() == ".wav":
        try:
            with wave.open(str(audio_path), "rb") as wf:
                if wf.getnchannels() == 2 and wf.getsampwidth() == 2 and wf.getframerate() == SFX_SAMPLE_RATE:
                    pcm = array.array("h")
                    pcm.frombytes(wf.readframes(wf.getnframes()))
                    return pcm
        except wave.Error:
            pass
    res = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-i", str(audio_path),
            "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "2", "-ar", str(SFX_SAMPLE_RATE), "-",
        ],
        capture_output=True,
        check=False,
    )
    if res.returncode != 0 or not res.stdout:
        stderr = (res.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"无法解码音效文件 {audio_path}: {stderr}")
    usable = len(res.stdout) - (len(res.stdout) % 4)
    pcm = array.array("h")
    pcm.frombytes(res.stdout[:usable])
    return pcm


def _detect_peak_offset_ms(pcm: array.array) -> float:
    """Locate the highest-energy stereo frame in ``pcm`` and return its offset in milliseconds."""
    frames = len(pcm) // 2
    if frames <= 0:
        return 0.0
    best_idx = 0
    best_mag = -1
    step = max(1, frames // 2400)
    for f in range(0, frames, step):
        mag = abs(pcm[2 * f]) + abs(pcm[2 * f + 1])
        if mag > best_mag:
            best_mag = mag
            best_idx = f
    return round(best_idx * 1000.0 / SFX_SAMPLE_RATE, 1)


def build_sfx_track(
    work_dir: str | Path,
    video_duration: float,
    tts_segments: list[dict],
) -> tuple[Path | None, list[dict]]:
    """Render timed SFX into work_dir/sfx_track.wav; return (None, []) when none configured."""
    root = Path(work_dir)
    raw_events = _load_raw_sfx_events(root)
    out_wav = root / "sfx_track.wav"
    if not raw_events:
        out_wav.unlink(missing_ok=True)
        return None, []

    assets_dir = root / "packaging_assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    protected = _kept_dialogue_windows(root, tts_segments, video_duration)
    total_frames = max(1, int(round(float(video_duration) * SFX_SAMPLE_RATE)))
    mix_l = [0.0] * total_frames
    mix_r = [0.0] * total_frames
    applied: list[dict] = []

    for idx, item in enumerate(raw_events):
        if not isinstance(item, dict):
            raise RuntimeError(f"音效条目 #{idx + 1} 必须是对象")
        start = float(item.get("start", 0.0))
        gain = float(item.get("gain", 0.28))
        if gain <= 0.0 or start >= video_duration - 0.05:
            continue
        start = max(0.0, start)
        raw_path = str(item.get("path") or "").strip()
        preset = str(item.get("preset") or "").strip()
        if raw_path:
            src_path = Path(raw_path).expanduser()
            if not src_path.is_absolute():
                src_path = (root / src_path).resolve()
            else:
                src_path = src_path.resolve()
            if not src_path.is_file():
                raise RuntimeError(f"音效文件不存在: {src_path}")
        elif preset:
            src_path = assets_dir / f"sfx_{preset}.wav"
            if not src_path.is_file():
                synthesize_studio_sfx(preset, src_path, seed=42 + idx)
        else:
            raise RuntimeError(f"音效条目 #{idx + 1} 必须提供 path 或 preset")

        pcm = _decode_sfx_pcm(src_path)
        frames = len(pcm) // 2
        if frames <= 0:
            continue
        dur = frames / SFX_SAMPLE_RATE
        raw_peak_ms = item.get("peak_offset_ms")
        if isinstance(raw_peak_ms, (int, float)) and not isinstance(raw_peak_ms, bool) and raw_peak_ms > 0:
            peak_ms = float(raw_peak_ms)
        elif item.get("align_peak") is True:
            peak_ms = _detect_peak_offset_ms(pcm)
        else:
            peak_ms = 0.0
        if peak_ms > 0.0:
            start = max(0.0, start - min(dur * 0.9, peak_ms / 1000.0))
        end = min(float(video_duration), start + dur)

        if not item.get("allow_over_dialogue", False):
            overlaps = [
                (ps, pe) for ps, pe in protected
                if min(end, pe) - max(start, ps) > 0.04
            ]
            if overlaps:
                ps, _pe = overlaps[0]
                shifted_end = ps - 0.02
                shifted_start = shifted_end - dur
                if shifted_start >= 0.0 and abs(shifted_start - start) <= 0.25 and all(
                    min(shifted_end, b) - max(shifted_start, a) <= 0.02 for a, b in protected
                ):
                    start, end = shifted_start, shifted_end
                else:
                    lib.log(f"  ⚠️ 跳过覆盖原声台词的音效 #{idx + 1} ({start:.2f}s–{end:.2f}s)")
                    continue

        s_frame = int(round(start * SFX_SAMPLE_RATE))
        max_copy = min(frames, total_frames - s_frame)
        if max_copy <= 0:
            continue
        fade_frames = min(int(0.015 * SFX_SAMPLE_RATE), max_copy // 3)
        for f in range(max_copy):
            env = gain
            if fade_frames > 0 and f < fade_frames:
                env *= f / fade_frames
            elif fade_frames > 0 and f > max_copy - fade_frames:
                env *= (max_copy - f) / fade_frames
            mix_l[s_frame + f] += (pcm[2 * f] / 32768.0) * env
            mix_r[s_frame + f] += (pcm[2 * f + 1] / 32768.0) * env

        applied.append({
            "index": idx,
            "path": str(src_path),
            "preset": preset or None,
            "start": round(start, 3),
            "end": round(start + max_copy / SFX_SAMPLE_RATE, 3),
            "gain": round(gain, 4),
            "peak_offset_ms": round(peak_ms, 1),
            **file_identity(src_path),
        })

    if not applied:
        out_wav.unlink(missing_ok=True)
        return None, []

    out_bytes = bytearray(total_frames * 4)
    for f in range(total_frames):
        vl = int(max(-0.96, min(0.96, math.tanh(mix_l[f]))) * 32767)
        vr = int(max(-0.96, min(0.96, math.tanh(mix_r[f]))) * 32767)
        struct.pack_into("<hh", out_bytes, f * 4, vl, vr)

    with wave.open(str(out_wav), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SFX_SAMPLE_RATE)
        wf.writeframes(out_bytes)
    lib.log(f"音效合成轨: {out_wav} ({len(applied)} 处音效)")
    return out_wav, applied


def sfx_settings(work_dir: str | Path | None) -> dict:
    """Audit record for assembly_manifest.json."""
    if work_dir is None:
        return {"present": False, "events": []}
    root = Path(work_dir)
    events = _load_raw_sfx_events(root)
    if not events:
        return {"present": False, "events": []}
    return {
        "present": True,
        "count": len(events),
        "events": [
            {
                "start": float(e.get("start", 0.0)),
                "gain": float(e.get("gain", 0.28)),
                "preset": e.get("preset"),
                "path": str(Path(e["path"]).expanduser().resolve()) if e.get("path") else None,
            }
            for e in events if isinstance(e, dict)
        ],
    }
