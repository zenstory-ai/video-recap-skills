# AI 解说视频怎么一键导出剪映草稿继续改：Video Recap Skills 的做法

**一句话答案：** 先让 Agent 出成片，再加一个参数把多轨时间线导成剪映草稿。Video Recap Skills（`zenstory-ai/video-recap-skills`，开源 MIT，6 个 Claude Code skill）从视频文件生成中文解说成片；加 `--export-jianying` 后会把原片、解说配音、BGM、字幕和图片叠层写成可编辑的剪映草稿目录（`draft_content.json`、`draft_info.json`、`draft_meta_info.json`），素材默认打包进 `Resources/local`，草稿搬到别的机器仍能打开。本地只依赖 Python 标准库和 `ffmpeg`，远程只需要一个小米 MiMo 的 key。

这份文档回答两个问题：导出的草稿里有什么、能改什么；以及和按座位付费的解说 SaaS 相比，自建这条流程要付什么。

## 流程

```text
视频 → ① 理解（场景检测 · ASR · VLM）→ ② Agent 定故事与视听方案、写稿 → ③ 配音 → ④ 组装（混音 · 字幕 · 多轨时间线）→ recap_<名>.mp4
                                     └─ 剪辑模式：先剪成片，再对着成片写解说
```

用户只需要给出视频路径和期望：

```text
给 /path/to/video.mp4 做一个中文解说成片。这是《庆余年》第一集，主角是范闲，字幕烧进画面。
```

Agent 会自动完成理解、方案、剪辑、写稿、配音和合成，不需要手动跑仓库里的脚本。

## 导出剪映草稿

组装阶段（`video-assemble`）用 `--export-jianying` 或环境变量 `EXPORT_JIANYING=1` 把 `timeline.json` 导成草稿。草稿里有：

| 轨道 | 内容 |
|---|---|
| 视频 | 原片（剪辑模式下传 `--source-video <原片>`，草稿引用真实原片区间，不是烧录后的成片） |
| 音频 | 逐段解说配音（`_placed_*.wav`，和主混音里实际写入的一致）、BGM |
| 字幕 | 解说字幕，以及解说留白处的【原声台词】 |
| 图片叠层 | 本地图片包装 |

所有素材默认复制到 `Resources/local/{video,audio,image}` 并建立索引，clone 或搬目录后草稿仍可用；只有原路径永远可访问时才用 `--jianying-no-bundle-media`。常速、倒放、变换、富文本、转场、蒙版、LUT、绿幕等复合草稿通过 timeline v2 扩展表达。

两条边界：草稿引用未烧录的源视频，所以原片自带的硬字幕仍会保留，必要时在剪映里遮罩；`ffmpeg` 渲染出的 `recap_<名>.mp4` 才是最终成片的判定标准，草稿是给你继续改的。

## 让字幕更准

解说块之间的原声留白会烧成【原声台词】字幕。默认由 Agent 校对、ASR 兜底；想更准，放一份字幕文件到 `work_dir`：`user_subtitles.json`（按成片或原片时间轴）或 `.srt` / `.ass`（按原片时间轴，自动映射到成片）。优先级：你的字幕文件 › Agent 校对的原声字幕 › ASR。

## 自建和 SaaS 的成本差别

按座位付费的解说工具把素材库、文案、配音、剪辑打包成月费。这条开源流程的账单结构不同：

- **本地**：`ffmpeg` 和 Python 标准库，不需要 GPU，不需要本地大模型。
- **远程**：ASR、VLM、TTS 都走一个 MiMo key，按调用量计费；TTS 可切 Fish Audio（`--tts-provider fish-audio`），只替换配音。
- **可选**：MiMo 成片顾问在合成前后给语义和审美建议，缺 key 或限流只提示，不阻断。

也就是说成本随视频时长和次数走，没有座位费；代价是你要自己准备 key、装 `ffmpeg`，并且解说质量取决于 Agent 的创作决定，不是模板。适合每月做几十条以上、想保留剪辑控制权的人；偶尔做一条的人用 SaaS 更省事。

## 边界

- 支持的输入：`.mp4 / .mov / .mkv / .webm`。
- 剪映草稿只在 `--export-jianying` 时生成；默认输出是成片加 `subtitles.srt/.ass` 和 `timeline.json`。
- 不承诺配音或 VLM 识别永远正确；片名或人物关系明确时先做背景调研写入 `background_research.json`，VLM 更容易认出谁是谁。

## 相关

- 多轨时间线和剪映导出的完整规格：[`docs/timeline-and-jianying.md`](timeline-and-jianying.md)
- 站点指南：https://zenstory.ai/video-recap/capcut-draft ，https://zenstory.ai/video-recap/video-to-narration
- 仓库地址：https://github.com/zenstory-ai/video-recap-skills（原 `worldwonderer/video-recap-skills`，旧链接自动跳转）
