"""Enforce narration timing, evidence, budget, pacing, and sentence-integrity rules.

This is the single validator for the agent-authored narration.json: shape, numeric
timing and text checks live here, and every later consumer trusts its result.
"""

import json
import re
from pathlib import Path

from lib import CONFIG, load_background_research, log
from agent_text import (
    TTS_UTTERANCE_OVERHEAD_SECONDS,
    _lint_char_budget,
    _scene_available_seconds,
    _spoken_unit_count,
    _text_char_count,
)
from entry_suggestion import (
    SUGGESTED_ANCHOR_MAX_SHIFT_SECONDS,
    _has_connected_predecessor,
    _is_number,
    _nearest_safe_anchor,
    _other_block_spans,
    _suggestion_window,
)
from lint_summary import format_lint_failure, format_lint_warnings
from speech_ownership import (
    anchor_boundary_use,
    entry_overlaps_source_speech,
    load_source_sentence_evidence,
)

PLACEHOLDER_RE = re.compile(r"【主角】|TA要赌上")
GENERIC_CHARACTER_RE = re.compile(
    r"(?:黑衣|白衣|灰衣|红衣|蓝衣|西装|年轻|中年|年老|陌生|神秘|高个|眼镜)(?:男子|女子|男人|女人)"
    r"|白发女子|黑发男子"
)
COMMENTARY_CLICHE_RE = re.compile(
    r"这是一场关于|命运的齿轮|好戏才刚刚开始|故事才刚刚开始"
    r"|传奇仍在继续|未来可期|拭目以待|让我们一起"
)
META_OPENER_RE = re.compile(r"^(?:大家好|今天我们来|今天聊聊|本期视频|接下来我们)")
SOURCE_HANDOFF_MAX_HOLD_SECONDS = 3.0
_SENTENCE_END_PUNCT = "。！？!?…."
_TRAILING_QUOTES = "”’\"'」』）》】] "


def _lint_issue(level, index, code, message, **extra):
    issue = {"level": level, "index": index, "code": code, "message": message}
    issue.update(extra)
    return issue


def _strip_stage_brackets(text):
    return re.sub(r"\[[^\]]*\]", "", text).strip()


def _has_terminal_punctuation(text):
    stripped = _strip_stage_brackets(text).rstrip(_TRAILING_QUOTES)
    return bool(stripped) and stripped[-1] in _SENTENCE_END_PUNCT


def _has_connected_successor(narration, index, end):
    if index + 1 >= len(narration):
        return False
    nxt = narration[index + 1]
    if not isinstance(nxt, dict):
        return False
    next_start = nxt.get("start")
    if not _is_number(next_start):
        return False
    return next_start - end <= CONFIG["duck_bridge_seconds"] + 1e-6


def _source_sentence_entry_issue(
    index, start, end, anchors, speech_owned, other_spans=(), window=(None, None),
):
    """Return a blocking issue when narration enters midway through source speech."""
    if not speech_owned:
        return None
    # A cold-open at the first frame is not an interruption: the source sentence
    # has not been allowed to start. All later entries must use a measured anchor.
    if start <= 0.25:
        return None
    if not anchors:
        return _lint_issue(
            "error",
            index,
            "source_sentence_anchors_unavailable",
            "此处与原声对白重叠，但没有已验证的句尾停顿锚点；"
            "请在 TTS 前移动或删除该段旁白，或重新生成语音边界证据。",
            entry_time=round(start, 3),
            suggested_start=None,
        )
    for anchor in anchors:
        when = anchor["time"]
        pause_start = anchor.get("pause_start", when - 0.12)
        if pause_start - 0.05 <= start <= when + 0.08:
            return None

    suggested = _nearest_safe_anchor(start, end, anchors, other_spans, window)
    return _lint_issue(
        "error",
        index,
        "interrupts_source_sentence",
        "旁白在原声句子说完前切入。请将整段旁白平移到建议的句尾锚点；"
        f"若前后 {SUGGESTED_ANCHOR_MAX_SHIFT_SECONDS:g}s 内没有不与相邻块冲突的锚点，"
        "请缩短、移动或删除该段后再重跑 validate。",
        entry_time=round(start, 3),
        suggested_start=round(suggested["time"], 3) if suggested else None,
        suggested_end=round(suggested["time"] + end - start, 3) if suggested else None,
        max_shift_seconds=SUGGESTED_ANCHOR_MAX_SHIFT_SECONDS,
        source_text_tail=str(suggested.get("text_tail", "")).strip() if suggested else "",
        anchor_confidence=suggested["confidence"] if suggested else None,
        anchor_boundary_use=anchor_boundary_use(suggested) if suggested else None,
    )


def _source_sentence_exit_issue(index, end, source_evidence, timeline_duration=None):
    """Warn when a narration run releases original audio inside detected speech with no nearby pause anchor."""
    anchors = source_evidence["anchors"]
    if not anchors:
        return None
    if timeline_duration is not None and timeline_duration - end <= SOURCE_HANDOFF_MAX_HOLD_SECONDS:
        return None
    if any(
        row["start"] - 0.05 <= end <= row["end"] + 0.05
        for row in source_evidence["quiet_windows"]
    ):
        return None
    in_mid_dialogue = any(
        row["start"] - 0.05 <= end < row["end"] - 0.35
        for row in source_evidence["dialogue_spans"]
    )
    if not in_mid_dialogue:
        return None
    if any(
        end - 0.05 <= anchor["time"] <= end + SOURCE_HANDOFF_MAX_HOLD_SECONDS
        for anchor in anchors
    ):
        return None
    return _lint_issue(
        "warning",
        index,
        "unsafe_source_release",
        f"旁白结束于 {end:.2f}s，处于检出对白区间内且后续 {SOURCE_HANDOFF_MAX_HOLD_SECONDS:g}s "
        "内无句末停顿锚点；请确认此处原声尚未开口或已自然收句，避免半句放出。",
        exit_time=round(end, 3),
        max_hold_seconds=SOURCE_HANDOFF_MAX_HOLD_SECONDS,
    )


def _suggestion_limits(output_duration):
    """Closed range a suggested move must stay in: [0, output_duration] when that is known."""
    usable = _is_number(output_duration) and output_duration > 0
    return 0.0, output_duration if usable else None


OVER_BUDGET_ERROR_RATIO = 1.25


def _over_budget_error(index, start, end, char_count, budget):
    limit = int(budget * OVER_BUDGET_ERROR_RATIO)
    return _lint_issue(
        "error",
        index,
        "over_budget",
        f"段 {index + 1} [{start:.2f}-{end:.2f}s] 写了 {char_count} 字，窗口建议约 {budget} 字"
        f"（硬上限 {limit} 字，超出 {char_count - limit} 字）；请精简文本或拉宽/平移窗口后重跑 validate。",
        start=start,
        end=end,
        budget_chars=budget,
        limit_chars=limit,
        actual_chars=char_count,
        over_chars=char_count - limit,
        tts_overhead_seconds=TTS_UTTERANCE_OVERHEAD_SECONDS,
    )


def _placeholder_leakage_issues(narration, work_dir):
    issues = []
    sources = [("narration", narration if isinstance(narration, list) else [])]
    if work_dir is not None:
        sub_path = Path(work_dir) / "original_subtitles.json"
        if sub_path.exists():
            try:
                subs = json.loads(sub_path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                subs = []
            if isinstance(subs, list):
                sources.append(("original_subtitles", subs))
    for source, items in sources:
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            text = str(item.get("narration" if source == "narration" else "text", "") or "").strip()
            matches = PLACEHOLDER_RE.findall(text)
            if matches:
                issues.append(
                    _lint_issue(
                        "error",
                        idx,
                        "placeholder_leakage",
                        "示例占位内容泄漏到成稿中（如【主角】/TA要赌上）",
                        source=source,
                        matches=matches,
                    )
                )
    return issues


def _generic_character_label_issues(narration, work_dir):
    """When background_research.json supplies named characters, warn on VLM appearance labels."""
    if work_dir is None or not isinstance(narration, list):
        return []
    research = load_background_research(work_dir)
    characters = research.get("characters") if isinstance(research, dict) else None
    if not isinstance(characters, dict) or not characters:
        return []
    known = [str(k).strip() for k in characters if str(k).strip()][:8]
    if not known:
        return []
    issues = []
    for idx, seg in enumerate(narration):
        if not isinstance(seg, dict) or not isinstance(seg.get("narration"), str):
            continue
        matches = GENERIC_CHARACTER_RE.findall(seg["narration"])
        if matches:
            issues.append(
                _lint_issue(
                    "warning",
                    idx,
                    "generic_character_label",
                    f"background_research.json 已提供角色名（{', '.join(known)}），"
                    f"建议将外貌泛称「{matches[0]}」改为角色名或身份关系（若确指路人可忽略）",
                    matches=matches,
                    known_characters=known,
                )
            )
    return issues


def _resolve_timeline_duration(scenes_analysis, mode, work_dir, output_duration):
    if _is_number(output_duration) and output_duration > 0:
        return float(output_duration)
    if mode != "full":
        return None
    rows = scenes_analysis if isinstance(scenes_analysis, list) and scenes_analysis else None
    if rows is None and work_dir is not None:
        scenes_path = Path(work_dir) / "scenes.json"
        if scenes_path.is_file():
            try:
                loaded = json.loads(scenes_path.read_text(encoding="utf-8"))
                if isinstance(loaded, list) and loaded:
                    rows = loaded
            except (OSError, ValueError):
                rows = None
    if not rows:
        return None
    ends = [
        float(row["end"])
        for row in rows
        if isinstance(row, dict) and _is_number(row.get("end")) and float(row["end"]) > 0
    ]
    return max(ends) if ends else None


def _has_local_timing_evidence(work_dir, mode):
    if work_dir is None:
        return False
    root = Path(work_dir)
    if mode == "cut_output":
        return (root / "speech_boundary_anchors_output.json").is_file()
    return any(
        (root / name).is_file()
        for name in ("asr_result.json", "silence_periods.json", "speech_boundary_anchors.json")
    )


def _timeline_pacing_issues(sorted_segments, source_evidence, timeline_duration):
    """Enforce crisp opening hook (<= max_cold_open_silence_seconds) and zero long dead air."""
    if not sorted_segments or timeline_duration is None or timeline_duration <= 0:
        return []
    raw_spans = [
        (max(0.0, float(s["start"])), min(timeline_duration, float(s["end"])))
        for s in sorted_segments
    ] + [
        (max(0.0, float(d["start"])), min(timeline_duration, float(d["end"])))
        for d in source_evidence["dialogue_spans"]
    ]
    intervals = sorted((a, b) for a, b in raw_spans if b > a)
    merged = []
    for left, right in intervals:
        if merged and left <= merged[-1][1] + 1e-6:
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))
        else:
            merged.append((left, right))

    issues = []
    max_cold = CONFIG["max_cold_open_silence_seconds"]
    if timeline_duration >= 8.0 and max_cold > 0:
        first_onset = merged[0][0] if merged else timeline_duration
        if first_onset > max_cold + 1e-6:
            issues.append(
                _lint_issue(
                    "warning",
                    0,
                    "weak_cold_open_silence",
                    f"开场前 {first_onset:.2f}s 无旁白也无原声对白（常规参考 ≤ {max_cold:.1f}s）；"
                    "若非刻意的动作/悬念冷开场留白，建议尽早切入钩子或原声台词",
                    first_audio_onset=round(first_onset, 3),
                    max_cold_open_silence_seconds=max_cold,
                )
            )

    max_dead = CONFIG["max_dead_air_seconds"]
    if max_dead > 0:
        cursor = 0.0
        for left, right in merged:
            if left - cursor > max_dead + 1e-6:
                issues.append(
                    _lint_issue(
                        "warning",
                        None,
                        "excessive_dead_air",
                        f"成片 [{cursor:.2f}-{left:.2f}s] 连续 {left - cursor:.2f}s "
                        f"无旁白也无原声对白（常规参考 ≤ {max_dead:.1f}s）；请确认是否为有意留给动作/音乐的呼吸段",
                        start=round(cursor, 3),
                        end=round(left, 3),
                        duration=round(left - cursor, 3),
                        max_dead_air_seconds=max_dead,
                    )
                )
            cursor = max(cursor, right)
        if timeline_duration - cursor > max_dead + 1e-6:
            issues.append(
                _lint_issue(
                    "warning",
                    None,
                    "excessive_dead_air",
                    f"成片尾部 [{cursor:.2f}-{timeline_duration:.2f}s] 连续 "
                    f"{timeline_duration - cursor:.2f}s 无旁白也无原声对白（常规参考 ≤ {max_dead:.1f}s）；"
                    "若画面与余韵已自然收尾可保留，否则可裁剪多余拖尾",
                    start=round(cursor, 3),
                    end=round(timeline_duration, 3),
                    duration=round(timeline_duration - cursor, 3),
                    max_dead_air_seconds=max_dead,
                )
            )
    return issues


def lint_narration(
    narration, scenes_analysis=None, *, mode="full", work_dir=None, output_duration=None,
):
    """Preflight-check agent narration before TTS; write narration_lint.json when work_dir is set.

    Segments must already be in chronological order; validation never reorders them.
    `output_duration` (cut_output) bounds where interrupt suggestions may move a block."""
    errors = []
    warnings = []
    normalized = []
    source_evidence = load_source_sentence_evidence(work_dir, mode=mode)
    source_sentence_anchors = source_evidence["anchors"]
    timeline_duration = _resolve_timeline_duration(
        scenes_analysis, mode, work_dir, output_duration
    )
    if not isinstance(narration, list):
        errors.append(
            _lint_issue(
                "error",
                None,
                "invalid_json_shape",
                "narration.json 顶层必须是 JSON 数组",
            )
        )
    else:
        previous_start = None
        suggested_spans = {}
        for idx, seg in enumerate(narration):
            if not isinstance(seg, dict):
                errors.append(
                    _lint_issue(
                        "error",
                        idx,
                        "invalid_segment",
                        "旁白条目必须是 JSON 对象",
                    )
                )
                continue
            start, end = seg.get("start"), seg.get("end")
            if not _is_number(start) or not _is_number(end):
                errors.append(
                    _lint_issue(
                        "error", idx, "invalid_time", "start 与 end 必须是有限数值"
                    )
                )
                continue
            if previous_start is not None and start < previous_start:
                errors.append(
                    _lint_issue(
                        "error",
                        idx,
                        "out_of_order",
                        "旁白条目必须按时间先后排序",
                        start=start,
                        previous_start=previous_start,
                    )
                )
            previous_start = start
            text = seg.get("narration")
            if not isinstance(text, str):
                errors.append(
                    _lint_issue(
                        "error",
                        idx,
                        "invalid_narration",
                        "narration 字段必须是字符串",
                        start=start,
                        end=end,
                    )
                )
                continue
            text = text.strip()
            pause = seg.get("pause_after_ms", CONFIG["breath_ms"])
            if not isinstance(pause, int) or isinstance(pause, bool) or pause < 0:
                errors.append(
                    _lint_issue(
                        "error",
                        idx,
                        "invalid_pause",
                        "pause_after_ms 必须是非负整数（毫秒）",
                    )
                )
                continue
            if end <= start:
                errors.append(
                    _lint_issue(
                        "error",
                        idx,
                        "invalid_time_range",
                        "end 必须大于 start",
                        start=start,
                        end=end,
                    )
                )
                continue
            raw_char_count = _text_char_count(text)
            if not text or raw_char_count == 0:
                errors.append(
                    _lint_issue(
                        "error",
                        idx,
                        "empty_narration",
                        "narration 文本不能为空（去除方括号提示与标点后须有可朗读文字）",
                        start=start,
                        end=end,
                    )
                )
                continue
            char_count = _spoken_unit_count(text) or 1
            budget = _lint_char_budget(start, end)
            play_rate = CONFIG["speech_rate"] * CONFIG["narration_speed"]
            estimated_tts_seconds = char_count / play_rate + TTS_UTTERANCE_OVERHEAD_SECONDS
            slot_seconds = _scene_available_seconds(start, end)
            over_limit = (
                mode == "full" and char_count > budget * OVER_BUDGET_ERROR_RATIO
            )
            if over_limit:
                errors.append(_over_budget_error(idx, start, end, char_count, budget))
            if budget < 5:
                warnings.append(
                    _lint_issue(
                        "warning",
                        idx,
                        "slot_too_short",
                        "旁白时间窗很短，TTS 可能放不下",
                        start=start,
                        end=end,
                        budget_chars=budget,
                    )
                )
            if not over_limit and estimated_tts_seconds > slot_seconds:
                warnings.append(
                    _lint_issue(
                        "warning",
                        idx,
                        "over_budget",
                        "字数预计朗读时长可能超出当前时间窗",
                        start=start,
                        end=end,
                        budget_chars=budget,
                        actual_chars=char_count,
                        estimated_tts_seconds=round(estimated_tts_seconds, 2),
                        slot_seconds=round(slot_seconds, 2),
                    )
                )
            if not _has_terminal_punctuation(text):
                errors.append(
                    _lint_issue(
                        "error",
                        idx,
                        "incomplete_sentence",
                        "旁白末尾必须有完整的句末标点",
                        text_tail=text[-8:],
                    )
                )
            cliche_matches = COMMENTARY_CLICHE_RE.findall(text)
            if idx == 0 and META_OPENER_RE.search(text):
                cliche_matches.append(META_OPENER_RE.search(text).group(0))
            if cliche_matches:
                warnings.append(
                    _lint_issue(
                        "warning",
                        idx,
                        "editorial_commentary_tone",
                        f"检测到评论/升华套话「{cliche_matches[0]}」；请改写为具体人物动机、筹码与因果行动，避免阅读理解式总结或慢热寒暄开场",
                        matches=cliche_matches,
                    )
                )

            if not _has_connected_predecessor(narration, idx, start):
                entry_issue = _source_sentence_entry_issue(
                    idx,
                    start,
                    end,
                    source_sentence_anchors,
                    entry_overlaps_source_speech(
                        start,
                        source_evidence,
                        authored_overlap=seg.get("overlaps_speech", True),
                    ),
                    _other_block_spans(narration, idx),
                    _suggestion_window(
                        narration, idx, suggested_spans,
                        _suggestion_limits(output_duration),
                    ),
                )
                if entry_issue:
                    errors.append(entry_issue)
                    if entry_issue.get("suggested_end") is not None:
                        suggested_spans[idx] = (
                            entry_issue["suggested_start"], entry_issue["suggested_end"],
                        )

            if timeline_duration is not None and not _has_connected_successor(narration, idx, end):
                exit_issue = _source_sentence_exit_issue(
                    idx, end, source_evidence, timeline_duration=timeline_duration
                )
                if exit_issue:
                    warnings.append(exit_issue)

            normalized.append(
                {"index": idx, "start": start, "end": end, "char_count": char_count}
            )

    sorted_segments = sorted(normalized, key=lambda item: item["start"])
    if isinstance(narration, list) and not sorted_segments:
        errors.append(
            _lint_issue(
                "error",
                None,
                "empty_narration_file",
                "narration.json 至少需要包含一条有效旁白",
            )
        )
    for prev, curr in zip(sorted_segments, sorted_segments[1:]):
        if curr["start"] < prev["end"]:
            errors.append(
                _lint_issue(
                    "error",
                    curr["index"],
                    "time_overlap",
                    "当前旁白段与上一段在时间轴上重叠",
                    previous_index=prev["index"],
                    previous_end=prev["end"],
                    start=curr["start"],
                    end=curr["end"],
                )
            )

    if _has_local_timing_evidence(work_dir, mode):
        warnings.extend(
            _timeline_pacing_issues(sorted_segments, source_evidence, timeline_duration)
        )
    errors.extend(_placeholder_leakage_issues(narration, work_dir))
    warnings.extend(_generic_character_label_issues(narration, work_dir))

    report = {
        "ok": not errors,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
    }
    if work_dir is not None:
        Path(work_dir, "narration_lint.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return report


class NarrationLintError(ValueError):
    """Lint found blocking errors: an ordinary result for the author to fix, not a crash.

    str() is the compact per-block summary; `report` is the full lint report."""

    def __init__(self, report, lint_path=None):
        self.report = report
        super().__init__(format_lint_failure(report, lint_path))


def validate_narration_or_raise(
    narration, scenes_analysis=None, *, mode="full", work_dir=None, output_duration=None,
):
    report = lint_narration(
        narration, scenes_analysis, mode=mode, work_dir=work_dir, output_duration=output_duration,
    )
    lint_path = None if work_dir is None else Path(work_dir, "narration_lint.json")
    if report["errors"]:
        raise NarrationLintError(report, lint_path)
    if report["warnings"]:
        log(format_lint_warnings(report, lint_path))
    else:
        log("narration lint：通过")
    return report
