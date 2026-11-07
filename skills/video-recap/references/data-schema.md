# 数据格式（中间 JSON）

中间产物保存在本次运行的工作目录 `work_dir/` 下。

`vlm_analysis.json`、`asr_result.json`、`asr_writing_chunks.json`、`silence_periods.json`、`timeline_fusion.json` 与 `background_research.json` 归视频理解阶段，格式见 video-understanding 技能的 `data-schema.md`，这里不重复。下面只列其余产物。

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
贴近 `boundary_use` 不为 `none` 的锚点；只有语气词的 ASR 窗口不算讲话（只在紧挨真实对白的一侧保留 1 秒），空文本 ASR 行只是时间证据，cut 门禁与 assemble 的入口检查用同一条规则。否则在 TTS 前用 `interrupts_source_sentence` 阻断，并返回
`suggested_start` / `suggested_end` 与 `source_text_tail`：建议的是入点前后 `max_shift_seconds`（10 秒）内离入点最近的锚点，整块按原时长平移过去后不与相邻块重叠或相接，cut_output 下平移后不超过 `--output-duration`。常规块使用
`source_entry_policy: "sentence_boundary"`；原声语句完整性没有抢断 override。范围内没有这样的锚点时 `suggested_start` 为 `null`，Agent 必须移动、缩短或删除该旁白块。

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

`clip_plan_identity` 是写入时 `clip_plan_validated.json` 的 `{size, mtime_ns}`：video-script 校验要求它与当前文件相等，assemble 要求本文件不早于 `clip_plan_validated.json`。缺失、过期或畸形的
output 证据一律 fail closed，不能回退到原片时钟或信任 Agent 写入的 `overlaps_speech=false`。多来源剪辑的每条映射记录还保留 `source_id` 和原片起止时间。

## narration.json

Agent 撰写的解说词。full 模式下使用原视频时间；**编排 cut 模式（`video-recap --edit-mode cut`）下**，第二次暂停时已经先剪出 `edited_source.mp4`，因此 `narration.json` 必须直接使用剪后成片的 OUTPUT 时间轴（0..成片总时长）；不存在原视频时间→输出时间的旁白映射产物：

```json
[
  {"start": 2.5, "end": 7.0, "narration": "解说文本", "pause_after_ms": 250, "overlaps_speech": true}
]
```

## narration_lint.json

续跑校验 `narration.json` 时生成的预检结果。它检查写稿、时间安全和解说覆盖。`metrics` 为 full 模式下的诊断指标（cut_output 下为空对象），不是要求命中某个旁白比例的创作配额；低覆盖 warning 应回到 `visual_audio_board.json` 检查是否为有意的原声/沉默选择。

```json
{
  "ok": false,
  "error_count": 2,
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

常见 code：`invalid_time`、`out_of_order`、`empty_narration`、`time_overlap`、`over_budget`、`incomplete_sentence`、`slot_too_short`、`under_narrated`、`fragmented_beats`、`no_original_blocks`。

## recap_story_plan.json / visual_audio_board.json（Agent 创作工作产物）

这两个 JSON 是 skill 层的创作决策记录：前者保存导演意图、备选剪辑假设、POV/主线和 change-based beats；后者保存每拍的画面/表演选择、入点/出点、原声锚点、`audio_owner` 与 `narration_job`。完整字段与工作流见 video-script 技能的 `creative-editing-playbook.md`。

CLI 不以它们作为渲染硬门禁，也不新增解析服务；Agent 用它们保证 cut、旁白和声音选择没有偏离同一个创作意图。

## style_card.json（Agent 撰写，可选/按 brief 要求）

`style_card.json` 是表达层契约：由 Agent 根据 `--style`、`--context`、素材证据、ASR 和用户偏好信号综合撰写。`--style` 是 freeform verbatim guidance（原样自由文本指导），不是枚举、preset、switch，也不是一组可穷举风格名；不要把它翻译成固定档位。它是当前生效的表达设定：用户对声音、节奏、字幕阅读或禁忌提出新反馈后，直接更新原文件并移除过期偏好，不要只改 `narration.json`。

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

内容锁定后的可选包装层契约，没有脚本读取。JSON object，字段：`title`、`cover_frame`（`{time, reason}`）、`first_line`、`viewer_promise`、`selling_points`（数组）、`packaging_notes`。Agent 自检时用它核对“包装承诺”和正文前 15 秒是否对齐；不反过来驱动故事取舍，也不覆盖 `style_card.json` 的声音、节奏或表达规则，包装需要的承诺仍要用素材证据兑现。

## narration_lint.json 的 deslop_qc（CLI 生成，报告型 QC）

`narration_lint.json` 的 `deslop_qc` 字段由本地 deterministic scanner 生成，Agent 不手写；不再单独写 `deslop_qc.json`。它输出可读性报告，区分下面两类结果；不判断文本是否由 AI 生成，也不自动改写。修改仍由 Agent/人工根据报告回到 `narration.json`、`style_card.json` 或字幕源里处理。

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

切镜吸附先执行，句末吸附随后执行，保证视觉边界不会覆盖声音安全边界；被切镜吸附移进讲话、句末吸附又修不回来的边界撤回原位置（原位置能过门禁时），`qc.boundary_status.shot_snaps` 对应项记 `reverted_unsafe`。
最后把入点对齐到源帧网格、时长对齐到整数输出帧，门禁检查对齐后的边界；对齐前后的时间写在 `qc.boundary_status.frame_snaps`，输出帧率与各源帧率写在 `qc.frame_grid`。
每段的 `frame_count` 是它渲染的帧数，`edited_source.mp4` 因此是恒定帧率；`output_start/end` 与 `total_duration` 按累计 `frame_count` 计算，与渲染的帧时间一致。

多视频 validated clip 另外保留 `source_id` 与 `source_path`，供 pass2 brief、timeline 和剪映导出追溯原素材。

## material library（可选，grep 复用）

`--material-library-dir <dir> --save-materials` 会把每个源视频的已分析小文件复制到 `<dir>/materials/<material_id>/`，不复制原始媒体。`--use-materials` 会在源文件路径、`source_video_identity`（`{size, mtime_ns}`）和分析 `settings`（`context`、`scene_threshold`、`style`、`edit_mode`、`target_duration`、`skip_asr`、`consolidate`）都相等时把这些 JSON/MD 产物恢复到当前 per-source `work_dir`；`--edit-mode` 不同也算不相等。Agent 已写进 `work_dir` 的 `background_research.json` 不会被恢复删除，比条目里的新时也不被覆盖；有故事索引的条目恢复时写 `understanding_index.json.meta.json`，绑定恢复后的 `vlm_analysis.json`，所以 cut 第二阶段重建的 brief 仍带索引。

```text
.video-materials/
  materials_index.jsonl          # 追加式 grep journal；旧行可保留为历史
  materials/<material_id>/
    material.json                # 当前权威 metadata
    material.md                  # grep 友好摘要
    artifacts/scenes.json
    artifacts/asr_result.json
    artifacts/vlm_analysis.json
    artifacts/understanding_index.json
    artifacts/consolidation.status.json  # 恢复后 brief-only 仍能提示 consolidate 失败/缺索引
```

`asr_timing_evidence.json` 不入库：它按 `{size, mtime_ns}` 绑定 `asr_result.json` 与 `audio.wav`，
恢复时前者被脱敏重写、后者不复制，恢复出的副本只会被判为 `MISSING_OR_STALE`。

`materials_index.jsonl` 每次保存追加一行，字段包括 `schema_version`, `event`, `material_id`, `source_name`, `source_path`, `source_video_identity`, `summary`, `tags`, `material_dir`, `updated_at`；`material.json` 另外记录分析 `settings` 字典与每个产物的 `bytes`。当前权威状态始终以 `materials/<material_id>/material.json` 为准。MVP 只承诺 `grep -R "关键词" <library>` 这类文件检索；没有 DB、embedding 或语义搜索。

保存时会对凭证形态（`tp-`/`sk-`/`gh*_`/`AKIA`/JWT 与 `KEY=VALUE` 赋值）和凭证命名的 JSON key 做脱敏，但这只是**尽力而为**的兜底，不是保证：陌生格式的密钥仍可能漏过。请从源头避免把密钥写进分析产物——key 从环境变量/`.env` 读取，不需要落进 scenes/ASR/VLM/summary 等 JSON。

## original_subtitles.json / user_subtitles.{json,srt,ass}（可选，原声留白字幕）

遮罩覆盖原声留白或用户字幕明确要求替换时，assemble 才为留白生成原声对白字幕，并用 `「」` 与旁白区分。来源优先级（高→低）：

1. **`work_dir/user_subtitles.json`**（用户自带，优先使用）— 数组 `[{start,end,text}]` 默认按**成片 OUTPUT** 时间轴直接使用；也可写成 `{"timeline": "source"|"output", "lines": [...]}`，`source` 表示按**原片**时间轴给出，由 assemble 依 `clip_plan_validated.json` 映射到成片。
2. **`work_dir/user_subtitles.srt` / `.ass`**（用户自带）— 默认按**原片**时间轴解析后映射到成片。
3. **`work_dir/original_subtitles.json`**（Agent 校对，cut pass2 写）— OUTPUT 时间轴 `[{start,end,text}]`，订正 ASR 错字/人名、只写留白里真正出声的句子。
4. **ASR 兜底** — 无上述文件时，用 `asr_result.json` 按留白粗略映射（中点估时，可能偏多偏乱）。

来源 1–3 直接采用声明的时间区间：每条按句**区间裁剪**落到所覆盖的留白边界（跨边界会拆分），不走 ASR 兜底的中点估时；过密行截断显示而非丢弃。时间声明仍需对照实际声音核准，不能仅凭文件来源认定已精确对齐。

```json
[
  {"start": 2.0, "end": 5.0, "text": "原声台词一句"}
]
```

## tts_meta.json（partial 失败可见性）

`video-voiceover` 正常输出 `{segments, engine, narration}`。当显式允许 partial TTS（`--allow-partial-tts`）让运行在部分段失败后继续时，失败段不会只埋在日志里，而会写入 `partial` 与 `failures[]`：

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

full / cut 流程合成完成后，video-recap 在 `work_dir` 写出 `resource_lock.json`，汇总运行清单、
`tts_meta.json.voice` 与 `assembly_manifest.json`（BGM 路径、字幕字体）里已有的事实；配置了资源库时，按解析后的路径
（音色按 provider + voice_id）把每项对上已登记的资源，带出授权与声音授权状态。

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

## assembly_manifest.json 的 audio_segments[]

每段记录 `fit_status`、`truncated`、`truncate_reason`、`placed_audio_duration`、`placed_audio_path`、`source_duck_end`、
`source_restore_at`、`source_handoff_status`、`source_entry_status` 与 `written_start`。放置、压低与恢复的规则见 video-assemble 技能的合成规则；
`placed_audio_path` 是实际写入 canonical `narration.wav` 的完整逐段 PCM，`timeline.json`/剪映必须引用它而不是更长的加速前文件。

`source_handoff_status` 取值：`sentence_boundary`（已验证锚点）、`sentence_boundary_unverified`（锚点为 `unverified`，含 schema 1 旧锚点）、
`held_to_timeline_end`（3 秒内无锚点且离片尾不足 3 秒，压到片尾）、`bounded_release`（3 秒内无锚点，在旁白结束处以 `duck_fade_seconds` 渐强回满，不阻断）、
`anchors_unavailable`（原声有讲话但没有可用锚点，阻断）、`no_source_speech`。

`source_entry_status` 每个压低段只给首块记，取值：`sentence_boundary` / `sentence_boundary_unverified` / `unverified` / `unsafe_entry` / `anchors_unavailable` /
`quiet_source`（入口落在实测安静或没有讲话的地方，不阻断）/ `non_dialogue_source`（入口落在只有语气词的讲话窗口里，不阻断）/
`paragraph_tightened`（段落内后续块真的提前了，`written_start` 是写的 `start`，未提前的块为 `null`；这样的块若成为新压低段的首块，改记该段在实际起点上的入口判定，`written_start` 保留）。

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

`metadata.warnings` 照抄 `visual_qc.json` 的 `warnings`：不阻断、不计入 `blocker_count`，但 Agent 交付时必须转告用户。目前只有 `subtitle_burn_degraded`，字段为 `reason`、`delivered`、`mask_dropped`（为 `true` 表示原本会画的原字幕遮罩也一并关掉了）、`message`、`next_action`，交付时怎么说见 `SKILL.md` §4.6；外挂字幕路径见 `assembly_manifest.json` 的 `subtitle_sidecar`。`final_qc.py` 打印的摘要 `final_qc.warnings` 列出这些 code。
