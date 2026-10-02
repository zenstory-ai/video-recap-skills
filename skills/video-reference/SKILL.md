---
name: video-reference
user-invocable: true
description: >
 按需把一部成片拆成可复用的制作参考：测镜头节奏与响度，标注段落与音轨分工，把原片事实与可迁移方法分开，
 导出不含原片人名台词的 production_reference.json 供下次制作参考。不在默认生产路径上。
 触发词：拆片、拆解成片、制作参考、参考模板、production reference。
---

## 1. 定位

本技能把一部**已完成的成片**拆成下一次制作能直接参考的方法与数值。它是按需的参考分析，不是生产阶段，也不是质检：
不调用 MiMo，不改其他产物，不给任何运行打分或拦截。

Agent 的角色是**拆片编辑**：先如实记录"这部片子怎么做的"（事实），再提炼"换一部素材还能怎么用"（方法）。
两者必须分开：事实只留在本地工作目录，导出物只含方法和测得的数值。

## 2. 输入

- 成片文件（`.mp4 / .mov / .mkv / .webm`）。
- 对**这部成片**跑出的视频理解产物目录 `U`（建议 `ASR_SEGMENT_SECONDS=5`，窗口越短，旁白语速越准）。
  本技能读取其中可选的 `asr_result.json`、`asr_timing_evidence.json`、`background_research.json`、`understanding_index.json`
  （其 `characters` 的名字、别名与 ASR 提及都进入人名泄漏扫描）；
  标注时 Agent 还应看故事板 / contact sheet 与 `vlm_analysis.json`。

没有理解产物也能跑，但旁白语速为空，泄漏扫描只剩 Agent 自己写的 `entities`，check 会给出警告。

下面的 `scripts/...` 均相对于本技能目录。

## 3. 流程

```bash
python3 scripts/reference.py measure <成片> --work-dir U        # 一次 ffmpeg：切点 + 响度，按文件身份缓存
python3 scripts/reference.py check   --work-dir U [--json]      # 校验并打印派生值
python3 scripts/reference.py export  --work-dir U --out <下次运行的 work_dir>/production_reference.json
```

1. **measure** 写 `U/reference_measurements.json`（只由脚本写）：镜头切点、镜长分布、每分钟切点数、10 秒切点曲线、
   整体响度 / LRA / 真峰值、逐秒短期响度。成片与设置不变时直接复用。它要解码整片：5 分钟 720p 约 70 秒，4K 约 7 分钟。
2. **标注**：Agent 写 `U/reference_breakdown.json` 的 `labels`——音轨归属 `audio_spans`、叙事段落 `sections`、
   字幕形态 `subtitles`、标注依据 `basis`。只有 labels 时就可以跑 check，它会打印 `derived`（各音轨占比、段内切点密度、
   旁白语速、声音切换与画面切点的对齐比例、分数位置的结构、第一次原声出现位置）。
3. **事实与方法**：在同一文件写 `source_facts`（带时间或测量锚点、显式 `entities`）和 `methods`
   （`rule`、`applies_when`、`avoid_when`、`applies_to`、`evidence`、`targets`）。`targets` 只写 `{"from": "<测量路径>"}`，
   数值由 export 从当前测量填入，Agent 永远不手写数字。
4. **check → export**：零 error 才导出；导出后对产物再做一次泄漏扫描。

字段、枚举和好/坏方法示例见 `references/reference-schema.md`。

## 4. 分离规则

check 的 error（退出码 1）：

| 规则 | 要求 |
|---|---|
| R1 | 顶层、labels、fact、method、target 都是封闭键集与封闭枚举；fact 不能带方法字段，method 不能带事实字段；id 为 `f1…` / `m1…` 且唯一；`subtitles` 值类型固定；`skipped_dimensions` 的值都是非空字符串 |
| R2 | `audio_spans`、`sections` 按时间排序、不重叠、间隙 ≤0.5s、覆盖整片；字幕证据时间在时长内 |
| R3 | 每条 fact 二选一锚定：`t:[a,b]` 在时长内，或 `measure:[路径]` 解析到 `shots` / `loudness` / `derived` 下的非字符串值；必须显式写 `entities` |
| R4 | 每条 method 至少一条证据：已有 fact id 或同样只认这三个根的 `measure:<路径>` |
| R5 | target 只写 `from`：`shots` / `loudness` / `derived` 下的数值叶子，或白名单派生对象（见 schema）；不得带列表下标 |
| R6 | `rule` / `applies_when` / `avoid_when` 不得含：原片实体名（忽略空白）、与台词或事实共有的连续 8 个汉字（标点隔开也算）或 5 个英文词、绝对时间码、"第 N 秒"或"N 分 M 秒"、绝对路径 |
| R7 | 五个维度各至少一条 method，或在 `skipped_dimensions` 写明原因 |
| R8 | 导出物的每个键和字符串再扫一遍 R6，且不得出现 `source_facts`、`labels`、`entities`、`evidence`、`statement`、`from`、`path` 键 |

警告不阻断：method 缺 `applies_when`、rule 正文写了数字、ASR 不是 `AVAILABLE_COARSE`、有中文对白却没有任何人名来源、`basis` 为空、
理解产物记录的成片身份与测量的成片不一致。

R6 只能拦住字面泄漏，拦不住改写过的剧情；写方法时要写"什么情况下怎么做"，不要复述"这部片里发生了什么"。
规则也不判断语义：方法的方向必须与 check 打印的派生值一致（例如先看各音轨的 `cuts_per_min` 再写"哪类段落切得密"）。

## 5. 交付与使用

`production_reference.json` 只含：时长与画布、`profile`（测得或标注得到的节奏数值，带 `provenance`）、
分数位置的 `structure`、字幕形态、`methods`、`skipped_dimensions`。

使用方式：把它复制进下一次制作的 `work_dir`。写稿阶段只在文件存在时阅读它，
并可在 `recap_story_plan.json` 的可选字段 `reference_methods` 记录每条方法的 adopt / adapt / skip。
它是参考，不是配额：与新素材的证据冲突时以素材为准。没有任何脚本读取它，也不会因为它的存在改变任何阶段的通过与否。

`U/` 里的 breakdown 含原片事实，只留在本地，不要复制或提交。

## 6. 能力边界

- 不调用 MiMo 或任何 LLM，不改动理解产物，不给成片打分，不做"新片与参考的差距"判定。
- 镜头只认 ffmpeg `scdet` 硬切：叠化会漏，闪光会多报；阈值用 `--scene-threshold` 调整。
- 旁白占比与语速是 `labeled` 精度：来自 Agent 的音轨标注与粗 ASR 窗口，不是逐词对齐。
- 泄漏扫描基于字面匹配，Agent 仍需自查方法是否在复述原片剧情。
