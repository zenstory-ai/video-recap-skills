# 数据格式（中间 JSON）

本阶段生成的中间产物统一存放在工作目录 `work_dir/` 下。

## vlm_analysis.json

逐场景的 VLM 画面观察结果，顶层为数组：

```json
[
  {
    "scene_id": 1,
    "start": 5.0,
    "end": 15.0,
    "description": "男子推门闯入卧室，神色戒备",
    "depth_analysis": "两人表面试探，实则都在确认对方有没有露出破绽。",
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

这里的 `start/end` 只是切分送入 MiMo ASR 的**固定粗分片时间窗**，不代表逐字起止时间或精确对白边界；窗口内 `text` 为空只代表未检出文字（原因未知），绝不能直接当成已证实的纯静音段。

## asr_timing_evidence.json

与 `asr_result.json` 配套的精度与溯源说明文件（不改动 `asr_result.json` 本身的数组结构）。它记录所绑定的源视频、`audio.wav`（存在时）和 `asr_result.json` 各自的文件大小与纳秒级修改时间（`size` + `mtime_ns`），并写明当前转写的时间精度边界：

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

`status` 取值范围为 `AVAILABLE_COARSE`、`EXPLICITLY_SKIPPED`、`UNAVAILABLE_NO_KEY`、`UNAVAILABLE_NO_DURATION`、`FAILED_AUDIO_EXTRACTION`、`FAILED_PROVIDER` 或 `EMPTY_UNKNOWN`。该文件还会记下当时参与纠错的人名表（`glossary.names`）；一旦人名表变动、绑定的任一文件被重写（`size` 或 `mtime_ns` 对不上）或该文件缺失，ASR 缓存就会失效并触发重跑。其中 `UNAVAILABLE_NO_DURATION` 与 `EMPTY_UNKNOWN` 属于可重试的未就绪状态，不当作有效缓存复用；生成写稿简报时也会核验该文件并打印当前状态，若缺失或已过期则标为 `MISSING_OR_STALE`。

## asr_writing_chunks.json

生成 `agent_narration_brief.md` 时自动写出。它把长篇 ASR 转写按句子边界与场景切点拆成方便写稿消化的语义块（中文按字数统计，非 CJK 语种按词数统计）：

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

检测出的静音与低能量窗口列表：

```json
[
  {"start": 2.0, "end": 8.5, "duration": 6.5, "has_speech": false}
]
```

`has_speech` 表示该窗口是否与 ASR 检出的对白时间重叠；下游写稿与编排只把 `has_speech=false` 的区间当作可无遮挡插入解说的安静窗口。

## timeline_fusion.json

生成写稿简报时自动写出。它把 VLM 场景描述、ASR 对白和静音窗口对齐到同一条时间线上，省去写稿时逐个文件交叉核对“这一幕有没有人说话、哪里能插旁白”的麻烦：

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

进入 `cut` 模式第二阶段（目录中已有 `edited_source.mp4`）后，本文件与 `asr_writing_chunks.json` 均切换为剪后成片时间（OUTPUT 时钟）：被拆进多个保留片段的同一原片场景，其 `scene_id` 记为 `"3.1"`（从 0 起算的原场景号加片段序号）；VLM 描述中凡落在保留区间内的原片秒数自动换算为成片输出时间，落在已删除区间的时刻则替换为 `[cut-away moment]`；只被剪进半截的粗分片 ASR 窗口，其文本统一替换为 `[partial ASR window: only part of it is in the cut, text withheld]`。JSON 内的 `scene_id` 一律从 0 起算，而写稿简报里展示给人的场景编号从 1 起算。

## background_research.json

可选的项目背景资料（由 Agent 在理解前通过搜索或读取已有文档整理写入）：

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

> `character_details`、`plot_arcs`、`cultural_notes` 为可选扩展字段；只包含 `synopsis`、`characters`、`worldbuilding`、`episode_context` 四个基础字段的 JSON 同样完全有效。

## 其他下游产物

`narration.json`、`narration_lint.json`、`style_card.json`、`packaging_plan.json`、`clip_plan.json` 与 `clip_plan_validated.json` 由后续写稿和剪辑阶段负责生成与校验，本技能既不写出也不校验它们；各文件字段规范见 `agent_narration_brief.md` 及编排阶段的数据契约说明。
