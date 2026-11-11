"""Agent-calibrated original-dialogue subtitle loading and gap placement."""

from artifacts import _load_work_json
from audio_mix import _seg_place_window
from lib import CONFIG
from assemble_constants import (
    _MIN_GAP_TO_SUBTITLE,
    _MIN_READABLE_SECONDS,
)
from subtitles.core import (
    _bracketed_original_chunks,
    _bridge_narration_micro_gaps,
    _subtitle_entries,
)


def _source_subtitle_mask():
    """Whether the source-subtitle mask runs, and why, for visual QC and the manifest.

    Masking is opt-in (MASK_SOURCE_SUBTITLES, or a measured --subtitle-y-top/bot band) and
    needs burned recap subtitles: a mask-only band would hide captions without replacing them.
    """
    burn = CONFIG["burn_subtitles"]
    requested = CONFIG["mask_source_subtitles"]
    active = requested and burn
    if not requested:
        trigger = "mask_off"
        reason = "source subtitle masking not requested"
    elif burn:
        trigger = "burned_subtitles"
        reason = "masking runs only with burned recap subtitles"
    elif CONFIG["burn_subtitles_degraded"]:
        trigger = "burn_subtitles_degraded"
        reason = (
            "subtitle burn-in degraded to the .srt sidecar; a mask-only black band is "
            "forbidden without burned recap subtitles"
        )
    else:
        trigger = "burn_subtitles_disabled"
        reason = "mask-only black band is forbidden without burned recap subtitles"
    return {
        "active": bool(active),
        "scope": (
            "measured_source_subtitle_band"
            if active and 0 <= CONFIG["subtitle_y_top"] < CONFIG["subtitle_y_bot"]
            else ("bottom_source_subtitle_band" if active else "none")
        ),
        "trigger": trigger,
        "reason": reason,
        "burn_subtitles": burn,
    }


def _load_agent_original_subtitles(work_dir):
    """Agent-calibrated original-dialogue subtitles (`original_subtitles.json`).

    OUTPUT-time `[{start, end, text}]` authored alongside `narration.json`. Only clean,
    verified lines actually audible in narration gaps belong here — never coarse ASR guesses.
    """
    if work_dir is None:
        return None
    data = _load_work_json(work_dir, "original_subtitles.json")
    if not data:
        return None
    out = []
    for index, row in enumerate(data, start=1):
        try:
            start, end, text = float(row["start"]), float(row["end"]), str(row["text"]).strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"original_subtitles.json 第 {index} 条缺少或无法解析 start/end/text: {row!r}"
            ) from exc
        if end <= start or not text:
            raise ValueError(
                f"original_subtitles.json 第 {index} 条无效（需要 end > start 且 text 非空）: {row!r}"
            )
        out.append({"start": start, "end": end, "text": text})
    return out or None


def _narration_gap_windows(tts_segments, video_duration, min_gap=_MIN_GAP_TO_SUBTITLE):
    """OUTPUT-timeline stretches with NO narration (the original-audio blocks)."""
    placed = sorted(map(_seg_place_window, tts_segments), key=lambda w: w[0])
    merged = []
    for s, e in placed:
        if e - s <= 0:
            continue
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    gaps, cursor = [], 0.0
    for s, e in merged:
        if s - cursor >= min_gap:
            gaps.append((cursor, s))
        cursor = max(cursor, e)
    if video_duration - cursor >= min_gap:
        gaps.append((cursor, float(video_duration)))
    return gaps


def _source_subtitle_mask_covers_gaps():
    """Whether the effective source mask hides hardcoded subtitles outside narration."""
    if not _source_subtitle_mask()["active"]:
        return False
    return CONFIG["subtitle_mask_opacity"] >= 1.0 - 1e-9 and CONFIG["source_subtitle_mask_timing"] == "all"


def _source_subtitles_already_visible_in_gaps():
    """True when source hardcoded subtitles remain visible in gaps (narration-only or translucent mask)."""
    if not _source_subtitle_mask()["active"]:
        return False
    return not _source_subtitle_mask_covers_gaps()


def _original_gap_subtitle_entries(tts_segments, work_dir, video_duration, *, max_chars=None):
    """Subtitle entries for agent-verified original dialogue (`original_subtitles.json`) in gaps.

    Never invents cues from coarse ASR and never slices Chinese text mid-sentence.
    """
    delivering = CONFIG["burn_subtitles"] or bool(CONFIG["burn_subtitles_degraded"])
    if not (delivering and CONFIG["subtitle_original_in_gaps"]):
        return []
    if _source_subtitles_already_visible_in_gaps():
        return []
    agent = _load_agent_original_subtitles(work_dir)
    if not agent:
        return []
    gaps = _narration_gap_windows(tts_segments, video_duration)
    if not gaps:
        return []
    limit = int(max_chars or CONFIG["subtitle_max_chars"])
    entries = []
    for seg in agent:
        seg_start, seg_end, text = seg["start"], seg["end"], seg["text"]
        best = max(
            ((max(seg_start, gs), min(seg_end, ge)) for gs, ge in gaps),
            key=lambda pair: pair[1] - pair[0],
            default=(0.0, 0.0),
        )
        cs, ce = best
        if ce - cs >= _MIN_READABLE_SECONDS:
            entries.extend(_bracketed_original_chunks(text, cs, ce, limit))
    return entries


def _load_narration_highlights(work_dir):
    """Load optional per-segment or plan-wide subtitle highlight keywords."""
    if work_dir is None:
        return {}, []
    by_idx = {}
    narr = _load_work_json(work_dir, "narration.json")
    if isinstance(narr, list):
        for idx, row in enumerate(narr):
            if isinstance(row, dict) and isinstance(row.get("highlights"), list):
                by_idx[idx] = [str(x).strip() for x in row["highlights"] if str(x).strip()]
    plan = _load_work_json(work_dir, "packaging_plan.json")
    global_hl = []
    if isinstance(plan, dict) and isinstance(plan.get("subtitle_highlights"), list):
        global_hl = [str(x).strip() for x in plan["subtitle_highlights"] if str(x).strip()]
    return by_idx, global_hl


def _combined_subtitle_entries(
    narration, work_dir, video_duration, *, max_chars=None, bridge_gap_seconds=None
):
    """Narration subtitle entries plus verified original-dialogue entries in the gaps."""
    by_idx, global_hl = _load_narration_highlights(work_dir)
    entries = _subtitle_entries(narration, max_chars=max_chars, highlights_by_index=by_idx)
    if global_hl:
        for item in entries:
            merged = list(item.get("highlights") or ()) + [
                h for h in global_hl if h not in (item.get("highlights") or ())
            ]
            item["highlights"] = merged
    entries.extend(
        _original_gap_subtitle_entries(narration, work_dir, video_duration, max_chars=max_chars)
    )
    entries.sort(key=lambda x: (x["start"], x["end"]))
    bridge = (
        CONFIG.get("subtitle_bridge_gap_seconds", 0.0)
        if bridge_gap_seconds is None
        else bridge_gap_seconds
    )
    return _bridge_narration_micro_gaps(entries, bridge)
