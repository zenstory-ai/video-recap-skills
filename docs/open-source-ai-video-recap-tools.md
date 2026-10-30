# 开源 AI 影视解说工具怎么选：8 个项目按使用方式对比（2026）

把一段视频自动做成中文解说，开源工具大致分四种用法：本地网页界面（NarratoAI）、桌面应用（dramaxis、解说猫 JSM、superAIAutoCutVideo、JJYB_AI_VideoAutoCut）、命令行（movie-narrator），以及装进编程 Agent 的 skill（video-recap-skills、narrator-ai-cli-skill）。分好类再核对四件事：它怎么“看懂”视频，模型和配音调用哪里、要不要付费，能不能导出剪映草稿接着改，许可证是否允许你的用法。

> 利益相关：本文由 video-recap-skills 的维护者编写，video-recap-skills 也在对比之列。所有项目按同一组标准列出，事实取自各项目自己的仓库、README 与 LICENSE，截至 2026-10-31；星标数和更新日期会变，引用前请回仓库核对。表中“未说明”表示 README 没有写，不代表没有这项能力。

## 一、总表

| 项目 | 使用方式 | 许可证 | 星标 / 最近推送 | 怎么看懂视频 | 配音与成片 | 剪映草稿 | 模型从哪来 |
|---|---|---|---|---|---|---|---|
| [NarratoAI](https://github.com/linyqh/NarratoAI) | 本地网页界面（Streamlit），有 Windows / macOS 整合包和 Docker | MIT | 11,643 / 2026-10-24 | 抽帧交给视觉模型，Fun-ASR 转字幕 | 多种 TTS（IndexTTS、豆包、腾讯云等），加字幕出成片 | 支持 | OpenAI 兼容接口，需自配云端 API key |
| [JJYB_AI_VideoAutoCut](https://github.com/jianjieyiban/JJYB_AI_VideoAutoCut) | Windows 10/11 桌面应用 | 作者自定个人使用许可，未经书面授权禁止商用 | 1,175 / 2026-10-18 | TransNetV2 或 OpenCV 切镜头，可选多家视觉模型，faster-whisper 或 FunASR 转写 | 多种 TTS（含 IndexTTS2）；镜头装不下配音时拒绝生成，不用定格凑时长 | 支持 | 自配，视觉模型可选 GPT、Claude、Gemini、Qwen-VL 等 |
| [movie-narrator](https://github.com/zcbacxc/movie-narrator) | 命令行（PyPI 安装），另有独立网页界面包 | AGPL-3.0 | 1,149 / 2026-09-16 | 先用 TMDB 影片资料写稿、配音，再做场景检测和片段匹配；可选视觉模型描述场景 | 默认 Edge-TTS（README 注明仅限非商用） | 未说明 | 默认本地 Ollama，也支持智谱、百炼、MiMo、硅基流动 |
| [dramaxis](https://github.com/Agions/dramaxis)（原 Voxplore） | 桌面应用（Tauri） | MIT | 449 / 2026-10-27 | FFmpeg 场景检测加多模态关键帧，本地 Whisper 提字幕 | 第一人称独白式解说；TTS 可选 Edge、GPT-SoVITS、MiniMax | 支持 | README 列出 11 家，含 Ollama、LM Studio 本地模型 |
| [superAIAutoCutVideo](https://github.com/xiaohu2206/superAIAutoCutVideo) | 桌面应用（Tauri + FastAPI），安装包在网盘分发 | MIT | 209 / 2026-04-19 | FunASR 字幕加 moondream 看帧；短剧解说目前只用字幕分析 | TTS 可选 Edge、腾讯、Qwen3-TTS | 支持 | OpenRouter 等聚合接口 |
| [解说猫 JSM](https://github.com/UUUBBB/JSM) | 仅 Windows 桌面程序，从 Releases 下载 | 仓库未声明 | 69 / 2026-07-02 | Gemini 分析场景、角色和剧情，TransNet 切镜头（用 GPU） | TTS 可选 Edge、IndexTTS、CosyVoice、豆包 | 支持，另可导出 CapCut、Premiere XML | 自配；更新日志提到激活码授权，价格未说明 |
| [narrator-ai-cli-skill](https://github.com/NarratorAI-Studio/narrator-ai-cli-skill) / [narrator-ai-cli](https://github.com/NarratorAI-Studio/narrator-ai-cli) | Agent skill + 命令行，调用商业平台 Narrator AI | MIT（客户端） | 3,080 / 2026-07-05；CLI 146 / 2026-07-27 | 在平台服务端完成，方式未说明 | 服务端合成，平台内置影片、BGM 和音色库 | 未说明 | 平台 API key 需向厂商申请，按积分扣费 |
| [video-recap-skills](https://github.com/zenstory-ai/video-recap-skills)（我们） | Agent skill，适配 Claude Code、Codex、OpenCode 等 | MIT | 598 / 2026-10-30 | 场景检测加抽帧，MiMo 做语音识别和画面理解 | 先剪后配，TTS 用 MiMo 或 Fish Audio，ffmpeg 混音加字幕出成片 | 支持（可选） | 需要小米 MiMo key，按量付费；写稿用 Agent 自己的模型 |

另外三个相关仓库：[video-recap-skills-plus](https://github.com/ops120/video-recap-skills-plus) 基于本仓库的旧版扩展，增加了字幕贴原片位置、声音克隆和解说时静音原声，本仓库的字幕带检测也改编自它；[Narrator-AI-Master-Coze](https://github.com/Narrator-AI/Narrator-AI-Master-Coze) 是面向转售的扣子（Coze）模板，仓库里是 SQL 脚本和教程，没有可运行的流程代码；[OpenMontage](https://github.com/calesthio/OpenMontage)（AGPL-3.0）是通用的 Agent 视频制作系统，没有专门的影视解说流程。

## 二、按你的情况选

**想要图形界面，装好就用。** 先看 NarratoAI。它星标最多，有 Windows 和 macOS 整合包，显卡不是必需，配音选择也多。只用 Windows 的，可以再看 JJYB_AI_VideoAutoCut 和解说猫 JSM：前者对镜头和配音时长的处理写得很清楚，但个人使用许可禁止未授权商用；后者需要激活码，仓库未声明许可证。superAIAutoCutVideo 半年没有更新，README 自述版本还不稳定。

**想尽量在本机跑模型。** 看 dramaxis 和 movie-narrator。两者都能接 Ollama 等本地模型；dramaxis 还用本地 Whisper 提字幕。movie-narrator 先按 TMDB 资料写稿再匹配画面，适合有公开资料的电影；它是 AGPL-3.0，默认的 Edge-TTS 仅限非商用。

**已经在用编程 Agent。** 看 video-recap-skills 和 narrator-ai-cli-skill，两者的路线不同。narrator-ai-cli-skill 是商业平台的客户端，写稿和合成都在平台服务端完成，需要向厂商申请 key 并按积分付费，好处是平台自带影片和音色素材。video-recap-skills 在本机用 ffmpeg 剪辑和渲染，只有语音识别、画面理解和配音调用 MiMo；每一拍旁白都记录取自哪段素材、哪一秒，依据是语音、画面还是硬字幕，方便核对解说有没有编造剧情。

## 三、选之前再核对三件事

1. **能否商用。** MIT 项目（NarratoAI、dramaxis、superAIAutoCutVideo、video-recap-skills）对工具本身的商用限制最少。JJYB_AI_VideoAutoCut 未经授权禁止商用；movie-narrator 是 AGPL-3.0，以服务形式提供修改版要公开源码；JSM 没有声明许可证。还要单独核对配音：Edge-TTS 等免费音色常有非商用限制。解说所用影视素材的版权和平台规则另算，与工具许可证无关。
2. **解说是否忠于画面。** 大多数工具让模型看抽帧或字幕后直接写稿，README 没有说明怎样防止模型补出画面里没有的情节。动手前可以用一段熟悉的片子试跑，逐句对照原片；具体检查方法见[AI 分析视频乱编剧情怎么办](ai-video-analysis-without-invented-plot.md)。
3. **费用花在哪里。** 工具本身免费，钱花在视觉模型、语音识别和配音上。片子越长、抽帧越密，调用越多。README 给出实测数字的只有个别项目，例如 video-recap-skills 自报一条完整视频约 1.3 元；用自己常做的片长试跑一条，按实际账单估算最可靠。

## 常见问题

**影视解说工具需要显卡吗？**

多数不需要，画面理解和配音走云端接口。需要显卡的通常是本地跑的部分：JSM 的镜头检测用 GPU，IndexTTS 一类本地配音模型要 8GB 以上显存。

**解说和视频翻译配音是一回事吗？**

两者是不同的任务。解说要重写文案、挑片段、重新剪辑；视频翻译配音（如 [pyvideotrans](https://github.com/jianchang512/pyvideotrans)、[YouDub-webui](https://github.com/liuzhao1225/YouDub-webui)）保留原片结构，只把语音转写、翻译后重新配音。只想把外语片配成中文，选后者更直接。

**导出剪映草稿有什么用？**

自动生成的成片很少一次到位。导出剪映草稿后，可以在剪映里继续调整片段、字幕和配音，不用从头剪。具体步骤见[导出剪映草稿继续编辑](capcut-jianying-draft-export.md)。

## 相关

- 解说文案怎么写：[影视解说文案怎么写](video-recap-script-writing.md)
- 安装与上手：[README](../README.md)
- 仓库地址：https://github.com/zenstory-ai/video-recap-skills
