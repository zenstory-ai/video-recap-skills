# Agent Note: 去掉 timeline 和 visual_qc 里恒定或可推导的键

Status: implemented

翻转 [移除 --audio-stream-index](2026-11-06-remove-audio-stream-index.md) 里“`timeline.json` 的 `selected_stream` 保留为常量 0”这一项；同一篇里其他落盘字段的决定不变。

## Problem

- source-mix 的 `timeline.json` 写 `clip.audio.selected_stream`、`clip.audio.mode` 和顶层 `audio_delivery`。音轨固定是第一条，`reconstructable`、`canonical_renderer` 是定值，没有读取方；剪映导出只读 `base_gain` 和 `volume_keyframes`。`references/audio-modes.md` 还写着导出器会拒绝其他音轨，而现在没有输入能请求其他音轨。当初保留 `selected_stream` 的理由是续跑比较和样例，但续跑比较的是 `audio_binding`，不是 `timeline.json`，guohuo 样例的时间线也是 cut 模式，不含这些键。
- `visual_qc.json` 的 `subtitles` 里，`max_lines` 和 `max_observed_lines` 是同一个值，`multi_line`、`multi_line_entries` 可由 `entry_facts[].line_count` 推出，`violations` 用一组同义词重复 `overflow_entries[].overflow_reasons`。阻断只看 `overflow`。

## Decision

- source-mix 的 `timeline.json` 只写 `clip.audio.base_gain`，不再有 `selected_stream`、`mode` 和 `audio_delivery`。解说模式的时间线不变。`audio-modes.md` 改掉过时说法。
- `visual_qc.json` 的 `subtitles` 去掉 `max_lines`、`max_observed_lines`、`multi_line`、`multi_line_entries`、`violations` 和 `_OVERFLOW_KINDS` 映射。`style.max_lines`（配置上限）、`entry_facts`、`overflow`、`overflow_entries` 保留。
- 恢复续跑比较的字段都不动：`recap_run_manifest.json` 的 `audio.selected_stream_index`、`assembly_manifest.json` 的 `selected_audio_stream_index`、`assembly_settings` 的 `audio.selected_stream_index`。

## Alternatives considered

- **保留这些键，保持输出不变。** 最强的理由是外部脚本可能在读，删键有破坏风险。不采用：全仓没有读取方，留在 `entry_facts` 和 `overflow_entries` 的信息足以诊断，常量键只是让读者以为有选择。

## Consequences

- **收益**：产物更短，`visual_qc` 每条事实只有一个来源；新增一个测试锁定导出按 `base_gain` 设音量。
- **代价**：读取这些键的外部脚本要改，记入 CHANGELOG 的 `Removed`。旧时间线仍能导出，校验器忽略多余的键。
