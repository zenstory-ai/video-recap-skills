# 导出剪映草稿继续精修

用 Agent 完成初剪与成片渲染后，只需在对话里补一句“导出剪映草稿”，配套工具 `export_jianying.py` 就会读取同一份规范 `timeline.json`（v3），直接生成可跨机打开的剪映 / CapCut 多轨草稿目录（`draft_content.json`、`draft_info.json`、`draft_meta_info.json`）。**Video Recap Skills**（`zenstory-ai/video-recap-skills`，MIT 开源，含通用剪辑主技能 `video-recap` 与按需参考拆片技能 `video-reference`）全程默认本地运行，仅依赖 Python 3.10+ 标准库与 `ffmpeg` / `ffprobe`，无需强制绑定任何云端 API Key。

## 端到端制作流程

```text
源视频 → ① 探测、抽帧拼图与本地优先转写（probe.py · frames.py · transcribe.py）
      → ② Agent 看图确认画幅与主线，写 Markdown 方案（BRIEF.md · MEDIA_NOTES.md · EDIT_PLAN.md）
      → ③ 编写 timeline.json v3，按需配音测时（tts.py）与加包装音效（packaging.py）
      → ④ 渲染成片与物理质检（render.py → out/final.mp4；check.py → check.json）
      → ⑤ 按需导出剪映草稿（export_jianying.py timeline.json --out-dir out/jianying_draft）
```

日常使用时不用手敲底层命令，直接把视频路径和剪辑要求告诉 Agent 即可：

```text
用 /path/to/ep1.mp4 剪一条 60 秒 3:4 满画幅剧集解说，保留关键原声对白，用 Remotion 加简约双层动效花字并导出剪映草稿。
```

## 剪映草稿包含哪些轨道？

`export_jianying.py` 单向消费 `timeline.json` v3（核心渲染不依赖它），把时间线映射到剪映多轨：

| 剪映轨道 | 轨道内容说明 |
|---|---|
| **视频主轨** | `video[]` 多源片段的源入出点（保留完整源素材引用，进剪映后可随时拖拽入出点微调帧数），并附带与 `render.py` 同一条曲线的 `KFTypeVolume` 原声闪避关键帧；`crop` / `focus` / `viewport` 不换算，片段按原画面铺满画布，构图需在剪映里手调 |
| **叠加视频 / 图片轨** | `overlays[]` 声明的透明动态视频（`packaging.py remotion-render` 产出的 ProRes 4444 `.mov` / VP9 `.webm`）或静态 `@2x` PNG 标题与卡片图层，按 `rect` 换算为剪映中心原点坐标；淡入淡出与 `slide_up` 动效不导出 |
| **多路独立音频轨** | 旁白 `audio.narration[]`、循环背景音乐 `audio.bgm[]`（带同源闪避关键帧）与真实素材音效 `audio.sfx[]` 分别独立成轨 |
| **可编辑字幕轨** | `subtitles[]` 逐条转为剪映原生白色文本（按 UTF-16 计算字符范围）；`subtitle_style` 只取 `safe_y` 定垂直位置，不折行、不去行尾标点 |

导出器默认将所有引用的音视频与图片按真实路径去重（同一文件只拷一次，MD5 只记入元数据）拷贝到草稿目录的 `Resources/local/{video,audio,image}`，整个文件夹复制到另一台电脑也能直接打开；仅当确认本机素材路径固定不移动时，才显式传 `--no-bundle-media` 跳过拷贝。

使用边界说明：第一，剪映草稿引用的是未烧录字幕的源视频，源片画面自带的硬字幕仍会保留，可在剪映内按需加遮罩或通过 `3:4` 满画幅构图避让；第二，`render.py` 渲染出的 `out/final.mp4` 为标准成片，剪映草稿用于二次精修，在剪映内手动调整的内容不会自动回写到 `timeline.json`。

## 成本结构：默认本地免费，云端按需选用

与按月订阅的封闭式剪辑平台不同，本仓库默认在本地即可跑完完整流程，云端服务完全按需配置：

- **纯本地零额外费用**：看片由宿主多模态 Agent 直接看 `frames.py` 拼图；转写优先读外挂/内封字幕或本机 Whisper；配音支持纯离线本机引擎（`--engine local`）；剪辑、混音、字幕烧录、物理质检、静态审片页与剪映导出仅依赖 Python 标准库和 `ffmpeg`（Remotion 动效包装在本地 Node 无头渲染）。
- **按需接入云端配音或转写**：需要更自然的中文或多语种旁白时，可显式启用 Fish Audio（`tts.py --engine fish`，默认免费层模型 `s2.1-pro-free`，支持 `--whole-article` 整篇连续合成与 `spoken_text` 单独校音）、OpenAI TTS 或自托管 IndexTTS；详见[远程调用披露](../skills/video-recap/references/remote-services.md)。

## 能力边界

- 支持的输入视频封装：`.mp4 / .mov / .mkv / .webm`。
- 剪映草稿按需通过 `python3 skills/video-recap/scripts/export_jianying.py timeline.json --out-dir out/jianying_draft` 导出，与核心渲染解耦。
- 导出重点覆盖视频、叠加层、多路音频、闪避关键帧与字幕基础轨道；裁切构图、图层淡入淡出与字幕样式（除垂直位置）需在剪映里重做；转场特效、调色 LUT、贴纸等可在打开剪映后自由添加。

## 延伸链接

- 多轨 `timeline.json` v3 与剪映草稿协议细节：[`docs/timeline-and-jianying.md`](timeline-and-jianying.md)
- 技能内剪映导出参考：[`skills/video-recap/references/jianying-export.md`](../skills/video-recap/references/jianying-export.md)
- 竖横屏 Remotion 包装与音效规范：[`skills/video-recap/references/packaging-and-sfx.md`](../skills/video-recap/references/packaging-and-sfx.md)
- 代码仓库：https://github.com/zenstory-ai/video-recap-skills
