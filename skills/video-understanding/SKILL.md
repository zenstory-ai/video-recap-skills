---
name: video-understanding
user-invocable: false
description: >
 把视频分析为结构化理解索引：场景检测、ASR 转写、逐场景 VLM 观察、静音窗口、融合时间线和写作 brief。
 用于理解、索引或总结视频，也作为后续创作前的分析阶段。输入视频文件；输出 scenes.json、
 asr_result.json、vlm_analysis.json、silence_periods.json、timeline_fusion.json、agent_narration_brief.md。
 触发词：视频理解、视频分析、视频索引、video understanding、analyze video、看懂视频。
---

## 1. 定位

本技能把源视频转成 Agent 与下游阶段可读取的理解索引。它的创作角色是**素材观察员 / 场记**，不是导演：

- 先观察，再解释；事实与推断分开。
- 除了“发生了什么”，还要让下游看见知识、权力、目标、关系或情绪在哪一刻变化。
- 标出由谁的 POV 承载变化、哪个反应或表演不可替代，以及哪里存在完整台词/动作的自然剪辑边界。
- 证据不足时保留不确定性，不制造戏剧结论。

## 2. 处理阶段

1. **场景检测**：写 `scenes.json`，包含切点、时长和废片段过滤结果。
2. **抽帧**：为视觉分析提取代表帧。
3. **ASR**：通过 `mimo-v2.5-asr` 写粗分段对白 `asr_result.json`，并写
   `asr_timing_evidence.json` 说明可用性、有限时间精度与文本修正来源。
4. **静音检测**：写 `silence_periods.json`，标注安静窗口与 `has_speech`。
5. **VLM 观察**：写 `vlm_analysis.json`，包含场景描述、深层分析和 `frame_facts`。
6. **时间线融合与创作 brief**：写 `timeline_fusion.json`、`asr_writing_chunks.json` 和 `agent_narration_brief.md`。

各阶段只有在输出产物与 provenance sidecar 同时匹配当前视频及影响结果的设置时才会复用；`--force` 强制重算。

## 3. 环境要求

```bash
# ffmpeg: brew install ffmpeg | apt install ffmpeg | choco install ffmpeg
export MIMO_API_KEY=***
```

ASR 使用 `mimo-v2.5-asr`；VLM 使用 `mimo-v2.5`。`--skip-asr` 可跳过对白转写，但完整理解仍需要 `MIMO_API_KEY` 运行 VLM。`--mimo-video-overview` 可开启按场景块的视频概览。未设置 key 时重跑会复用已缓存的转写、画面分析、概览与故事索引（key 决定的默认 endpoint 不参与比对）；需要请求模型的 consolidation 记为 `skipped_no_key`，不发请求。缓存对不上（例如复制 work_dir 时没保留文件时间）而已有转写时，运行停下并保留转写：用 `cp -p` / `cp -Rp` / `rsync -t` 保留时间重新复制，或设置 key 后重跑（会重新转写）。不要用 `--skip-asr` 绕过，它会把现有转写替换成 `[]`。

若 `work_dir/background_research.json` 存在，本技能会把剧情梗概和角色名折入 VLM 上下文；`--context` 可补充一条简短提示。

下面的 `scripts/...` 均相对于本技能目录。若执行器从仓库根目录启动，请给脚本路径加上本技能的绝对目录。

## 4. 运行命令

```bash
python3 scripts/understand.py <video> --work-dir <work_dir> [选项]
```

| 选项 | 默认 | 作用 |
|------|------|------|
| `<video>` | 必填 | 源视频 |
| `--work-dir` | 必填 | 产物目录；不存在时创建 |
| `--context "..."` | 空 | 补充给 VLM 的简短上下文（节目名、角色名），与 `background_research.json` 合并 |
| `--scene-threshold` | `0.1` | 场景检测阈值 |
| `--style` | `纪录片` | 写进创作简报的解说风格 |
| `--edit-mode full\|cut` | 不设 | 写进简报的 recap 模式；`cut` 时按剪后时长估算旁白预算，已有 `edited_source.mp4` 时句末锚点改用剪后时间 |
| `--target-duration` | 不设 | 写进简报的 cut 目标时长；尚无 `clip_plan_validated.json` 时用它估算旁白预算 |
| `--skip-asr` | 关 | 不转写对白，把 `asr_result.json` 写成 `[]`（已有转写会被覆盖），ASR 证据标为显式跳过 |
| `--mimo-video-overview` | 关 | 按场景块运行 MiMo 视频概览，并作为逐场景主描述 |
| `--force` | 关 | 忽略缓存，全部重算 |
| `--brief-only` | 关 | 只用现有产物重建 `agent_narration_brief.md`，不抽帧、不调 API |
| `--edited-storyboard-only` | 关 | 只按 `clip_plan_validated.json` 写剪后时间线 `storyboard/edited_storyboard.*`，并在已有的 `agent_narration_brief.md` 顶部加 storyboard 指引；多源计划（带 `sources`）从各来源 `source_work_dir` 的 `frames/` 按其 `frames_manifest.json` 的 fps 取帧，tile 标 `S1`/`S2`…；不抽帧、不调 API，故事板生成失败只记日志。与 `--brief-only` 互斥 |
| `--consolidate` / `--no-consolidate` | 开 | 生成全局故事索引 `understanding_index.*` |
| `--consolidate-asr` | 关 | 另外清洗 ASR 文本，写 `asr_clean.json` |

## 5. 输出契约

默认运行写出下表产物；各阶段产物旁的 `*.meta.json` 是缓存 provenance sidecar。

| 文件 | 内容 |
|------|------|
| `frames/frame_*.jpg`、`frames_manifest.json` | 按 fps 抽出的帧及其清单 |
| `scenes.json` | 场景切点、起止时间与时长 |
| `audio.wav` | 16 kHz 单声道音频，供 ASR、静音检测与句末锚点使用 |
| `asr_result.json` | `[{start, end, text}]` 时间戳对白 |
| `asr_timing_evidence.json` | ASR 可用性状态、粗窗口精度、glossary 前后文本，以及它所描述的源视频/音频/结果文件（路径存在性 + size/mtime） |
| `silence_periods.json` | `[{start, end, duration, has_speech}]` 安静窗口 |
| `speech_boundary_anchors.json` | ASR 句末标点对齐到短停顿的句末锚点；缺音频或 ASR 时 `status: unavailable` |
| `vlm_analysis.json` | 逐场景描述、深层分析与 `frame_facts` |
| `mimo_video_overview.status.json` | 视频概览状态（未启用时为 `disabled`）；启用成功另写 `mimo_video_overview.json` |
| `understanding_index.json`、`understanding_index.md` | 全局故事索引（`--no-consolidate` 时不写） |
| `consolidation.status.json` | 故事索引与 ASR 清洗的运行状态 |
| `storyboard/source_storyboard.{json,jpg}` | 原片时间线联系表（超页时续写 `_001.jpg` 等）；已有 `clip_plan_validated.json` 时另写 `edited_storyboard.*`；`STORYBOARD=0` 关闭 |
| `timeline_fusion.json` | VLM、ASR 与静音信息的统一时间线 |
| `asr_writing_chunks.json` | 按句界和场景切分的 ASR 写作块 |
| `agent_narration_brief.md` | Agent 首先阅读的创作简报 |

后续写作阶段根据创作简报与索引制定方案并写 `narration.json`。

## 6. 参考资料

- 背景调研：`references/research-guide.md`，产出 `background_research.json`。
- JSON 结构：`references/data-schema.md`。

## 7. 能力边界

- 不写解说词，也不做解说评分；只负责生成理解索引与创作简报。
- 不编造信号无法支持的剧情；当 ASR / VLM 过薄时输出素材警告。
- MiMo ASR 的 `start/end` 是固定分片形成的**粗窗口**，不是词级对齐；空文本只表示原因未知，
  不能当作已证实静音。`asr_timing_evidence.json` 的状态字段见 `references/data-schema.md`。
