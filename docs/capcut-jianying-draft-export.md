# 导出剪映草稿继续精修

用 Agent 剪辑并渲染成片后，只需在对话里加一句“导出剪映草稿”，单向叶工具 `export_jianying.py` 就会读取同一份规范 `timeline.json`（v3），直接生成可跨机打开的剪映 / CapCut 多轨草稿目录（`draft_content.json`、`draft_info.json`、`draft_meta_info.json`）。**Video Recap Skills**（`zenstory-ai/video-recap-skills`，MIT 开源，含通用剪辑主技能 `video-recap` 与按需参考拆片技能 `video-reference`）全程默认 **100% 本地优先**，仅依赖 Python 3.10+ 标准库与 `ffmpeg` / `ffprobe`，无需强制绑定任何单一云端 API Key。

## 端到端制作流程

```text
源视频 → ① 探测/抽帧拼图/本地优先转写（probe.py · frames.py · transcribe.py）
      → ② Agent 亲眼看图定主线与画幅，写纯 Markdown 方案（BRIEF.md · MEDIA_NOTES.md · EDIT_PLAN.md）
      → ③ 写 timeline.json v3，按需配音（tts.py 实测时长）与 Remotion/PNG 视觉包装（packaging.py）
      → ④ 渲染与硬/软双层体检（render.py → out/final.mp4；check.py → check.json）
      → ⑤ 按需导出剪映草稿（export_jianying.py timeline.json --out-dir out/jianying_draft）
```

日常使用时不用手敲底层命令，直接把视频路径和要求发给 Agent 即可：

```text
用 /path/to/ep1.mp4 剪一条 60 秒 3:4 满屏剧集解说，保留高光原声对白，用 Remotion 加现代杂志风无底框花字并导出剪映草稿。
```

## 剪映草稿里包含哪些轨道？

`export_jianying.py` 是严格单向消费 `timeline.json` v3 的叶工具（核心渲染从不反向依赖它），会把时间线一比一映射到剪映多轨：

| 剪映轨道 | 轨道内容说明 |
|---|---|
| **视频主轨** | `video[]` 多源片段与真实裁切区间（保留源素材完整引用，进剪映后可随时拖拽入出点微调帧数），并附与 `render.py` 同源的 `KFTypeVolume` 原声闪避关键帧 |
| **叠加视频 / 图片轨** | `overlays[]` 声明的透明动态视频（`packaging.py remotion-render` 产出的 ProRes 4444 `.mov` / VP9 `.webm`）或静态 `@2x` PNG 标题/卡片图层，自动换算为剪映中心原点坐标 |
| **多路独立音频轨** | 旁白 `audio.narration[]`、循环背景音乐 `audio.bgm[]`（带同源闪避关键帧）与真实素材音效 `audio.sfx[]` 分别独立成轨 |
| **可编辑字幕轨** | `subtitles[]` 与 `subtitle_style` 转为剪映原生文本轨（正确处理 UTF-16 字符范围，默认语义均衡双行、去行尾标点） |

导出器默认将所有引用的音视频与图片按 MD5 去重拷贝到草稿目录的 `Resources/local/{video,audio,image}`，整个文件夹拷到另一台电脑也能直接打开；仅当确认本机素材路径不移动时，才显式传 `--no-bundle-media` 省去拷贝。

注意两条使用边界：第一，剪映草稿引用未烧录字幕的源视频，源片画面自带的硬字幕仍会显示，可在剪映内自行加遮罩或用 `3:4` `fit="cover"` 裁切避让；第二，`render.py` 渲染出的 `out/final.mp4` 是标准成片，剪映草稿用于二次精修，在剪映内手改的内容不会自动回写 `timeline.json`。

## 成本结构：默认本地免费，云端按需自选

不同于按席位包月的封闭 SaaS 剪辑工具，本仓库默认本地即可跑通全链路，云端能力完全按需可选：

- **纯本地零费用**：看图由宿主多模态 Agent 直接看 `frames.py` 拼图；转写优先读外挂/内封字幕或本机 Whisper；配音支持纯离线本机引擎（`--engine local`）；剪辑、混音、字幕烧录、物理体检、静态审片页与剪映导出均仅需 Python 标准库 + `ffmpeg`（Remotion 动效包装在本地 Node 无头渲染）。
- **按需接入云端配音或转写**：追求自然讲故事口吻的中文/多语种旁白时，可显式启用 Fish Audio（`tts.py --engine fish`，默认免费层可用模型 `s2.1-pro-free`，支持 `--whole-article` 整篇连贯合成与 `spoken_text` 读音分离）、OpenAI TTS 或自托管 IndexTTS；详见[远程调用披露](../skills/video-recap/references/remote-services.md)。

## 能力边界

- 支持的输入视频封装：`.mp4 / .mov / .mkv / .webm`。
- 剪映草稿按需通过 `python3 skills/video-recap/scripts/export_jianying.py timeline.json --out-dir out/jianying_draft` 导出，不与核心渲染耦合。
- 草稿导出专注映射视频、叠加层、多路音频、闪避关键帧与字幕基础轨道；转场特效、调色 LUT、贴纸等可在打开剪映后自由叠加。

## 延伸链接

- 多轨 `timeline.json` v3 与剪映草稿协议细节：[`docs/timeline-and-jianying.md`](timeline-and-jianying.md)
- 技能内剪映导出参考：[`skills/video-recap/references/jianying-export.md`](../skills/video-recap/references/jianying-export.md)
- 竖横屏 Remotion 包装与音效规范：[`skills/video-recap/references/packaging-and-sfx.md`](../skills/video-recap/references/packaging-and-sfx.md)
- 代码仓库：https://github.com/zenstory-ai/video-recap-skills
