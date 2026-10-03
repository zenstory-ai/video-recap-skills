"""Pure functions: derive labeled metrics from labels + measurements, and project the export.

`derived` only ever lives in memory: `check` prints it so the agent can write methods against
it, `export` copies the needed values into production_reference.json. The agent never types a
number — a method target names a measurement path and the export fills the value in.

The agent's cut review (labels.cut_fixes) is applied first: every shot number, derived cut
density and export value is computed from the reviewed cut list, never from the raw detector.
"""
import statistics

from reference_measure import shot_stats

PRODUCTION_SCHEMA = "video-reference.production.v1"
CUT_MATCH_S = 0.1          # a removal or addition this close to a detected cut refers to that cut
OWNERS = ("narration", "original_dialogue", "action_sound", "ambience", "music", "silence")
SWITCH_TOLERANCE_S = 0.25
MIN_WINDOW_COVERAGE = 0.8
NULL_ASR_STATUSES = ("EXPLICITLY_SKIPPED",)


def cut_fix_counts(labels):
    fixes = (labels or {}).get("cut_fixes") or {}
    return len(fixes.get("add") or []), len(fixes.get("remove") or [])


def apply_cut_fixes(measurements, labels):
    """Measurements whose `shots` come from the reviewed cut list (unchanged without fixes)."""
    fixes = (labels or {}).get("cut_fixes") or {}
    if not any(cut_fix_counts(labels)):
        return measurements
    removed = fixes.get("remove") or []
    cuts = [c for c in measurements["shots"]["cuts"] if not any(abs(c - r) <= CUT_MATCH_S for r in removed)]
    cuts = sorted({*cuts, *(round(float(t), 3) for t in fixes.get("add") or [])})
    shots = shot_stats(cuts, measurements["source"]["duration_s"])
    shots["review_windows"] = measurements["shots"].get("review_windows") or []
    return {**measurements, "shots": shots}


def _overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


def _seconds_in(spans, start, end):
    return sum(_overlap(start, end, s["start"], s["end"]) for s in spans)


def _cuts_inside(cuts, start, end):
    return sum(1 for c in cuts if start < c < end)


def _per_min(count, seconds):
    return round(count * 60.0 / seconds, 2) if seconds > 0 else None


def _r(value, digits=3):
    return None if value is None else round(value, digits)


def _merged_blocks(spans, owner):
    """Contiguous runs of one owner (adjacent spans with different narration_job join)."""
    blocks = []
    for span in spans:
        if span["owner"] != owner:
            continue
        if blocks and abs(blocks[-1][1] - span["start"]) < 1e-6:
            blocks[-1][1] = span["end"]
        else:
            blocks.append([span["start"], span["end"]])
    return blocks


def _mean_lufs(series, blocks):
    values = [
        series[i] for i in range(len(series or []))
        if series[i] is not None and any(a <= i + 0.5 < b for a, b in blocks)
    ]
    return _r(sum(values) / len(values), 1) if values else None


def _by_owner(spans, cuts, duration, series):
    result = {}
    for owner in OWNERS:
        blocks = _merged_blocks(spans, owner)
        if not blocks:
            continue
        seconds = sum(b - a for a, b in blocks)
        result[owner] = {
            "seconds": _r(seconds, 2),
            "share": _r(seconds / duration),
            "blocks": len(blocks),
            "block_median_s": _r(statistics.median(b - a for a, b in blocks), 2),
            "cuts_per_min": _per_min(sum(_cuts_inside(cuts, a, b) for a, b in blocks), seconds),
            "mean_short_term_lufs": _mean_lufs(series, blocks),
        }
    return result


def _visible_chars(text):
    return sum(1 for ch in str(text or "") if ch.isalnum())


def narration_rate(spans, asr_windows, asr_status):
    """Chars/s over ASR windows that narration spans cover by >= 80%; null when unusable."""
    narration = [s for s in spans if s["owner"] == "narration"]
    usable = asr_status is None or not (
        asr_status.startswith("FAILED_") or asr_status in NULL_ASR_STATUSES
    )
    used, chars, seconds, lengths = 0, 0, 0.0, []
    for window in asr_windows if usable else []:
        start, end = float(window.get("start", 0)), float(window.get("end", 0))
        if end <= start:
            continue
        lengths.append(end - start)
        if _seconds_in(narration, start, end) / (end - start) >= MIN_WINDOW_COVERAGE:
            used += 1
            chars += _visible_chars(window.get("text"))
            seconds += end - start
    return {
        "value": _r(chars / seconds, 2) if seconds > 0 and chars > 0 else None,
        "windows_used": used,
        "window_s": _r(statistics.median(lengths), 2) if lengths else None,
        "precision": "coarse_asr_windows",
    }


def switch_on_cut_share(spans, cuts):
    """Share of audio-owner switch points that land within ±0.25 s of a picture cut."""
    switches = [b["start"] for a, b in zip(spans, spans[1:]) if a["owner"] != b["owner"]]
    if not switches:
        return None
    hits = sum(1 for t in switches if any(abs(t - c) <= SWITCH_TOLERANCE_S for c in cuts))
    return _r(hits / len(switches))


def _lead_owner(spans, start, end):
    totals = {}
    for span in spans:
        totals[span["owner"]] = totals.get(span["owner"], 0.0) + _overlap(
            start, end, span["start"], span["end"])
    return max(totals, key=totals.get) if totals and max(totals.values()) > 0 else None


def derive(labels, measurements, asr_windows=(), asr_status=None):
    """All labeled metrics from well-formed labels; see references/reference-schema.md."""
    duration = measurements["source"]["duration_s"]
    cuts = measurements["shots"]["cuts"]
    series = (measurements.get("loudness") or {}).get("short_term_1s") or []
    spans = sorted(labels.get("audio_spans") or [], key=lambda s: s["start"])
    sections = sorted(labels.get("sections") or [], key=lambda s: s["start"])
    narration = [s for s in spans if s["owner"] == "narration"]

    by_section = {}
    for section in sections:
        entry = by_section.setdefault(section["function"], {"seconds": 0.0, "cuts": 0, "narr": 0.0})
        entry["seconds"] += section["end"] - section["start"]
        entry["cuts"] += _cuts_inside(cuts, section["start"], section["end"])
        entry["narr"] += _seconds_in(narration, section["start"], section["end"])
    first_original = next((s for s in spans if s["owner"] == "original_dialogue"), None)
    derived = {
        "by_owner": _by_owner(spans, cuts, duration, series),
        "narration_chars_per_s": narration_rate(spans, asr_windows, asr_status),
        "switch_on_cut_share": switch_on_cut_share(spans, cuts),
        "by_section": {
            function: {
                "seconds": _r(e["seconds"], 2),
                "cuts_per_min": _per_min(e["cuts"], e["seconds"]),
                "narration_share": _r(e["narr"] / e["seconds"]) if e["seconds"] > 0 else None,
            }
            for function, e in by_section.items()
        },
        "structure": [
            {
                "function": s["function"],
                "at": [_r(s["start"] / duration), _r(s["end"] / duration)],
                "lead_owner": _lead_owner(spans, s["start"], s["end"]),
            }
            for s in sections
        ],
        "first_original_at": None if first_original is None else {
            "s": _r(first_original["start"], 2),
            "fraction": _r(first_original["start"] / duration),
        },
    }
    jobs = {}
    for span in narration:
        if span.get("narration_job"):
            jobs[span["narration_job"]] = jobs.get(span["narration_job"], 0.0) + span["end"] - span["start"]
    narration_seconds = sum(s["end"] - s["start"] for s in narration)
    if jobs and narration_seconds > 0:
        derived["narration_jobs"] = {job: _r(sec / narration_seconds) for job, sec in jobs.items()}
    return derived


_MISSING = object()


def resolve(path, measurements, derived):
    """Value at a dot path in measurements (or in derived for `derived.*`); _MISSING if absent."""
    parts = str(path).split(".")
    node = {"derived": derived} if parts[0] == "derived" else measurements
    for part in parts:
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return _MISSING
    return node


def is_resolved(value):
    return value is not _MISSING


def _metric(value, provenance, **extra):
    return {"value": value, "provenance": provenance, **extra}


def build_production(breakdown, measurements, derived):
    """production_reference.json: methods + numbers only; no facts, labels, evidence or paths."""
    labels = breakdown["labels"]
    shots = measurements["shots"]
    loudness = measurements.get("loudness") or {}
    added, removed = cut_fix_counts(labels)
    shot_provenance = "reviewed" if "cut_fixes" in labels else "measured"   # {} = looked, no change
    profile = {
        "shot_median_s": _metric(shots.get("median_s"), shot_provenance),
        "cuts_per_min": _metric(shots.get("cuts_per_min"), shot_provenance),
        "integrated_lufs": _metric(loudness.get("integrated_lufs"), "measured"),
        "narration_share": _metric(
            (derived["by_owner"].get("narration") or {}).get("share", 0.0), "labeled"),
        "narration_chars_per_s": _metric(
            derived["narration_chars_per_s"]["value"], "labeled", precision="coarse_asr_windows"),
        "switch_on_cut_share": _metric(derived["switch_on_cut_share"], "labeled"),
        "first_original_at": _metric(
            None if derived["first_original_at"] is None
            else {"fraction": derived["first_original_at"]["fraction"]},
            "labeled"),
    }
    methods = []
    for method in breakdown.get("methods") or []:
        entry = {key: method[key] for key in (
            "id", "dimension", "rule", "applies_when", "avoid_when", "applies_to") if key in method}
        targets = {}
        for name, target in (method.get("targets") or {}).items():
            source = target["from"]
            provenance = ("labeled" if source.startswith("derived.")
                          else shot_provenance if source.startswith("shots.") else "measured")
            value = resolve(source, measurements, derived)
            if source == "derived.first_original_at":   # the absolute `s` stays local, as in the profile
                value = {"fraction": value["fraction"]}
            targets[name] = _metric(value, provenance)
        if targets:
            entry["targets"] = targets
        methods.append(entry)
    subtitles = {k: v for k, v in (labels.get("subtitles") or {}).items() if k != "evidence_t"}
    settings = measurements.get("settings") or {}
    cut_detection = {key: settings.get(key) for key in (
        "detector", "hard_score", "soft_score", "isolation_ratio", "isolation_window_s", "scaled")}
    return {
        "schema": PRODUCTION_SCHEMA,
        "duration_s": measurements["source"]["duration_s"],
        "canvas": measurements["source"]["canvas"],
        "cut_detection": {**cut_detection, "agent_added": added, "agent_removed": removed},
        "profile": {name: metric for name, metric in profile.items() if metric["value"] is not None},
        "structure": derived["structure"],
        "subtitles": subtitles,
        "methods": methods,
        "skipped_dimensions": dict(breakdown.get("skipped_dimensions") or {}),
    }
