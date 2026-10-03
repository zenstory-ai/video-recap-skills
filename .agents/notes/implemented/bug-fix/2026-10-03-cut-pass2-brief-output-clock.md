# Agent Note: cut 第二轮 brief 只用 OUTPUT 时钟

Status: implemented

## Problem

cut 第二轮（`edited_source.mp4` 已渲染）的 brief 要求按输出时间写 `narration.json`，但有三处仍在用原片时钟或给出错误数字：

- 结尾的 `## Scene timing guide` 照搬第一轮：列出全部原片场景（包括被剪掉的场景和片尾演职员表），起止、安静窗口、帧动作、ASR 都是原片秒，"max budget if fully narrated" 也按原片场景长度算。写稿 Agent 在同一份 brief 里读到两套时钟。
- 时长标签按整分钟取整（`{:.0f}min`），90 秒的目标和 101.5 秒的剪辑都显示 `~2min`，按它估的块数和篇幅偏大。
- 场景表换成输出时钟后，混在里面的原片时间还剩两处：VLM 写的描述 / 深层分析会直接引用原片时间（帧标签的 `12.0s` 写法、`00:30`）；粗粒度 ASR 窗口（约 15 秒）只有一部分被剪进成片时，映射后的片段只截了时间、文字仍是整窗，成片里听不到的台词也被当作这里的对白列出（如输出 `100.1-101.5` 带着整窗 15 秒的文字）。
- 同一份 brief 用两套场景号：Scene timing guide 写从 1 数的 `source scene N`，`### Fusion scene N` 与 `ASR chunk … | scenes …` 却印 0 起的 `scene_id`，同一个场景在相邻两节里差 1。
- 句末锚点映射到输出时钟时，原片时间允许超出片段边界 0.05 秒。片段终点被帧对齐（181.42 → 181.40）后，落在原终点上的锚点被映射到成片结尾之后；起点一侧同理会出现负时间。`speech_boundary_anchors_output.json` 和 brief 都列出了这种旁白无法开始的锚点。多视频 recap 自己写的同名文件有同样的映射。

## Decision

- `briefing/builder.py`：第二轮的场景表改由已经映射到输出时钟的场景、ASR 与安静窗口生成（与 `timeline_fusion.json` 用同一份映射），标题为 `## Scene timing guide (OUTPUT time)`，每节写作 `### OUTPUT a-b s (source scene N)`，被拆到多个片段的场景写作 `source scene N part M`；未保留的场景不出现。
- 第二轮映射场景时（`briefing/timeline._remap_scenes_to_output_for_brief`），`description` 与 `depth_analysis` 里引用的时间（`12.0s`、`12秒`、`01:05`、`1:02:03`）只要落在该场景的原片区间内（±0.25 秒）就当作原片时间：在本片段保留区间内的改写成输出时间（`2.0s`），落在被剪掉部分的换成 `[cut-away moment]`；场景区间之外的数字（"停顿了3秒"这类时长、画面里的钟点 `12:30`）原样保留。
- 第二轮给 brief 用的 ASR（`_remap_asr_to_output_for_brief`，ASR chunks、Timeline fusion、场景表共用）在一个窗口只被保留了一部分时，不显示窗口文字，改为 `[partial ASR window: only part of it is in the cut, text withheld]`（标记里不带小数点，ASR 分块按句号切句）；时间与对白重叠秒数照旧计算。保留片段在接缝两侧合起来覆盖整个窗口时（差不超过 0.05 秒）照旧显示文字。`speech_boundary_anchors_output.json` 的 `speech_spans` 不走这条路，仍带原文（下游按文字判断是否只有语气词）。
- brief 里所有给人看的场景号都从 1 数，由 `briefing/context._scene_number` 统一生成：`### Scene N`、`### OUTPUT … (source scene N)`、`### Fusion scene N`、`ASR chunk … | scenes N, M`、`Moderation-refused scenes (Scene N)`；拆分场景写 `N part M`。`timeline_fusion.json` / `asr_writing_chunks.json` 里的 `scene_id` / `scene_ids` 仍是 0 起的原值。第一轮（写 `clip_plan.json`）和 full 模式仍用原片时间的 `## Scene timing guide`，多视频 recap 摘录各来源 brief 时按这个标题取段，不受影响。
- `_duration_label`：不足一分钟写 `Ns`，不足一小时时整分钟写 `Nmin`、其余写 `NmSSs`；满一小时写 `Nh` / `NhMMm` / `NhMMmSSs`（低位全为 0 时省略，`7265` 秒写 `2h01m05s`）；都四舍五入到整秒，`101.5` 秒写 `1m42s`。brief 中 cut 输出与原片时长都用它。
- `briefing/timeline._sentence_entry_anchors_for_brief` 和 video-recap 的 `recap_timeline._write_multi_source_output_speech_evidence` 先算出输出时间，落在 `[0, 输出总长]` 之外就丢掉；输出总长取 `clip_plan_validated.json` 各片段 `output_end` 的最大值。两处是不同技能各自的实现，没有共用代码，同一次提交里一起改。
- 测试：`test_brief_numbers_and_clocks.py` 覆盖各节场景号一致从 1 数且 JSON 不变、第二轮 VLM 文字里的原片时间被改写或标记、只保留一部分的 ASR 窗口不显示文字、接缝两侧覆盖整窗时保留文字、场景区间外的数字不动；`test_consolidation_brief.py` 覆盖时长标签各档（含小时）、90 秒目标的 brief、第二轮场景表只列保留片段（拆分场景、帧动作、ASR、安静窗口都按输出时间，片尾场景不出现）、越界锚点被丢掉；`test_io_fixes.py` 覆盖多视频写出的越界锚点被丢掉。

## Alternatives considered

- **第二轮直接删掉场景表，只留 Timeline fusion 与 Kept clips。** 最强理由：这两节已经是输出时钟，brief 更短。没采用：Timeline fusion 最多列 40 个场景且不带帧动作与深度分析，Kept clips 只有区间和剪辑理由；写稿 Agent 需要的逐场景画面细节只在场景表里。
- **越界锚点夹到边界（0 或输出总长）而不是丢掉。** 最强理由：不丢信息。没采用：夹到成片结尾的锚点后面没有可以放旁白的时间，夹到 0 的锚点也不是真实的句末停顿；夹过去只会制造一个看似可用的假入口。
- **把 0.05 秒容差改成严格落在片段内。** 最强理由：从源头杜绝越界。没采用：容差是为了让恰好落在片段边界上的句末（帧对齐前后差几十毫秒）仍能映射到相邻位置，内部接缝处这些锚点仍然有用；只有落到成片两端之外的才无法使用。
- **部分保留的 ASR 窗口按保留比例截取文字。** 最强理由：大半窗口被保留时还能看到大部分台词。没采用：粗粒度窗口没有逐字时间，按比例截出来的字仍可能是被剪掉的那几句，正是这次要消除的泄漏；看不准的文字宁可不给。
- **部分保留的窗口把文字清空。** 最强理由：最简单，不新增标记。没采用：空文字在 brief 里表示"未证实的空档"，写稿 Agent 会以为那里没有原声对白；标记保留"这里有人说话、文字不可定位"这一事实。
- **第二轮把 VLM 文字里所有带单位的数字都删掉。** 最强理由：不用判断是不是时间，一定不漏。没采用：会删掉"停顿了3秒"这类时长和画面里的钟点，损坏描述；按场景原片区间判断只改真正指向原片时刻的数字。

## Consequences

- 收益：第二轮 brief 只有一套时钟，场景表里的字数上限按真实保留的长度计算；时长标签与实际相差不超过半秒；输出时钟的锚点都在成片内，cut_output lint 与混音不会拿到成片外的入口。
- 代价：第二轮场景表的条目数随保留片段变化，拆分场景会出现多条；时长标签变长（`1m42s`）。部分保留的 ASR 窗口在 brief 里看不到文字，写稿 Agent 要自己听 `edited_source.mp4` 才知道那几秒说了什么。VLM 文字里的时间靠数值是否落在场景区间判断：场景内一个写成 `3秒` 的时长若恰好落在区间里会被误改；VLM 不按 `Ns`、`N秒`、`mm:ss` 写的时间（如"第十二秒"）不会被识别。内部接缝处、超出片段 0.05 秒以内的锚点改为钉在所属片段的入点或出点上，见 `2026-10-03-cut-output-anchors-stay-in-own-clip.md`；本篇的越界丢弃在那之前判断，用未夹的输出时间。
