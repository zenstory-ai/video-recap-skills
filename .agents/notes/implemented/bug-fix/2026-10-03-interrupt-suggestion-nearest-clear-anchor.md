# Agent Note: interrupts_source_sentence 只建议能整块挪过去的近处锚点

Status: implemented

## Problem

旁白入点落在原声句子中间时，`narration_lint._source_sentence_entry_issue` 报 `interrupts_source_sentence`，`suggested_start` 是入点之后的第一个锚点（`time > start + 0.08`）。它不看距离，也不看别的块：下一个锚点可能在 60 秒外（照着挪，旁白就离开了它写的画面），也可能正好是另一块的 `start`（照着挪就和那一块重叠，下一轮 lint 报 `time_overlap`）。摘要的改法写的是"把 start 挪到建议入点"，Agent 会照做。

## Decision

- `_nearest_safe_anchor(start, end, anchors, other_spans)` 选建议锚点：只看离入点不超过 `SUGGESTED_ANCHOR_MAX_SHIFT_SECONDS`（10 秒）的锚点，前后都看；把整块按原时长平移到锚点后，它与每个其他块之间都要留出大于 `CONNECTED_HANDOFF_SECONDS`（0.15 秒）的间隔，既不重叠也不相接（相接就成了 `_has_connected_predecessor` 认的首尾交接，入点检查被跳过，后一块的含义也变了）；在合格的锚点里取离入点最近的，距离相同取后面的。没有合格锚点时 `suggested_start` 为 `null`。
- 其他块的时间窗由 `_other_block_spans(narration, idx)` 收集（只取数值合法且 `end > start` 的对象块）。`_has_connected_predecessor` 改用同一个 `CONNECTED_HANDOFF_SECONDS` 常量，值不变。
- 报告新增 `suggested_end`（平移后的结束时间）与 `max_shift_seconds`；`source_text_tail`、`anchor_confidence`、`anchor_boundary_use` 照旧描述被建议的那个锚点。摘要写 `建议入点 X-Y`，没有建议时写"前后 10 秒内没有能整块挪过去、又不与其他块重叠或相接的句尾锚点"；改法改为"把整块（时长不变）挪到建议的时间窗"。
- 往前的锚点也算：入点 6.10 秒、上一句 5.81 秒说完时，建议 5.81 而不是 8 秒后的下一句；最后一个锚点之后进入讲话区的块，现在会得到那个靠前的锚点，不再是 `null`。
- 测试：`tests/script/test_pure_script.py` 的参数化用例改为期待更近的靠前锚点、60 秒外的锚点不算建议；新增用例覆盖下一锚点正是另一块开头时跳到下一个安全锚点、挪过去会与前一块相接（间隔 0.1 秒）或重叠时都跳过、范围内没有合格锚点时为 `null` 且摘要那一行的确切文字。

## Alternatives considered

- **只往后找，跳过会重叠的锚点。** 最强理由：与原来"往后等句子说完"的直觉一致，块只会往后挪，不会去讲还没发生的画面。没采用：刚错过句尾零点几秒的入点，往前挪 0.3 秒就安全，只往后找会把它推到下一句之后，离原画面更远；两个方向都受同一个 10 秒上限约束。
- **保留 end 不动，只挪 start。** 最强理由：块不会侵入后面的时间。没采用：建议锚点在 `end` 之后时没法只挪 start；字数是按原时长写的，缩短时间窗常常直接变成 `over_budget`。整块平移让"挪过去"在每种情况下都是一个合法的时间窗，安全性只需检查一次。
- **把上限做成可配置项。** 最强理由：不同节奏的片子能接受的位移不同。没采用：这只是给 Agent 的建议，不改变门禁；超过上限 Agent 照样可以自己挪，而项目刚删掉一批无人使用的调参项，不为一句提示再加一个。

## Consequences

- 收益：照着建议改不会再造出重叠或首尾相接；建议总在原画面附近，远处才有安全锚点时明确说没有，让 Agent 重新考虑这一块。
- 代价：以前有建议的块（下一锚点远在 10 秒外）现在得到 `null`，Agent 要自己决定移动、缩短还是删除；靠前锚点被选中时，块会提前开始，可能比原来更早讲到后面的画面。
- 代价：cut 模式（源时间）下不检查平移后是否还在原片段内，挪出片段会在下一轮由 `outside_clip_plan` 报出；cut_output 与 full 没有片段约束。
