# 数据格式（中间 JSON）

全流程各阶段的中间产物统一存放在本次运行的工作目录 `work_dir/` 下。

## vlm_analysis.json

逐场景的 VLM 画面观察结果，顶层为数组：

```json
[
  {
    "scene_id": 1,
    "start": 5.0,
    "end": 15.0,
    "description": "男子推门闯入卧室，神色戒备",
    "depth_analysis": "两人表面客套试探，实则都在盯对方手里的底牌。",
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
| `description` | string | 画面简述（不超过 80 字） |
| `depth_analysis` | string | 深层分析（情绪变化、人物关系与潜台词） |
| `frame_facts` | object | 逐帧观察事实，键为时间戳字符串 |

## asr_result.json

语音转写结果数组：

```json
[
  {"start": 0.0, "end": 3.5, "text": "What are you doing here?"}
]
```

## asr_timing_evidence.json

记录 `speech_boundary_anchors.json` 得以采信的本地计算证据链：包含锚点生成器版本、`asr_result.json` 与 `audio.wav` 的文件身份（`{size, mtime_ns}`）、声学停顿检测配置，以及每个 ASR 分片窗口内停顿与句末标点的匹配统计。当目录内存在 `audio.wav` 时，理解阶段会校验该证据是否与当前音频及转写结果吻合；若缺失或过期（如旧缓存目录）则自动重算，确保句末锚点始终有据可查。

## silence_periods.json

检测出的静音与低能量窗口列表：

```json
[
  {"start": 2.0, "end": 8.5, "duration": 6.5, "has_speech": false}
]
```

`has_speech` 表示该窗口是否与 ASR 检出的对白时间重叠；下游写稿与编排只把 `has_speech=false` 的区间当作可无遮挡插入解说的安静窗口。

## speech_boundary_anchors.json

原声句末安全切入点。脚本将 ASR 句末标点的估算时间吸附到实测的短声学停顿上；之所以与 `silence_periods.json` 分开存，是因为约 0.1–0.5 秒的气口虽不够塞下一整段旁白，却是压低原声、切入旁白而不斩断原话的最佳入口：

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

由于 ASR 时间只精确到分片窗口，句末真实落点只能在窗口内估算。`timing_bound_seconds` 记录停顿点到所属 ASR 窗口两端的较远距离（即句末真实位置的最坏误差上界）；`confidence` 取它与 `alignment_error` 中的较大值分级（`≤0.6` 为 `high`，`≤1.2` 为 `medium`，其余为 `low`）。`boundary_use` 决定下游门禁是否采信该锚点：

- `verified`：`high` 或 `medium` 置信度的锚点；
- `unverified`：虽然 ASR 窗口偏粗，但声学停顿吸附误差 `≤1.2s`，仍可作为门禁切入点（简报中会标出 `unverified ±N s`）；
- `none`：误差过大，不可用作切点。

缺少 `boundary_use` 字段的旧版（schema 1）锚点，下游会将 `high`/`medium` 归为 `unverified`、其余归为 `none`。理解阶段若发现 `schema_version` 小于 2 且目录内存在 `audio.wav`，会基于音频与转写结果重新生成 schema 2 锚点；若是从素材库恢复、没有本地音频文件，则保留原文件并由下游按上述兼容规则读取。剪后成片时钟下的 `speech_boundary_anchors_output.json` 中，`time`、`pause_start`、`pause_end` 与 `expected_time` 均为成片输出时间，对应的原片秒数保存在 `source_*` 字段中。

当某段旁白的 `overlaps_speech=true` 且并非从 0 秒冷开场时，`narration` 校验要求其 `start` 必须贴近 `boundary_use` 不为 `none` 的句末锚点。判断切入点是否踩进原声讲话时，校验、剪辑与合成共用同一套语气词豁免规则：只含单字语气词的 ASR 窗口（如“啊！”、“Hi.”）不算完整讲话，仅在紧挨真实对白的一侧留出 1 秒保护区；空文本或纯空白的 ASR 行只作分窗记录，不算讲话也不留保护；三处统一读取 `asr_result.json`。

一旦旁白起点踩进原声句子中间，校验会在送 TTS 前以 `interrupts_source_sentence` 报错拦截，并给出 `suggested_start`、`suggested_end` 与 `source_text_tail` 供 Agent 调整：推荐位置取入点前后 `max_shift_seconds`（10 秒）内最近的安全锚点，要求整段旁白按原时长平移过去后仍处在相邻两段之间、与前后段间隔均大于 0.15 秒（避免粘连成同一段），且在 `cut_output` 模式下不超出成片总时长（距离相等时取靠后的锚点）。常规解说段统一使用 `source_entry_policy: "sentence_boundary"`，系统不提供强行打断原话的绕过开关；若 10 秒范围内找不到合适锚点，`suggested_start` 返回 `null`，Agent 需手动挪位置、缩减字数或删掉该段。

`cut` 模式第二阶段会额外写出 `speech_boundary_anchors_output.json`，把句末锚点、ASR 讲话区间和安静窗口统一换算到剪后成片（OUTPUT）时间轴上。换算后的锚点绝不越出所属片段边界：若某个原片锚点落在片段入点前或出点后 0.05 秒容差内、且没有被相邻片段播放，就会吸附到该片段的入点或出点上（落在 `[0, 成片时长]` 之外的直接丢弃），同时在 `source_time` 中保留原片实测秒数：

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

其中 `clip_plan_identity` 记录生成该文件时 `clip_plan_validated.json` 的 `{size, mtime_ns}`：旁白校验阶段要求两者完全一致，合成阶段则要求该文件修改时间不早于 `clip_plan_validated.json`。由于剪辑阶段在剪辑计划未变时重跑不会改写 `clip_plan_validated.json`，第二阶段生成的输出时间证据在后续续跑时依然有效；一旦重新修改并保存了 `clip_plan.json`，则会触发全套重写。缺失、过期或损坏的输出时间证据一律报错停机（fail closed），绝不回退到原片时钟，也不会盲信 Agent 手写的 `overlaps_speech=false`。多源剪辑下的每条映射还会额外保留 `source_id` 与原片起止时间。

## narration.json

Agent 撰写的旁白解说稿。`full` 模式填原片秒数；**编排 `cut` 模式（`video-recap --edit-mode cut`）**在第二次暂停时已经先剪出了 `edited_source.mp4`，因此 `narration.json` 必须直接填写剪后成片的 OUTPUT 时间戳（`0 .. 成片总时长`），不存在从原片时间自动映射成片时间的旁白转换步骤：

```json
[
  {"start": 2.5, "end": 7.0, "narration": "相恋五年揣着户口本去求婚，推门却撞见他正搂着千金谈联姻。", "pause_after_ms": 250, "overlaps_speech": true}
]
```

## narration_lint.json

续跑校验 `narration.json` 时写出的确定性预检报告，涵盖时间边界安全、原声避让与字数预算：

```json
{
  "ok": false,
  "error_count": 2,
  "warning_count": 1,
  "errors": [
    {"level": "error", "index": 2, "code": "time_overlap", "message": "Segment overlaps the previous narration segment"},
    {"level": "error", "index": 5, "code": "over_budget", "budget_chars": 28, "limit_chars": 35, "actual_chars": 42, "over_chars": 7, "tts_overhead_seconds": 0.45}
  ],
  "warnings": [
    {"level": "warning", "index": 0, "code": "over_budget", "budget_chars": 28, "actual_chars": 42}
  ]
}
```

推荐字数 `budget_chars` 会先从时间窗里扣掉 `tts_overhead_seconds`（约 0.45 秒，即每次 TTS 合成自带的首尾静音）再折算字数，因此比写稿简报窗口上直接标出的字数少一两个字。`full` 模式下凡字数超过推荐值 1.25 倍（`limit_chars`）的段落都会报 `over_budget` error（校验器绝不会越俎代庖替 Agent 删字）；略超推荐值但未越过 1.25 倍硬上限、以及 `cut_output` 模式下的超字数则记为 warning。所有解说段必须按 `start` 严格升序排列，否则报 `out_of_order` error。

常见错误与警告码：`invalid_time`、`out_of_order`、`empty_narration`、`time_overlap`、`over_budget`、`incomplete_sentence`（残句硬阻断）、`interrupts_source_sentence`（抢断原话）、`unsafe_source_release`（旁白结束在角色半句台词中间突然放回原声）、`weak_cold_open_silence`（总长 `>= 8s` 时片头超过 `2.5s` 既无旁白也无原声对白）、`excessive_dead_air`（连续超过 `5.0s` 既无旁白也无原声对白）、`generic_character_label`（`background_research.json` 已提供具名角色时仍使用“黑衣男子”“年轻女子”等笼统外貌词）、`placeholder_leakage`。

## recap_story_plan.json / visual_audio_board.json（Agent 创作工作产物）

这两份 JSON 是创作阶段的拍板记录：`recap_story_plan.json` 保存导演意图、候选剪辑方案、主视角（POV）/主线与逐拍局势变化；`visual_audio_board.json` 保存每一拍选定的画面表演瞬间、出入点理由、原声锚点、声音主导方（`audio_owner`）与旁白任务（`narration_job`）。完整字段定义见 `./creative-editing-playbook.md`。

渲染管线不拿它们当硬门禁，也不额外部署解析服务；Agent 靠它们沉淀可复查的创作决定，保证选镜、原声留白和旁白词从头到尾咬住同一个故事意图。

## style_card.json（Agent 撰写，可选/按 brief 要求）

`style_card.json` 记录本片的声音人设与表达边界，由 Agent 结合 `--style`、`--context`、画面与对白证据及用户反馈综合写成。注意 `--style` 是原封不动传入的自由文本要求，并非固定预设、枚举值或分类标签，千万别把它硬套进某几个死板档位。每当用户对口播腔调、快慢节奏、字幕断行或忌讳词提出修改意见时，应先更新这张风格卡并删掉过时设定，再动手改 `narration.json`。

该文件只管讲述口气、呼吸节奏、收尾落点与用词规矩（字段可随项目自由增删，下游仅按普通 JSON 对象读取），不管外部标题、封面字或宣发卖点：

```json
{
  "voice": "像知根知底的说书人盯场面，语气克制但句句扎在言行反差上，不讲空洞大道理",
  "pacing": "前 6 秒直接甩出身份错位钩子，每一拍一口气把‘处境→动作→当场后果’讲透，关键原声台词前主动让位",
  "payoff_intent": "先看她当众扇耳光划清界限的狠劲，再用夜里看雪人和病中梦话掀开藏不住的真心",
  "subtitle_read_posture": "TTS 朗读稿保持自然口语长短句，字幕按阅读宽度切分，绝不为了迁就字幕分行把口播稿剁碎",
  "evidence_intent": ["用具体手头动作代替抽象心理总结", "关键狠话与耳光声完整留给原声"]
}
```

## packaging_plan.json（Agent 撰写，可选）

`packaging_plan.json` 是正片内容锁死后按需编写的外层宣发包装方案，涵盖常驻标题、封面帧选择、开场首句、看点承诺与发布文案备注。它用来帮 Agent 交付复核时对齐“外层吆喝的看点”和“正片前 15 秒实际演出的内容”有否货不对板，绝不能反过来绑架正片剪辑去迎合标题党。

它也不替代 `style_card.json` 的口播文风设定；凡是包装层承诺了的爽点或反转，正片里都必须有实打实的画面与原声证据兑现：

```json
{
  "title": "死去三年的白月光挽着亲哥进门，逼我当众改口叫大嫂",
  "cover_frame": {"time": 12.4, "reason": "沈清欢挽着大哥亮相、裴钧庭瞳孔骤缩的同框对峙帧"},
  "first_line": "三年前他以为沈清欢已经死在火海，三年后她却挽着他亲哥回来，成了人人得叫一声的未来大嫂。",
  "viewer_promise": "60 秒看懂一对昔日恋人如何在叔嫂名分下嘴硬试探、步步失守",
  "selling_points": ["死而复生变大嫂的身份反差", "暗处强吻与耳光划界的原声高光"],
  "packaging_notes": "横屏成片搭配上下双行常驻标题，花字只点破口是心非，不复读对白"
}
```

## multi_source_manifest.json（多视频 cut）

多视频剪辑模式下，项目根目录下的 `work_dir/multi_source_manifest.json` 是编排、剪辑与合成阶段共用的多源清单。其中 `source_id` 根据源文件名主干与字节大小生成（格式为 `src_<stem>_<size>`，同名同大小的后续文件按顺序追加 `_2`、`_3`）；`source_video_identity` 记录各源文件的 `{size, mtime_ns}`，供断点续跑时逐项核验：

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

`cut` 模式下由 Agent 编写的选镜计划，顶层写数组或 `{"clips": [...]}` 均可。默认各片段在同源内不得重叠，以免同一段原片画面被重复映射到多个成片位置：

```json
{
  "target_duration": "10m",
  "clips": [
    {"start": 12.0, "end": 38.0, "reason": "b01 | hook | knowledge: unknown→threat | POV=沈曼 | 撞见未婚夫搂着千金谈联姻 | 入点=推门看清两人动作 | 出点=转身攥紧户口本"}
  ]
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `start` | float | 原视频片段开始秒数 |
| `end` | float | 原视频片段结束秒数 |
| `reason` | string | 选入该片段的节拍、变化、POV 与出入点依据 |

多视频 `cut` 的 `clip_plan.json` 必须为每个片段标明 `source_id`（重叠检查按 `source_id` 分别计算，不同源视频的相同秒数互不冲突）：

```json
{
  "target_duration": "10m",
  "clips": [
    {"source_id": "src_episode1_734003200", "start": 12.0, "end": 38.0, "reason": "b01 | setup | knowledge: unknown→clue | POV=沈曼 | 门外听见联姻实情 | 入点=脚步停在门边 | 出点=听完冷笑转身"},
    {"source_id": "src_episode2_689110016", "start": 4.0, "end": 22.0, "reason": "b02 | payoff | power: passive→counterattack | POV=沈曼 | 转头与死对头同日领证 | 入点=红本盖章落定 | 出点=抬头迎上视线"}
  ]
}
```

## clip_plan_validated.json

剪辑脚本校验并吸附 `clip_plan.json` 后写出的执行清单，补齐了剪后成片输出时间轴与帧数：

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
      "reason": "b01 | hook | knowledge: unknown→threat | POV=沈曼 | 撞见未婚夫搂着千金谈联姻 | 入点=推门看清两人动作 | 出点=转身攥紧户口本"
    }
  ],
  "total_duration": 26.0,
  "target_duration": 600.0
}
```

文件中的 `qc.boundary_status.sentence_checks` 会逐一记录每个片段入点与出点的语音安全状态（`safe`、`unchecked` 或 `blocking`；落在 `unverified` 句末锚点上的切点记为 `safe`，原因标为 `unverified_sentence_boundary`）。只要理解阶段已有 ASR 对白时间，任何既没落在原片首尾、句末锚点或静音窗口内，也不属于同源相邻片段无缝顺接（计划中紧挨着且 `clip_id` 连续）的切点，都会记入 `qc.blocking[].code=unsafe_clip_sentence_boundary` 并阻断剪辑。被拦截的切点会附带 `nearest_safe` 字段，直接给出前后 5 秒内已经对齐好帧网格、重跑时也不会被切镜吸附拉回对白内部的最近安全切点秒数（若 5 秒内无安全点则为 `null`）：

```json
{"clip_id": 0, "edge": "start", "time": 3.0, "status": "blocking", "reason": "inside_detected_speech",
 "nearest_safe": {"before": {"time": 0.0, "reason": "source_start", "delta": -3.0},
                  "after": {"time": 4.8, "reason": "sentence_or_quiet_boundary", "delta": 1.8}}}
```

边界吸附按三步顺序执行，确保视觉防闪切绝不破坏原声完整：

1. **先做镜头硬切吸附（shot snap）**，避开原片硬切边缘的残留闪帧（片段最短不低于 `0.8s`）；
2. **再做句末与静音吸附（sentence snap）**：出点吸附到句末停顿起点后，会在静音窗口内自动顺延最多 `0.22s` 反应/呼吸气口（绝不跨越原片硬切点），消除话音未落就硬切的突兀落点；若附近没有停顿窗、句末吸附修不回来，且某条原本能过语音门禁的切点是被第一步硬切吸附推进对白里的，脚本会在不暴露 `<= 0.20s` 闪帧的前提下把它退回原位（记下 `start_action`/`end_action: "reverted_unsafe"`），若退回原位会重新暴露 `<= 0.20s` 闪帧则直接报 `shot_speech_conflict` 阻断；
3. **最后对齐到源视频帧网格与整数输出帧（frame snap）**，并在渲染 `edited_source.mp4` 后自动运行 `shot_review` 写入 `shot_review.json`（检出 `<= 0.20s` 的 `flash_slivers` 直接阻断）：微调幅度不超过一帧，优先选语音门禁仍判安全、仍在停顿区内且不丢失必保画面证据（`required_evidence`）的一侧；若两侧同样安全，则选不跨越原片硬切的一侧（入点落在切点上或之后，出点落在切点上或之前），彻底杜绝夹带一帧隔壁镜头。

每段实际渲染出的帧数记入 `frame_count`，从而保证拼接出的 `edited_source.mp4` 为严格恒定帧率（CFR）；帧对齐前后的秒数明细记在 `qc.boundary_status.frame_snaps`，全局帧率汇总记在 `qc.frame_grid`：

```json
{"output_frame_rate": "25", "frame_count": 650, "duration": 26.0,
 "sources": [{"source_id": null, "path": "/abs/source.mp4", "frame_rate": "25/1",
              "video_start_offset": 0.0, "start_snapped": true}]}
```

单源剪辑时 `output_frame_rate` 沿用源视频的 `r_frame_rate`（若隔行扫描素材报的是场频，或可变帧率素材的标称帧率高出 `avg_frame_rate` 1.5 倍以上，则改取实际平均帧率）；多源混剪时统一使用目标画布帧率（NTSC 帧率写作 `30000/1001`）。`output_start/end` 与 `total_duration` 均按累计 `frame_count` 精确换算，与渲染出的画面帧钟分毫不差。若某源视频帧率未知（报 `0/0` 或超过 120），则标记 `start_snapped: false`，入点保持不动、仅将片段时长对齐到整数输出帧。

多视频 `clip_plan_validated.json` 还会额外保留来源标识字段，方便第二阶段简报、`timeline.json` 与剪映草稿反查原片：

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
      "reason": "b01 | hook | knowledge: unknown→threat | POV=沈曼 | 撞见未婚夫搂着千金谈联姻 | 入点=推门看清两人动作 | 出点=转身攥紧户口本"
    }
  ]
}
```

## material library（可选，grep 复用）

传入 `--material-library-dir <dir> --save-materials` 会把每个源视频已完成分析的轻量 JSON/MD 文件归档到 `<dir>/materials/<material_id>/` 下（不复制体积庞大的源视频与音频）。之后传入 `--use-materials` 时，只要源文件路径、文件指纹 `source_video_identity`（`{size, mtime_ns}`）及分析配置 `settings` 完全吻合，就会直接把这些分析产物恢复到当前源的 `work_dir` 中：

```text
.video-materials/
  materials_index.jsonl          # 追加式检索日志；旧行保留作历史记录
  materials/<material_id>/
    material.json                # 当前最新元数据
    material.md                  # 方便 grep 阅读的文本摘要
    artifacts/scenes.json
    artifacts/asr_result.json
    artifacts/vlm_analysis.json
    artifacts/speech_boundary_anchors.json
    artifacts/background_research.json
```

注意 `asr_timing_evidence.json` 不会归档入库：因为它靠 `{size, mtime_ns}` 强绑定原始 `asr_result.json` 与 `audio.wav`，而归档时会脱敏重写前者、且不拷贝后者，就算存进去恢复出来也只会被判为过期（`MISSING_OR_STALE`）。

每次归档都会往 `materials_index.jsonl` 末尾追加一行（含 `schema_version`、`event`、`material_id`、`source_name`、`source_path`、`source_video_identity`、`summary`、`tags`、`material_dir`、`updated_at`），而 `material.json` 额外记下分析参数 `settings` 与各文件字节数；当前有效状态一律以 `materials/<material_id>/material.json` 为准。检索时直接用 `grep -R "关键词" <library>` 即可。

归档保存时会自动扫描常见密钥格式（如 `tp-`/`sk-`/`gh*_`/`AKIA`/JWT 及 `KEY=VALUE` 赋值）和敏感字段名并做脱敏替换，但这只是兜底防呆：请从源头保证 API key 只走环境变量或 `.env`，不要写进上下文提示词或分析文件里。

## original_subtitles.json（可选，原声留白字幕）

当成片留有原声高光段落（`>= 1.5s` 的无旁白留白窗口），且开启底部遮罩盖住了原片硬字幕、或 Agent 显式核准了原声字幕时，可在 `work_dir/original_subtitles.json` 中按**成片 OUTPUT 时间轴**写出校对后的原声对白，合成阶段会在留白窗口内渲染带 `「」` 引号的原声台词字幕（不再使用粗糙的 ASR 15 秒窗口中点兜底，避免错字或时间漂移污染成片）：

```json
[
  {"start": 2.0, "end": 5.0, "text": "原声台词一句"}
]
```

合成器直接采用文件声明的成片起止时间，按留白区间边界做裁剪（横跨多段留白时自动切分），遇到字数过密的行只做单行截断而不整条丢弃。渲染后仍应亲耳核对字幕与原声是否合拍。

## background_research.json

可选的项目背景资料（由 Agent 通过搜索或读取文档整理写入）：

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

> `character_details`、`plot_arcs`、`cultural_notes` 为可选扩展字段；仅含 `synopsis`、`characters`、`worldbuilding`、`episode_context` 四个基础字段的旧版 JSON 同样有效。

## tts_meta.json（partial 失败可见性）

配音阶段正常跑完会写出 `{segments, engine, narration}`。若显式开启了部分容错（`--allow-partial-tts`）允许个别段落合成失败后继续往下走，失败段的信息会明确记入 `partial: true` 与 `failures[]` 数组，绝不只藏在终端日志里：

```json
{
  "segments": [{"index": 0, "start": 0.0, "end": 1.0, "narration": "第一段。", "audio_path": "tts_segments/narr_000.wav"}],
  "engine": "mimo-tts",
  "narration": "narration.json",
  "partial": true,
  "failures": [{"index": 1, "start": 1.0, "end": 2.5, "text": "第二段。", "error": "network timeout"}]
}
```

> 全部成功时也会固定写出 `"partial": false, "failures": []`。带有失败段的成片仅供内部排障预览，切勿直接对外发布。

顶层 `voice` 字段记录本次实际采用的配音音色：`{"provider": "mimo-tts", "model": "mimo-v2.5-tts", "voice_id": "冰糖", "reference": null}`。使用参考音频克隆时 `voice_id` 为 `null`、`reference` 记录 `{path, size, mtime_ns}`；Fish Audio 下 `voice_id` 为其 reference id，`index-tts` 下则为 `INDEX_TTS_VOICE`。不含 `voice` 字段的旧版文件按仅知晓 `engine` 处理。

## resource_lock.json（本次运行用到的资源）

每次 `full` 或 `cut` 流程合成完毕后，编排器都会在 `work_dir` 下写出 `resource_lock.json`，把运行清单、`tts_meta.json.voice` 与 `assembly_manifest.json` 里的源视频、配音音色、BGM 路径与字幕字体汇总到一处；若配置了资源库，还会按解析后的实际文件路径（音色按 `provider + voice_id`）自动对上库内登记条目，一并带出版权与声音授权状态：

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

其中 `role` 取值为 `source_video`、`voice`、`bgm`、`subtitle_font`（使用 `--project` 绑定时还会包含模板引用的图片等资源）。`attention` 数组列出需要人工过目的提醒项：版权未确认或受限（`license_unknown` / `license_restricted`）、参考音色授权未确认或被拒（`consent_unknown` / `consent_denied`），以及启用了资源库但该文件尚未入库登记（`unregistered`）。这些提醒仅作合规审计提示，不阻断渲染，也不会写入 `final_qc.json`。

## assembly_manifest.json 的 audio_segments[]

音画组装完成后，`assembly_manifest.json.audio_segments[]` 会逐段记录落点与压低（ducking）明细：`fit_status`、`truncated`、`truncate_reason`、`placed_audio_duration`、`placed_audio_path`、`source_duck_end`、`source_restore_at`、`source_handoff_status`、`source_entry_status` 与 `written_start`。注意以下核心机制：

- **绝不裁切旁白尾音**：若某段配音在允许的倍速上限内仍塞不进可用窗口，合成器直接以 `no_safe_fit` 报错阻断。`placed_audio_path` 指向实际写入 `narration.wav` 的最终逐段音频，`timeline.json` 与剪映草稿必须引用该文件，一旦引用了加速前的原始长音频会以 `timeline_audio_mismatch` 阻断。
- **原声回升贴紧句末停顿与片尾平滑收音**：一段旁白读完后，原声最多可保持压低状态 3 秒，等待这 3 秒内出现的第一个句末锚点——一直压到该锚点的停顿起点 `pause_start`（记为 `source_duck_end`），再在实测停顿区间内平滑渐强，到 `source_restore_at` 恢复原音量，从而避免渐强过早把上一句原话的尾巴漏出来。`source_handoff_status` 记录回升方式：`sentence_boundary`（命中已验证锚点）、`sentence_boundary_unverified`（命中 `unverified` 锚点）、`held_to_timeline_end`（3 秒内无锚点且距片尾不足 3 秒，直接压到片尾）、`bounded_release`（3 秒内无锚点但处于非对白或静音窗口，平滑回升）、`unsafe_mid_sentence_release`（3 秒内无锚点且落在角色半句对白中间、剩余对白 `> 0.35s`，直接报 `unsafe_source_handoff` 阻断）、`anchors_unavailable`（原声有人说话但无可用锚点，直接阻断）或 `no_source_speech`。成片尾部默认对原声轨与 BGM 轨施加 `350ms` 平滑淡出（`ending_audio_fade_ms`），杜绝片尾声音戛然而止。
- **切入状态**：每个连续压低区间的首块会记录切入状态 `source_entry_status`（`sentence_boundary`、`sentence_boundary_unverified`、`quiet_source`、`non_dialogue_source`、`unverified`、`unsafe_entry` 或 `anchors_unavailable`），其中落在静音区的 `quiet_source` 与落在纯语气词窗口的 `non_dialogue_source` 均安全放行。单次压低延续时长（`source_restore_at - actual_place_end`）的最大值记入 `assembly_qc.json` 的 `summary.max_source_duck_hold_seconds` 供参考。

## final_qc.json

成片渲染完毕后，`recap.py` 会在工作目录写出 `final_qc.json`（也可对已有目录单独跑 `scripts/final_qc.py --work-dir <dir>` 重新生成）。它只对最终交付的 MP4 文件做本地确定性体检，检出的每一条 `finding` 都是硬阻断项：

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

阻断码涵盖：文件不存在或为空（`missing_final_output` / `empty_final_output`）、`ffprobe` 探测失败（`probe_failed`）、缺少视频流（`missing_video_stream`）、时长缺失或非法（`missing_duration` / `invalid_duration`）、编码格式缺失（`missing_codec`）、帧率缺失或非法（`missing_fps` / `invalid_fps`）、片尾最后 2 秒解码失败（`undecodable_stream`），以及上游已绑定的组装门禁阻断（`assembly_qc_blocking`）和剪辑成片残镜闪帧（`flash_frame_sliver`）。每条都附有可直接执行的修复动作 `next_action`。当且仅当 `blocker_count` 为 0 时 `ok` 为 `true`（`recap.py` 默认开启 `--require-final-qc`，依据这两个字段决定退出码；看板还会读取每条问题的 `code`、`message` 与 `blocking`）。

`metadata.artifacts` 汇总了 `assembly_manifest.json`、`assembly_qc.json`、`visual_qc.json` 与 `shot_review.json` 的核心摘要（无法解析时记为 `{"invalid": true}`）。`metadata.probe` 只摘录检查所需的流与容器规格（编码、宽高、帧率、时长、采样率等），主动剥离了原片容器可能夹带的 `tags`、`disposition` 和文件名，防止原片注释或下载链接混入报告。

`metadata.warnings` 原样承接 `visual_qc.json` 等上游写出的非阻断警告：它们不计入 `blocker_count`，但 **Agent 在向用户交付成片时必须明确告知**。常见警告包括开启 `--allow-partial-tts` 后出现的 `partial_tts` 缺段，以及本机 ffmpeg 缺少 `libass` 导致默认烧录字幕自动降级为外挂 `.srt`：

```json
{"code": "subtitle_burn_degraded", "reason": "ffmpeg_missing_libass", "delivered": "sidecar_srt",
 "mask_dropped": false, "message": "…", "next_action": "…"}
```

此时新字幕已保存在成片同目录的同名 `.srt` 文件中（路径见 `assembly_manifest.json` 的 `subtitle_sidecar`），而原片自带的硬字幕依然保留在画面上；若 `mask_dropped` 为 `true`，说明原本打算叠加的原片字幕遮罩也已一并取消。`assemble.py` 与 `recap.py` 结束时都会在终端醒目打印这条警告及外挂字幕路径。
