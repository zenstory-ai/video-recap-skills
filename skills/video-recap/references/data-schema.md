# 数据格式（中间 JSON）

所有中间文件均在 pipeline 工作目录（work_dir/）下。

## vlm_analysis.json

每场景的 VLM 分析结果，数组格式：

```json
[
  {
    "scene_id": 1,
    "start": 5.0,
    "end": 15.0,
    "description": "男子闯入房间",
    "depth_analysis": "角色情绪分析...",
    "frame_facts": {
      "5.0": ["男子闯入房间, 头发蓬乱表情紧张"],
      "10.0": ["男子俯身盯着床上男孩, 男孩睁眼惊醒"]
    }
  }
]
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `scene_id` | int | 场景编号 |
| `start` | float | 开始时间（秒） |
| `end` | float | 结束时间（秒） |
| `description` | string | 画面简述（≤80字） |
| `depth_analysis` | string | 深层分析（情绪/关系/潜台词） |
| `frame_facts` | object | 帧级事实，key 为时间戳字符串 |

## asr_result.json

语音转文字结果：

```json
[
  {"start": 0.0, "end": 3.5, "text": "What are you doing here?"}
]
```

## asr_writing_chunks.json

由 CLI 在生成 `agent_narration_brief.md` 时自动写出。它把长 ASR 按句子边界拆成适合 Agent 消化的语义块；中文按字符计数，非 CJK 文本按词数计数，并尽量保留 scene 对齐。

```json
[
  {
    "chunk_id": 0,
    "start": 0.0,
    "end": 28.5,
    "scene_ids": [0, 1],
    "char_count": 642,
    "text": "第一段对白……",
    "segments": [
      {"start": 0.0, "end": 3.5, "text": "第一句。", "char_count": 4}
    ]
  }
]
```

## silence_periods.json

静音窗口列表（适合放解说）：

```json
[
  {"start": 2.0, "end": 8.5, "duration": 6.5, "has_speech": false}
]
```

`has_speech` 标记该窗口是否与检测到的 ASR 语音重叠；下游（pipeline / narration）只把 `has_speech=false` 的窗口当作可放解说的安静窗口。

## speech_boundary_anchors.json

原声句末安全切入点。脚本把 ASR 终止标点的估算时间吸附到短声学停顿；它与
`silence_periods.json` 分开，因为约 0.1–0.5 秒的停顿适合作为旁白入口，却不足以容纳整段旁白。

```json
{
  "schema_version": 2,
  "sentence_anchors": [
    {
      "time": 5.809,
      "confidence": "low",
      "boundary_use": "unverified",
      "timing_bound_seconds": 9.778,
      "timing_basis": "asr_window",
      "alignment_error": 0.012,
      "text_tail": "带你重走詹姆斯的二十一年。",
      "pause_start": 5.222,
      "pause_end": 5.809
    }
  ]
}
```

ASR 时间只到窗口级，句末位置只能在窗口内估计。`timing_bound_seconds` 是停顿到窗口两端的较远距离，
即句末真实位置的最坏误差；`confidence` 取它与 `alignment_error` 的较大值（≤0.6 high，≤1.2 medium，否则 low）。
`boundary_use` 决定下游用不用：`verified`（high/medium）、`unverified`（窗口太粗但吸附误差 ≤1.2s，仍作门禁锚点，
brief 里标 `unverified ±N s`）、`none`（不用）。缺 `boundary_use` 的锚点来自 schema 1 的旧估计器：high/medium 视为 `unverified`、其余为 `none`；
理解阶段发现 `schema_version` 不是 2 时：有 `audio.wav` 就重新生成本文件；没有（素材库恢复）就按 `asr_result.json`
原地重标（锚点时间不变，记 `upgraded_from_schema: 1`），重标不了就保持原样。剪后输出时钟的
`speech_boundary_anchors_output.json` 里 `time` / `pause_start` / `pause_end` / `expected_time` 都是输出时钟，原片值在 `source_*` 字段。

当 `overlaps_speech=true` 且旁白不是从 0 秒冷开场时，`narration` lint 要求 `start`
贴近 `boundary_use` 不为 `none` 的锚点。入口是否落在原声讲话里，与 cut 门禁用同一条语气词规则：只有语气词的 ASR 窗口（"啊！"、"Hi."）不算讲话，只在紧挨真实对白的一侧保留 1 秒；文本为空或只有空白的 ASR 行只是时间证据，不算讲话也不留保护；有 `asr_clean.json` 时三处都以它为准，否则读 `asr_result.json`；assemble 的入口检查同样如此。否则在 TTS 前用 `interrupts_source_sentence` 阻断，并返回
`suggested_start` / `suggested_end` 与 `source_text_tail` 给 Agent 调整：建议的是入点前后
`max_shift_seconds`（10 秒）内离入点最近的锚点，整块按原时长平移过去后仍在前后两块之间（不越过相邻块），
既不与其他块重叠、也不与它们相接（间隔须大于 0.15 秒，否则就成了首尾相连的交接）；前一块也收到建议时，
同时避开它原来和建议的时间窗。cut 模式下平移后仍在该块所属片段内，cut_output 下不超过 `--output-duration`
（full 模式不检查视频结尾）。距离相同时取后面的。常规块使用
`source_entry_policy: "sentence_boundary"`；原声语句完整性没有抢断 override。范围内没有这样的锚点时
`suggested_start` 为 `null`，Agent 必须移动、缩短或删除该旁白块。

剪辑模式第二阶段会另外生成 `speech_boundary_anchors_output.json`，把锚点、ASR 语音区间和
安静窗口映射到剪后 OUTPUT 时间轴，避免拿原片时间检查剪后旁白。输出时钟上的值不越出所属片段：比片段入/出点早/晚不超过
0.05 秒、又没有别的片段播放的锚点钉在该段的入点或出点上（换算后落在 `[0, 成片时长]` 之外的直接丢掉），`source_time` 仍是实测的原片时间：

```json
{
  "schema_version": 2,
  "timeline": "cut_output",
  "clip_plan_identity": {"size": 4970, "mtime_ns": 1790949817370765842},
  "sentence_anchors": [{"time": 4.0, "pause_start": 3.8, "confidence": "high"}],
  "speech_spans": [{"start": 0.0, "end": 3.8}],
  "quiet_windows": [{"start": 3.8, "end": 4.1}]
}
```

`clip_plan_identity` 是写入时 `clip_plan_validated.json` 的 `{size, mtime_ns}`：video-script
校验要求它与当前文件相等，assemble 要求本文件不早于 `clip_plan_validated.json`。video-cut
对同一剪辑计划重跑（续跑时复用 `edited_source.mp4`）不会重写内容未变的 `clip_plan_validated.json`，
所以第二遍写下的证据在第三遍续跑时仍然有效；`clip_plan.json` 被重新保存后则必然重写。缺失、过期或畸形的
output 证据一律 fail closed，不能回退到原片时钟或信任 Agent 写入的
`overlaps_speech=false`。多来源剪辑的每条映射记录还保留 `source_id` 和原片起止时间。

## timeline_fusion.json

由 CLI 在生成 brief 时自动写出。它把 VLM 场景、ASR 对白和静音窗口按时间轴 overlap 合并，减少写稿时手工推断“这一幕有没有对白/能不能插解说”的成本。

```json
[
  {
    "scene_id": 0,
    "time_range": [0.0, 10.0],
    "visual_description": "两人在门口对峙",
    "depth_analysis": "关系紧张",
    "frame_facts": {"1.0": ["女子回头"]},
    "dialogue_segments": [
      {"start": 2.0, "end": 4.0, "overlap_seconds": 2.0, "text": "你到底是谁"}
    ],
    "dialogue_overlap_seconds": 2.0,
    "narration_slots": [
      {"start": 5.0, "end": 7.0, "duration": 2.0, "char_budget": 5}
    ],
    "recommended_mode": "ducked-bed"
  }
]
```

cut 第二轮（已有 `edited_source.mp4`）时本文件与 `asr_writing_chunks.json` 都在 OUTPUT 时间轴上：被拆到多个片段的场景 `scene_id` 写作 `"3.1"`（0 起的原场景号加片段序号）；VLM 文字里落在场景原片区间内的时间改写成输出时间，落在被剪掉部分的写 `[cut-away moment]`；只被剪进一部分的 ASR 窗口文字换成 `[partial ASR window: only part of it is in the cut, text withheld]`。`scene_id` 一律 0 起，brief 里给人看的场景号从 1 数。

## narration.json

Agent 撰写的解说词。full 模式下使用原视频时间；**orchestrated cut 模式（`video-recap --edit-mode cut`）下，第二次暂停时已经先剪出 `edited_source.mp4`，因此 `narration.json` 必须直接使用剪后成片的 OUTPUT 时间轴（0..成片总时长）；不存在原视频时间→输出时间的旁白映射产物：

```json
[
  {"start": 2.5, "end": 7.0, "narration": "解说文本", "pause_after_ms": 250, "overlaps_speech": true}
]
```

## narration_lint.json

续跑校验 `narration.json` 时生成的预检结果。它检查写稿、时间安全和解说覆盖。`metrics` 为 full 模式下的诊断指标（cut 模式为空对象），不是要求命中某个旁白比例的创作配额；低覆盖 warning 应回到 `visual_audio_board.json` 检查是否为有意的原声/沉默选择。

```json
{
  "ok": false,
  "error_count": 1,
  "warning_count": 1,
  "metrics": {
    "segment_count": 12,
    "narration_coverage": 0.68,
    "narration_seconds": 61.2,
    "timeline_seconds": 90.0,
    "avg_block_chars": 48,
    "original_block_count": 4
  },
  "errors": [
    {"level": "error", "index": 2, "code": "time_overlap", "message": "Segment overlaps the previous narration segment"},
    {"level": "error", "index": 5, "code": "over_budget", "budget_chars": 28, "limit_chars": 35, "actual_chars": 42, "over_chars": 7, "tts_overhead_seconds": 0.45}
  ],
  "warnings": [
    {"level": "warning", "index": 0, "code": "over_budget", "budget_chars": 28, "actual_chars": 42}
  ]
}
```

推荐字数 `budget_chars` 先从时间窗扣掉 `tts_overhead_seconds`（约 0.45 秒，每块一次 TTS 合成自带的首尾静音）再折算，所以比 brief 里每个窗口标的字数少一两个字。full 模式下字数超过推荐字数 1.25 倍（`limit_chars`）的段是 `over_budget` error（校验不会替 Agent 截短）；略超实际朗读时长但未过硬上限、以及 cut_output 的超预算仍是 warning。段落必须按 `start` 排序，否则报 `out_of_order` error。

常见 code：`invalid_time`、`out_of_order`、`empty_narration`、`time_overlap`、`outside_clip_plan`、`over_budget`、`incomplete_sentence`、`slot_too_short`、`under_narrated`、`over_narrated`、`fragmented_beats`、`no_original_blocks`。

## recap_story_plan.json / visual_audio_board.json（Agent 创作工作产物）

这两个 JSON 是 skill 层的创作决策记录：前者保存导演意图、备选剪辑假设、POV/主线和 change-based beats；后者保存每拍的画面/表演选择、入点/出点、原声锚点、`audio_owner` 与 `narration_job`。完整字段与工作流见 video-script 技能的 `creative-editing-playbook.md`。

CLI 不以它们作为渲染硬门禁，也不新增解析服务；建议型解说评审在文件存在时读取它们，Agent 则用它们保证 cut、旁白和声音选择没有偏离同一个创作意图。

## style_card.json（Agent 撰写，可选/按 brief 要求）

`style_card.json` 是表达层契约：由 Agent 根据 `--style`、`--context`、素材证据、ASR 和用户偏好信号综合撰写。`--style` 是 freeform verbatim guidance（原样自由文本指导），不是枚举、preset、switch，也不是一组可穷举风格名；不要把它翻译成固定档位。它是当前版本的活动契约：用户对声音、节奏、字幕阅读或禁忌提出新反馈后，更新原文件并移除过期偏好，不要只改 `narration.json`。

这个文件记录声音、节奏、回收意图和证据支撑的表达判断；字段可以随项目增减，下游只把它当 JSON object 读取，不要求固定键名。它不负责标题、封面、首句承诺或卖点包装。

```json
{
  "voice": "冷静但有压迫感，少讲大道理，多用人物动作和台词里的证据推进",
  "pacing": "前 15 秒紧凑建立冲突，每个 beat 连续说完一个思路；中段留原声喘息，结尾回收开头疑问",
  "payoff_intent": "让观众先看到误会，再看到人物选择的代价",
  "subtitle_read_posture": "TTS 保持连续口语，字幕按阅读宽度拆 cue，不用字幕换行切碎朗读",
  "evidence_intent": ["优先引用画面动作", "关键转折保留原声"]
}
```

## packaging_plan.json（Agent 撰写，可选）

`packaging_plan.json` 是内容锁定后的可选包装层契约：标题、封面帧/视觉钩子、首句、观众承诺、卖点和发布包装信息。它帮助 review 判断“包装承诺”和正文前 15 秒是否对齐；不应反过来驱动故事取舍。

它不是文风策略，不覆盖 `style_card.json` 的声音、节奏或表达规则；如果包装需要某个承诺，正文仍要用素材证据兑现。

```json
{
  "title": "一句能对外展示的标题",
  "cover_frame": {"time": 12.4, "reason": "人物第一次正面做出关键选择"},
  "first_line": "开场第一句解说",
  "viewer_promise": "观众看完会明白的冲突/反转/信息增量",
  "selling_points": ["强冲突", "原声高光"],
  "packaging_notes": "发布侧备注，不写文风规则"
}
```

## narration_lint.json 的 deslop_qc（CLI 生成，报告型 QC）

`narration_lint.json` 的 `deslop_qc` 字段由本地 deterministic scanner 生成，Agent 不手写；不再单独写 `deslop_qc.json`。它只是 report-only QC：不是 AIGC detector，不判断文本是不是 AI 写的，不会自动改写。修改仍由 Agent/人工根据报告回到 `narration.json`、`style_card.json` 或字幕源里处理。

报告分两层：

- `blockers`：客观阻断项，会并入 `narration_lint.json` 的 error，例如破折号、占位符泄漏。
- `advisories`：建议项，只提示可读性/口语化风险，例如缺少或空的 `style_card.json`、模板化“不是……而是……”转折、套话密度、抽象总结词、解释链、比喻标记、过长段落；它们不自动阻断，也不自动改写。

```json
{
  "ok": false,
  "contract": "Local readability/QC report only: this is not an AIGC detector, does not claim AI-generation accuracy, and never rewrites text. Corrections remain human/agent rewrite work.",
  "scanner": "deslop_qc.py",
  "blocker_count": 1,
  "advisory_count": 1,
  "blockers": [
    {"severity": "blocker", "code": "em_dash", "source": "narration", "index": 3, "message": "破折号（—/——）不得出现在 narration/original_subtitles 中"}
  ],
  "advisories": [
    {"severity": "advisory", "code": "cliche_density", "source": "narration", "index": null, "message": "套话/高频抽象词偏密，建议换成具体行动、选择和后果"}
  ],
  "metrics": {"segments_scanned": 12, "text_units": 860, "sentence_count": 38}
}
```

## multi_source_manifest.json（多视频 cut）

多视频剪辑模式下，项目级 `work_dir/multi_source_manifest.json` 是编排、剪辑与合成阶段共用的来源契约。`source_id` 由源文件名主干与文件大小派生为 `src_<stem>_<size>`；同一项目里得到相同 id 的后续来源按输入顺序追加 `_2`、`_3` 后缀。`source_video_identity` 记录该文件的 `{size, mtime_ns}`，续跑时与当前输入逐项比对。

```json
{
  "schema_version": 1,
  "sources": [
    {
      "source_id": "src_episode1_734003200",
      "source_path": "/abs/episode1.mp4",
      "source_name": "episode1.mp4",
      "source_video_identity": {"size": 734003200, "mtime_ns": 1758326400000000000},
      "source_work_dir": "sources/src_episode1_734003200",
      "material_id": "episode1-734003200"
    }
  ]
}
```

## clip_plan.json

cut 模式下 Agent 选择要保留的原片片段，数组或 `{ "clips": [...] }` 都可接受。默认片段不能重叠，避免同一原片时间映射到多个输出位置：

```json
{
  "target_duration": "10m",
  "clips": [
    {"start": 12.0, "end": 38.0, "reason": "b01 | hook | knowledge: unknown→threat | POV=主角 | 保留倾听反应 | 入点=问题已问出 | 出点=沉默落地"}
  ]
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `start` | float | 原视频片段开始秒数 |
| `end` | float | 原视频片段结束秒数 |
| `reason` | string | 选择该片段的剧情/信息原因 |

多视频 cut 的 `clip_plan.json` 必须给每个片段加 `source_id`；重叠检测按 `source_id` 分开计算，不同源视频的相同时间范围不互相冲突：

```json
{
  "target_duration": "10m",
  "clips": [
    {"source_id": "src_episode1_734003200", "start": 12.0, "end": 38.0, "reason": "b01 | setup | knowledge: unknown→clue | POV=主角 | 保留迟疑反应 | 入点=线索出现 | 出点=疑问成立"},
    {"source_id": "src_episode2_689110016", "start": 4.0, "end": 22.0, "reason": "b02 | payoff | power: suspect→hero | POV=主角 | 保留最终选择 | 入点=证据落下 | 出点=代价显现"}
  ]
}
```

## clip_plan_validated.json

CLI 校验 `clip_plan.json` 后写出，额外包含输出时间轴：

```json
{
  "clips": [
    {
      "clip_id": 0,
      "source_start": 12.0,
      "source_end": 38.0,
      "output_start": 0.0,
      "output_end": 26.0,
      "duration": 26.0,
      "frame_count": 650,
      "reason": "b01 | hook | knowledge: unknown→threat | POV=主角 | 保留倾听反应 | 入点=问题已问出 | 出点=沉默落地"
    }
  ],
  "total_duration": 26.0,
  "target_duration": 600.0
}
```

`qc.boundary_status.sentence_checks` 逐项记录每个片段 start/end 是 `safe`、`unchecked`
还是 `blocking`（落在 `unverified` 句末锚点上的边界为 `safe`，`reason` 记 `unverified_sentence_boundary`）。理解阶段已有 ASR 讲话时间时，任何未落到源头/源尾、句末锚点/静音窗，且
不是同源无损连续连接（计划里相邻、`clip_id` 连续的同源片段）的边界都会写入 `qc.blocking[].code=unsafe_clip_sentence_boundary`。
被阻断的边界额外带 `nearest_safe`，给出前后 5 秒内最近的安全边界（原片秒，已是帧对齐后的落点、复核后仍安全；重跑时切镜吸附不会再把它拉回讲话内，见下文），没有则为 `null`：

```json
{"clip_id": 0, "edge": "start", "time": 3.0, "status": "blocking", "reason": "inside_detected_speech",
 "nearest_safe": {"before": {"time": 0.0, "reason": "source_start", "delta": -3.0},
                  "after": {"time": 4.8, "reason": "sentence_or_quiet_boundary", "delta": 1.8}}}
```

切镜吸附先执行，句末吸附随后执行，保证视觉边界不会覆盖声音安全边界；附近没有停顿窗、句末吸附修不回来时，被切镜吸附移进讲话的边界若原位置能过门禁，就撤回原位置（不与其他片段重叠时），`qc.boundary_status.shot_snaps` 对应项记 `end_action`/`start_action: "reverted_unsafe"`、`rejected_end`/`rejected_start`（被撤回的切点）与 `end_revert_reason`/`start_revert_reason`；最后把入点对齐到源帧网格、
时长对齐到整数输出帧（只移动不到一帧，优先选门禁仍判为安全、仍在停顿内的一侧，也不会因此丢掉必保证据的边缘；两侧同样安全时选不跨过切镜扫描到的原片硬切的一侧，入点落在切点上或之后、出点落在切点上或之前，不闪一帧另一个镜头），
门禁检查对齐后的边界。每段的 `frame_count` 是它渲染的帧数，`edited_source.mp4` 因此是恒定帧率；
对齐前后的时间写在 `qc.boundary_status.frame_snaps`，汇总写在 `qc.frame_grid`：

```json
{"output_frame_rate": "25", "frame_count": 650, "duration": 26.0,
 "sources": [{"source_id": null, "path": "/abs/source.mp4", "frame_rate": "25/1",
              "video_start_offset": 0.0, "start_snapped": true}]}
```

单源时 `output_frame_rate` 沿用源的 `r_frame_rate`（隔行素材报场频、或可变帧率素材的标称帧率高出 `avg_frame_rate` 1.5 倍以上时，按实际帧率取），多源时是画布帧率（NTSC 写成 `30000/1001`）。
`output_start/end` 与 `total_duration` 按累计 `frame_count` 计算，与渲染的帧时间一致。
`start_snapped: false` 表示该源帧率未知（`0/0` 或大于 120），入点不动，时长仍对齐到整数输出帧。

多视频 validated clip 会额外保留来源字段，供 pass2 brief、timeline 和剪映导出追溯原素材：

```json
{
  "clips": [
    {
      "clip_id": 0,
      "source_id": "src_episode1_734003200",
      "source_path": "/abs/episode1.mp4",
      "source_start": 12.0,
      "source_end": 38.0,
      "output_start": 0.0,
      "output_end": 26.0,
      "duration": 26.0,
      "frame_count": 780,
      "reason": "b01 | hook | knowledge: unknown→threat | POV=主角 | 保留倾听反应 | 入点=问题已问出 | 出点=沉默落地"
    }
  ]
}
```

## material library（可选，grep 复用）

`--material-library-dir <dir> --save-materials` 会把每个源视频的已分析小文件复制到 `<dir>/materials/<material_id>/`，不复制原始媒体。`--use-materials` 会在源文件路径、`source_video_identity`（`{size, mtime_ns}`）和分析 `settings` 都相等时把这些 JSON/MD 产物恢复到当前 per-source `work_dir`。

```text
.video-materials/
  materials_index.jsonl          # 追加式 grep journal；旧行可保留为历史
  materials/<material_id>/
    material.json                # 当前权威 metadata
    material.md                  # grep 友好摘要
    artifacts/scenes.json
    artifacts/asr_result.json
    artifacts/asr_clean.json       # 启用 --consolidate-asr 时保留，恢复后 brief/review 优先使用
    artifacts/vlm_analysis.json
    artifacts/understanding_index.json
    artifacts/consolidation.status.json  # 恢复后 brief-only 仍能提示 consolidate 失败/缺索引
```

`asr_timing_evidence.json` 不入库：它按 `{size, mtime_ns}` 绑定 `asr_result.json` 与 `audio.wav`，
恢复时前者被脱敏重写、后者不复制，恢复出的副本只会被判为 `MISSING_OR_STALE`。

`materials_index.jsonl` 每次保存追加一行，字段包括 `schema_version`, `event`, `material_id`, `source_name`, `source_path`, `source_video_identity`, `summary`, `tags`, `material_dir`, `updated_at`；`material.json` 另外记录分析 `settings` 字典与每个产物的 `bytes`。当前权威状态始终以 `materials/<material_id>/material.json` 为准。MVP 只承诺 `grep -R "关键词" <library>` 这类文件检索；没有 DB、embedding 或语义搜索。

保存时会对凭证形态（`tp-`/`sk-`/`gh*_`/`AKIA`/JWT 与 `KEY=VALUE` 赋值）和凭证命名的 JSON key 做脱敏，但这只是**尽力而为**的兜底，不是保证：陌生格式的密钥仍可能漏过。请从源头避免把密钥写进分析产物——key 从环境变量/`.env` 读取，不需要落进 scenes/ASR/VLM/summary 等 JSON。

## original_subtitles.json / user_subtitles.{json,srt,ass}（可选，原声留白字幕）

解说块之间的原声留白会把【原声台词】烧成字幕（assemble 阶段用 `「」` 包裹以区分解说）。来源优先级（高→低）：

1. **`work_dir/user_subtitles.json`**（用户自带，最准）— 数组 `[{start,end,text}]` 默认按**成片 OUTPUT** 时间轴直接使用；也可写成 `{"timeline": "source"|"output", "lines": [...]}`，`source` 表示按**原片**时间轴给出，由 assemble 依 `clip_plan_validated.json` 映射到成片。
2. **`work_dir/user_subtitles.srt` / `.ass`**（用户自带）— 默认按**原片**时间轴解析后映射到成片。
3. **`work_dir/original_subtitles.json`**（Agent 校对，cut pass2 写）— OUTPUT 时间轴 `[{start,end,text}]`，订正 ASR 错字/人名、只写留白里真正出声的句子。
4. **ASR 兜底** — 无上述文件时，用 `asr_result.json` 按留白粗略映射（中点估时，可能偏多偏乱）。

来源 1–3 为「精确来源」：每条按句**区间裁剪**落到所覆盖的留白边界（跨边界会拆分），不走 ASR 兜底的中点估时；over-dense 行截断显示而非丢弃。

```json
[
  {"start": 2.0, "end": 5.0, "text": "原声台词一句"}
]
```

## background_research.json

可选的背景调研结果（由 Agent 使用任意可用搜索/浏览方式整理）：

```json
{
  "synopsis": "剧情概要",
  "characters": {"角色名": "角色简介"},
  "worldbuilding": "世界观设定",
  "episode_context": "集数上下文",
  "character_details": {
    "角色名": {
      "aliases": ["别名/昵称"],
      "role": "主角|配角|反派|次要角色",
      "relationships": ["与XX是夫妻", "与YY是师徒"]
    }
  },
  "plot_arcs": [
    {"name": "线索名称", "description": "简要描述", "status": "进行中|已解决|伏笔"}
  ],
  "cultural_notes": [
    {"item": "文化梗/典故/时代背景", "explanation": "解释"}
  ]
}
```

> `character_details`、`plot_arcs`、`cultural_notes` 为（可选，新增）字段。仅含 `synopsis`、`characters`、`worldbuilding`、`episode_context` 四个原始字段的旧 JSON 仍然有效。

## narration_review.json

解说评审阶段输出 LLM-as-judge 结果，只有 `verdict` / `summary` / `findings` 三项内容字段，外加运行记录 `evidence_contract`（时间轴、时钟、选中的证据区间、分块数、警告）；有警告时另有顶层 `warnings`，分块评审时另有 `chunked_review` 计数，评审输出无法解析时另有 `parse_error` / `raw`。硬门禁是 `findings` 里的 error（事实矛盾/残句）经 `--require-narration-review` 严格模式拦截；`verdict` 只是建议信号，词表为 `PASS|REVISE|FAIL`，模型偶尔返回的 `OK` 按 `PASS` 记录。

```json
{
  "verdict": "PASS|REVISE|FAIL",
  "summary": "总体判断",
  "findings": [{"segment": 0, "severity": "warning", "category": "weak_hook", "issue": "问题", "fix": "改法"}],
  "evidence_contract": {"schema_version": 1, "timeline": "source", "clock": "source", "selected_ranges": [], "chunk_count": 1, "warnings": []}
}
```

> 若 `work_dir` 提供了 `packaging_plan.json` / `recap_story_plan.json` / `visual_audio_board.json` / `style_card.json`（可选、Agent 撰写），review 会把它们并入评审上下文；缺失时 review 仅基于解说与画面/对白证据评审，行为不受影响。

`cut_output` 解说评审按 `source_id` 映射 `multi_source_manifest.json` 指向的逐源 VLM/ASR，避免项目根目录没有单一 ASR 文件时产生空证据。

## tts_meta.json（partial 失败可见性）

`video-voiceover` 正常输出 `{segments, engine, narration}`。当显式允许 partial TTS（`--allow-partial-tts` / `ALLOW_PARTIAL_TTS=1`）让运行在部分段失败后继续时，失败段不会只埋在日志里，而会写入 `partial` 与 `failures[]`：

```json
{
  "segments": [{"index": 0, "start": 0.0, "end": 1.0, "narration": "第一段。", "audio_path": "tts_segments/narr_000.wav"}],
  "engine": "mimo-tts",
  "narration": "narration.json",
  "partial": true,
  "failures": [{"index": 1, "start": 1.0, "end": 2.5, "text": "第二段。", "error": "network timeout"}]
}
```

> 正常（无失败）运行也会带 `"partial": false, "failures": []`。partial 成片只适合预览，不建议直接发布。

`voice` 记录本次实际使用的音色：`{"provider": "mimo-tts", "model": "mimo-v2.5-tts", "voice_id": "冰糖", "reference": null}`。
用参考音频克隆时 `voice_id` 为 `null`，`reference` 为 `{path, size, mtime_ns}`；Fish Audio 的 `voice_id` 是 reference id，
index-tts 的是 `INDEX_TTS_VOICE`。旧 `tts_meta.json` 没有这个字段时按只知道 `engine` 处理。

## resource_lock.json（本次运行用到的资源）

full / cut 流程（含本地采用路径）合成完成后，video-recap 在 `work_dir` 写出 `resource_lock.json`，汇总运行清单、
`tts_meta.json.voice` 与 `assembly_manifest.json`（BGM 路径、字幕字体）里已有的事实；配置了资源库时，按解析后的路径
（音色按 provider + voice_id）把每项对上已登记的资源，带出授权与声音授权状态。dub 模式不写。

```json
{
  "schema": "video-recap.resource-lock.v1",
  "work_dir": "/abs/work_dir",
  "library": "/abs/library",
  "project": null,
  "templates": [],
  "resources": [
    {"role": "bgm", "path": "/abs/library/resources/bgm/pulse-demo/pulse-demo.wav", "size": 8044, "mtime_ns": 1,
     "detail": {}, "library": {"id": "pulse-demo", "kind": "bgm", "license": "owned", "consent": null}}
  ],
  "attention": [{"code": "license_unknown", "role": "voice", "message": "…"}]
}
```

`role` ∈ `source_video` / `voice` / `bgm` / `subtitle_font`（项目绑定后还会有模板引入的资源）。`attention` 列出需要人确认的项：
`license_unknown` / `license_restricted`、参考音频的 `consent_unknown` / `consent_denied`，以及配置了资源库但没有登记的
`unregistered`。它只提示、不阻断；`final_qc.json` 只承载阻断项，不包含这些提示。

组装后，`assembly_manifest.json.audio_segments[]` 另外记录 `fit_status`、`truncated`、
`truncate_reason`、`placed_audio_duration`、`placed_audio_path`、`source_duck_end`、
`source_restore_at`、`source_handoff_status`、`source_entry_status` 与 `written_start`。组装阶段从不按时间裁旁白尾音：放不下时用
`no_safe_fit` 阻断。`placed_audio_path` 是实际写入 canonical `narration.wav` 的完整逐段 PCM；
`timeline.json`/剪映必须引用它而不是更长的加速前文件。素材时长与序列化后的时间线段长不一致时，
`assembly_qc.json` 用 `timeline_audio_mismatch` 阻断。旁白结束后原声最多再压低 3 秒，等这段时间内的
第一个句末锚点：压到它的 `pause_start`（`source_duck_end`），只在实测停顿内渐强，并于 `source_restore_at`
完成，避免渐强提前泄露上一句尾音。`source_handoff_status` 取值：`sentence_boundary`（已验证锚点）、
`sentence_boundary_unverified`（锚点为 `unverified`，含 schema 1 旧锚点）、`held_to_timeline_end`（3 秒内无锚点且离片尾
不足 3 秒，压到片尾）、`bounded_release`（3 秒内无锚点，在旁白结束处以 `duck_fade_seconds` 渐强回满，不阻断）、
`anchors_unavailable`（原声有讲话但没有可用锚点，阻断）、`no_source_speech`；入口状态在 `source_entry_status`
（`sentence_boundary` / `sentence_boundary_unverified` / `quiet_source` / `non_dialogue_source` / `unverified` /
`unsafe_entry` / `anchors_unavailable` / `paragraph_tightened`）。每个压低段只给首块记入口状态：`quiet_source` 是入口落在
实测安静或没有讲话的地方，`non_dialogue_source` 是入口落在只有语气词（尖叫、"Hi."）的讲话窗口里，两者都不阻断。
段落内与上一块作者留白不超过 1.6 秒的后续块会紧接上一块实际结尾 0.35 秒后播放，最多比写的 `start` 提前 1.2 秒；
旁白校验只检查过写的 `start`，所以提前的这一段不进入原声对白（ASR 对白区间减去实测安静窗口），有对白时最多提前到
最后一段对白结束处。真的提前了的块记 `paragraph_tightened`，`written_start` 是写的 `start`（未提前的块为 `null`）；
这样的块若因间隔超过 `duck_bridge_seconds` 成为新压低段的首块，入口状态改记该段在实际起点上的入口判定，`written_start` 保留。
每次压低延续 = `source_restore_at - actual_place_end`，最大值写在 `assembly_qc.json` 的 `summary.max_source_duck_hold_seconds`，只作信息、不阻断。

## dub_lint.json

Dub 模式下，`dub_script.json` 在 voiceclone **之前**先经过 deterministic lint，把明显不可发布的脚本挡在昂贵的克隆 TTS 之前。空译文、相邻行重叠、时间越界、`room < 0.4s` 等 **error** 会 `verdict=FAIL` 并阻断 render；`fast_speech`、`trim_risk`（有效字数 ≥ 7 字/秒；`dub_brief.md` 要求的目标约 5 字/秒）等是 warning，不阻断。

每行 voiceclone 原始 WAV 会把中文台词、模型/提示等合成设置和参考音频信息写入相邻的 `*.wav.meta.json`。台词与设置完全相等且 WAV 可读取时，dub render 直接复用并在 `dub_manifest.json.lines[].tts_cache` 记录 `hit`；台词、参考音频、模型或提示变化都会重新合成。时长远超读完台词所需（`TTS_MIN_SPEECH_RATE`）的克隆音频视为幻读：按 `TTS_RETRIES` 重试、不写 `.meta.json`，用尽则 render 失败，最后一次被拒的音频留在 `dub_tts/line_NNN_raw.rejected.wav`；旧缓存里的这类 WAV 会重新合成。

```json
{
  "schema_version": 1,
  "verdict": "PASS|FAIL",
  "blocking": false,
  "errors": [],
  "issues": [{"severity": "warning", "code": "fast_speech", "line": 2, "message": "translation is dense (8.4 chars/s)", "start": 1.0, "end": 2.0}],
  "summary": {"lines": 12, "errors": 0, "warnings": 1, "max_chars_per_second": 8.4, "trim_risk_lines": []}
}
```

> dub 渲染阶段在克隆前写 `dub_lint.json`，lint 非 PASS 即中止；dub 只有 `--edit-mode dub` 驱动的准备 / 渲染两个阶段，没有单独的手动 lint / review 入口。
> 最终 `dub_<name>.mp4` 显式输出 48 kHz AAC；不能沿用 `loudnorm` 内部的 96 kHz 分析采样率。
## final_qc.json

渲染后 `recap.py` 写 `final_qc.json`；`scripts/final_qc.py --work-dir <dir>` 也可对已有 work_dir 单独重写。它只对最终 mp4 做本地确定性检查，每条 finding 都是阻断项：

```json
{
  "schema_version": 2,
  "artifact": "final_qc.json",
  "ok": false,
  "blocker_count": 1,
  "finding_count": 1,
  "findings": [{"code": "missing_fps", "message": "final output probe metadata is missing a positive finite video fps",
                "blocking": true, "evidence": {"fps": null, "video_stream": {"codec_type": "video", "codec_name": "h264"}},
                "next_action": "rerender_final_output_with_valid_fps"}],
  "metadata": {
    "work_dir": "/abs/work", "final_output": {"path": "/abs/out/recap_x.mp4", "exists": true, "bytes": 1048576},
    "artifacts": {"assembly_qc.json": {"path": "assembly_qc.json", "exists": true, "bytes": 512,
                  "summary": {"schema_version": 1, "verdict": "PASS", "blocking": false, "blocking_codes": []}}},
    "probe": {"streams": [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "avg_frame_rate": "30/1"}],
              "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "61.2"}},
    "probe_error": null,
    "warnings": [],
    "auto_repair": false
  }
}
```

阻断码：`missing_final_output`、`empty_final_output`、`probe_failed`（ffprobe 不可用或失败）、`missing_video_stream`、`missing_duration` / `invalid_duration`、`missing_codec`、`missing_fps` / `invalid_fps`，以及尾部 2 秒解码失败的 `undecodable_stream`。`next_action` 是可直接执行的修复提示。`ok` 为 `true` 当且仅当 `blocker_count` 为 0；`--require-final-qc` 只读这两个字段，dashboard 另外读 finding 的 `code` / `message` / `blocking`。

`metadata.artifacts` 汇总 `assembly_manifest.json`、`assembly_qc.json`、`visual_qc.json` 的 `schema_version` / `verdict` / `blocking` / `blocking_codes`（不可解析时为 `{"invalid": true}`），只作记录、不转成 final_qc 的阻断项：assembly/visual QC 阻断时 video-assemble 已经非零退出，流程到不了 final_qc。`metadata.probe` 只保留检查用到的流与容器字段（codec、宽高、帧率、时长、采样率等），不保存 `tags` / `disposition` / 文件名，因此从原片带过来的容器标签（如 comment、purl 里的 URL）不会写进报告。

`metadata.warnings` 照抄 `visual_qc.json` 的 `warnings`：不阻断、不计入 `blocker_count`，但 Agent 交付时必须转告用户。目前只有一种：默认烧录字幕而 ffmpeg 缺 libass 时降级为外挂字幕，

```json
{"code": "subtitle_burn_degraded", "reason": "ffmpeg_missing_libass", "delivered": "sidecar_srt",
 "mask_dropped": false, "message": "…", "next_action": "…"}
```

这时成片里没有字幕，字幕在成片旁的同名 `.srt`（路径见 `assembly_manifest.json` 的 `subtitle_sidecar`）；`mask_dropped` 为 `true` 表示原本会画的原字幕遮罩也一并关掉了。`final_qc.py` 打印的摘要 `final_qc.warnings` 列出这些 code，`recap.py` 完成时打印一行警告和外挂字幕路径。
