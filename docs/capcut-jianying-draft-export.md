# 导出剪映草稿继续精修

用 Agent 生成解说成片后，只需在指令里加一句“导出剪映草稿”（或传 `--export-jianying`），就能把整条多轨时间线直接转成剪映可编辑草稿。**Video Recap Skills**（`zenstory-ai/video-recap-skills`，MIT 开源，含 6 个生产技能 + 1 个按需参考技能）在合成出片的同时，会把原片视频、逐段解说配音、BGM、字幕和静态包装图层一并写入剪映草稿目录（含 `draft_content.json`、`draft_info.json`、`draft_meta_info.json`），默认将用到的媒体文件打包进草稿内部的 `Resources/local/`，哪怕把整个草稿文件夹拷到另一台电脑上也能直接双击打开。全流程本机只依赖 Python 标准库与 `ffmpeg`，云端只需配置一个小米 MiMo API Key。

## 端到端制作流程

```text
源视频 → ① 视频理解（切点检测 · ASR · VLM）→ ② Agent 定主线与声画分工、写稿 → ③ TTS 配音 → ④ 合成组装（混音 · 字幕 · 多轨时间线）→ recap_<名>.mp4
                                           └─ 剪辑模式（cut）：先剪出画面母版，再对着成片时间轴写解说词
```

日常使用时无需手动敲复杂的命令行参数，直接把视频路径和要求发给 Agent 即可：

```text
给 /path/to/video.mp4 做一个中文解说成片。这是《庆余年》第一集，主角是范闲，字幕烧进画面，同时导出剪映草稿。
```

Agent 会自动串联理解、选段、剪辑、写稿、配音、合成与草稿导出。

## 剪映草稿里包含哪些轨道？

合成阶段（`video-assemble`）通过 `--export-jianying`（或环境变量 `EXPORT_JIANYING=1`）读取标准时间线 `timeline.json`，生成多轨剪映草稿：

| 剪映轨道 | 轨道内容说明 |
|---|---|
| **视频轨** | 源视频画面（在 `cut` 模式下传入 `--source-video <原片>` 后，草稿会直接引用原片文件与真实裁切区间，方便在剪映里随时拖动边缘加减帧数） |
| **音频轨** | 逐段解说配音（直接引用实际混入成片的 `_placed_*.wav`）与背景音乐（BGM） |
| **字幕轨** | 解说字幕，以及原声留白处带 `「」` 引号的角色原声台词 |
| **图片叠层轨** | `packaging_layers.json` 中配置的静态边框、角标或背景图 |

导出器默认会把视频、音频和图片复制进草稿目录下的 `Resources/local/{video,audio,image}` 并写好本地素材索引，保证草稿自包含、可迁移；只有当你确定原始媒体路径在本机永远不会移动且剪映有权限读取时，才使用 `--jianying-no-bundle-media` 省去复制。草稿只映射上述四类基础轨道，转场、蒙版、调色、花字贴纸等精修效果可在打开剪映后自由添加。

注意两条使用边界：第一，剪映草稿直接引用未烧录字幕的源视频，因此源视频画面自带的硬字幕仍会显示，如需遮挡可在剪映内添加遮罩；第二，`ffmpeg` 渲染出的 `recap_<名>.mp4` 才是流水线的标准交付物，剪映草稿是供你二次微调的工程附件。

## 怎样让原声留白处的台词字幕一字不差？

当原片人物在旁白留白处开口飙戏时，如果需要烧录或导出对应的原声字幕，系统优先读取你提供的准确字幕文件，其次读取 Agent 校对过的 `original_subtitles.json`，最后才用粗粒度 ASR 转写兜底。你可以直接把现成的字幕放进 `work_dir`：支持 `user_subtitles.json`（默认按成片时间轴，若外层包成 `{"timeline": "source", "lines": [...]}` 则按原片时间轴自动映射）或原片时间轴的 `user_subtitles.srt / .ass`。

## 自建开源流程与按月订阅 SaaS 的账单对比

市面上按席位包月的商业解说工具通常把云端算力、配音额度和模板绑成固定月费；而本仓库采用“本地免费工具 + 云端按量 API”的轻量组合：

- **本机零成本**：只用 Python 标准库和 `ffmpeg`，不需要独显 GPU，也不用在本地跑几十 GB 的大模型。
- **云端按实际用量计费**：语音转写（ASR）、画面理解（VLM）和默认配音（TTS）全部共用同一个小米 MiMo Key，跑多少秒算多少秒的钱（实测制作一条完整解说视频的 API 消耗约 1.3 元人民币）；配音也可以单独切换为 Fish Audio（`--tts-provider fish-audio`）。

## 能力边界

- 支持的输入视频封装：`.mp4 / .mov / .mkv / .webm`。
- 剪映草稿仅在显式开启 `--export-jianying` 时生成；默认运行产出 `recap_<名>.mp4`、`subtitles.srt/.ass` 与 `timeline.json`。
- 只要能从文件名或需求里认出剧名，建议先让 Agent 把人物全名与关系写入 `background_research.json`，能大幅减少 VLM 把角色叫成“黑衣男子”和 ASR 听错人名的情况。

## 延伸链接

- 多轨时间线与剪映草稿协议细节：[`docs/timeline-and-jianying.md`](timeline-and-jianying.md)
- 在线图文指南：https://zenstory.ai/zh/video-recap/capcut-draft · https://zenstory.ai/zh/video-recap/video-to-narration
- 代码仓库：https://github.com/zenstory-ai/video-recap-skills（原 `worldwonderer/video-recap-skills`，旧地址自动跳转）
