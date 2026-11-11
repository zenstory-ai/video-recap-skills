---
name: video-reference
user-invocable: true
description: 按需把一部成片拆成可复用的制作参考：测镜头节奏与响度，标注段落与音轨分工，把原片事实与可迁移方法分开， 导出不含原片人名台词的 production_reference.json 供下次制作参考。不在默认生产路径上。 触发词：拆片、拆解成片、制作参考、参考模板、production reference。
---

## 1. 定位

本技能用于按需拆解一部**已经做好的优秀解说成片**，把它的剪辑节奏、声音分工与叙事手法提炼成下一次制作可直接参考的方法卡片与量化指标。全程在本地运行，只输出测量数据、拆片标注与脱敏参考文件，不调用 MiMo，也不对日常生产流水线做打分或阻断。

Agent 在此扮演**拆片编辑**：先把“这条成片具体是怎么剪、怎么配的”（原片事实）如实记下来，再从中提炼出“换一部新素材还能怎么借鉴”（可迁移方法）。**事实与方法必须彻底分家**：包含原片人名、剧情和具体台词的事实只留在本地拆片目录里，最终导出的 `production_reference.json` 只保留通用方法与实测节奏数值。

## 2. 输入

- 待拆解的成片视频文件（支持 `.mp4 / .mov / .mkv / .webm`）。
- 针对**这部成片**跑出的视频理解产物目录 `U`（建议在理解阶段设 `ASR_SEGMENT_SECONDS=5`，分窗越细，测算出的旁白语速越准）。本技能会从中读取可选的 `asr_result.json`、`asr_timing_evidence.json` 与 `background_research.json`（其中登记的角色名、别名及 ASR 台词都会纳入防剧透泄漏扫描）；Agent 做人工标注时还应结合故事板拼图（storyboard）与 `vlm_analysis.json` 一起看。

即使没有跑过视频理解，本技能也能单独运行，只是导出的旁白语速字段为空、防泄漏扫描词表仅依赖 Agent 手写的 `entities`，且 `check` 会给出相应警告；此时可用 `frames --span 0,<时长> --step 2` 抽帧生成拼图来辅助看片。

以下 `scripts/...` 路径均相对于本技能目录。

## 3. 拆片流程

```bash
python3 scripts/reference.py measure <成片> --work-dir U        # 跑一次 ffmpeg 实测切点与响度，按文件身份缓存
python3 scripts/reference.py frames  <成片> --work-dir U --review          # 对被算法压下的疑似切点逐帧出图复核
python3 scripts/reference.py frames  <成片> --work-dir U --longest 5       # 抽出最长的 5 个镜头，排查里面有无漏检切点
python3 scripts/reference.py check   --work-dir U [--json]      # 校验拆片稿并打印派生指标
python3 scripts/reference.py export  --work-dir U --out <下次运行的 work_dir>/production_reference.json
```

1. **实测镜头与响度（`measure`）**：由脚本生成 `U/reference_measurements.json`（人工切勿手改），记录全片镜头切点、镜头时长分布、每分钟切点数、10 秒滑动窗口切点密度曲线、整体响度（Integrated LUFS）、响度动态范围（LRA）、真峰值以及逐秒短期响度。该步骤需完整解码整部成片（5 分钟 720p 视频约耗时 15–70 秒，4K 视频约 7 分钟）；只要成片文件未变就会直接复用缓存，单独调整 `--soft-score` 或 `--hard-score` 阈值无需重新解码。
   - **切点初筛逻辑**：`scdet` 场景切换得分 `≥10` 的帧直接判为切点；得分在 `4` 到 `10` 之间时，只有当该帧得分达到前后 `0.3` 秒窗口内其余非相邻帧最高分的两倍以上（即鹤立鸡群的局部峰值），才判为切点。这样能过滤掉大部分跟拍晃动、急推拉和闪光灯，把这些拿不准的帧归入待复核窗口 `shots.review_windows`。此外，由于 `scdet` 得分会扣减上一帧的画面变化量，从剧烈运动镜头突然硬切到静止画面时，`scdet` 得分可能被压到只有 1 分左右；因此工具会把帧间画面差（`mafd`）自身突起的峰值也一并收进 `shots.review_windows` 供人工看图确认，但不会直接算作自动切点。
2. **看图复核切点（`frames`）**：任何固定阈值在真实成片面前都会顾此失彼（昏暗夜戏的硬切可能只有 5–7 分，而激烈打斗的一个长镜头里每隔 0.2 秒就能蹦出 7–8 分的峰值），因此必须看图复核。运行 `frames --review` 会把每个待复核窗口逐帧拼成图片，运行 `frames --longest 5` 会在全片最长的 5 个镜头里各均匀抽取 12 帧拼图；图片输出在 `U/reference_frames/` 下，终端会打印每一格对应的时间秒数。看完图后在 `labels.cut_fixes` 中登记修正：`add` 填入算法漏掉的真切点秒数，`remove` 填入算法误报的假切点秒数（与测得切点误差在 `±0.1` 秒内即可匹配）；若确认全部准确、无需增删，显式写 `"cut_fixes": {}` 表示已复核。注意：一旦重跑 `measure` 导致待复核窗口发生变化，旧的 `cut_fixes` 不再算数，Agent 必须对照新窗口重新核对更新。复核完成后，后续所有镜头统计指标、段落切点密度和最终导出物都会按修正后的切点重新计算。
3. **标注音轨与段落（`labels`）**：由 Agent 在 `U/reference_breakdown.json` 中填写 `labels`——包括音轨归属 `audio_spans`、叙事功能段落 `sections`、字幕版式 `subtitles` 与标注依据 `basis`。
   - **音轨边界必须卡在真实声音起止点**：`audio_spans` 的切换点要落在**耳朵真正听见人声开口或收声的时刻**，不要照抄字幕条出现或消失的时刻（按字幕切入帧标注的旁白收尾点，实测往往比真实现场收声晚 `0.24–0.42` 秒）。若目录内已有 `U/speech_boundary_anchors.json`，直接对照其中的 `acoustic_pauses`（`start` 即上一句收声点，`end` 即下一句开口点）对齐边界；因为声画同步换手率 `switch_on_cut_share` 只认切点前后 `±0.25` 秒的误差，边界一旦标偏，算出来的换手率就会严重失真。原片人物自带的画外音或内心独白仍属于原片音轨，统一标为 `original_dialogue`，如需在打法上区分，写进后面的 `source_facts` 与 `methods` 即可。
   - 填完 `labels` 后即可先跑一次 `check`，查看终端打印出的派生统计值 `derived`（各音轨时长占比、各段落切点密度、旁白字速、声音换手落在画面硬切上的比例、全片各叙事功能段落在时间轴上的起止占比 `structure`，以及第一次原声亮相的相对位置 `first_original_at`）。
4. **提炼事实与可迁移方法（`source_facts` 与 `methods`）**：对照刚刚算出的客观派生数据，在同一份 `reference_breakdown.json` 中继续填写原片事实 `source_facts`（必须锚定具体时间区间或测量路径，并显式列出涉及的专有名称 `entities`）与可迁移方法 `methods`（包含做法规则 `rule`、适用场景 `applies_when`、避坑条件 `avoid_when`、作用的下游文件 `applies_to`、支撑证据 `evidence` 与量化参考指标 `targets`）。注意：`targets` 里只允许写引用路径 `{"from": "<测量路径>"}`，严禁在规则正文里手写具体数字；导出时脚本会自动把实测数值填进去。
5. **校验并导出（`check` → `export`）**：运行 `check` 确认零 error 后，执行 `export` 导出 `production_reference.json`；导出器在写盘前会对产物再做一遍全字段防剧透泄漏扫描。

完整字段定义、枚举词表以及好/坏方法写法对比见 `references/reference-schema.md`。

## 4. 校验与防泄漏规则

`reference.py check` 遇到以下任一错误（error）都会以退出码 `1` 阻断：

| 规则编号 | 校验要求 |
|---|---|
| R1 | 顶层、`labels`、`fact`、`method`、`target`、`cut_fixes` 均采用严格封闭键集与封闭枚举；`fact` 不得混入方法字段，`method` 不得混入事实字段；编号须形如 `f1…` / `m1…` 且全局唯一；`subtitles` 字段类型固定；`skipped_dimensions` 的原因说明必须为非空字符串 |
| R2 | `audio_spans` 与 `sections` 必须按时间升序排列、互不重叠、相邻间隙 `≤0.5` 秒且完整覆盖全片时长；字幕取证时间点必须落在全片时长内；`cut_fixes.remove` 必须匹配某个已测得的切点，`add` 不得与已有切点重复 |
| R3 | 每条 `fact` 必须在时间锚点 `t: [a, b]`（落在成片时长内）与测量锚点 `measure: [路径]`（解析到 `shots` / `loudness` / `derived` 下的非字符串数值）之间二选一，且必须显式填写 `entities` 数组 |
| R4 | 每条 `method` 至少绑定一条支撑证据：引用已定义的 `fact` ID，或引用 `measure:<路径>`（同样仅限上述三个根路径） |
| R5 | `target` 只允许写 `from` 键，指向 `shots`、`loudness` 或 `derived` 下的数值叶子节点或白名单派生对象（见 schema），严禁使用列表下标 |
| R6 | `rule`、`applies_when`、`avoid_when` 以及 `skipped_dimensions` 的原因说明中，严禁出现：原片专有名称（忽略空格比对）、与 ASR 台词/背景资料/`source_facts` 完全重合的连续 8 个汉字（跨标点、跨相邻 ASR 窗口同样拦截）或连续 5 个英文单词、绝对时间码、“第 N 秒”或“N 分 M 秒”（含汉字数字）、本地绝对路径 |
| R7 | 五大制作维度（`narrative_structure`、`pacing`、`shots_editing`、`narration_subtitles`、`audio_visual`）每个维度至少提炼一条 `method`，若某维度确实无法提炼则必须在 `skipped_dimensions` 中写明原因 |
| R8 | 导出 `production_reference.json` 时，对所有键名和字符串值再跑一遍 R6 防泄漏扫描，且导出物任意层级严禁出现 `source_facts`、`labels`、`entities`、`evidence`、`statement`、`from`、`path` 键 |

防泄漏词表会自动汇总 `source_facts[].entities`、`background_research.json` 中的角色与 `character_details` 别名、不超过 5 个字的 `cultural_notes` 词条，以及资料中带《》「」引号的短词。若报出与 `source_facts` 连续 8 字重合，只需把其中一侧的通用剪辑表述换个说法即可。

以下情况仅报警告（warning），不阻断导出：`method` 未填 `applies_when`、`rule` 正文里直接写了阿拉伯数字、ASR 精度不是 `AVAILABLE_COARSE`、成片含中文对白但理解产物与资料库均未提供人名（此时仅靠手写的 `entities` 把关）、`basis` 为空、理解产物记录的视频文件身份与当前测量的成片不一致、存在待复核切点窗口但未填写 `cut_fixes`。

注意：R6 只能拦住字面重合，拦不住换了词却仍在复述原片剧情的写法；编写 `method` 时必须总结“遇到什么素材特征时怎么处理”，绝不能写成“主角进门后怎么剪”。此外，校验器不检查自然语言语义是否与数据吻合——动笔写“哪类段落切镜更密”前，必须先核对 `check` 打印出的 `cuts_per_min` 实测值，切忌凭印象写出与实测数据相反的结论。

## 5. 交付物与后续使用

最终导出的 `production_reference.json` 只包含：成片总时长与画布尺寸、`cut_detection`（切点检测参数及人工增删的切点数）、`profile`（核心节奏与响度指标，附带数据来源标记 `provenance`：`measured` 表示纯算法实测、`reviewed` 表示经过 `cut_fixes` 人工复核、`labeled` 表示依赖人工音轨/段落标注）、全片各叙事段落在时间轴上的相对位置分布 `structure`、字幕版式特征 `subtitles`、可迁移方法列表 `methods` 以及跳过的维度说明 `skipped_dimensions`。

使用方法：将导出的 `production_reference.json` 放进下一次制作任务的 `work_dir`（或通过资源库的 `production_reference` 模板绑定）。下游写稿阶段发现该文件存在时会主动读取，并在 `recap_story_plan.json` 的可选字段 `reference_methods` 中逐条记录对每条方法的采纳决定（`adopt` / `adapt` / `skip`）。借鉴参考方法时永远以新素材的真实画面和台词证据为准，绝不把参考片里的切点密度或留白占比当成硬性配额生搬硬套。没有任何生产脚本会机械校验该文件，它的存在与否也不会改变任何门禁的放行结果。

`U/` 目录下的 `reference_breakdown.json` 与 `reference_frames/` 抽帧拼图包含原片剧情事实与画面，仅供本机拆片使用，切勿复制进新项目或提交到公共仓库。

## 6. 能力边界

- 全程纯本地计算，不调用 MiMo 或任何大模型，不修改上游视频理解产物，也不给成片好坏打分或计算“新片与参考片的差距”。
- 镜头切点基于 ffmpeg `scdet` 分数加人工看图复核：慢速叠化转场无法被算法自动检出；间隔不足 `0.3` 秒的连续两个快切、恰好卡在 `soft_score` 临界线上的切点（不同 ffmpeg 版本可能算出 `4.00` 与 `3.989` 的微小差异）、以及剧烈运动后突入暗场的硬切（得分可能低至 `0.9`）都不会被直接算作自动切点，而是统一收进待复核窗口靠人工看图兜底。在一部 5 分钟的剧集解说实测中，算法初筛抓出 82 个切点，并在 11 个待复核窗口中框住了全部 5 个漏检真切点（其余 7 个窗口为急推拉、翻页与运动镜头）；人工看图补齐这 5 个切点后，与片头、暗场、中段三处逐帧人工真值（共 27 个切点）100% 吻合、零误报。
- 旁白占比与语速属于 `labeled` 级别精度：由人工标注的音轨区间结合粗粒度 ASR 分窗估算得出，而非逐字时间戳对齐。
- 防剧透泄漏扫描基于字面字符串比对，Agent 落笔时仍需主动自查，确保 `methods` 写的是通用创作手法而非原片故事梗概。
