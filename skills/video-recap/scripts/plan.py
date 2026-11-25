#!/usr/bin/env python3
"""Long-form recap compiler: expands the agent-written `recap` section of timeline.json into clips, narration timing and subtitles.

Mechanical only: the agent writes every narration sentence, picks one source shot per clause and the source range of every
original-dialogue block; this tool measures, quantises and lays them out, and reports (never rewrites) what does not fit.

  plan.py voice timeline.json                       # recap narration -> audio.narration[] (then run tts.py --update-timeline)
  plan.py build timeline.json --probe media/probe.json [--report media/plan.json]
"""

from __future__ import annotations

import argparse
import math
import re
import subprocess
import sys
from array import array
from pathlib import Path
from typing import Any, Callable, Sequence

from _common import load_json, parse_fps, resolve_source, run_cmd, save_json

__all__ = ["align_cuts", "build_plan", "clause_durations", "clauses", "main", "orig_parts", "quiet_point", "sync_voice"]

_SPLIT = re.compile(r"[，,；;：:]")
_SILENCE_START = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
_SILENCE_END = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")
_GAP, _KEEP, _HEAD, _TAIL = 1.2, 0.3, 0.1, 0.15


def _units(text: str) -> int:
    return len(re.sub(r"[\s\W_]", "", text))


def clauses(text: str) -> list[str]:
    """Split a narration sentence at commas/semicolons/colons; short parts (<6 chars) merge forward with the next part (or backward if final)."""
    out: list[str] = []
    for part in (p.strip() for p in _SPLIT.split(text.strip().rstrip("。！？!?.…"))):
        if not part:
            continue
        if out and _units(out[-1]) < 6:
            out[-1] += "，" + part
        else:
            out.append(part)
    if len(out) > 1 and _units(out[-1]) < 6:
        tail = out.pop()
        out[-1] += "，" + tail
    return out or [text.strip()]


def _pauses(wav: Path) -> list[float]:
    res = run_cmd(["ffmpeg", "-hide_banner", "-i", str(wav), "-af", "silencedetect=n=-35dB:d=0.05", "-f", "null", "-"], timeout=120)
    mids: list[float] = []
    open_start: float | None = None
    for line in (res.stderr or "").splitlines():
        if m := _SILENCE_START.search(line):
            open_start = max(0.0, float(m.group(1)))
        elif (m := _SILENCE_END.search(line)) and open_start is not None:
            mids.append((open_start + max(open_start, float(m.group(1)))) / 2)
            open_start = None
    return mids


def clause_durations(parts: Sequence[str], total: float, pauses: Sequence[float]) -> list[float]:
    """Cut the measured sentence at the real pause nearest each clause's character-proportional boundary (else proportionally)."""
    weights = [max(1, _units(p)) for p in parts]
    edges = [0.0]
    for j, wgt in enumerate(weights[:-1]):
        share = (total - edges[-1]) * wgt / sum(weights[j:])
        expect, lim = edges[-1] + share, max(0.6, 0.45 * share)
        hi = total - 0.3 * (len(weights) - 1 - j)
        near = [(abs(m - expect), m) for m in pauses if edges[-1] + 0.3 < m < hi and abs(m - expect) <= lim]
        edges.append(min(near)[1] if near else expect)
    edges.append(total)
    return [round(b - a, 4) for a, b in zip(edges, edges[1:])]


def align_cuts(cuts: Sequence[float], x: float, y: float, t0: float, t1: float) -> tuple[float, float]:
    """Move block edges onto the nearest nearby source cut so no stray frames of the neighbouring shot slip in.

    Within 0.4s when it does not clip speech. A start within 0.12s (3 frames) and an end within 0.3s of a cut always move:
    a stray sliver of the next shot is more visible than trimming the padded tail of a subtitle cue."""
    nx = min((c for c in cuts if x < c <= x + 0.4 and (c <= t0 + 0.25 or c - x <= 0.12)), default=x)
    ny = max((c for c in cuts if y - 0.4 <= c < y and (c >= t1 - 0.25 or y - c <= 0.3)), default=y)
    return (nx, ny) if nx < ny else (x, y)


def quiet_point(media: str | Path, t: float, lo: float, hi: float) -> float:
    """The point in [lo, hi] nearest `t` whose 20 ms RMS is within the quietest fifth of that span (else `t`)."""
    if hi - lo < 0.06:
        return t
    res = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, lo):.3f}", "-t", f"{hi - lo:.3f}", "-i", str(media), "-vn", "-ac", "1",
                          "-ar", "16000", "-f", "s16le", "-"], capture_output=True, timeout=60)
    pcm = array("h")
    pcm.frombytes(res.stdout[:len(res.stdout) // 2 * 2])
    rms = [math.sqrt(sum(v * v for v in pcm[i * 320:(i + 1) * 320]) / 320) for i in range(len(pcm) // 320)]
    if len(rms) < 3:
        return t
    thr = sorted(rms)[len(rms) // 5] * 1.2 + 1.0
    quiet = [max(0.0, lo) + (i + 0.5) * 0.02 for i, r in enumerate(rms) if r <= thr]
    return min(quiet, key=lambda q: abs(q - t)) if quiet else t


def orig_parts(item: dict[str, Any], segs: Sequence[dict[str, Any]], cuts: Sequence[float], fps: float,
               snap: Callable[[float, float, float], float] | None = None) -> list[dict[str, Any]]:
    """Split an original-dialogue block at pauses longer than 1.2s (keeping 0.3s either side), move each edge to a nearby quiet
    point (`snap`, never into the neighbouring line), align edges to cuts, floor to frames."""
    a, b = float(item["in"]), float(item["out"])
    inside = [s for s in segs if s["end"] > a and s["start"] < b]
    groups: list[list[dict[str, Any]]] = []
    for s in inside:
        if groups and s["start"] - groups[-1][-1]["end"] <= _GAP:
            groups[-1].append(s)
        else:
            groups.append([s])
    if not groups:
        groups = [[{"start": a, "end": b, "text": ""}]]
    parts = []
    for n, g in enumerate(groups):
        x = a if n == 0 else g[0]["start"] - _KEEP
        y = b if n == len(groups) - 1 else g[-1]["end"] + _KEEP
        if snap and g[0].get("text"):
            prev = max((s["end"] for s in segs if s["end"] <= g[0]["start"] and s is not g[0]), default=0.0)
            nxt = min((s["start"] for s in segs if s["start"] >= g[-1]["end"] and s is not g[-1]), default=y + 1.0)
            x = snap(x, max(prev + 0.05, x - 0.3), min(g[0]["start"] + 0.05, x + 0.3))
            y = snap(y, max(g[-1]["end"] - 0.05, y - 0.3), min(nxt - 0.05, y + 0.3))
        x, y = align_cuts(cuts, x, y, g[0]["start"], g[-1]["end"])
        frames = math.floor((y - x) * fps + 1e-6)
        if frames > 0:
            parts.append({"in": round(x, 4), "out": round(x + frames / fps, 4), "frames": frames, "segs": g})
    return parts


def sync_voice(tl: dict[str, Any]) -> int:
    """Mirror recap narration items into audio.narration[] (keeping measured file/duration when the text is unchanged)."""
    old = {str(n.get("id")): n for n in (tl.get("audio") or {}).get("narration") or []}
    new = []
    for it in (tl.get("recap") or {}).get("items") or []:
        if it.get("type") != "narr":
            continue
        prev = old.get(str(it["id"]), {})
        spoken = it.get("spoken_text") or it["text"]
        keep = prev.get("text") == it["text"] and (prev.get("spoken_text") or prev.get("text")) == spoken
        new.append({"id": it["id"], "text": it["text"], **({"spoken_text": it["spoken_text"]} if it.get("spoken_text") else {}),
                    **({"file": prev["file"], "duration": prev["duration"]} if keep and prev.get("file") and prev.get("duration") else {})})
    tl.setdefault("audio", {})["narration"] = new
    return sum(1 for n in new if "file" not in n)


def _shot_pair(sh: Any) -> tuple[float, float] | None:
    if not isinstance(sh, (list, tuple)) or len(sh) != 2:
        return None
    try:
        a, b = float(sh[0]), float(sh[1])
    except (TypeError, ValueError):
        return None
    return (a, b) if math.isfinite(a) and math.isfinite(b) else None


def _overlap(p: dict[str, Any], q: dict[str, Any]) -> float:
    return min(p["out"], q["out"]) - max(p["in"], q["in"]) if p["source"] == q["source"] else 0.0


def build_plan(tl: dict[str, Any], base: Path, cuts: dict[str, list[float]]) -> dict[str, Any]:
    recap = tl.get("recap") or {}
    fps_f = parse_fps((tl.get("canvas") or {}).get("fps"))
    fps = float(fps_f)
    narr = {str(n["id"]): n for n in (tl.get("audio") or {}).get("narration") or []}
    trans = {k: load_json(base / v).get("segments") or [] for k, v in (recap.get("transcripts") or {}).items()}
    order = list((tl.get("sources") or {}).keys())
    video, subs, errors, warnings, frames = [], [], [], [], 0
    for it in recap.get("items") or []:
        iid, src = str(it.get("id")), it.get("source")
        if src not in order:
            errors.append(f"{iid}: source {src!r} is not in sources")
            continue
        t0 = frames / fps
        if it.get("type") == "narr":
            n = narr.get(iid)
            if not n or not n.get("file") or not n.get("duration"):
                errors.append(f"{iid}: no measured narration; run plan.py voice then tts.py --update-timeline")
                continue
            if n.get("text") != it.get("text") or (n.get("spoken_text") or n.get("text")) != (it.get("spoken_text") or it.get("text")):
                errors.append(f"{iid}: narration text out of sync with audio.narration; re-run plan.py voice then tts.py --update-timeline")
                continue
            wav = resolve_source(tl, str(n["file"]), base)
            if not wav.is_file():
                errors.append(f"{iid}: narration audio file not found: {wav}")
                continue
            parts, shots = clauses(it["text"]), it.get("shots") or []
            if not isinstance(shots, (list, tuple)) or len(shots) != len(parts):
                errors.append(f"{iid}: {len(parts)} clauses {parts} need {len(parts)} shots, got {len(shots) if isinstance(shots, (list, tuple)) else type(shots).__name__}")
                continue
            durs = clause_durations(parts, float(n["duration"]), _pauses(wav))
            n["at"] = round(t0 + _HEAD, 4)
            spoken = n["at"]
            for j, (part, sh, d) in enumerate(zip(parts, shots, durs)):
                pair = _shot_pair(sh)
                if pair is None:
                    errors.append(f"{iid} clause {j} 「{part}」 has invalid shot {sh!r}; expected [in, out]")
                    continue
                a, b = pair
                k = max(1, round((d + (_HEAD if j == 0 else 0) + (_TAIL if j == len(parts) - 1 else 0)) * fps))
                need = k / fps
                if a < 0:
                    errors.append(f"{iid} clause {j} 「{part}」 shot [{a}, {b}] has negative start")
                    continue
                if b - a < need - 1e-3:
                    errors.append(f"{iid} clause {j} 「{part}」 needs {need:.2f}s of picture, shot [{a}, {b}] has {b - a:.2f}s; pick a longer shot")
                    continue
                x = max(0.0, min(max(a, (a + b) / 2 - need / 2), b - need))
                cross = [c for c in cuts.get(src, []) if x + 0.04 < c < x + need - 0.04]
                if cross:
                    errors.append(f"{iid} clause {j} 「{part}」 picture [{x:.2f}, {x + need:.2f}] crosses a source cut at {cross[0]:.2f}; pick a range inside one shot")
                    continue
                video.append({"id": f"{iid}c{j}", "source": src, "in": round(x, 4), "out": round(x + need, 4), "audio": "mute"})
                subs.append({"start": round(spoken, 4), "end": round(spoken + d, 4), "text": part, "style": "narration"})
                spoken += d
                frames += k
        elif it.get("type") == "orig":
            try:
                oi, oo = float(it["in"]), float(it["out"])
            except (KeyError, TypeError, ValueError):
                errors.append(f"{iid}: original block needs numeric 'in' and 'out'")
                continue
            if not (math.isfinite(oi) and math.isfinite(oo)) or oi < 0 or oo <= oi:
                errors.append(f"{iid}: original block needs out > in >= 0, got [{oi}, {oo}]")
                continue
            media = resolve_source(tl, src, base)
            snap = (lambda t, lo, hi, m=media: quiet_point(m, t, lo, hi)) if media.is_file() else None
            parts = orig_parts(it, trans.get(src, []), cuts.get(src, []), fps, snap)
            if not parts:
                errors.append(f"{iid}: original block [{oi}, {oo}] produced 0 frames")
                continue
            if sum(p["frames"] for p in parts) / fps > 30:
                warnings.append(f"{iid}: original block runs {sum(p['frames'] for p in parts) / fps:.0f}s after trimming pauses; keep the key exchange")
            for p_i, p in enumerate(parts):
                start = frames / fps
                video.append({"id": f"{iid}p{p_i}", "source": src, "in": p["in"], "out": p["out"], "audio": "keep",
                              "keep_windows": [[s["start"], s["end"]] for s in p["segs"] if s.get("text")]})
                for s in p["segs"]:
                    s0, s1 = max(s["start"], p["in"]), min(s["end"], p["out"] - 0.05)
                    if s.get("text") and s1 > s0:
                        subs.append({"start": round(start + s0 - p["in"], 4), "end": round(start + s1 - p["in"], 4), "text": s["text"], "style": "dialogue"})
                frames += p["frames"]
        else:
            errors.append(f"{iid}: type must be narr or orig")
    for clip in video if recap.get("crop") else ():  # e.g. cut off subtitles burned into the source
        clip["crop"] = recap["crop"]
    items = recap.get("items") or []
    cur = None  # soft structure hints: never block, never rewrite
    for i, it in enumerate(items):
        win = it.get("window") if it.get("type") == "narr" else None
        if win:
            w0, w1 = float(win[0]), float(win[1])
            shots_raw = it.get("shots") if isinstance(it.get("shots"), (list, tuple)) else ()
            out = [j for j, sh in enumerate(shots_raw) if (p := _shot_pair(sh)) and (p[0] < w0 - 1 or p[1] > w1 + 1)]
            if out:
                warnings.append(f"{it.get('id')}: clause shots {out} lie outside its window [{w0}, {w1}]; pick inside the time it tells")
            nxt = items[i + 1] if i + 1 < len(items) else {}
            if nxt.get("type") == "orig" and nxt.get("source") == it.get("source") and nxt.get("in") is not None:
                try:
                    nxt_in = float(nxt["in"])
                except (TypeError, ValueError):
                    nxt_in = None
                if nxt_in is not None and w1 > nxt_in + 0.5:
                    warnings.append(f"{it.get('id')}: window [{w0}, {w1}] runs into {nxt.get('id')} [{nxt['in']}, {nxt.get('out')}] right after it; lead in without telling what it says")
        if it.get("seg") == "hook" or it.get("source") not in order:
            continue
        shots_raw = it.get("shots") if isinstance(it.get("shots"), (list, tuple)) else ()
        first_shot = _shot_pair(shots_raw[0]) if shots_raw else None
        t_val = win[0] if win else (it.get("in") if it.get("in") is not None else (first_shot[0] if first_shot else 0.0))
        try:
            key = (order.index(it["source"]), float(t_val))
        except (TypeError, ValueError):
            continue
        if cur and key < cur:
            warnings.append(f"{it.get('id')}: goes back in source time (source order / time {key} < {cur})")
        cur = max(cur, key) if cur else key
    for i, p in enumerate(video):
        for q in video[i + 1:]:
            if _overlap(p, q) > 0.2:
                warnings.append(f"{p['id']} and {q['id']} reuse the same source picture ({_overlap(p, q):.1f}s)")
    return {"video": video, "subtitles": subs, "errors": errors, "warnings": warnings, "duration_s": round(frames / fps, 3)}


def _match_probe_row(rows: Sequence[dict[str, Any]], want: Path, roots: Sequence[Path]) -> dict[str, Any] | None:
    for root in roots:
        for r in rows:
            raw = str(r.get("path") or "")
            if not raw:
                continue
            p = Path(raw)
            if (p.resolve() if p.is_absolute() else (root / p).resolve()) == want:
                return r
    by_name = [r for r in rows if r.get("path") and Path(str(r["path"])).name == want.name]
    return by_name[0] if len(by_name) == 1 else None


def _cuts_by_source(tl: dict[str, Any], base: Path, probe: str | None) -> tuple[dict[str, list[float]], list[str], list[str]]:
    if not probe:
        return {}, [], []
    rows = load_json(probe).get("sources") or []
    probe_dir = Path(probe).resolve().parent
    roots = (base, probe_dir, probe_dir.parent, Path.cwd())
    used = {it.get("source") for it in (tl.get("recap") or {}).get("items") or []}
    cuts: dict[str, list[float]] = {}
    errors: list[str] = []
    warnings: list[str] = []
    for k in tl.get("sources") or {}:
        row = _match_probe_row(rows, resolve_source(tl, k, base).resolve(), roots)
        if row is None:
            cuts[k] = []
            if k in used:
                warnings.append(f"{k}: no entry in {probe}; cut checks skipped for this source")
        elif "cuts" not in row or not isinstance(row["cuts"], list):
            cuts[k] = []
            if k in used:
                errors.append(f"{k}: {probe} has no 'cuts'; re-run probe.py --scenes")
        else:
            cuts[k] = [float(c) for c in row["cuts"]]
    return cuts, errors, warnings


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Expand the agent's recap section of timeline.json into clips, narration timing and subtitles.")
    ap.add_argument("command", choices=["voice", "build"])
    ap.add_argument("timeline")
    ap.add_argument("--probe", help="probe.json from probe.py --scenes (cuts for edge alignment).")
    ap.add_argument("--report", help="Write errors/warnings JSON here.")
    args = ap.parse_args(argv)
    tl, base = load_json(args.timeline), Path(args.timeline).resolve().parent
    if not (tl.get("recap") or {}).get("items"):
        sys.stderr.write("timeline.json has no recap.items\n")
        return 2
    if args.command == "voice":
        todo = sync_voice(tl)
        save_json(args.timeline, tl)
        sys.stdout.write(f"narration: {len(tl['audio']['narration'])} lines, {todo} need tts.py\n")
        return 0
    cuts, probe_errors, probe_warnings = _cuts_by_source(tl, base, args.probe)
    res = build_plan(tl, base, cuts)
    res["errors"] = [*probe_errors, *res["errors"]]
    res["warnings"] = [*probe_warnings, *res["warnings"]]
    if args.report:
        save_json(args.report, {k: res[k] for k in ("errors", "warnings", "duration_s")})
    for msg in res["errors"]:
        sys.stdout.write(f"ERROR {msg}\n")
    for msg in res["warnings"]:
        sys.stdout.write(f"HINT {msg}\n")
    if res["errors"]:
        sys.stdout.write(f"{len(res['errors'])} errors, timeline not changed\n")
        return 3
    tl["video"], tl["subtitles"] = res["video"], res["subtitles"]
    save_json(args.timeline, tl)
    sys.stdout.write(f"{len(res['video'])} clips, {len(res['subtitles'])} cues, {res['duration_s'] / 60:.1f} min, {len(res['warnings'])} hints\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
