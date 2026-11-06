---
name: video-understanding
user-invocable: false
description: 将视频拆解为结构化理解索引：完成场景切分、ASR 语音转写、逐场 VLM 画面观察、静音窗口检测、多轨时间线融合与写稿简报生成。既可单独用于看懂和索引视频，也是后续剪辑与写稿的前置分析阶段。输入源视频文件；输出 scenes.json、asr_result.json、vlm_analysis.json、silence_periods.json、timeline_fusion.json 与 agent_narration_brief.md。触发词：视频理解、视频分析、视频索引、video understanding、analyze video、看懂视频。
---

## 1. 定位

本技能将源视频转化为供 Agent 与下游阶段直接读取的结构化理解索引，扮演**现场场记与素材观察员**的角色：

- **先记事实，再做推断**：把肉眼可见的动作、表情和原话与人物心理推断严格分开记录。
- **盯住局势转折**：除了记下“画面里发生了什么”，更要标出知情范围、强弱态势、人物目标、关系亲疏或情绪气压在哪一秒发生了反转。
- **抓准戏眼与切点**：点明这一拍由谁的视角（POV）立住、哪个变脸或停顿反应不可替代，以及哪里有完整台词落音或动作收势的自然切点。
- **有一分证据说一分话**：画面与台词线索不够时如实记下不确定性，绝不强行脑补戏剧结论。

## 2. 处理阶段

1. **场景检测**：写出 `scenes.json`，记录镜头切点、起止秒数及无效黑场/水印片段的过滤结果。
2. **代表帧抽取**：按固定帧率抽出关键帧，供视觉分析与故事板拼图使用。
3. **语音转写（ASR）**：调用 `mimo-v2.5-asr` 生成粗分窗对白 `asr_result.json`，同时写出 `asr_timing_evidence.json`，如实记录转写可用性、粗窗口时间精度边界及人名表（glossary）替换前后的文本。
4. **静音窗口检测**：写出 `silence_periods.json`，标出适合插播解说的安静区间及 `has_speech` 标记。
5. **画面深度观察（VLM）**：写出 `vlm_analysis.json`，包含各场景画面简述、深层关系分析与逐帧事实（`frame_facts`）。
6. **时间线融合与写稿简报**：合并写出 `timeline_fusion.json`、`asr_writing_chunks.json` 与供 Agent 首读的 `agent_narration_brief.md`。

各阶段产物旁均带有 `*.meta.json` 溯源记录（provenance sidecar），只有当产物与源视频文件及关键配置完全匹配时才会命中缓存复用；传入 `--force` 可忽略缓存全部重算。

## 3. 环境要求

```bash
# ffmpeg: brew install ffmpeg | apt install ffmpeg | choco install ffmpeg
export MIMO_API_KEY=***
```

语音转写默认调用 `mimo-v2.5-asr`，画面分析与汇总默认调用 `mimo-v2.5`。加 `--skip-asr` 可跳过语音转写，但跑完整画面分析仍需配置 `MIMO_API_KEY`；加 `--mimo-video-overview` 可额外按场景分块生成视频级概览。未配置 API key 时重跑，脚本会自动复用本地已缓存的转写、画面分析、视频概览和故事索引（由 key 前缀决定的默认接口地址不参与缓存校验）；需要联网请求模型的故事索引汇总则会记为 `skipped_no_key` 并直接跳过，不发任何网络请求。如果缓存校验未通过（比如复制 `work_dir` 时没保留文件修改时间）但目录里已有转写结果，脚本会立即停下以保护既有转写：请用 `cp -p`、`cp -Rp` 或 `rsync -t` 保留时间戳重新复制，或配好 key 后重跑重转。切勿用 `--skip-asr` 强行绕过，否则会把已有的 `asr_result.json` 清空成 `[]`。

只要 `work_dir/background_research.json` 存在，本技能就会把里面的剧情梗概与角色名注入 VLM 提示词；命令行也可通过 `--context` 传入简短背景说明。

### 远程调用与本地读取

- **外发内容**：ASR 阶段把切分后的音频片发往 MiMo ASR；逐场 VLM 把抽取的代表帧图片、帧时间戳与分析提示词发往 MiMo；开启 `--mimo-video-overview` 时，还会把按场景截取的视频小片段发往 MiMo 视频模型；故事索引汇总（`consolidate`）与可选的 ASR 文本清洗（`--consolidate-asr`）只发送画面描述、对白转写和背景资料等纯文字。
- **接口地址与凭据**：所有请求均走 MiMo 的 OpenAI 兼容接口。可通过 `MIMO_API_URL` 统一指定接口地址，也可用 `MIMO_ASR_API_URL`、`MIMO_VIDEO_API_URL` 单独指定 ASR 与视频概览接口（同理支持各自独立的 API key）。未手动指定地址时，普通按量计费 key 默认发往 `https://api.xiaomimimo.com/v1`，以 `tp-` 开头的 Token Plan key 发往 `MIMO_TOKEN_PLAN_CLUSTER` 所选集群；逐场抽帧分析与汇总固定使用 `MIMO_API_KEY`。
- **纯本地运行开关**：`--skip-asr` 只关掉语音转写，`--no-consolidate` 只关掉索引汇总，两者都不会关闭 VLM 画面分析。如果只想在本地基于已有分析文件重出简报或剪后故事板，请使用 `--brief-only` 或 `--edited-storyboard-only`，这两条路径全程离线、不调任何远程接口。

下文中的 `scripts/...` 均相对于本技能目录；若从仓库根目录启动，请补上本技能的绝对路径。

## 4. 运行命令

```bash
python3 scripts/understand.py <video> --work-dir <work_dir> [选项]
```

| 选项 | 默认 | 作用 |
|------|------|------|
| `<video>` | 必填 | 待分析的源视频路径 |
| `--work-dir` | 必填 | 中间产物与简报输出目录（不存在时自动创建） |
| `--context "..."` | 空 | 传给 VLM 的简短背景提示（如片名、主角名），会与 `background_research.json` 合并使用 |
| `--scene-threshold` | `0.1` | 场景切分灵敏度阈值 |
| `--style` | `纪录片` | 写入创作简报的解说风格说明（原样透传） |
| `--edit-mode full\|cut` | 不设 | 写入简报的成片模式：`cut` 模式按剪后目标时长折算旁白预算；目录中已有 `edited_source.mp4` 时，句末切入锚点自动换算为剪后成片时间 |
| `--target-duration` | 不设 | 写入简报的 `cut` 模式目标时长；尚未生成 `clip_plan_validated.json` 时按它估算旁白段数 |
| `--skip-asr` | 关 | 跳过语音转写，将 `asr_result.json` 写为空数组 `[]`（会覆盖已有转写），并在时间证据中记为显式跳过 |
| `--mimo-video-overview` | 关 | 按场景分块调用 MiMo 视频理解生成全局概览，并用作各场景的主描述 |
| `--force` | 关 | 忽略本地缓存，全流程重新计算 |
| `--brief-only` | 关 | 仅利用目录内现有分析文件重建 `agent_narration_brief.md`，不抽帧、不联网调接口 |
| `--edited-storyboard-only` | 关 | 仅根据 `clip_plan_validated.json` 生成剪后时间线故事板 `storyboard/edited_storyboard.*`，并在现有 `agent_narration_brief.md` 顶部补上故事板路径指引；多源剪辑计划（含 `sources`）会按各自 `source_work_dir` 下 `frames_manifest.json` 的帧率取帧并标上 `S1`/`S2`…；全程不重抽帧、不调接口，拼图失败只打日志不阻断，且与 `--brief-only` 互斥 |
| `--consolidate` / `--no-consolidate` | 开 | 汇总生成全局人物与剧情索引 `understanding_index.*` |
| `--consolidate-asr` | 关 | 额外调用模型清洗 ASR 错别字与人名，写出 `asr_clean.json` |

## 5. 输出契约

默认运行会在 `work_dir` 下写出以下文件（同名 `*.meta.json` 为缓存校验文件）：

| 文件 | 内容说明 |
|------|------|
| `frames/frame_*.jpg`、`frames_manifest.json` | 按固定帧率抽取的视频帧及帧清单 |
| `scenes.json` | 场景切点、起止秒数与有效时长 |
| `audio.wav` | 16 kHz 单声道波形文件，供 ASR 转写、静音检测与句末停顿锚点共用 |
| `asr_result.json` | `[{start, end, text}]` 粗分窗对白转写列表 |
| `asr_timing_evidence.json` | ASR 可用性状态、粗窗口精度说明、人名表替换前后对照，以及所绑定的源视频、音频与转写文件指纹（文件大小 + 修改时间） |
| `silence_periods.json` | `[{start, end, duration, has_speech}]` 安静窗口列表 |
| `speech_boundary_anchors.json` | 将 ASR 句末标点对齐到实测短停顿得出的句末切入锚点；无音频或无转写时标为 `status: unavailable` |
| `vlm_analysis.json` | 逐场景画面简述、深层人物关系分析与逐帧动作标签 `frame_facts` |
| `mimo_video_overview.status.json` | 视频概览运行状态（未开启时为 `disabled`）；开启且成功时另写 `mimo_video_overview.json` |
| `understanding_index.json`、`understanding_index.md` | 全局人物、关系与关键情节点索引（传 `--no-consolidate` 时不生成） |
| `consolidation.status.json` | 全局故事索引与 ASR 清洗的运行状态记录 |
| `storyboard/source_storyboard.{json,jpg}` | 原片时间线抽帧故事板拼图（超页自动续写 `_001.jpg` 等）；存在 `clip_plan_validated.json` 时同步生成剪后故事板 `edited_storyboard.*`；设环境变量 `STORYBOARD=0` 可关闭 |
| `timeline_fusion.json` | 将 VLM 画面、ASR 对白与静音窗口对齐到同一时间轴的融合视图 |
| `asr_writing_chunks.json` | 按句子边界与场景切分好的长对白阅读块 |
| `agent_narration_brief.md` | 供写稿 Agent 第一时间阅读的创作简报 |

下游写稿阶段直接读取这份创作简报与索引文件，拍板创作方案并撰写 `narration.json`。

## 6. 参考资料

- 理解前背景调研：`references/research-guide.md`，生成 `background_research.json`。
- 中间产物 JSON 规范：`references/data-schema.md`。

## 7. 能力边界

- 本技能不撰写解说词，也不对旁白稿打分，只负责把音画证据整理成结构化索引与创作简报。
- 严禁脑补画面与原声没有依据的剧情；当 ASR 与 VLM 识别出的有效信息过薄时，会在简报里显式拉响素材匮乏警告。
- MiMo ASR 给出的 `start/end` 只是按固定时长切片送检的**粗略时间窗**，并非逐字对齐时间戳；窗口文本为空只代表未检出文字（原因未知），绝不能直接当成已证实的纯静音区间。`asr_timing_evidence.json` 的状态枚举详见 `references/data-schema.md`。
