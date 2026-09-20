"""Original-dialogue subtitle loading, source mapping, and gap placement."""

import json
import re
from pathlib import Path

from artifacts import _load_work_json
from audio_mix import _seg_place_window
from lib import CONFIG
from media import _plan_clip_spans
from assemble_constants import (
    _AUTO_ORIGINAL_READ_CPS,
    _CLIP_CONTIGUITY_TOLERANCE,
    _MAX_ORIGINAL_READ_CPS,
    _MIN_ASR_CLIP_OVERLAP,
    _MIN_GAP_TO_SUBTITLE,
    _MIN_READABLE_SECONDS,
    _SUBTITLE_CLOSING_QUOTES,
)
from subtitle_core import (
    _bracketed_original_chunks,
    _subtitle_entries,
)

def _has_user_subtitles(work_dir):
    """True when the user dropped a bring-your-own original-subtitle file into work_dir."""
    return work_dir is not None and any(
        (Path(work_dir) / name).exists()
        for name in ("user_subtitles.json", "user_subtitles.srt", "user_subtitles.ass")
    )


def _source_subtitle_mask_policy(work_dir=None):
    """Explicit source-subtitle mask policy and trigger facts for visual QC/cache keys.

    Older builds treated ``MASK_SOURCE_SUBTITLES=True`` as an ambient default black
    band. The visual contract now requires an explicit policy, so a bare truthy
    legacy flag is represented as ``legacy_implicit`` and blocks the visual gate
    instead of silently masking picture information.
    """
    burn = CONFIG["burn_subtitles"]
    raw_policy = CONFIG["source_subtitle_mask_policy"]
    legacy_flag = CONFIG["mask_source_subtitles"]
    allowed = {"off", "opt_in", "safe", "forced"}
    declared = CONFIG["source_subtitle_mask_policy_declared"] or raw_policy in {"opt_in", "safe", "forced"}
    implicit = False
    if legacy_flag and not declared:
        raw_policy = "legacy_implicit"
        implicit = True
    elif raw_policy not in allowed:
        implicit = True
    user_subtitles = _has_user_subtitles(work_dir)
    active = False
    trigger = "policy_off"
    reason = "source subtitle masking disabled by explicit policy"
    if raw_policy == "off":
        active = False
    elif raw_policy in {"opt_in", "forced"}:
        active = burn and legacy_flag
        trigger = "burn_subtitles_and_legacy_mask_flag"
        reason = "explicit policy permits masking only with burned recap subtitles"
    elif raw_policy == "safe":
        active = burn and (legacy_flag or user_subtitles)
        trigger = "safe_policy_with_burned_subtitles"
        reason = "safe policy masks only when recap subtitles are burned and an original-subtitle source is declared"
    else:
        active = False
        trigger = "implicit_or_invalid_policy"
        reason = "mask_source_subtitles requires explicit SOURCE_SUBTITLE_MASK_POLICY"
    if not burn and active:
        active = False
        trigger = "burn_subtitles_disabled"
        reason = "mask-only black band is forbidden without burned recap subtitles"
    return {
        "policy": raw_policy,
        "declared": bool(declared and raw_policy in allowed),
        "active": bool(active),
        "scope": (
            "measured_source_subtitle_band"
            if active and 0 <= CONFIG["subtitle_y_top"] < CONFIG["subtitle_y_bot"]
            else ("bottom_source_subtitle_band" if active else "none")
        ),
        "trigger": trigger,
        "reason": reason,
        "burn_subtitles": burn,
        "legacy_mask_flag": legacy_flag,
        "user_subtitles_present": user_subtitles,
        "blocking": implicit,
    }


def _load_original_asr(work_dir):
    """The original speech transcription (asr_result.json), SOURCE-time [{start,end,text}]; [] when absent."""
    data = _load_work_json(work_dir, "asr_result.json")
    if data is None:
        return []
    return [{"start": float(s["start"]), "end": float(s["end"]), "text": s["text"]} for s in data]


def _load_agent_original_subtitles(work_dir):
    """Agent-calibrated original-dialogue subtitles (original_subtitles.json): OUTPUT-time
    [{start,end,text}] the writer authors alongside narration.json — the corrected, gap-aligned
    transcript of what is ACTUALLY said in each original-audio gap (ASR errors/names fixed).
    None when absent or empty (then assemble falls back to a conservative auto-ASR mapping)."""
    data = _load_work_json(work_dir, "original_subtitles.json")
    if not data:
        return None
    return [{"start": float(s["start"]), "end": float(s["end"]), "text": s["text"]} for s in data]


def _user_subtitle_entries(rows, source):
    """Validate user-authored {start,end,text} rows; a malformed row is an error, not a skip."""
    out = []
    for index, row in enumerate(rows, start=1):
        try:
            start, end, text = float(row["start"]), float(row["end"]), row["text"].strip()
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ValueError(f"{source} 第 {index} 条缺少或无法解析 start/end/text: {row!r}") from exc
        if end <= start or not text:
            raise ValueError(f"{source} 第 {index} 条无效（需要 end > start 且 text 非空）: {row!r}")
        out.append({"start": start, "end": end, "text": text})
    return out


def _parse_srt_timestamp(value):
    """Parse an SRT 'HH:MM:SS,mmm' (or ASS 'H:MM:SS.cc') timestamp into seconds."""
    m = re.match(r"\s*(\d+):(\d{1,2}):(\d{1,2})[.,](\d{1,3})\s*$", value)
    if not m:
        raise ValueError(f"无法解析字幕时间戳: {value!r}")
    h, mm, ss, frac = m.groups()
    return int(h) * 3600 + int(mm) * 60 + int(ss) + int(frac) / (10 ** len(frac))


def _parse_srt_text(text, source):
    """Minimal SRT parser → [{start,end,text}]. Blank lines and missing indices are tolerated;
    a block without a parseable timing line is an error. Cues with no text are dropped."""
    segs = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n")):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        if lines[0].strip().isdigit():
            lines = lines[1:]
        if not lines or "-->" not in lines[0]:
            raise ValueError(f"{source}: 字幕块缺少时间行: {block.strip()!r}")
        start_text, end_text = lines[0].split("-->", 1)
        start, end = _parse_srt_timestamp(start_text), _parse_srt_timestamp(end_text)
        if end <= start:
            raise ValueError(f"{source}: 字幕结束时间必须晚于开始时间: {lines[0].strip()!r}")
        body = " ".join(lines[1:]).strip()
        if body:
            segs.append({"start": start, "end": end, "text": body})
    return segs


def _parse_ass_text(text, source):
    """Minimal ASS Dialogue parser → [{start,end,text}] (Start, End are fields 2 and 3)."""
    segs = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not line.startswith("Dialogue:"):
            continue
        fields = line[len("Dialogue:"):].split(",", 9)
        if len(fields) < 10:
            raise ValueError(f"{source}: Dialogue 行字段不足: {line!r}")
        start, end = _parse_srt_timestamp(fields[1]), _parse_srt_timestamp(fields[2])
        if end <= start:
            raise ValueError(f"{source}: 字幕结束时间必须晚于开始时间: {line!r}")
        body = re.sub(r"\{[^}]*\}", "", fields[9]).replace("\\N", " ").replace("\\n", " ").strip()
        if body:
            segs.append({"start": start, "end": end, "text": body})
    return segs


def _load_user_original_subtitles(work_dir):
    """User-supplied original-dialogue subtitles, the highest-priority source (above the agent file).

    Accepts (first existing wins):
      - user_subtitles.json: a bare list [{start,end,text}] (treated as OUTPUT-time, used verbatim),
        OR a wrapper {"timeline":"source"|"output", "lines":[...]} — "source" is remapped to OUTPUT
        via the cut clip spans, "output" (default) is used directly.
      - user_subtitles.srt / user_subtitles.ass: parsed minimally and defaulted to SOURCE-time,
        so they are remapped to OUTPUT via the cut clip spans.
    Returns OUTPUT-time [{start,end,text}], or None when no user file exists. A malformed
    file raises: the user asked for these subtitles, so silently falling back is wrong."""
    work = Path(work_dir)
    json_path = work / "user_subtitles.json"
    if json_path.exists():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ValueError(f"{json_path} 不是合法 JSON: {exc}") from exc
        if isinstance(data, dict):
            timeline = data.get("timeline", "output")
            if timeline not in {"source", "output"}:
                raise ValueError(f"{json_path}: timeline 必须是 source 或 output，当前为 {timeline!r}")
            rows = data["lines"]
        else:
            timeline, rows = "output", data
        segs = _user_subtitle_entries(rows, json_path.name)
        if timeline == "source":
            segs = _map_asr_to_output(segs, _plan_clip_spans(work))
        return segs

    for name in ("user_subtitles.srt", "user_subtitles.ass"):
        path = work / name
        if not path.exists():
            continue
        parser = _parse_ass_text if name.endswith(".ass") else _parse_srt_text
        segs = parser(path.read_text(encoding="utf-8"), name)
        # .srt/.ass default to SOURCE-time → remap onto the output timeline (identity in full mode).
        return _map_asr_to_output(segs, _plan_clip_spans(work))

    return None


def _map_asr_to_output(asr_segs, clip_spans):
    """Map SOURCE-time ASR segments onto the OUTPUT timeline. Full mode (clip_spans None) is
    identity. Keep one utterance across source/output-continuous picture cuts;
    real source deletions, output gaps and repeated playback remain separate."""
    if clip_spans is None:
        return [dict(s) for s in asr_segs]
    out = []
    for seg in asr_segs:
        fragments = []
        for c in clip_spans:
            ov_s, ov_e = max(seg["start"], c["source_start"]), min(seg["end"], c["source_end"])
            if ov_e <= ov_s:
                continue
            start = c["output_start"] + (ov_s - c["source_start"])
            end = c["output_start"] + (ov_e - c["source_start"])
            entry = c.get("entry", {})
            source_key = (c.get("source_id") or entry.get("source_id"),
                          c.get("source_path") or entry.get("source_path"))
            if (fragments and abs(fragments[-1]["source_end"] - ov_s) < _CLIP_CONTIGUITY_TOLERANCE
                    and abs(fragments[-1]["end"] - start) < _CLIP_CONTIGUITY_TOLERANCE
                    and fragments[-1]["source_key"] == source_key):
                fragments[-1].update(end=end, source_end=ov_e)
            else:
                fragments.append({"start": start, "end": end, "source_end": ov_e,
                                  "source_key": source_key})
        # Filter after joining: individually tiny pieces may be one readable phrase.
        out.extend({"start": f["start"], "end": f["end"], "text": seg["text"]}
                   for f in fragments if f["end"] - f["start"] > _MIN_ASR_CLIP_OVERLAP)
    return out


def _narration_gap_windows(tts_segments, video_duration, min_gap=_MIN_GAP_TO_SUBTITLE):
    """OUTPUT-timeline stretches with NO narration (the original-audio blocks): the complement of
    the merged narration placement windows within [0, video_duration], keeping gaps >= min_gap."""
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


def _original_gap_subtitle_entries(tts_segments, work_dir, video_duration):
    """Subtitle entries for the ORIGINAL dialogue during the original-audio blocks (narration
    gaps), so the band is not blank while the original speaks. Off unless we are burning and
    subtitle_original_in_gaps is set; no-op when there is no ASR. Cut mode remaps ASR to output."""
    # Fill the gaps when either (a) we are masking the source's own burned-in subs (so the band is
    # blank without us), or (b) the user supplied their own subtitle file — a clear signal they want
    # the original dialogue shown, e.g. a clean/foreign source with mask OFF (no burned subs to
    # double). Without a user file we keep the mask requirement so we don't double the source's own
    # visible subs. subtitle_original_in_gaps is the explicit override either way.
    if not (CONFIG["burn_subtitles"]
            and CONFIG["subtitle_original_in_gaps"]
            and (_source_subtitle_mask_covers_gaps(work_dir) or _has_user_subtitles(work_dir))):
        return []
    gaps = _narration_gap_windows(tts_segments, video_duration)
    if not gaps:
        return []

    # Source ladder (highest priority first): user-supplied file → agent-calibrated transcript →
    # conservative auto-ASR mapping. The user file and the agent file are time-precise (their spans
    # are the real on-screen windows), so they take the interval-clip "precise" path; raw ASR is
    # coarse and stays on the midpoint+over-render-guard fallback path.
    max_chars = CONFIG["subtitle_max_chars"]
    user = _load_user_original_subtitles(work_dir)
    if user is not None:
        return _precise_gap_entries(user, gaps, max_chars)
    agent = _load_agent_original_subtitles(work_dir)
    if agent is not None:
        return _precise_gap_entries(agent, gaps, max_chars)
    asr = _load_original_asr(work_dir)
    if not asr:
        return []
    return _fallback_gap_entries(_map_asr_to_output(asr, _plan_clip_spans(work_dir)), gaps, max_chars)


def _precise_gap_entries(candidates, gaps, max_chars):
    """Precise path for time-accurate sources (user / agent-calibrated): interval-CLIP each line
    across the gap boundaries it overlaps, emitting one sub-entry per overlapped gap (clipped to
    that gap). A line straddling two gaps is split, not snapped to one or dropped; only sub-fragments
    shorter than _MIN_READABLE_SECONDS are dropped. No over-render guard (the source is trusted)."""
    entries = []
    for seg in candidates:
        text = seg["text"]
        seg_start, seg_end = float(seg["start"]), float(seg["end"])
        overlaps = [
            (max(seg_start, gs), min(seg_end, ge))
            for gs, ge in gaps
            if min(seg_end, ge) - max(seg_start, gs) >= _MIN_READABLE_SECONDS
        ]
        if not overlaps:
            continue
        if len(overlaps) == 1:
            # the common case (a line authored within one gap): show it whole in that gap
            cs, ce = overlaps[0]
            entries.extend(_bracketed_original_chunks(text, cs, ce, max_chars))
            continue
        # the line straddles a narration block: show each gap only ITS portion of the text
        # (proportional to the time the line overlaps that gap) instead of the whole line twice.
        seg_dur = seg_end - seg_start
        n = len(text)
        for cs, ce in overlaps:
            lo = max(0, int(round((cs - seg_start) / seg_dur * n)))
            hi = min(n, int(round((ce - seg_start) / seg_dur * n)))
            piece = text[lo:hi].strip()
            if piece:
                entries.extend(_bracketed_original_chunks(piece, cs, ce, max_chars))
    return entries


def _split_sentences_keep_delims(text):
    """Split on terminal CJK sentence marks 。！？ keeping each delimiter with its sentence. A
    fragment that is only closing quotes/brackets (e.g. a trailing 」 after a 。 inside a quote) is
    re-attached to the previous sentence so quoted speech is never split off into a bare 」."""
    parts = [p.strip() for p in re.split(r"(?<=[。！？])", text) if p.strip()]
    merged = []
    for part in parts:
        if merged and all(ch in _SUBTITLE_CLOSING_QUOTES for ch in part):
            merged[-1] += part
        else:
            merged.append(part)
    return merged


def _fallback_gap_entries(candidates, gaps, max_chars):
    """Coarse-ASR fallback. Each coarse-ASR line spans a whole window with no per-sentence onset, so
    it is split into WHOLE sentences (never mid-word); each sentence is assigned to the gap its
    char-proportional midpoint lands in, and within a gap the assigned sentences are packed
    SEQUENTIALLY from the first one's estimated onset at a comfortable read rate — so two lines in
    one gap never overlap or scatter to char-proportional tail slots — capped at the gap end. An
    over-dense gap front-truncates (shown) rather than dropping to blank."""
    # 1) split each coarse line into WHOLE sentences (never mid-word) and assign each to the gap
    #    its char-proportional midpoint lands in (the only "which gap" signal coarse ASR gives).
    buckets = {}  # gap_index -> [(estimated_onset, sentence_text)]
    for seg in candidates:
        for text in _split_sentences_keep_delims(seg["text"]):
            sub = _sentence_subspan(seg, text)
            mid = (sub["start"] + sub["end"]) / 2.0
            gi = next((i for i, (gs, ge) in enumerate(gaps) if gs <= mid < ge), None)
            if gi is None:
                continue
            buckets.setdefault(gi, []).append((sub["start"], text))
    # 2) within each gap, pack the assigned sentences SEQUENTIALLY from the gap onset at a
    #    comfortable read rate. Anchoring to the gap onset (vs each sentence's char-proportional
    #    tail position) stops a line heard early from being shoved to the END of its window — the
    #    coarse-ASR lag. Full mode keeps the real ASR onset (the first sentence's own start); an
    #    over-dense gap front-truncates (shown) rather than dropping to blank.
    entries = []
    for gi, items in buckets.items():
        gs, ge = gaps[gi]
        items.sort(key=lambda it: it[0])
        # start at the first assigned sentence's estimated onset (clamped into the gap), then pack
        # the rest sequentially so they never overlap or scatter to char-proportional tail slots.
        cursor = min(ge, max(gs, min(start for start, _ in items)))
        for _, text in items:
            if cursor >= ge - _MIN_READABLE_SECONDS:
                break
            ce2 = min(ge, cursor + max(_MIN_READABLE_SECONDS, len(text) / _AUTO_ORIGINAL_READ_CPS))
            if ce2 - cursor < _MIN_READABLE_SECONDS:
                break
            max_len = int((ce2 - cursor) * _MAX_ORIGINAL_READ_CPS)
            entries.extend(_bracketed_original_chunks(text[:max_len], cursor, ce2, max_chars))
            cursor = ce2
    return entries


def _sentence_subspan(seg, sentence):
    """The slice of seg's [start,end] window that this sentence occupies, by character proportion.
    Single-sentence lines return the whole span unchanged."""
    full = seg["text"].strip()
    idx = full.find(sentence)
    if not full or sentence == full or idx < 0:
        return {"start": seg["start"], "end": seg["end"]}
    span = seg["end"] - seg["start"]
    s = seg["start"] + span * (idx / len(full))
    e = seg["start"] + span * ((idx + len(sentence)) / len(full))
    return {"start": s, "end": e}


def _combined_subtitle_entries(narration, work_dir, video_duration):
    """Narration subtitle entries plus original-dialogue entries in the gaps, sorted by start.
    Original entries are confined to narration gaps, so they never overlap narration entries."""
    entries = _subtitle_entries(narration)
    entries.extend(_original_gap_subtitle_entries(narration, work_dir, video_duration))
    entries.sort(key=lambda x: (x["start"], x["end"]))
    return entries


def _source_subtitle_mask_covers_gaps(work_dir=None):
    """Whether the effective source mask hides hardcoded subtitles outside narration."""
    if not _source_subtitle_mask_policy(work_dir)["active"]:
        return False
    return CONFIG["subtitle_mask_opacity"] >= 1.0 - 1e-9 and CONFIG["source_subtitle_mask_timing"] == "all"
