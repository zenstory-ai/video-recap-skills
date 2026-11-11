"""Render a narration lint report as the compact lines the author reads on the console.

narration_lint.json keeps every field; this summary repeats, per flagged block, the
numbers needed to fix it without opening that file. Errors (failed lint) and warnings
(passed lint) use the same line format. Block numbers are 1-based ("段 N",
the block's position in narration.json); the report's `index` fields stay 0-based. A
placeholder-leakage blocker found in original_subtitles.json carries `source: "original_subtitles"`
and an index into that file, so it is labelled "原声字幕第 N 条", never "段 N".
"""

MAX_LISTED_ERRORS = 12


def block_label(index):
    """'段 N' for a 0-based block index; '整体' for a file-level issue."""
    return "整体" if index is None else f"段 {index + 1}"


def _label(e):
    if e.get("source") == "original_subtitles":
        index = e.get("index")
        return "原声字幕" if index is None else f"原声字幕第 {index + 1} 条"
    return block_label(e.get("index"))


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


_WARNING_DETAILS = {
    "slot_too_short": lambda e: f"{_window(e)} 只容得下约 {e.get('budget_chars')} 字，TTS 可能放不下",
    "incomplete_sentence": lambda e: f"结尾「{e.get('text_tail', '')}」没有句末标点",
}


def _details(e):
    code = e.get("code")
    if code == "over_budget" and "budget_chars" in e:
        return _over_budget(e)
    if code == "interrupts_source_sentence":
        return _interrupts(e)
    if code == "source_sentence_anchors_unavailable":
        return f"入点 {_seconds(e.get('entry_time'))} 压在原声上，但没有已验证的句尾锚点"
    if code == "unsafe_source_release":
        return (
            f"出点 {_seconds(e.get('exit_time'))} 落在原声句子中间，后续 "
            f"{e.get('max_hold_seconds', 3):g}s 内无句尾锚点，恢复原声会露出半截台词"
        )
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
    return f"- {_label(e)} {e.get('code')}：{_details(e)}"


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
