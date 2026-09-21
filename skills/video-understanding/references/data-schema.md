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

`start/end` 仅表示送入 MiMo ASR 的固定粗分片窗口。该列表为兼容产物，不能据此声称词级
时间、准确对白边界或静音证明；空 `text` 的原因未知。

## asr_timing_evidence.json

ASR 的独立证据 sidecar，不改变 `asr_result.json` 的既有数组结构。它记录自己描述的是哪一份源视频、
`audio.wav`（存在时）和 `asr_result.json`（各自的 `size` + `mtime_ns`），并明确当前精度边界：

```json
{
  "schema_version": 2,
  "status": "AVAILABLE_COARSE",
  "source_video": {"size": 123456, "mtime_ns": 1700000000000000000},
  "audio": {"size": 2048, "mtime_ns": 1700000001000000000},
  "asr_result": {"size": 512, "mtime_ns": 1700000002000000000},
  "glossary": {
    "names": ["叶轻眉"],
    "name_count": 1
  },
  "precision": {
    "window_timing": "COARSE_SEGMENT_WINDOWS",
    "dialogue_boundaries": "NOT_VERIFIED",
    "word_alignment": "NOT_PERFORMED",
    "empty_text_meaning": "UNKNOWN_NOT_PROVEN_SILENCE"
  },
  "windows": [{
    "index": 0,
    "start": 0.0,
    "end": 3.5,
    "text_availability": "AVAILABLE",
    "observed_text": "她叫叶青眉",
    "post_glossary_text": "她叫叶轻眉",
    "glossary_modified": true
  }]
}
```

`status` 可为 `AVAILABLE_COARSE`、`EXPLICITLY_SKIPPED`、`UNAVAILABLE_NO_KEY`、
`UNAVAILABLE_NO_DURATION`、`FAILED_AUDIO_EXTRACTION`、`FAILED_PROVIDER`、`EMPTY_UNKNOWN`
或 `LEGACY_UNVERIFIED`。`LEGACY_UNVERIFIED` 标记没有旧 sidecar 的兼容缓存，可离线复用但
`observed_text`/`glossary_modified` 为 `null`，且始终保持 legacy 身份；非 legacy sidecar 记录
当时参与修正的人名/别名列表（`glossary.names`），人名表变化或所描述的文件被重写（size/mtime 不再
一致）都会使 ASR 缓存失效。
`UNAVAILABLE_NO_DURATION` 与 `EMPTY_UNKNOWN` 是可重试的不可用结果，不作为缓存命中；写作
brief 会校验 sidecar 并打印当前状态，缺失或与当前文件不一致时显示 `MISSING_OR_STALE`。

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

## deslop_qc_requirements.json（工具/brief 生成的运行契约）

`deslop_qc_requirements.json` 是 tool/brief generated run contract：工具或 brief 生成本次运行的 QC 要求，供 `deslop_qc` 读取，不由 Agent 手写。字段为 `schema_version` 与 `style_card_required`。

`style_card_required` 默认 `false`（advisory）：缺少 `style_card.json` 只是 warning，不阻断出片。将来的 opt-in 运行可把它设为 `true`，让 `style_card.json` 成为硬性要求——`deslop_qc` 只读这个字段判断缺少 `style_card.json` 是否是 blocker，不扫描 `agent_narration_brief.md` 的 prompt wording 来推断。如果 requirements 文件缺失或损坏，按 legacy/migration advisory 处理，不作为 hard failure。

该契约不改变 `--style`：`--style` 仍是 freeform verbatim guidance，不增加固定风格档位。它也不改变 `deslop_qc` 边界：仍然是 report-only，不是 AIGC detector，不自动改写。

最小示例：

```json
{
  "schema_version": 1,
  "style_card_required": false
}
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

## 其他产物

`narration.json`、`narration_lint.json`、`style_card.json`、`packaging_plan.json`、`deslop_qc.json`、`clip_plan.json`、`clip_plan_validated.json` 由后续的写稿与剪辑阶段读写，本技能既不生成也不校验它们；其格式以本技能生成的 `agent_narration_brief.md` 和编排器的中间产物契约为准。
