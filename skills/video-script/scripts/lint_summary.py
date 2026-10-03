"""Render a narration lint report as the compact lines the author reads on the console.

narration_lint.json keeps every field; this summary repeats, per flagged block, the
numbers needed to fix it without opening that file. Errors (failed lint) and warnings
(passed lint) use the same line format. Block numbers are 1-based ("段 N",
the block's position in narration.json); the report's `index` fields stay 0-based. A
deslop blocker found in original_subtitles.json carries `source: "original_subtitles"`
and an index into that file, so it is labelled "原声字幕第 N 条", never "段 N".
"""

MAX_LISTED_ERRORS = 12

_HINTS = {
    "over_budget": "缩短文字，或放宽/挪动时间窗",
    "interrupts_source_sentence": "把整块（时长不变）挪到建议的时间窗；没有建议入点时缩短、挪走或删掉这块",
    "source_sentence_anchors_unavailable": "挪走或删掉这块，或重新生成含句尾锚点的语音证据",
    "time_overlap": "调整时间窗，让两块不重叠",
    "out_of_order": "按 start 从小到大排列各块",
    "outside_clip_plan": "把这块挪进某个选中片段的时间范围",
    "ambiguous_source_clip": "给这块写 source_clip_id",
    "em_dash": "删掉破折号，改用逗号、句号或把句子拆开",
    "placeholder_leakage": "把示例占位（如【主角】）换成真实人名或内容",
    "slot_too_short": "放宽时间窗，或把这块并进相邻块",
    "incomplete_sentence": "用句号、问号或感叹号收尾",
    "outside_scene": "核对时间，确认这块对应哪段画面",
    "crosses_scene_boundary": "把这块收进所在场景，或在场景切换处拆成两块",
    "visual_beat_too_broad": "拆块或收紧时间窗，让旁白贴着当前画面",
    "crosses_clip_boundary": "把这块收进所在片段，超出部分会被裁掉",
    "no_original_blocks": "在几个强原声时刻不写旁白，让原声满音量播放",
    "under_narrated": "确认每个长空档都有意交给原声；只在有明确解说任务时加块",
    "no_original_breaks": "在块之间留几个多秒的空档给原声",
    "fragmented_beats": "把相邻、讲同一件事的短句并成一块",
}

_ORIGINAL_SUBTITLE_HINTS = {
    "em_dash": "在 original_subtitles.json 里删掉这条的破折号（不是改 narration.json）",
    "placeholder_leakage": "在 original_subtitles.json 里替换这条的示例占位（不是改 narration.json）",
}
_ORIGINAL_SUBTITLE_FALLBACK_HINT = "改 original_subtitles.json 里的这一条，不是 narration.json"


def block_label(index):
    """'段 N' for a 0-based block index; '整体' for a file-level issue."""
    return "整体" if index is None else f"段 {index + 1}"


def _label(e):
    if e.get("source") == "original_subtitles":
        index = e.get("index")
        return "原声字幕" if index is None else f"原声字幕第 {index + 1} 条"
    return block_label(e.get("index"))


def _hint(e):
    code = e.get("code")
    if e.get("source") == "original_subtitles":
        return _ORIGINAL_SUBTITLE_HINTS.get(code, _ORIGINAL_SUBTITLE_FALLBACK_HINT)
    return _HINTS.get(code)


def _seconds(value):
    return f"{value:.2f}s" if isinstance(value, (int, float)) else "?"


def _window(e):
    return f"{_seconds(e.get('start'))}-{_seconds(e.get('end'))}"


def _over_budget(e):
    head = f"{_window(e)} 写了 {e.get('actual_chars')} 字，窗口约 {e.get('budget_chars')} 字"
    if "limit_chars" in e:  # the full-mode error
        return f"{head}（硬上限 {e.get('limit_chars')}，超出 {e.get('over_chars')} 字）"
    return (
        f"{head}，估计读完 {_seconds(e.get('estimated_tts_seconds'))}，"
        f"可用 {_seconds(e.get('slot_seconds'))}"
    )


def _interrupts(e):
    text = f"入点 {_seconds(e.get('entry_time'))} 落在原声句子中间"
    if e.get("suggested_start") is None:
        shift = e.get("max_shift_seconds")
        within = f"前后 {shift:g} 秒内" if isinstance(shift, (int, float)) else "附近"
        return text + f"，{within}没有能整块挪过去、留在前后两块之间又不与其他块重叠或相接的句尾锚点"
    tail = e.get("source_text_tail")
    tail = f"，句尾「{tail}」" if tail else ""
    target = _seconds(e["suggested_start"])
    if e.get("suggested_end") is not None:
        target += f"-{_seconds(e['suggested_end'])}"
    return (
        f"{text}，建议入点 {target}"
        f"（锚点 {e.get('anchor_confidence')}/{e.get('anchor_boundary_use')}{tail}）"
    )


def _ratio(value):
    return f"{value:.0%}" if isinstance(value, (int, float)) else "?"


_WARNING_DETAILS = {
    "slot_too_short": lambda e: f"{_window(e)} 只容得下约 {e.get('budget_chars')} 字，TTS 可能放不下",
    "incomplete_sentence": lambda e: f"结尾「{e.get('text_tail', '')}」没有句末标点",
    "outside_scene": lambda e: f"{_window(e)} 的中点不在任何检测到的场景里",
    "crosses_scene_boundary": lambda e: (
        f"{_window(e)} 超出所在场景 "
        f"{_seconds(e.get('scene_start'))}-{_seconds(e.get('scene_end'))}"
    ),
    "visual_beat_too_broad": lambda e: (
        f"{_window(e)} 共 {_seconds(e.get('duration'))}，"
        f"跨过 {e.get('frame_fact_count', '?')} 个画面锚点"
    ),
    "crosses_clip_boundary": lambda e: (
        f"{_window(e)} 超出所在片段 {_seconds(e.get('clip_start'))}-{_seconds(e.get('clip_end'))}"
    ),
    "no_original_blocks": lambda e: (
        f"旁白覆盖率 {_ratio(e.get('narration_coverage'))}，超过上限 {_ratio(e.get('coverage_max'))}"
    ),
    "under_narrated": lambda e: (
        f"旁白覆盖率 {_ratio(e.get('narration_coverage'))}，低于下限 {_ratio(e.get('coverage_min'))}"
    ),
    "no_original_breaks": lambda e: (
        f"块与块之间没有 ≥{_seconds(e.get('original_block_min_seconds'))} 的原声段"
    ),
    "fragmented_beats": lambda e: (
        f"平均每块 {e.get('avg_block_chars')} 字，少于 {e.get('block_min_chars')} 字"
    ),
}


def _details(e):
    code = e.get("code")
    if code == "over_budget" and "budget_chars" in e:
        return _over_budget(e)
    if code == "interrupts_source_sentence":
        return _interrupts(e)
    if code == "source_sentence_anchors_unavailable":
        return f"入点 {_seconds(e.get('entry_time'))} 压在原声上，但没有已验证的句尾锚点"
    if code == "time_overlap":
        return (
            f"{_seconds(e.get('start'))}-{_seconds(e.get('end'))} 与"
            f"{block_label(e.get('previous_index'))}（结束于 {_seconds(e.get('previous_end'))}）重叠"
        )
    if code == "out_of_order":
        return f"start {_seconds(e.get('start'))} 早于上一块的 {_seconds(e.get('previous_start'))}"
    if code in _WARNING_DETAILS:
        return _WARNING_DETAILS[code](e)
    return str(e.get("message", "")).strip()


def _issue_line(e):
    line = f"- {_label(e)} {e.get('code')}：{_details(e)}"
    hint = _hint(e)
    if hint:
        line += f"。改法：{hint}"
    return line


def _warning_lines(report):
    """One line per warning, every warning listed: they never block, so none is cut."""
    return [_issue_line(w) for w in report.get("warnings", [])]


def format_lint_warnings(report, lint_path=None):
    """The console summary of a lint that passed with warnings."""
    lines = [f"narration lint：通过，{len(report.get('warnings', []))} 个 warning（不阻塞）"]
    lines.extend(_warning_lines(report))
    if lint_path is not None:
        lines.append(f"完整报告：{lint_path}")
    return "\n".join(lines)


def format_lint_failure(report, lint_path=None):
    errors = report.get("errors", [])
    lines = [f"narration.json 预检失败：{len(errors)} 个 error，修改后重跑 validate。"]
    lines.extend(_issue_line(e) for e in errors[:MAX_LISTED_ERRORS])
    if len(errors) > MAX_LISTED_ERRORS:
        lines.append(f"- …另有 {len(errors) - MAX_LISTED_ERRORS} 个 error 未列出")
    warnings = _warning_lines(report)
    if warnings:
        lines.append(f"另有 {len(warnings)} 个 warning（不阻塞）：")
        lines.extend(warnings)
    if lint_path is not None:
        lines.append(f"完整报告（含全部字段）：{lint_path}")
    return "\n".join(lines)
