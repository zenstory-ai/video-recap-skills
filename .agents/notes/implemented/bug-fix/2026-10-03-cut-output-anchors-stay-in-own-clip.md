# Agent Note: cut 输出时间轴的句末锚点不越出所属片段

Status: implemented

## Problem

cut 第二遍把原片句末锚点映射到 `edited_source.mp4` 的输出时间，写进 `speech_boundary_anchors_output.json`，供 video-script 的 cut_output lint（旁白入点必须在句末停顿内）和 assemble 的原声闪避使用。映射时片段的原片区间放宽 ±0.05 秒（帧对齐会把出点移到停顿结束前几毫秒），但输出时间按 `output_start + (time − source_start)` 直接算：

- 停顿结束比片段出点晚 20 ms，输出时间就落在下一段里 20 ms 处；多视频时下一段是另一个来源，锚点却仍标着本段的 `source_id`。lint 会把这里当成句末，放行压在下一段原声上的旁白。
- 比入点早几毫秒的锚点同理落进上一段，第一段时输出时间甚至为负。
- 同源的另一段本身播放该时刻时，同一个锚点会被两段各映射一次。

多视频写入方是 `video-recap/scripts/recap_timeline.py::_write_multi_source_output_speech_evidence`，单视频是 `video-understanding/scripts/briefing/timeline.py::_sentence_entry_anchors_for_brief`，两处都有同样的问题（0.6.0 已存在，帧对齐让片段边缘更常落在停顿结束前后几毫秒）。

## Decision

- 未夹之前的输出时间（`output_start + time − source_start`）落在 `[0, 输出总长]` 之外的锚点先丢掉（见 `2026-10-03-cut-pass2-brief-output-clock.md`），下面的夹取只作用于成片内部的接缝。
- 锚点时间严格落在某段的原片区间内时，只归这些片段；没有任何片段播放该时刻、但在某段 ±0.05 秒容差内时，归这些片段，并钉在该段的入点或出点上。
- 输出时间一律夹在本段的 `[output_start, output_end]` 内（`output_end` 是帧对齐后按累计帧数算出的值）；`pause_start`、`expected_time` 用同一换算，`source_pause_start` 不超过本段出点。多视频的讲话 / 安静区间本来已按原片区间裁剪，输出时间也走同一换算。
- `source_time` 仍记实测的原片锚点时间，`source_pause_end` 不变；只有输出时钟上的字段被夹住。
- 测试：`tests/orchestrator/test_io_fixes.py` 用两个来源、帧对齐后的出点断言每个锚点的 `time` / `pause_start` 都在自己来源的片段内，且同源另一段播放的时刻不重复；`tests/understanding/test_consolidation_brief.py` 对单视频做同样的检查。

## Alternatives considered

- **容差外的锚点直接丢弃，不放宽 ±0.05 秒。** 最强理由：输出时间永远等于原片偏移，不用夹。没采用：帧对齐把出点放在停顿内、比停顿结束早一帧以内是常态，丢掉这些锚点后 lint 会认为片段结尾没有句末，正当的旁白入点被拦。
- **只修多视频一处。** 最强理由：任务只报了多视频，单视频在另一个技能里。没采用：单视频的连续片段同样会把锚点映射进下一段的原片内容，两处行为本应一致（多视频写入方注释里就写着沿用单视频的默认值）；技能之间不共享代码，所以两处各改一次。

## Consequences

- 收益：输出时间轴上的锚点不会再落在另一个来源或另一段原片里，lint 和闪避看到的句末都属于正在播放的那段。
- 代价：钉在边缘上的锚点与原片偏移不再一一对应（最多差 0.05 秒），读 `source_time` 与 `time` 推算偏移的代码会看到这个差；同一锚点在两段的容差内（两段都不播放它）时仍各映射一次，分别钉在两段的边缘上。
