"""Render a failed narration lint report as the compact error the author reads.

narration_lint.json keeps every field; this summary repeats, per failing block, the
numbers needed to fix it without opening that file. Block numbers are 1-based ("段 N",
the block's position in narration.json); the report's `index` fields stay 0-based.
"""

MAX_LISTED_ERRORS = 12

_HINTS = {
    "over_budget": "缩短文字，或放宽/挪动时间窗",
    "interrupts_source_sentence": "把这块的 start 挪到建议入点；没有建议入点时缩短、挪走或删掉这块",
    "source_sentence_anchors_unavailable": "挪走或删掉这块，或重新生成含句尾锚点的语音证据",
    "time_overlap": "调整时间窗，让两块不重叠",
    "out_of_order": "按 start 从小到大排列各块",
    "outside_clip_plan": "把这块挪进某个选中片段的时间范围",
    "ambiguous_source_clip": "给这块写 source_clip_id",
}


def block_label(index):
    """'段 N' for a 0-based block index; '整体' for a file-level issue."""
    return "整体" if index is None else f"段 {index + 1}"


def _seconds(value):
    return f"{value:.2f}s" if isinstance(value, (int, float)) else "?"


def _over_budget(e):
    return (
        f"{_seconds(e.get('start'))}-{_seconds(e.get('end'))} 写了 {e.get('actual_chars')} 字，"
        f"窗口约 {e.get('budget_chars')} 字（硬上限 {e.get('limit_chars')}，超出 {e.get('over_chars')} 字）"
    )


def _interrupts(e):
    text = f"入点 {_seconds(e.get('entry_time'))} 落在原声句子中间"
    if e.get("suggested_start") is None:
        return text + "，后面没有已验证的句尾锚点"
    tail = e.get("source_text_tail")
    tail = f"，句尾「{tail}」" if tail else ""
    return (
        f"{text}，建议入点 {_seconds(e['suggested_start'])}"
        f"（锚点 {e.get('anchor_confidence')}/{e.get('anchor_boundary_use')}{tail}）"
    )


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
    return str(e.get("message", "")).strip()


def format_lint_failure(report, lint_path=None):
    errors = report.get("errors", [])
    lines = [f"narration.json 预检失败：{len(errors)} 个 error，修改后重跑 validate。"]
    for e in errors[:MAX_LISTED_ERRORS]:
        line = f"- {block_label(e.get('index'))} {e.get('code')}：{_details(e)}"
        hint = _HINTS.get(e.get("code"))
        if hint:
            line += f"。改法：{hint}"
        lines.append(line)
    if len(errors) > MAX_LISTED_ERRORS:
        lines.append(f"- …另有 {len(errors) - MAX_LISTED_ERRORS} 个 error 未列出")
    if lint_path is not None:
        lines.append(f"完整报告（含 warning 与全部字段）：{lint_path}")
    return "\n".join(lines)
