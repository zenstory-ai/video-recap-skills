# 制作参考：字段、枚举与正反例

拆解成片时按本规范填写测量修正、原片事实与可迁移方法；后续制作新片时，由写稿阶段根据新素材特征自行决定采纳哪些方法。

## 标注词表

| 字段路径 | 允许枚举值 |
|---|---|
| `audio_spans[].owner` | `narration` · `original_dialogue` · `action_sound` · `ambience` · `music` · `silence` |
| `audio_spans[].narration_job`（可选，仅 `owner=narration` 时填写） | `context` · `causal_link` · `foreshadow` · `interpretation` · `transition` |
| `sections[].function`（可重复出现） | `hook` · `setup` · `turn` · `escalation` · `payoff` |
| `dimension` | `narrative_structure` · `pacing` · `shots_editing` · `narration_subtitles` · `audio_visual` |
| `applies_to` | `story_plan` · `visual_audio_board` · `clip_plan` · `narration` · `style_card` |

`audio_spans` 记录每个时间段**由哪种声音主导听觉焦点**（人声对白底下垫着的轻音乐不算主导；原片角色自带的画外音或内心独白统一记为 `original_dialogue`）；`sections` 记录全片的叙事结构分段。两者都必须按时间升序排列、互不重叠、相邻间隙不超过 `0.5` 秒且完整覆盖全片。

## `reference_breakdown.json`（Agent 唯一需要编写的文件）

顶层只允许出现 `schema`、`labels`、`source_facts`、`methods`、`skipped_dimensions` 五个键；每一层对象均采用严格封闭键集。

```json
{
  "schema": "video-reference.breakdown.v1",
  "labels": {
    "audio_spans": [
      {"start": 0, "end": 13.6, "owner": "narration", "narration_job": "context"},
      {"start": 13.6, "end": 30.6, "owner": "original_dialogue"}
    ],
    "sections": [{"start": 0, "end": 30.6, "function": "hook"}],
    "subtitles": {"burned": true, "max_lines": 1, "marks_original": "「」", "evidence_t": [12.0]},
    "basis": "5s ASR 分窗 + 故事板拼图；切点经 frames --review 与 --longest 5 逐帧复核",
    "cut_fixes": {"add": [10.8], "remove": []}
  },
  "source_facts": [
    {"id": "f1", "dimension": "narrative_structure", "statement": "开场旁白用两句话点明主角被夺走工牌的处境，随后直接切进当面质问的原声冲突",
     "t": [0, 13.6], "entities": ["<原片人名>"]},
    {"id": "f2", "dimension": "pacing", "statement": "原声交锋段切镜密集，旁白讲述段则多用较长的单镜头",
     "measure": ["derived.by_owner.original_dialogue.cuts_per_min", "derived.by_owner.narration.cuts_per_min"],
     "entities": []}
  ],
  "methods": [
    {"id": "m1", "dimension": "audio_visual",
     "rule": "当面质问或争执时顺着对话保留快频反打，转入解说交代前因时换成稳定画面托底，避免频繁跳切分散听众对信息的注意力",
     "applies_when": "人物当面交锋激烈、又需要旁白穿插交代前因后果的剧情解说",
     "avoid_when": "纯靠连续动作推进、无需旁白解释的追逐或打斗段落",
     "applies_to": "visual_audio_board",
     "evidence": ["f2", "measure:derived.switch_on_cut_share"],
     "targets": {"narration_cuts_per_min": {"from": "derived.by_owner.narration.cuts_per_min"}}}
  ],
  "skipped_dimensions": {"narration_subtitles": "当前未跑 ASR，无法测算旁白语速"}
}
```

- `cut_fixes`（可选）：只允许包含 `add` 与 `remove` 两个秒数数组。`remove` 中的每个时间点必须落在某个已测得切点的 `±0.1` 秒范围内；`add` 中的时间点则不得落在任何已测得切点的 `±0.1` 秒内。只要显式填写了 `cut_fixes`（哪怕写 `{}`），即代表已完成人工看图复核；有增删时，所有 `shots.*` 及与切点关联的 `derived.*` 指标都会按修正后的切点列表重新计算。
- `subtitles`（可选）：`burned`（布尔值）与 `max_lines`（`≥1` 的整数）必填，`marks_original`（标识原声字幕的引号样式）与 `evidence_t`（抽样取证秒数数组）可选。
- `skipped_dimensions`：键为跳过的维度名，值为一句非空的原因说明；该说明会原样写入导出物，因此同样受 R8 防剧透泄漏扫描约束。
- `source_facts` 条目：`id`、`dimension`、`statement`、`entities` 必填，时间锚点 `t` 与测量锚点 `measure` 二选一。`entities` 需列出该条事实涉及的所有人名、地名、机构名（无专名时写 `[]`），它们会自动加入导出时的防泄漏拦截词表。
- `methods` 条目：`id`、`dimension`、`rule`、`applies_to`、`evidence` 必填；`applies_when`、`avoid_when`、`targets` 可选。
- `targets` 条目：每个指标只写 `{"from": "<测量路径>"}`，路径根只能是 `shots`、`loudness` 或 `derived`，且不得带列表下标；路径解析结果必须是单个数值叶子节点，或以下白名单派生对象之一：`derived.structure`、`derived.first_original_at`（导出时仅保留相对位置占比 `{fraction}`，去掉绝对秒数）、`derived.narration_jobs`、`derived.by_owner.<owner>`、`derived.by_section.<function>`。像 `shots.cuts`、`shots.curve`、`loudness.short_term_1s` 这类逐时刻时间序列不能作为 `target` 导出。
- `fact` 的 `measure` 列表与 `method` 的 `measure:` 证据同样只认 `shots`、`loudness`、`derived` 三个根路径，且不能指向字符串类型的字段。

## 实测指标与派生指标路径

`reference_measurements.json` 由 `measure` 命令生成（其中 `settings` 记录切点检测参数，`scdet_scores` 缓存 `≥2` 分的逐帧切点分数，`mafd_peaks` 记录帧间画面差突起峰值；这三个内部字段不可被引用）。可供 `target` 与 `evidence` 引用的 `shots.*` 和 `loudness.*` 路径如下（填写了 `cut_fixes` 时按修正后的切点计算）：

| 测量路径 | 指标含义 |
|---|---|
| `shots.cuts` · `count` · `mean_s` · `median_s` · `p10_s` · `p90_s` | 切点时间列表、总镜头数、平均镜长、中位镜长、10% 与 90% 分位镜长（秒） |
| `shots.review_windows` | 被算法过滤掉的可疑切点与帧间画面差突变峰合并成的待复核时间窗 `[[start, end], …]`，供 `frames --review` 抽帧看图 |
| `shots.share_under_1s` · `share_over_8s` · `cuts_per_min` · `curve` | 短于 1 秒的短镜占比、长于 8 秒的长镜占比、全片每分钟切点数、10 秒滑动窗口切点密度曲线（每窗折算为每分钟切点数） |
| `loudness.integrated_lufs` · `lra_lu` · `true_peak_dbtp` · `short_term_1s` | 全片整体响度（LUFS）、响度动态范围（LU）、真峰值（dBTP）、逐秒短期响度序列（前约 2 秒为滤波器预热空值） |

`derived.*` 由 `check` 在内存中结合人工标注实时算出，并在 `export` 时将被引用的指标写入导出物：

| 派生路径 | 指标含义 |
|---|---|
| `derived.by_owner.<owner>` | 该音源主导的总秒数 `seconds`、全片时长占比 `share`、连续段落数 `blocks`、段落中位时长 `block_median_s`、每分钟切点数 `cuts_per_min`、平均短期响度 `mean_short_term_lufs` |
| `derived.narration_chars_per_s` | 旁白每秒字数（仅统计被旁白覆盖 `≥80%` 的 ASR 分窗；未跑 ASR 时 `value` 为 `null`） |
| `derived.switch_on_cut_share` | 声音主导权切换点恰好落在画面硬切点 `±0.25` 秒内的比例（衡量声画同步换手率） |
| `derived.by_section.<function>` | 该叙事功能段落的总秒数 `seconds`、每分钟切点数 `cuts_per_min`、旁白时长占比 `narration_share` |
| `derived.structure` | 全片叙事段落排布结构：`[{function, at: [起点全片占比, 终点全片占比], lead_owner}]` |
| `derived.first_original_at` | 第一段原声（`original_dialogue`）登场的时刻 `{s, fraction}`（导出时仅保留相对全片进度的占比 `fraction`） |
| `derived.narration_jobs` | 各类旁白职能（`narration_job`）占旁白总时长的比例（仅在标注了 `narration_job` 时生成） |

## `production_reference.json`（脱敏导出物，供下次写稿读取）

```json
{
  "schema": "video-reference.production.v1",
  "duration_s": 302.66,
  "canvas": {"width": 1280, "height": 676},
  "cut_detection": {"detector": "scdet-isolated-v1", "hard_score": 10.0, "soft_score": 4.0, "isolation_ratio": 2.0,
                    "isolation_window_s": 0.3, "scaled": true, "agent_added": 3, "agent_removed": 0},
  "profile": {
    "shot_median_s": {"value": 2.58, "provenance": "reviewed"},
    "cuts_per_min": {"value": 16.85, "provenance": "reviewed"},
    "integrated_lufs": {"value": -14.8, "provenance": "measured"},
    "narration_share": {"value": 0.65, "provenance": "labeled"},
    "narration_chars_per_s": {"value": 4.0, "provenance": "labeled", "precision": "coarse_asr_windows"},
    "switch_on_cut_share": {"value": 0.5, "provenance": "labeled"},
    "first_original_at": {"value": {"fraction": 0.045}, "provenance": "labeled"}
  },
  "structure": [{"function": "hook", "at": [0.0, 0.1], "lead_owner": "narration"}],
  "subtitles": {"burned": true, "max_lines": 1, "marks_original": "「」"},
  "methods": [
    {"id": "m1", "dimension": "audio_visual", "rule": "…", "applies_when": "…", "avoid_when": "…",
     "applies_to": "visual_audio_board",
     "targets": {"narration_cuts_per_min": {"value": 14.2, "provenance": "labeled"}}}
  ],
  "skipped_dimensions": {}
}
```

- `provenance`（数据来源标记）：引用 `derived.*` 的指标记为 `labeled`（依赖人工音轨与段落标注）；引用 `shots.*` 的指标在填写了 `cut_fixes`（含 `{}`）时记为 `reviewed`，未填时与 `loudness.*` 一样记为 `measured`。值为 `null` 的指标不会写入 `profile`。
- `cut_detection` 记录切点检测阈值及人工增删的切点数，方便阅读者判断镜头统计数据的可信度。
- 导出器会自动剥离 `source_facts`、`labels`、`evidence`、`entities`、`from`、源文件身份及一切本地路径。
- `written_by` 与 `template` 两个保留键供资源库项目绑定写出副本时使用，本技能导出时不写这两个键。

## 方法提炼的正反例

一条合格的 `method` 必须写清“在什么素材条件下、具体怎么处理、解决什么问题”，并把具体数字交给 `targets` 自动填入。

**正例（可迁移、有条件、有数据支撑）**：

1. `"rule": "当面质问或争执时顺着对话保留快频反打，转入解说交代前因时换成稳定画面托底，避免频繁跳切分散听众对信息的注意力"`，搭配 `"applies_when": "人物当面交锋激烈、又需要旁白穿插交代前因后果的剧情解说"`。注意：规则描述的方向必须与 `check` 算出的 `derived.by_owner.*.cuts_per_min` 实测值一致，不要闭眼瞎写。
2. `"rule": "开场先用一句旁白点破人物处境与身份落差，紧接着尽早放出第一句原声冲突把观众拽进现场"`，搭配 `"targets": {"first_original": {"from": "derived.first_original_at"}}`。
3. `"rule": "旁白与原声的交接点尽量卡在画面硬切点上，借镜头切换掩护声音换手"`，搭配 `"evidence": ["measure:derived.switch_on_cut_share"]`。

**反例（剧透泄漏、写死数字或空话套话）**：

1. `"范闲进殿后旁白交代他的身世"` —— 带有原片人名与具体剧情，这是原片事实（`source_facts`），不是可迁移方法（触发 R6 拦截）。
2. `"在 1:23 切到原声，旁白语速保持每秒 4 字"` —— 写死了绝对时间码（触发 R6 拦截）并手写了具体数字（应通过 `targets` 引用测量路径）。
3. `"照着这部片子的结构剪"` —— 既无适用条件也无具体做法，换一部素材根本没法执行。
