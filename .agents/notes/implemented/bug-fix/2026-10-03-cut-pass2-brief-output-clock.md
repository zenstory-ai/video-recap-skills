# Agent Note: cut 第二轮 brief 只用 OUTPUT 时钟

Status: implemented

## Problem

cut 第二轮（`edited_source.mp4` 已渲染）的 brief 要求按输出时间写 `narration.json`，但有三处仍在用原片时钟或给出错误数字：

- 结尾的 `## Scene timing guide` 照搬第一轮：列出全部原片场景（包括被剪掉的场景和片尾演职员表），起止、安静窗口、帧动作、ASR 都是原片秒，"max budget if fully narrated" 也按原片场景长度算。写稿 Agent 在同一份 brief 里读到两套时钟。
- 时长标签按整分钟取整（`{:.0f}min`），90 秒的目标和 101.5 秒的剪辑都显示 `~2min`，按它估的块数和篇幅偏大。
- 句末锚点映射到输出时钟时，原片时间允许超出片段边界 0.05 秒。片段终点被帧对齐（181.42 → 181.40）后，落在原终点上的锚点被映射到成片结尾之后；起点一侧同理会出现负时间。`speech_boundary_anchors_output.json` 和 brief 都列出了这种旁白无法开始的锚点。多视频 recap 自己写的同名文件有同样的映射。

## Decision

- `briefing/builder.py`：第二轮的场景表改由已经映射到输出时钟的场景、ASR 与安静窗口生成（与 `timeline_fusion.json` 用同一份映射），标题为 `## Scene timing guide (OUTPUT time)`，每节写作 `### OUTPUT a-b s (source scene N)`，被拆到多个片段的场景写作 `source scene N part M`；未保留的场景不出现。第一轮（写 `clip_plan.json`）和 full 模式仍用原片时间的 `## Scene timing guide`，多视频 recap 摘录各来源 brief 时按这个标题取段，不受影响。
- `_duration_label`：不足一分钟写 `Ns`，整分钟写 `Nmin`，其余写 `NmSSs`（四舍五入到整秒，`101.5` 秒写 `1m42s`）。brief 中 cut 输出与原片时长都用它。
- `briefing/timeline._sentence_entry_anchors_for_brief` 和 video-recap 的 `recap_timeline._write_multi_source_output_speech_evidence` 先算出输出时间，落在 `[0, 输出总长]` 之外就丢掉；输出总长取 `clip_plan_validated.json` 各片段 `output_end` 的最大值。两处是不同技能各自的实现，没有共用代码，同一次提交里一起改。
- 测试：`test_consolidation_brief.py` 覆盖时长标签各档、90 秒目标的 brief、第二轮场景表只列保留片段（拆分场景、帧动作、ASR、安静窗口都按输出时间，片尾场景不出现）、越界锚点被丢掉；`test_io_fixes.py` 覆盖多视频写出的越界锚点被丢掉。

## Alternatives considered

- **第二轮直接删掉场景表，只留 Timeline fusion 与 Kept clips。** 最强理由：这两节已经是输出时钟，brief 更短。没采用：Timeline fusion 最多列 40 个场景且不带帧动作与深度分析，Kept clips 只有区间和剪辑理由；写稿 Agent 需要的逐场景画面细节只在场景表里。
- **越界锚点夹到边界（0 或输出总长）而不是丢掉。** 最强理由：不丢信息。没采用：夹到成片结尾的锚点后面没有可以放旁白的时间，夹到 0 的锚点也不是真实的句末停顿；夹过去只会制造一个看似可用的假入口。
- **把 0.05 秒容差改成严格落在片段内。** 最强理由：从源头杜绝越界。没采用：容差是为了让恰好落在片段边界上的句末（帧对齐前后差几十毫秒）仍能映射到相邻位置，内部接缝处这些锚点仍然有用；只有落到成片两端之外的才无法使用。

## Consequences

- 收益：第二轮 brief 只有一套时钟，场景表里的字数上限按真实保留的长度计算；时长标签与实际相差不超过半秒；输出时钟的锚点都在成片内，cut_output lint 与混音不会拿到成片外的入口。
- 代价：第二轮场景表的条目数随保留片段变化，拆分场景会出现多条；时长标签变长（`1m42s`）。内部接缝处、超出片段 0.05 秒以内的锚点仍会映射到下一片段开头的几十毫秒里，这部分行为不变。
