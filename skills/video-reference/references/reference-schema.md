# 制作参考：字段、枚举与示例

本文件只列字段与枚举；剧作与剪辑手法的具体规则以后续写稿阶段自己的手册为准，这里不复述。

## 标注词表

| 字段 | 取值 |
|---|---|
| `audio_spans[].owner` | `narration` · `original_dialogue` · `action_sound` · `ambience` · `music` · `silence` |
| `audio_spans[].narration_job`（可选，仅 narration 段） | `context` · `causal_link` · `foreshadow` · `interpretation` · `transition` |
| `sections[].function`（可重复出现） | `hook` · `setup` · `turn` · `escalation` · `payoff` |
| `dimension` | `narrative_structure` · `pacing` · `shots_editing` · `narration_subtitles` · `audio_visual` |
| `applies_to` | `story_plan` · `visual_audio_board` · `clip_plan` · `narration` · `style_card` |

`audio_spans` 记录**谁占有这段声音**（对白下压着的背景乐不算主导）；`sections` 记录叙事功能。两者都必须按时间排序、
不重叠、间隙不超过 0.5 秒、覆盖整片。

## `reference_breakdown.json`（Agent 唯一要写的文件）

顶层只允许 `schema`、`labels`、`source_facts`、`methods`、`skipped_dimensions`；每一层的键都是封闭集合。

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
    "basis": "5s ASR 窗口 + 故事板"
  },
  "source_facts": [
    {"id": "f1", "dimension": "narrative_structure", "statement": "开场旁白交代主角身世后切入原声冲突",
     "t": [0, 13.6], "entities": ["<原片人名>"]},
    {"id": "f2", "dimension": "pacing", "statement": "原声段沿用原片较密的切点，旁白段切得更少",
     "measure": ["derived.by_owner.original_dialogue.cuts_per_min", "derived.by_owner.narration.cuts_per_min"],
     "entities": []}
  ],
  "methods": [
    {"id": "m1", "dimension": "audio_visual",
     "rule": "原声段保留原片自身的剪辑节奏，旁白段换成较长的镜头承载解说，让注意力留在声音上",
     "applies_when": "对白冲突强、需旁白补前情的剧集解说", "avoid_when": "主要靠动作声承载的段落",
     "applies_to": "visual_audio_board", "evidence": ["f2", "measure:derived.switch_on_cut_share"],
     "targets": {"narration_cuts_per_min": {"from": "derived.by_owner.narration.cuts_per_min"}}}
  ],
  "skipped_dimensions": {"narration_subtitles": "ASR 不可用，未测语速"}
}
```

- `subtitles` 可选：`burned`、`max_lines` 必填，`marks_original`、`evidence_t` 可选。
- fact：`id`、`dimension`、`statement`、`entities` 必填，`t` 与 `measure` 二选一。`entities` 写这条事实涉及的人名、地名、
  组织名，没有就写空列表——它同时是泄漏扫描的词表。
- method：`id`、`dimension`、`rule`、`applies_to`、`evidence` 必填；`applies_when`、`avoid_when`、`targets` 可选。
- target：只写 `from`，路径根只能是 `shots`、`loudness` 或 `derived`，解析结果必须是数值或对象（`derived.structure` 可为列表）。

## 测量与派生值

`reference_measurements.json` 由 measure 写，路径即 target / evidence 可引用的 `shots.*`、`loudness.*`：

| 路径 | 含义 |
|---|---|
| `shots.cuts` · `count` · `mean_s` · `median_s` · `p10_s` · `p90_s` | 硬切时间与镜长分布 |
| `shots.share_under_1s` · `share_over_8s` · `cuts_per_min` · `curve` | 短/长镜头占比、切点密度、10 秒窗口切点曲线 |
| `loudness.integrated_lufs` · `lra_lu` · `true_peak_dbtp` · `short_term_1s` | 整体响度、动态范围、真峰值、逐秒短期响度（前约 2 秒为预热空值） |

`derived.*` 由 check 在内存里计算，export 写入用到的部分：

| 路径 | 含义 |
|---|---|
| `derived.by_owner.<owner>` | `seconds` · `share` · `blocks` · `block_median_s` · `cuts_per_min` · `mean_short_term_lufs` |
| `derived.narration_chars_per_s` | 只用被旁白覆盖 ≥80% 的 ASR 窗口；ASR 失败或跳过时 `value` 为 null |
| `derived.switch_on_cut_share` | 音轨归属切换点落在画面切点 ±0.25 秒内的比例 |
| `derived.by_section.<function>` | `seconds` · `cuts_per_min` · `narration_share` |
| `derived.structure` | `[{function, at:[开始占比, 结束占比], lead_owner}]` |
| `derived.first_original_at` | 第一次原声对白的 `{s, fraction}` |
| `derived.narration_jobs` | 各 job 占旁白时长的比例（标注了 job 时才有） |

## `production_reference.json`（导出物，消费方只读这个）

```json
{
  "schema": "video-reference.production.v1",
  "duration_s": 302.66,
  "canvas": {"width": 1280, "height": 676},
  "profile": {
    "shot_median_s": {"value": 3.06, "provenance": "measured"},
    "cuts_per_min": {"value": 11.7, "provenance": "measured"},
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

- `provenance`：路径在 `derived.*` 下为 `labeled`（依赖 Agent 标注），否则为 `measured`。值为 null 的 profile 项不导出。
- 导出时丢弃 `source_facts`、`labels`、`evidence`、`entities`、`from`、文件身份和任何路径。
- 保留键 `written_by`、`template` 给以后的资源库绑定副本使用，本技能不写。

## 好方法与坏方法

方法写"在什么条件下、怎么做、为什么"，数字放进 `targets`。

好：

1. "原声段保留原片自身的剪辑节奏，旁白段换成较长的镜头承载解说" + `applies_when: "对白冲突强、需旁白补前情的剧集解说"`。
   规则方向必须与 check 打印的派生值一致（这里是 `derived.by_owner.*.cuts_per_min`）；R1–R8 不检查语义是否自相矛盾。
2. "开场先用一句旁白交代身份与处境，第一次原声冲突尽早接管" + `targets.first_original.from: "derived.first_original_at"`。
3. "声音归属的切换尽量落在画面硬切上，让观众用画面感知换手" + `evidence: ["measure:derived.switch_on_cut_share"]`。

坏：

1. "范闲进殿后旁白交代他的身世"——原片人名与事件，是事实不是方法（R6）。
2. "在 1:23 切到原声，旁白语速保持每秒 4 字"——绝对时间码（R6）与手写数字（应写 target）。
3. "照着这部片的结构做"——没有条件、不可迁移，也无法被后续制作采纳或拒绝。
