# 开源 AI 影视解说工具怎么选：8 个项目按使用方式对比（2026）

把一段长视频做成中文解说短片，目前主流的开源项目按交互形态可以分为四类：**本地网页界面**（NarratoAI）、**桌面客户端**（dramaxis、解说猫 JSM、superAIAutoCutVideo、JJYB_AI_VideoAutoCut）、**命令行工具**（movie-narrator），以及**装进编程 Agent 的技能包**（video-recap-skills、narrator-ai-cli-skill）。挑选时先看你习惯哪种操作方式，再重点核对四件事：它靠什么“看懂”画面与对白、背后的大模型和配音怎么收费、能不能导出剪映草稿二次精修，以及开源许可证是否允许商用。

> 利益相关说明：本文由 `video-recap-skills` 维护团队整理，`video-recap-skills` 亦在对比表格中。所有项目均按同一套客观维度罗列，信息取自各项目公开仓库、README 与 LICENSE（截至 2026-10-31）；星标数与更新日期会随时间变化，选型前建议点击仓库链接核对最新状态。表中标为“未说明”仅代表其公开 README 未记载，不代表一定没有该功能。

## 一、8 个开源项目横向对比总表

| 项目 | 交互形态 | 开源许可证 | 星标 / 最近推送 | 怎么看懂视频 | 配音与成片渲染 | 剪映草稿导出 | 模型与 API 来源 |
|---|---|---|---|---|---|---|---|
| [NarratoAI](https://github.com/linyqh/NarratoAI) | 本地网页界面（Streamlit），提供 Windows / macOS 整合包与 Docker | MIT | 11,643 / 2026-10-24 | 抽帧送视觉大模型 + Fun-ASR 转写字幕 | 支持多种 TTS（IndexTTS、豆包、腾讯云等），自动加字幕合成 | 支持 | 兼容 OpenAI 格式接口，需自配云端 API Key |
| [JJYB_AI_VideoAutoCut](https://github.com/jianjieyiban/JJYB_AI_VideoAutoCut) | Windows 10/11 桌面客户端 | 作者自定个人使用许可（未经书面授权禁止商用） | 1,175 / 2026-10-18 | TransNetV2 或 OpenCV 切镜头 + 多家视觉模型 + faster-whisper / FunASR 转写 | 支持多种 TTS（含 IndexTTS2）；镜头装不下配音时直接报错拒绝，不用定格凑时长 | 支持 | 用户自配，视觉模型可选 GPT、Claude、Gemini、Qwen-VL 等 |
| [movie-narrator](https://github.com/zcbacxc/movie-narrator) | 命令行（PyPI 安装），另有独立网页前端包 | AGPL-3.0 | 1,149 / 2026-09-16 | 先根据 TMDB 影片资料写稿配音，再做场景检测与画面匹配；可选视觉模型描述镜头 | 默认使用 Edge-TTS（README 注明仅限非商用） | 未说明 | 默认调本地 Ollama，也支持智谱、百炼、MiMo、硅基流动 |
| [dramaxis](https://github.com/Agions/dramaxis)（原 Voxplore） | 跨平台桌面应用（Tauri） | MIT | 449 / 2026-10-27 | FFmpeg 场景切点检测 + 多模态关键帧分析 + 本地 Whisper 提取字幕 | 主打第一人称独白式解说；TTS 可选 Edge、GPT-SoVITS、MiniMax | 支持 | README 列出 11 家供应商，含 Ollama、LM Studio 本地模型 |
| [superAIAutoCutVideo](https://github.com/xiaohu2206/superAIAutoCutVideo) | 桌面应用（Tauri + FastAPI），安装包通过网盘分发 | MIT | 209 / 2026-04-19 | FunASR 转字幕 + moondream 看抽帧；短剧解说目前仅依赖字幕分析 | TTS 可选 Edge、腾讯云、Qwen3-TTS | 支持 | 接入 OpenRouter 等聚合接口 |
| [解说猫 JSM](https://github.com/UUUBBB/JSM) | Windows 桌面客户端（从 Releases 下载安装包） | 仓库未声明 | 69 / 2026-07-02 | Gemini 分析场景、角色与剧情 + TransNet 切镜头（需本地 GPU） | TTS 可选 Edge、IndexTTS、CosyVoice、豆包 | 支持，另可导出 CapCut 与 Premiere XML | 用户自配；更新日志提及需激活码授权，具体价格未公开 |
| [narrator-ai-cli-skill](https://github.com/NarratorAI-Studio/narrator-ai-cli-skill) / [narrator-ai-cli](https://github.com/NarratorAI-Studio/narrator-ai-cli) | Agent Skill + 命令行，底层调用商业平台 Narrator AI | MIT（仅客户端） | 3,080 / 2026-07-05；CLI 146 / 2026-07-27 | 在商业平台服务端完成，具体算法未公开 | 服务端云合成，平台内置成套影片、BGM 与音色库 | 未说明 | 需向平台厂商申请 API Key，按积分充值扣费 |
| [video-recap-skills](https://github.com/zenstory-ai/video-recap-skills)（本项目） | Agent Skill，直接装进 Claude Code、Codex、OpenCode、OpenClaw | MIT | 598 / 2026-10-30 | 本机 FFmpeg 场景检测与抽帧 + 小米 MiMo 语音转写与画面理解 | 先剪画面后配旁白；TTS 支持 MiMo / Fish Audio / IndexTTS，本机 FFmpeg 混音烧字幕 | 支持（可选） | 仅需一个小米 MiMo Key 按量计费；写稿直接用 Agent 自身的模型 |

此外还有三个常被搜到的相关仓库：
- [video-recap-skills-plus](https://github.com/ops120/video-recap-skills-plus)：基于本仓库早期版本派生的扩展分支，加入了原片字幕位置检测、音色克隆与旁白期间静音原声（本仓库现已吸收并重写了字幕带位置检测能力）。
- [Narrator-AI-Master-Coze](https://github.com/Narrator-AI/Narrator-AI-Master-Coze)：面向扣子（Coze）工作流的商业转售模板仓库，内含 SQL 脚本与图文教程，不含本地可运行的剪辑代码。
- [OpenMontage](https://github.com/calesthio/OpenMontage)（AGPL-3.0）：面向通用视频生产的 Agent 编排系统，并未针对中文影视解说做专门的声画让位或句界门禁设计。

## 二、按你的使用场景对号入座

- **想要点点鼠标就能用的图形界面**：优先看 **NarratoAI**。它社区体量最大，提供开箱即用的 Windows / macOS 整合包，不挑本地显卡，对接的国内配音接口也很全。如果主力用 Windows，还可以对比 **JJYB_AI_VideoAutoCut**（对配音时长与镜头长度的对齐处理很扎实，但注意其许可证禁止未授权商用）与 **解说猫 JSM**（支持导出 Premiere XML，但依赖本地 GPU 且需激活码）。
- **想尽量在自己电脑上跑免费本地模型**：重点看 **dramaxis** 和 **movie-narrator**。两者都能直接挂本地 Ollama 模型；dramaxis 还能用本地 Whisper 跑字幕提取。movie-narrator 采用“先抓 TMDB 影评资料写稿、再回头找画面配对”的逆向做法，适合公开资料丰富的院线老电影，但需留意其 AGPL-3.0 协议及 Edge-TTS 的非商用限制。
- **平时已经在用 Claude Code、Codex 等编程 Agent**：对比 **video-recap-skills** 与 **narrator-ai-cli-skill**。后者本质上是商业 SaaS 平台的命令行遥控器，理解、写稿和渲染全在厂商云端黑盒完成，按积分扣费；而 **video-recap-skills** 把剪辑、句界门禁和混音渲染全部留在你本机的 `ffmpeg` 上执行，云端只按量调用小米 MiMo 做转写、看图和配音，并且强制要求每一拍解说都写明对应原片哪一秒的画面或原声证据，从根上管住模型乱编剧情。

## 三、动手前务必核对的三笔账

1. **商用合规账**：MIT 协议项目（NarratoAI、dramaxis、superAIAutoCutVideo、video-recap-skills）对软件本身的商用限制最宽松；`JJYB_AI_VideoAutoCut` 明确禁止未经书面授权的商业使用；`movie-narrator` 采用 AGPL-3.0；`JSM` 未声明开源协议。除此之外，还要单独确认**配音接口**（例如微软 Edge-TTS 条款明确限制商业用途）以及**原片影视素材自身**的版权归属。
2. **事实准确账（防 AI 脑补）**：绝大多数自动化工具让模型扫一眼抽帧或残缺字幕就直接生成全篇解说词，极易把“两个人说话”脑补成“密谋背叛”。正式投产前，建议拿一集你熟知剧情的片子试跑，逐句核对解说词有没有无中生有；具体防脑补方法见[《AI 分析视频总爱乱编剧情怎么办》](ai-video-analysis-without-invented-plot.md)。
3. **API 消耗账**：开源软件本身免费，真金白银花在云端视觉理解、语音转写和 TTS 配音上。原片越长、抽帧越密，账单越高。以 `video-recap-skills` 实测为例，跑完一条完整视频的 MiMo API 消耗约 1.3 元人民币；建议先拿自己日常要做的单集片长实测一轮，算清每条成片的真实边际成本。

## 常见问题

**跑这些影视解说工具需要配独立显卡（GPU）吗？**

大部分不需要，因为最吃算力的画面理解和配音通常走云端 API。只有当你选择在本地跑镜头切点模型（如 JSM 的 TransNet）、本地 Whisper 语音识别，或者本地部署 IndexTTS / GPT-SoVITS 配音模型时，才需要 8GB 以上显存的独立显卡。

**“影视解说”和“外语视频翻译配音”是一回事吗？**

完全是两码事。影视解说需要从几十分钟原片里挑出核心冲突镜头重新拼剪、重写解说词，并与关键原声做声画交接；而视频翻译配音（例如 [pyvideotrans](https://github.com/jianchang512/pyvideotrans)、[YouDub-webui](https://github.com/liuzhao1225/YouDub-webui)）则是原封不动保留原片画面时长，只把外语对白逐句翻译替换成中文配音。如果你只是想把英文播客或公开课转成中文配音，选专业翻译工具更对口（本仓库也内置了实验性的 `--edit-mode dub` 克隆译制路径）。

**为什么大家都在看支不支持“导出剪映草稿”？**

因为全自动生成的成片很难每一刀、每一句气口都百分百合你心意。支持导出剪映草稿意味着：AI 帮你把最苦最累的扒片、初剪、配音和打轴干完，最后留出 10% 的微调空间让你在剪映里拖一拖片段、改两个字或换首 BGM，不用为了改一秒画面重跑整套流程。具体用法见[《导出剪映草稿继续精修》](capcut-jianying-draft-export.md)。

## 延伸链接

- 解说文案写作实战：[《影视解说文案怎么写》](video-recap-script-writing.md)
- 快速安装与上手：[仓库 README](../README.md)
- 代码仓库：https://github.com/zenstory-ai/video-recap-skills
