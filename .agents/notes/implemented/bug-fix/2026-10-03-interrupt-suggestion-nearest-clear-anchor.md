# Agent Note: interrupts_source_sentence 只建议能整块挪过去的近处锚点

Status: implemented

## Problem

旁白入点落在原声句子中间时，`narration_lint._source_sentence_entry_issue` 报 `interrupts_source_sentence`，`suggested_start` 是入点之后的第一个锚点（`time > start + 0.08`）。它不看距离，也不看别的块：下一个锚点可能在 60 秒外（照着挪，旁白就离开了它写的画面），也可能正好是另一块的 `start`（照着挪就和那一块重叠，下一轮 lint 报 `time_overlap`）。摘要的改法写的是"把 start 挪到建议入点"，Agent 会照做。

## Decision

- 选建议锚点的逻辑在 `skills/video-script/scripts/entry_suggestion.py`（`narration_lint.py` 拆出来以守 800 行上限；`_has_connected_predecessor`、`_other_block_spans`、`_is_number` 也一起搬过去）。`_nearest_safe_anchor(start, end, anchors, other_spans, window)` 只看离入点不超过 `SUGGESTED_ANCHOR_MAX_SHIFT_SECONDS`（10 秒）的锚点，前后都看；把整块按原时长平移到锚点后要同时满足：
  - 落在 `window` 这个开区间里：`_suggestion_window` 取列表里前一个、后一个数值合法的块（narration 已按时间排序），下界是前一块的 `end` 加 `CONNECTED_HANDOFF_SECONDS`（0.15 秒），上界是后一块的 `start` 减 0.15 秒。块不会越过相邻块：越过去照改就是 `out_of_order`，作者若改成调换顺序，两个故事节拍就反了。
  - 前一块自己也收到了建议时，下界取它原来和建议的 `end` 中较晚的那个（`lint_narration` 按顺序处理，把已给出的 `suggested_start/end` 记在 `suggested_spans`）。这样相邻两块的建议无论照改哪几条都不会重叠或相接，也不会抢到同一个时间窗。
  - 与每个其他块之间都留出大于 0.15 秒的间隔（`_shift_is_clear` 对照所有块原来的位置），既不重叠也不相接（相接就成了 `_has_connected_predecessor` 认的首尾交接，入点检查被跳过，后一块的含义也变了）。
  - 不超出 `_suggestion_limits` 给的闭区间：cut 模式下是该块唯一匹配的片段（源时间，`source_clip_id` 优先），cut_output 下是 `[0, output_duration]`（`validate.py` 把 `--output-duration` 经 `validate_narration_or_raise(output_duration=...)` 传进来）；full 模式不知道视频时长，只限制不早于 0。
- 在合格的锚点里取离入点最近的，距离相同取后面的。没有合格锚点时 `suggested_start` 为 `null`。
- 报告新增 `suggested_end`（平移后的结束时间）与 `max_shift_seconds`；`source_text_tail`、`anchor_confidence`、`anchor_boundary_use` 照旧描述被建议的那个锚点。摘要写 `建议入点 X-Y`，没有建议时写"前后 10 秒内没有能整块挪过去、留在前后两块之间又不与其他块重叠或相接的句尾锚点"；改法改为"把整块（时长不变）挪到建议的时间窗"。
- 往前的锚点也算：入点 6.10 秒、上一句 5.81 秒说完时，建议 5.81 而不是 8 秒后的下一句；最后一个锚点之后进入讲话区的块，现在会得到那个靠前的锚点，不再是 `null`。
- 测试（`tests/script/test_pure_script.py`）：参数化用例期待更近的靠前锚点、60 秒外的锚点不算建议；同样近的两个锚点里后面那个正是另一块开头时选前面那个；唯一的锚点在下一块之后时为 `null`，有更远但在空档内的锚点时选它（同一例里第 2 块得到 16-18）；两个相邻的抢话块，第 2 块避开第 1 块的建议位置（13.1 只离 13.0 差 0.1 秒）；下一个锚点会让块与后一块相接时选更早的；cut 模式不挪出片段、cut_output 经 `validate.py` 不超出 `--output-duration`；范围内没有合格锚点时为 `null` 且摘要那一行的确切文字。

## Alternatives considered

- **只往后找，跳过会重叠的锚点。** 最强理由：与原来"往后等句子说完"的直觉一致，块只会往后挪，不会去讲还没发生的画面。没采用：刚错过句尾零点几秒的入点，往前挪 0.3 秒就安全，只往后找会把它推到下一句之后，离原画面更远；两个方向都受同一个 10 秒上限约束。
- **保留 end 不动，只挪 start。** 最强理由：块不会侵入后面的时间。没采用：建议锚点在 `end` 之后时没法只挪 start；字数是按原时长写的，缩短时间窗常常直接变成 `over_budget`。整块平移让"挪过去"在每种情况下都是一个合法的时间窗，安全性只需检查一次。
- **只用相邻块原来的位置当边界。** 最强理由：每块的建议互相独立，不依赖处理顺序，规则一句话说得清。没采用：两个相邻的抢话块共享同一段空档，前一块往后挪、后一块往前挪，各自都离对方原来的位置够远，两条一起照改照样相接或重叠；把前一块的建议位置也算进去只多一个字典，换来"照改任意几条都安全"。
- **把上限做成可配置项。** 最强理由：不同节奏的片子能接受的位移不同。没采用：这只是给 Agent 的建议，不改变门禁；超过上限 Agent 照样可以自己挪，而项目刚删掉一批无人使用的调参项，不为一句提示再加一个。

## Consequences

- 收益：照着建议改（相邻几块的建议照改任意几条）不会造出重叠、首尾相接或调换顺序；cut 与 cut_output 下也不会挪出片段或输出时长；建议总在原画面附近，远处才有安全锚点时明确说没有，让 Agent 重新考虑这一块。
- 代价：以前有建议的块（下一锚点远在 10 秒外，或只能越过相邻块）现在得到 `null`，Agent 要自己决定移动、缩短还是删除；靠前锚点被选中时，块会提前开始，可能比原来更早讲到后面的画面。
- 代价：后一块的建议依赖前一块有没有收到建议，前一块改动后后一块的建议可能跟着变；前一块原来与建议位置之间的那段空档后一块用不上，偶尔会因此得到 `null`。
- 代价：full 模式下 lint 不知道视频时长，最后一块往后挪可能超出视频结尾，报告的 message 提醒 Agent 自己守住。
