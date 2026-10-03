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

没有理解产物也能跑，但旁白语速为空，泄漏扫描只剩 Agent 自己写的 `entities`，check 会给出警告；
看画面改用 `frames --span 0,<时长> --step 2` 的接触表。

下面的 `scripts/...` 均相对于本技能目录。

## 3. 流程

```bash
python3 scripts/reference.py measure <成片> --work-dir U        # 一次 ffmpeg：切点 + 响度，按文件身份缓存
python3 scripts/reference.py frames  <成片> --work-dir U --review          # 逐帧看被压下的疑似切点
python3 scripts/reference.py frames  <成片> --work-dir U --longest 5       # 看最长镜头里有没有漏切
python3 scripts/reference.py check   --work-dir U [--json]      # 校验并打印派生值
python3 scripts/reference.py export  --work-dir U --out <下次运行的 work_dir>/production_reference.json
```

1. **measure** 写 `U/reference_measurements.json`（只由脚本写）：镜头切点、镜长分布、每分钟切点数、10 秒切点曲线、
   整体响度 / LRA / 真峰值、逐秒短期响度。它要解码整片：5 分钟 720p 约 15–70 秒，4K 约 7 分钟；成片不变时复用，
   只改 `--soft-score/--hard-score` 不重新解码。切点规则：scdet 分数 ≥10 必算；≥4 且是前后 0.3 秒内其他帧（相邻帧除外）
   两倍以上的孤立峰才算。运动镜头、急推拉、闪光会被压下，并列进 `shots.review_windows`；scdet 分数会减去前一帧的帧差，
   从快速运动切进静止镜头的硬切可能只有 1 分，所以帧差本身的单侧峰（`mafd_peaks`）也进待复核窗口，但不会自动算切点。
2. **复核切点**：固定阈值在真实成片上两头都错（暗场硬切只有 5–7 分；快速运动的单个镜头每 0.2 秒一个 7–8 分的峰），
   所以每次都要看图。`frames --review` 逐帧拼出每个待复核窗口，`frames --longest 5` 在最长的 5 个镜头里均匀取 12 帧；
   页面在 `U/reference_frames/`，命令打印每格对应的秒数。看完写 `labels.cut_fixes`：`add` 漏掉的切点秒数，
   `remove` 误报的切点秒数（±0.1 秒内对上测得的切点）；看过无需改动就写 `{}`。重新 `measure` 后待复核窗口数变了，旧的 `cut_fixes` 不再算数，按新窗口重看一遍。之后所有镜头数值、段内切点密度和导出都用复核后的切点。
3. **标注**：Agent 写 `U/reference_breakdown.json` 的 `labels`——音轨归属 `audio_spans`、叙事段落 `sections`、
   字幕形态 `subtitles`、标注依据 `basis`。`audio_spans` 的边界放在**声音实际起止**处（听得到的人声起点与止点），
   不放在字幕或旁白块的开始处（按字幕出现帧定的旁白结束点实测晚 0.24–0.42 秒）。`U/speech_boundary_anchors.json` 存在时，
   用它的 `acoustic_pauses`（`start` 是人声停下处，`end` 是下一句开口处）对齐边界；`switch_on_cut_share` 只容差 ±0.25 秒，
   边界放错它就只反映标注习惯。原片自带的画外音、内心独白也是原片音轨，记 `original_dialogue`；要区分时写进 fact 和方法。
   只有 labels 时就可以跑 check，它会打印 `derived`（各音轨占比、段内切点密度、
   旁白语速、声音切换与画面切点的对齐比例、分数位置的结构、第一次原声出现位置）。
4. **事实与方法**：在同一文件写 `source_facts`（带时间或测量锚点、显式 `entities`）和 `methods`
   （`rule`、`applies_when`、`avoid_when`、`applies_to`、`evidence`、`targets`）。`targets` 只写 `{"from": "<测量路径>"}`，
   数值由 export 从当前测量填入，Agent 永远不手写数字。
5. **check → export**：零 error 才导出；导出后对产物再做一次泄漏扫描。

字段、枚举和好/坏方法示例见 `references/reference-schema.md`。

## 4. 分离规则

check 的 error（退出码 1）：

| 规则 | 要求 |
|---|---|
| R1 | 顶层、labels、fact、method、target、`cut_fixes` 都是封闭键集与封闭枚举；fact 不能带方法字段，method 不能带事实字段；id 为 `f1…` / `m1…` 且唯一；`subtitles` 值类型固定；`skipped_dimensions` 的值都是非空字符串 |
| R2 | `audio_spans`、`sections` 按时间排序、不重叠、间隙 ≤0.5s、覆盖整片；字幕证据时间在时长内；`cut_fixes.remove` 对得上测得的切点，`add` 不与测得的切点重复 |
| R3 | 每条 fact 二选一锚定：`t:[a,b]` 在时长内，或 `measure:[路径]` 解析到 `shots` / `loudness` / `derived` 下的非字符串值；必须显式写 `entities` |
| R4 | 每条 method 至少一条证据：已有 fact id 或同样只认这三个根的 `measure:<路径>` |
| R5 | target 只写 `from`：`shots` / `loudness` / `derived` 下的数值叶子，或白名单派生对象（见 schema）；不得带列表下标 |
| R6 | `rule` / `applies_when` / `avoid_when` 和 `skipped_dimensions` 的原因不得含：原片实体名（忽略空白）、与台词、背景资料或事实共有的连续 8 个汉字（标点隔开、跨相邻 ASR 窗口也算）或 5 个英文词、绝对时间码、"第 N 秒"或"N 分 M 秒"（含中文数字）、绝对路径 |
| R7 | 五个维度各至少一条 method，或在 `skipped_dimensions` 写明原因 |
| R8 | 导出物的每个键和字符串再扫一遍 R6，且不得出现 `source_facts`、`labels`、`entities`、`evidence`、`statement`、`from`、`path` 键 |

实体名来自 fact `entities`、`understanding_index.json` 的 `characters` / `entities` / `research_glossary` 名字与别名、`background_research.json` 的角色与
`character_details` 别名、≤5 字的 `cultural_notes` 条目，以及资料里《》「」引号中的短词。与事实共有 8 字时错误会注明来自
source_facts：通用剪辑措辞改写任一侧即可。

警告不阻断：method 缺 `applies_when`、rule 正文写了数字、ASR 不是 `AVAILABLE_COARSE`、有中文对白但理解产物与背景资料都没给出名字（此时只扫 fact `entities`）、`basis` 为空、
理解产物记录的成片身份与测量的成片不一致、有待复核窗口却没写 `cut_fixes`。

R6 只能拦住字面泄漏，拦不住改写过的剧情；写方法时要写"什么情况下怎么做"，不要复述"这部片里发生了什么"。
规则也不判断语义：方法的方向必须与 check 打印的派生值一致（例如先看各音轨的 `cuts_per_min` 再写"哪类段落切得密"）。

## 5. 交付与使用

`production_reference.json` 只含：时长与画布、`cut_detection`（切点规则的参数与 Agent 增删的切点数）、
`profile`（节奏数值，`provenance` 为 `measured` / `reviewed`（写了 `cut_fixes`，`{}` 也算）/ `labeled`）、
分数位置的 `structure`、字幕形态、`methods`、`skipped_dimensions`。

使用方式：把它复制进下一次制作的 `work_dir`。写稿阶段只在文件存在时阅读它，
并可在 `recap_story_plan.json` 的可选字段 `reference_methods` 记录每条方法的 adopt / adapt / skip。
它是参考，不是配额：与新素材的证据冲突时以素材为准。没有任何脚本读取它，也不会因为它的存在改变任何阶段的通过与否。

`U/` 里的 breakdown 与 `reference_frames/` 含原片事实和画面，只留在本地，不要复制或提交。

## 6. 能力边界

- 不调用 MiMo 或任何 LLM，不改动理解产物，不给成片打分，不做"新片与参考的差距"判定。
- 切点来自 ffmpeg `scdet` 分数：慢叠化测不到；相隔 0.3 秒内分数相近的两个真切点、刚好压在 soft 线上的切点（不同 ffmpeg 构建
  4.00 与 3.989 分）、运动后的暗场硬切（0.9 分）都不算切点，只进待复核窗口。在一部 5 分钟剧集解说上测得 82 个切点，
  11 个待复核窗口里 4 个含漏掉的 5 个真切点，其余是急推拉、翻页和运动镜头；补上后，三段逐帧真值（开场、暗场、中段共 27 个）全部对上、无误报。
- 旁白占比与语速是 `labeled` 精度：来自 Agent 的音轨标注与粗 ASR 窗口，不是逐词对齐。
- 泄漏扫描基于字面匹配，Agent 仍需自查方法是否在复述原片剧情。
