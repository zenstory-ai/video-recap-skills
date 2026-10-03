"""Remap cut evidence and format output-timeline brief directives."""

import json
import math
import re
from pathlib import Path

from lib import CONFIG, file_identity


def _parse_target_seconds(value):
    """Parse a cut-mode target duration ("30m" / "600" / "1h5m" / "00:30:00") to seconds.

    None or blank means the knob is unset (None). Any other value that does not parse to
    a positive duration is a typo in user input and raises ValueError.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
    else:
        text = str(value).strip().lower()
        if not text:
            return None
        if ":" in text:
            parts = [float(p) for p in text.split(":")]
            if len(parts) == 2:
                seconds = parts[0] * 60 + parts[1]
            elif len(parts) == 3:
                seconds = parts[0] * 3600 + parts[1] * 60 + parts[2]
            else:
                raise ValueError(f"unparseable target duration: {value!r}")
            if any(p < 0 for p in parts):
                raise ValueError(f"unparseable target duration: {value!r}")
        else:
            factors = {"ms": 0.001, "s": 1, "m": 60, "h": 3600}
            seconds = 0.0
            pos = 0
            for m in re.finditer(r"([0-9]+(?:\.[0-9]+)?)(ms|s|m|h)?", text):
                if m.start() != pos:
                    break
                pos = m.end()
                seconds += float(m.group(1)) * factors[m.group(2) or "s"]
            if pos == 0 or pos != len(text):
                raise ValueError(f"unparseable target duration: {value!r}")
    if not seconds > 0:
        raise ValueError(f"target duration must be positive: {value!r}")
    return seconds


def _format_research_directive(work_dir, substrate):
    """A loud, actionable research-first directive — but ONLY when the substrate is too thin
    for real commentary (no dialogue/story spine) and no background_research.json exists yet.

    The narration's quality ceiling is how much story context the agent has: with only frame
    descriptions it can only narrate pixels, so research the title FIRST (see
    references/research-guide.md). Fires only for thin/empty substrate — NOT merely because a
    title was given — so a dialogue-rich titled run is never nagged.
    """
    if (Path(work_dir) / "background_research.json").exists():
        return []  # already researched; _format_background_research surfaces it
    if substrate["level"] not in ("thin", "empty"):
        return []  # rich enough to write from dialogue/spine; do not nag
    context = CONFIG["context_info"].strip()
    return [
        "## ⚑ Research the story FIRST (do this before writing narration)",
        "",
        "Reason: the understanding substrate is thin — no dialogue/story spine, only frame",
        "descriptions, so without research the narration can only describe pixels.",
        "1. Pull the title/keywords from `--context`, the filename, or the user's description"
        + (f" (context: {context})." if context else "."),
        "2. Use any available web-search/browser tool to look up synopsis, characters, and",
        "   relationships (see `references/research-guide.md`).",
        "3. Write `work_dir/background_research.json`, then re-read this brief and write narration",
        "   that names people and reads the picture through the plot.",
        "4. If no tool/network or nothing found: skip — keep beats sparse and strictly grounded in",
        "   the visible ASR/frame evidence rather than inventing drama.",
        "",
    ]


def _load_cut_output_spans_for_brief(work_dir, *, required=False):
    """Load fresh source→output spans for cut pass 2 brief evidence.

    Pass 2 narration is authored against edited_source.mp4's OUTPUT timeline, so
    ASR chunks and timeline_fusion must use the same OUTPUT clock. Before pass 2
    (no edited_source.mp4 yet), callers may fall back to source-time evidence; once
    pass 2 exists, missing/stale validated spans are a hard contract failure.
    """

    def fail(reason):
        if required:
            raise SystemExit(
                "cut pass2 brief requires fresh clip_plan_validated.json with explicit "
                f"source/output spans ({reason})"
            )
        return None

    work_dir = Path(work_dir)
    if not (work_dir / "edited_source.mp4").exists():
        return None
    validated_path = work_dir / "clip_plan_validated.json"
    if not validated_path.exists():
        return fail("missing clip_plan_validated.json")
    plan = json.loads(validated_path.read_text(encoding="utf-8"))
    raw_path = work_dir / "clip_plan.json"
    if (
        raw_path.exists()
        and validated_path.stat().st_mtime_ns < raw_path.stat().st_mtime_ns
    ):
        return fail("stale clip_plan_validated.json")
    spans = []
    for clip in plan["clips"]:
        span = {
            key: clip[key]
            for key in ("source_start", "source_end", "output_start", "output_end")
        }
        if not all(math.isfinite(value) for value in span.values()):
            return fail("non-finite clip span")
        if span["source_end"] <= span["source_start"] or span["output_end"] <= span["output_start"]:
            return fail("non-positive clip span")
        spans.append(span)
    return spans or fail("no clips")


def _source_output_overlaps_for_brief(start, end, spans):
    overlaps = []
    for span in spans:
        source_start = max(start, span["source_start"])
        source_end = min(end, span["source_end"])
        if source_end <= source_start:
            continue
        output_start = span["output_start"] + (source_start - span["source_start"])
        output_end = span["output_start"] + (source_end - span["source_start"])
        overlaps.append(
            {
                "source_start": source_start,
                "source_end": source_end,
                "output_start": output_start,
                "output_end": output_end,
            }
        )
    return overlaps


def _remap_frame_facts_for_brief(frame_facts, overlap):
    out = {}
    for raw_ts, vals in frame_facts.items():
        ts = float(raw_ts)
        if not (overlap["source_start"] <= ts <= overlap["source_end"]):
            continue
        out_ts = overlap["output_start"] + (ts - overlap["source_start"])
        out[f"{out_ts:.3f}"] = vals
    return out


# A time the VLM quoted in its prose: "12.0s" (the frame-tag form), "12秒", "01:05",
# "1:02:03". Only a value inside the scene's own source range counts as a source timestamp;
# anything else (a duration, an on-screen clock) is left as written.
_PROSE_TIME_RE = re.compile(
    r"(?<![\d.:])(?:(?:(\d+):)?(\d{1,2}):(\d{2}(?:\.\d+)?)(?![\d:])"
    r"|(\d+(?:\.\d+)?)\s*(?:s|秒)(?![A-Za-z]))"
)
_PROSE_TIME_SLACK = 0.25
_CUT_AWAY_TIME = "[cut-away moment]"


def _remap_prose_times_for_brief(text, scene, overlap):
    """Rewrite source timestamps quoted in VLM prose onto the OUTPUT clock.

    A time inside this part's kept source range becomes its output time ("3.2s"); a time
    elsewhere in the scene was cut away and is replaced by a marker, so an OUTPUT section
    never quotes a source-clock time."""

    def replace(match):
        hours, minutes, secs, bare = match.groups()
        if bare is not None:
            when = float(bare)
        else:
            when = int(hours or 0) * 3600 + int(minutes) * 60 + float(secs)
        if not (scene["start"] - _PROSE_TIME_SLACK <= when <= scene["end"] + _PROSE_TIME_SLACK):
            return match.group(0)
        if not (
            overlap["source_start"] - _PROSE_TIME_SLACK
            <= when
            <= overlap["source_end"] + _PROSE_TIME_SLACK
        ):
            return _CUT_AWAY_TIME
        out = overlap["output_start"] + (when - overlap["source_start"])
        return f"{min(max(out, overlap['output_start']), overlap['output_end']):.1f}s"

    return _PROSE_TIME_RE.sub(replace, text) if isinstance(text, str) else text


def _remap_scenes_to_output_for_brief(scenes, spans):
    out = []
    for scene in scenes:
        overlaps = _source_output_overlaps_for_brief(scene["start"], scene["end"], spans)
        for part_idx, overlap in enumerate(overlaps):
            item = dict(scene)
            item["start"] = round(overlap["output_start"], 3)
            item["end"] = round(overlap["output_end"], 3)
            item["frame_facts"] = _remap_frame_facts_for_brief(
                scene.get("frame_facts", {}), overlap
            )
            for field in ("description", "depth_analysis"):
                if field in item:
                    item[field] = _remap_prose_times_for_brief(item[field], scene, overlap)
            if len(overlaps) > 1:
                item["scene_id"] = f"{scene['scene_id']}.{part_idx}"
            out.append(item)
    out.sort(key=lambda x: (x["start"], x["end"]))
    return out


def _remap_segments_to_output_for_brief(segments, spans):
    out = []
    for seg in segments:
        for overlap in _source_output_overlaps_for_brief(seg["start"], seg["end"], spans):
            item = dict(seg)
            item["start"] = round(overlap["output_start"], 3)
            item["end"] = round(overlap["output_end"], 3)
            if "duration" in item:
                item["duration"] = round(item["end"] - item["start"], 3)
            out.append(item)
    out.sort(key=lambda x: (x["start"], x["end"]))
    return out


_COVERAGE_SLACK = 0.05
# No "." inside: the ASR chunker splits sentences on it.
_PARTIAL_WINDOW_TEXT = "[partial ASR window: only part of it is in the cut, text withheld]"


def _remap_asr_to_output_for_brief(segments, spans):
    """ASR rows on the OUTPUT clock for the brief's writing evidence.

    A coarse window the cut keeps only part of cannot say which of its words survived, so
    each kept piece shows a partial-window marker instead of the window's text: the
    brief never quotes dialogue that may not be in the cut. A window the kept clips cover
    completely (even across a seam) keeps its text. `speech_spans` in
    speech_boundary_anchors_output.json are remapped separately and keep the raw text."""
    out = []
    for seg in segments:
        overlaps = _source_output_overlaps_for_brief(seg["start"], seg["end"], spans)
        kept = sum(o["source_end"] - o["source_start"] for o in overlaps)
        window = seg["end"] - seg["start"]
        partial = bool(str(seg.get("text") or "").strip()) and kept < window - _COVERAGE_SLACK
        for overlap in overlaps:
            item = dict(seg)
            item["start"] = round(overlap["output_start"], 3)
            item["end"] = round(overlap["output_end"], 3)
            if "duration" in item:
                item["duration"] = round(item["end"] - item["start"], 3)
            if partial:
                item["text"] = _PARTIAL_WINDOW_TEXT
            out.append(item)
    out.sort(key=lambda x: (x["start"], x["end"]))
    return out


def _remap_brief_evidence_to_output_timeline(
    work_dir, scenes_analysis, asr_result, silence_periods, *, required=False
):
    spans = _load_cut_output_spans_for_brief(work_dir, required=required)
    if not spans:
        return scenes_analysis, asr_result, silence_periods
    return (
        _remap_scenes_to_output_for_brief(scenes_analysis, spans),
        _remap_asr_to_output_for_brief(asr_result, spans),
        _remap_segments_to_output_for_brief(silence_periods, spans),
    )


def _read_json(path, default):
    """JSON artifact, or `default` when the optional file was never written."""
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _source_to_span_output(source_time, span):
    """`source_time` on one clip span's OUTPUT clock, clamped to that span: the next span on
    the output timeline plays a different source range."""
    when = min(max(source_time, span["source_start"]), span["source_end"])
    return round(min(span["output_start"] + when - span["source_start"], span["output_end"]), 3)


def _sentence_entry_anchors_for_brief(work_dir, edit_mode):
    """Load sentence anchors and remap them to cut OUTPUT time when needed."""
    work_dir = Path(work_dir)
    anchors = [
        dict(item)
        for item in _read_json(
            work_dir / "speech_boundary_anchors.json", {"sentence_anchors": []}
        )["sentence_anchors"]
    ]
    if edit_mode != "cut" or not (work_dir / "edited_source.mp4").exists():
        return anchors

    spans = _load_cut_output_spans_for_brief(work_dir, required=True)
    output_duration = max(span["output_end"] for span in spans)
    remapped = []
    for anchor in anchors:
        source_time = anchor["time"]
        # A pause end a few ms past a frame-snapped clip edge belongs to that clip, at the
        # edge; only when no clip plays the instant itself.
        owners = [span for span in spans
                  if span["source_start"] <= source_time <= span["source_end"]] or [
            span for span in spans
            if span["source_start"] - 0.05 <= source_time <= span["source_end"] + 0.05]
        for span in owners:
            # The 0.05 s match slack can carry an anchor past the output's ends (a clip
            # end frame-snapped 181.42 -> 181.40): narration cannot start there.
            unclamped = round(span["output_start"] + source_time - span["source_start"], 3)
            if not 0 <= unclamped <= output_duration:
                continue
            item = dict(anchor)
            item["source_time"] = round(source_time, 3)
            item["time"] = _source_to_span_output(source_time, span)
            # Preserve the measured safe pause in OUTPUT time too, so cut-mode lint
            # compares one clock.
            source_pause_start = max(
                span["source_start"], min(anchor.get("pause_start", source_time - 0.12), source_time)
            )
            item["source_pause_start"] = round(min(source_pause_start, span["source_end"]), 3)
            item["pause_start"] = _source_to_span_output(source_pause_start, span)
            # `time` IS the pause end; keep the source value apart so the artifact
            # never mixes clocks.
            item["source_pause_end"] = round(anchor.get("pause_end", source_time), 3)
            item["pause_end"] = item["time"]
            if "expected_time" in anchor:
                expected = float(anchor["expected_time"])
                item["source_expected_time"] = round(expected, 3)
                item["expected_time"] = _source_to_span_output(expected, span)
            remapped.append(item)

    speech_rows = [
        row for row in _read_json(work_dir / "asr_result.json", []) if row["text"]
    ]
    quiet_rows = [
        row
        for row in _read_json(work_dir / "silence_periods.json", [])
        if not row["has_speech"]
    ]
    out_payload = {
        "schema_version": 2,
        "artifact": "speech_boundary_anchors_output.json",
        "timeline": "cut_output",
        "source_artifact": "speech_boundary_anchors.json",
        "clip_plan_identity": file_identity(work_dir / "clip_plan_validated.json"),
        "sentence_anchors": sorted(remapped, key=lambda item: item["time"]),
        "speech_spans": _remap_segments_to_output_for_brief(speech_rows, spans),
        "quiet_windows": _remap_segments_to_output_for_brief(quiet_rows, spans),
    }
    (work_dir / "speech_boundary_anchors_output.json").write_text(
        json.dumps(out_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return out_payload["sentence_anchors"]


def _anchor_boundary_use(anchor):
    # Schema-1 anchors (no `boundary_use`) came from the old coarse estimator: high/medium
    # labels there are usable but unverified.
    return anchor.get("boundary_use") or (
        "unverified" if anchor["confidence"] in {"high", "medium"} else "none"
    )


def _format_sentence_entry_anchors_for_brief(work_dir, edit_mode):
    anchors = [
        anchor
        for anchor in _sentence_entry_anchors_for_brief(work_dir, edit_mode)
        if _anchor_boundary_use(anchor) != "none"
    ]
    if not anchors:
        return []
    lines = [
        "## 原声句末安全切入点",
        "",
        "这些时间是 ASR 句末标点与短声学停顿对齐后的旁白入口。旁白在原声已开始后切入时，"
        "必须从其中一个点开始；否则会在 TTS 前被 `interrupts_source_sentence` 硬阻断。",
        "- 标 `unverified` 的点是由粗粒度 ASR 窗口里的标点位置估计、再吸附到短停顿得到的："
        "落在停顿里，不会切断单词，但不保证原声句子已经说完（误差上限见 `±`）。请自己听一下再定；"
        "门禁本身照常生效。",
        "- 调整方式：优先把 `start` 移到建议锚点；放不下时缩短文本、移动整块或删除该旁白，不能让脚本静默挪音频。",
        '- 原声句子完整性是硬约束：切入块写 `"source_entry_policy": "sentence_boundary"`；'
        "不存在 `intentional_interrupt` 绕过方式。没有后续可靠句末锚点时，移动、缩短或删除旁白块。",
    ]
    for anchor in anchors:
        source_suffix = (
            f" (SOURCE {anchor['source_time']:.2f}s)" if "source_time" in anchor else ""
        )
        text_tail = str(anchor.get("text_tail", "")).strip()
        if _anchor_boundary_use(anchor) == "verified":
            label = anchor["confidence"]
        else:
            # Schema-1 anchors carry no error bound to print.
            label = (
                f"unverified ±{float(anchor['timing_bound_seconds']):.1f}s"
                if "timing_bound_seconds" in anchor
                else "unverified"
            )
        lines.append(
            f"- {anchor['time']:.2f}s [{label}]{source_suffix} {text_tail}".rstrip()
        )
    lines.append("")
    return lines


def _format_output_clip_list(work_dir):
    """List the kept clips on the OUTPUT timeline (cut-first/narrate-second pass 2), so the
    agent narrates against the real rendered cut instead of the source timeline."""
    path = Path(work_dir) / "clip_plan_validated.json"
    if not path.exists():
        return []
    plan = json.loads(path.read_text(encoding="utf-8"))
    out = ["## Kept clips on the OUTPUT timeline", ""]
    for c in plan["clips"]:
        reason = c.get("reason", "")
        out.append(
            f"- OUTPUT {c['output_start']:.1f}–{c['output_end']:.1f}s ← SOURCE[{c.get('source_id', '0')}] "
            f"{c['source_start']:.1f}–{c['source_end']:.1f}s (clip_id={c.get('clip_id', '?')})"
            + (f" — {reason}" if reason else "")
        )
    out.append("")
    return out if len(out) > 2 else []
