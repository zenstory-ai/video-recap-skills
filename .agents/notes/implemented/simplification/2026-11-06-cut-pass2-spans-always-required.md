# Agent Note: 第二轮 brief 的片段映射不再分必需和可选

Status: implemented

相关事实更新：[[2026-10-03-cut-pass2-brief-output-clock]]。

## Problem

`_load_cut_output_spans_for_brief` 的 `required=False` 只给 `builder.py` 算时长用。同一次运行在 `edited_source.mp4` 存在时，后面还会用 `required=True` 加载一次，两次之间只有纯格式化函数，也没有写文件，所以 `required=False` 换不来任何不同的结果，只是把同一个 `SystemExit` 推迟几十行。

`_remap_brief_evidence_to_output_timeline` 只有一个调用方且总是 `required=True`，它的 `if not spans` 走不到。`_format_output_clip_list` 只在第二轮运行，此时计划和 `clips` 都已证明存在，它却再检查一遍。`_remap_asr_to_output_for_brief` 和 `_remap_segments_to_output_for_brief` 是同一个循环，只差一个"部分窗口"标记。

## Decision

- `cut_pass2` 提到 `builder.py` 算时长之前。第二轮的时长取 `_load_cut_output_spans_for_brief(work_dir)` 的结果，其他 cut 情况用目标时长估算。
- `_load_cut_output_spans_for_brief` 不再有 `required` 参数，缺失、过期或无效的计划一律 `SystemExit`，消息不变。调用方都已确认 `edited_source.mp4` 存在，函数里不再检查。
- 两个 remap 合并成 `_remap_segments_to_output_for_brief(segments, spans, withhold_partial=False)`。brief 的 ASR 传 `withhold_partial=True`。`speech_spans` 和 `quiet_windows` 用默认值，保留原文。
- `_PARTIAL_WINDOW_TEXT` 的文字不变。场景、帧事实、旁白时间的 remap 不合并。

## Alternatives considered

- **保留两个 remap 函数。** 最强理由：名字直接说明用途，调用处不用记参数。没采用：两个函数九成相同，改舍入或 `duration` 时要改两遍；默认值 False 让 `speech_spans` 这条路径不用改。
- **把 `speech_spans` 也改成隐藏部分窗口。** 最强理由：一个规则走到底。没采用：下游要靠原文判断是否只有语气词，那是另一个决定。

## Consequences

- 收益：第二轮的"必需"不再需要参数来表达，少一个走不到的分支。
- 代价：`SystemExit` 提前几十行发生。计划无效同时 `--target-duration` 写错时，以前先报目标时长的 `ValueError`，现在报计划的 `SystemExit`。
