#!/usr/bin/env python3
"""Minimal physical-only media verifier emitting check.json v1 (zero subjective editorial blocking)."""

from __future__ import annotations

import argparse
import array
import json
import math
import re
import statistics
import sys
import tempfile
import wave
from pathlib import Path
from typing import Any, Sequence

from _audio import measure_loudness
from _common import has_tool, load_json, parse_fps, probe_media, run_cmd, save_json, timeline_video_spans

__all__ = ["check_media", "main"]

_BLACK_RE = re.compile(r"black_start:(\d+(?:\.\d+)?)\s+black_end:(\d+(?:\.\d+)?)\s+black_duration:(\d+(?:\.\d+)?)")
_FREEZE_START_RE = re.compile(r"freeze_start:\s*(\d+(?:\.\d+)?)")
_FREEZE_DUR_RE = re.compile(r"freeze_duration:\s*(\d+(?:\.\d+)?)")
_SILENCE_DUR_RE = re.compile(r"silence_duration:\s*(\d+(?:\.\d+)?)")


def _entry(status: str, detail: str, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"status": status, "detail": detail, "evidence": evidence or {}}


def _decode_check(media: Path) -> dict[str, Any]:
    if not has_tool("ffmpeg"):
        return _entry("skip", "ffmpeg unavailable")
    for label, extra in (("head", ["-t", "2"]), ("tail", ["-sseof", "-2"])):
        res = run_cmd(["ffmpeg", "-v", "error", "-xerror", *extra, "-i", str(media), "-f", "null", "-"], timeout=60)
        if res.returncode != 0:
            return _entry("fail", f"{label} decode error", {"stderr": (res.stderr or "").strip()[:300]})
    return _entry("pass", "head+tail decoded with -xerror")


def _scan_visual(media: Path) -> tuple[list[dict[str, float]], list[dict[str, float]]]:
    if not has_tool("ffmpeg"):
        return [], []
    res = run_cmd(["ffmpeg", "-hide_banner", "-i", str(media), "-vf", "blackdetect=d=0.5:pix_th=0.10,freezedetect=n=-60dB:d=2.0", "-f", "null", "-"], timeout=120)
    err = res.stderr or ""
    blacks = [{"start": round(float(m.group(1)), 3), "end": round(float(m.group(2)), 3), "duration": round(float(m.group(3)), 3)} for m in _BLACK_RE.finditer(err)]
    freezes, f_start = [], None
    for line in err.splitlines():
        if ms := _FREEZE_START_RE.search(line):
            f_start = float(ms.group(1))
        if (md := _FREEZE_DUR_RE.search(line)) and f_start is not None:
            dur = float(md.group(1))
            freezes.append({"start": round(f_start, 3), "end": round(f_start + dur, 3), "duration": round(dur, 3)})
            f_start = None
    return blacks, freezes


def _scan_dead_air(media: Path, min_s: float = 3.0) -> list[float]:
    if not has_tool("ffmpeg"):
        return []
    res = run_cmd(["ffmpeg", "-hide_banner", "-i", str(media), "-af", f"silencedetect=noise=-55dB:d={min_s:.2f}", "-f", "null", "-"], timeout=120)
    return [round(float(m.group(1)), 3) for m in _SILENCE_DUR_RE.finditer(res.stderr or "")]


def _scan_edge_audio(media: Path, timeline: dict[str, Any] | None, spans: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    if not timeline or not spans:
        return []
    warns: list[dict[str, Any]] = []
    narrs = [(n.get("id"), float(n.get("at", 0.0)), float(n.get("at", 0.0)) + float(n.get("duration") or 0.0)) for n in (timeline.get("audio") or {}).get("narration") or ()]
    for s in spans:
        c = s["clip"]
        if str(c.get("audio", "keep")) != "mute" and float(c.get("audio_gain", 1.0)) > 0 and not c.get("keep_windows"):
            for nid, n0, n1 in narrs:
                if n1 > n0 and s["timeline_start"] < n1 - 0.05 and s["timeline_end"] > n1 + 0.35:
                    warns.append({"code": "narration_bed_audio_leak", "clip_id": s["id"], "narration_id": nid, "tail_s": round(s["timeline_end"] - n1, 3)})
    if not has_tool("ffmpeg") or not media.is_file():
        return warns
    with tempfile.TemporaryDirectory(prefix="chk_edge_") as td:
        wav_p = Path(td) / "a.wav"
        if run_cmd(["ffmpeg", "-y", "-v", "error", "-i", str(media), "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav_p)], timeout=60).returncode != 0 or not wav_p.is_file():
            return warns
        with wave.open(str(wav_p), "rb") as wf:
            sr, samples = wf.getframerate(), array.array("h", wf.readframes(wf.getnframes()))
    def _rms_db(t0: float, t1: float) -> float:
        seg = samples[max(0, int(t0 * sr)) : min(len(samples), int(t1 * sr))]
        return 20.0 * math.log10(max(math.sqrt(sum(x * x for x in seg) / len(seg)), 1e-9) / 32768.0) if seg else -99.0
    for i, s in enumerate(spans):
        if str(s["clip"].get("audio", "keep")) == "mute" or float(s["clip"].get("audio_gain", 1.0)) <= 0:
            continue
        if i > 0 and (spans[i - 1]["source"] != s["source"] or abs(spans[i - 1]["out"] - s["in"]) > 0.05):
            if (db := _rms_db(s["timeline_start"], s["timeline_start"] + 0.08)) > -22.0:
                warns.append({"code": "edge_speech_truncation", "clip_id": s["id"], "edge": "in", "rms_dbfs": round(db, 1)})
        if i + 1 < len(spans) and (spans[i + 1]["source"] != s["source"] or abs(s["out"] - spans[i + 1]["in"]) > 0.05):
            if (db := _rms_db(s["timeline_end"] - 0.08, s["timeline_end"])) > -22.0:
                warns.append({"code": "edge_speech_truncation", "clip_id": s["id"], "edge": "out", "rms_dbfs": round(db, 1)})
    return warns


def check_media(media_path: str | Path, *, timeline: dict[str, Any] | None = None, timeline_path: str | Path | None = None) -> dict[str, Any]:
    """Run all 7 physical-only checks and return check.json v1 payload."""
    media = Path(media_path)
    limitations = ["Physical checks only: story arc, framing taste, BGM fit, and subtitle prose require Agent or human review."]
    checks: dict[str, dict[str, Any]] = {}
    review_hints: list[dict[str, Any]] = []
    telemetry: dict[str, Any] = {
        "duration_s": 0.0, "clip_count": 0, "median_clip_s": 0.0, "lufs_i": None,
        "true_peak_dbtp": None, "black_spans": [], "freeze_spans": [],
        "flash_sliver_warnings": [], "edge_audio_warnings": [],
    }

    # 1. container_valid
    info: dict[str, Any] | None = None
    if not media.is_file() or media.stat().st_size == 0:
        checks["container_valid"] = _entry("fail", f"Missing or empty media file: {media}")
        for key in ("decode_integrity", "geometry_and_cfr", "av_sync", "audio_health", "visual_health", "subtitle_and_timing"):
            checks[key] = _entry("skip", "Skipped because container_valid failed")
        return {"schema_version": 1, "media": str(media), "timeline": str(timeline_path) if timeline_path else None, "ok": False, "checks": checks, "review_hints": review_hints, "telemetry": telemetry, "limitations": limitations}

    try:
        info = probe_media(media)
        if not info.get("has_video") or info.get("duration_s", 0.0) <= 0:
            checks["container_valid"] = _entry("fail", "No playable video stream or zero duration", info)
        else:
            checks["container_valid"] = _entry("pass", f"{info.get('format_name')}, video+{'audio' if info.get('has_audio') else 'no-audio'}, {info['size_bytes']}B", {"size_bytes": info["size_bytes"], "duration_s": info["duration_s"]})
    except Exception as exc:
        checks["container_valid"] = _entry("fail", f"ffprobe failed: {exc}")

    dur = float((info or {}).get("duration_s") or 0.0)
    telemetry["duration_s"] = round(dur, 4)

    # 2. decode_integrity
    checks["decode_integrity"] = _decode_check(media) if info else _entry("skip", "No probed container")

    # 3. geometry_and_cfr
    if info and info.get("has_video"):
        w, h, pix, problems = info["width"], info["height"], info.get("pix_fmt"), []
        if w % 2 != 0 or h % 2 != 0:
            problems.append(f"odd dimensions {w}x{h}")
        if pix != "yuv420p":
            problems.append(f"pix_fmt={pix} (expected yuv420p)")
        if timeline and timeline.get("canvas"):
            c = timeline["canvas"]
            if c.get("width") and int(c["width"]) != w:
                problems.append(f"width {w} != canvas {c['width']}")
            if c.get("height") and int(c["height"]) != h:
                problems.append(f"height {h} != canvas {c['height']}")
            if c.get("fps") and abs(float(info["fps"]) - float(parse_fps(c["fps"]))) > 0.25:
                problems.append(f"fps {info['fps']} != canvas {float(parse_fps(c['fps'])):.3f}")
        checks["geometry_and_cfr"] = _entry("fail" if problems else "pass", "; ".join(problems) if problems else f"{w}x{h} {pix} @ {info.get('fps_rational')}", {"width": w, "height": h, "pix_fmt": pix, "fps": info.get("fps")})
    else:
        checks["geometry_and_cfr"] = _entry("skip", "No video stream")

    # 4. av_sync
    expected_dur, spans = None, []
    if timeline and timeline.get("video"):
        spans, expected_dur = timeline_video_spans(timeline)
        durs = [s["duration"] for s in spans]
        telemetry["clip_count"] = len(spans)
        telemetry["median_clip_s"] = round(statistics.median(durs), 3) if durs else 0.0
        telemetry["flash_sliver_warnings"] = [{"id": s["id"], "duration_s": round(s["duration"], 4)} for s in spans if s["duration"] < 0.1]
        review_hints.extend({"check": "av_sync", "code": "flash_sliver", **w} for w in telemetry["flash_sliver_warnings"])
    sync_issues = []
    if info and info.get("has_audio") and abs(dur - float(info.get("audio_duration_s") or dur)) > 0.2:
        sync_issues.append(f"video={dur:.3f}s vs audio={float(info.get('audio_duration_s') or dur):.3f}s")
    if expected_dur is not None and abs(dur - expected_dur) > 0.25:
        sync_issues.append(f"actual={dur:.3f}s vs timeline={expected_dur:.3f}s")
    checks["av_sync"] = _entry("fail" if sync_issues else "pass", "; ".join(sync_issues) if sync_issues else f"v={dur:.3f}s" + (f" timeline={expected_dur:.3f}s" if expected_dur else ""), {"duration_s": dur, "expected_duration_s": expected_dur})

    # 5. audio_health
    expect_audio = bool((info or {}).get("has_audio"))
    if timeline:
        v_audio = any(str(c.get("audio", "keep")) != "mute" and float(c.get("audio_gain", 1.0)) > 0 for c in timeline.get("video") or ())
        a_sec = timeline.get("audio") or {}
        expect_audio = v_audio or bool(a_sec.get("narration") or a_sec.get("bgm") or a_sec.get("sfx"))
    if not expect_audio:
        checks["audio_health"] = _entry("skip", "Timeline/media is intentionally silent")
        limitations.append("Audio health skipped: no active audio expected.")
    elif not (info and info.get("has_audio")):
        checks["audio_health"] = _entry("fail", "Expected audio stream is missing")
    else:
        loud = measure_loudness(media)
        if not loud:
            checks["audio_health"] = _entry("fail", "Audio stream is silent or unmeasurable")
        else:
            telemetry["lufs_i"], telemetry["true_peak_dbtp"] = round(loud["integrated"], 2), round(loud["true_peak"], 2)
            a_fails = [f"clipping true_peak={loud['true_peak']:.2f} dBTP > 0.0"] if loud["true_peak"] > 0.0 else []
            a_warns: list[str] = []
            if loud["integrated"] < -55.0:
                a_warns.append(f"near-silent integrated={loud['integrated']:.2f} LUFS")
                review_hints.append({"check": "audio_health", "code": "near_silent_audio", "lufs_i": telemetry["lufs_i"], "detail": a_warns[-1]})
            dead_air = _scan_dead_air(media, min_s=3.0) if dur >= 4.0 else []
            if dead_air:
                a_warns.append(f"{len(dead_air)} dead-air span(s) >= 3s")
                review_hints.append({"check": "audio_health", "code": "dead_air", "spans_s": dead_air, "detail": a_warns[-1]})
            edge_warns = _scan_edge_audio(media, timeline, spans)
            telemetry["edge_audio_warnings"] = edge_warns
            if edge_warns:
                a_warns.append(f"{len(edge_warns)} edge audio warning(s)")
                review_hints.extend({"check": "audio_health", **w} for w in edge_warns)
            status = "fail" if a_fails else ("warn" if a_warns else "pass")
            detail = "; ".join(a_fails) if a_fails else f"{loud['integrated']:.1f} LUFS, {loud['true_peak']:.1f} dBTP" + (f" ({'; '.join(a_warns)})" if a_warns else "")
            checks["audio_health"] = _entry(status, detail, {"lufs_i": telemetry["lufs_i"], "true_peak_dbtp": telemetry["true_peak_dbtp"], "dead_air_spans_s": dead_air, "edge_audio_warnings": edge_warns})

    # 6. visual_health (soft warn only: black cards / deliberate freezes require Agent visual check)
    blacks, freezes = _scan_visual(media)
    telemetry["black_spans"], telemetry["freeze_spans"] = blacks, freezes
    review_hints.extend({"check": "visual_health", "code": "black_span", **b} for b in blacks)
    review_hints.extend({"check": "visual_health", "code": "freeze_span", **f} for f in freezes)
    checks["visual_health"] = _entry("warn" if (blacks or freezes) else "pass", f"black_spans={len(blacks)}, freeze_spans={len(freezes)}", {"black_spans": blacks, "freeze_spans": freezes})

    # 7. subtitle_and_timing (hard fail only on deterministic non-positive spans; overlaps/trailing are soft warns)
    if not timeline:
        checks["subtitle_and_timing"] = _entry("skip", "No --timeline provided")
    else:
        subs = sorted([s for s in (timeline.get("subtitles") or ()) if str(s.get("text") or "").strip()], key=lambda s: float(s["start"]))
        narrs = sorted((timeline.get("audio") or {}).get("narration") or (), key=lambda n: float(n.get("at", 0.0)))
        if not subs and not narrs:
            checks["subtitle_and_timing"] = _entry("skip", "Timeline has no subtitles or narration")
        else:
            t_fails, t_warns = [], []
            for i, s in enumerate(subs):
                st, en = float(s["start"]), float(s["end"])
                if en <= st:
                    t_fails.append(f"subtitle[{i}] non-positive [{st}, {en}]")
                if en > dur + 0.15:
                    msg = f"subtitle[{i}] ends at {en:.2f}s past video {dur:.2f}s"
                    t_warns.append(msg)
                    review_hints.append({"check": "subtitle_and_timing", "code": "subtitle_past_video_end", "index": i, "end": en, "video_end": round(dur, 3), "detail": msg})
                if i > 0 and st < float(subs[i - 1]["end"]) - 0.02:
                    msg = f"subtitle[{i - 1}]/{i} overlap at {st:.2f}s"
                    t_warns.append(msg)
                    review_hints.append({"check": "subtitle_and_timing", "code": "subtitle_overlap", "indices": [i - 1, i], "at": st, "detail": msg})
            for i in range(1, len(narrs)):
                prev_end = float(narrs[i - 1].get("at", 0.0)) + float(narrs[i - 1].get("duration") or 0.0)
                cur_at = float(narrs[i].get("at", 0.0))
                if cur_at < prev_end - 0.02:
                    msg = f"narration[{i - 1}]/{i} overlap at {cur_at:.2f}s"
                    t_warns.append(msg)
                    review_hints.append({"check": "subtitle_and_timing", "code": "narration_overlap", "indices": [i - 1, i], "at": cur_at, "detail": msg})
            status = "fail" if t_fails else ("warn" if t_warns else "pass")
            checks["subtitle_and_timing"] = _entry(status, "; ".join(t_fails + t_warns) if (t_fails or t_warns) else f"{len(subs)} subtitles, {len(narrs)} narration segs, 0 collisions")

    return {"schema_version": 1, "media": str(media), "timeline": str(timeline_path) if timeline_path else None, "ok": all(row["status"] != "fail" for row in checks.values()), "checks": checks, "review_hints": review_hints, "telemetry": telemetry, "limitations": limitations}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run physical-only media checks (check.json v1).")
    parser.add_argument("media", help="Rendered media path.")
    parser.add_argument("--timeline", help="Optional canonical timeline.json to verify geometry/sync/timing against.")
    parser.add_argument("--out", help="Output check.json path (prints to stdout if omitted).")
    args = parser.parse_args(argv)
    tl = load_json(args.timeline) if args.timeline else None
    report = check_media(args.media, timeline=tl, timeline_path=args.timeline)
    save_json(args.out, report) if args.out else sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
