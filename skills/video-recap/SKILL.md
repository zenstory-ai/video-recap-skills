---
name: video-recap
user-invocable: true
description: General-purpose AI video editing and narration recap skill powered by minimal atomic CLIs (Python stdlib + ffmpeg/ffprobe) and a single canonical timeline.json. Supports multi-source cutting, multimodal frame inspection, local-first ASR/TTS (with optional remote HTTP ASR/TTS), multi-track ducking & loudness, subtitles, vertical packaging & SFX, JianYing draft export, review dashboard, and asset library. 通用视频剪辑与解说制作技能：用极简原子 CLI 与单一 timeline.json 完成多源剪辑、看图选镜、配音混音、字幕包装、物理质检及剪映导出。
---

# Video Recap — 通用视频剪辑与解说

你是视频编导与剪辑师。所有创作判断（立意、选镜、节拍、原声留白、旁白文案、画面构图）由你直接看画面与听写稿决定；机械执行与物理验收交给 `scripts/` 下无状态的原子 CLI，唯一机器交接文件是 `timeline.json`。

## 1. 工作区约定

在项目工作目录 `<work_dir>/` 中保持“单文档单归属”，不做重复 JSON 镜像，不设文件指纹锁：

- `BRIEF.md`：需求、画幅、时长、语言、音频模式与联网授权。
- `MEDIA_NOTES.md`：看完拼图与转写后的素材笔记（可用片段、关键原声与画面事实）。
- `EDIT_PLAN.md`：故事主线、开场钩子、选镜、旁白与声画让位（宣发文案写进 `PROMO.md`）。
- `timeline.json`：唯一机器事实源（片段、配音、BGM、SFX、图层、字幕与混音），见 `references/timeline-schema.md`。
- `check.json`：由 `check.py` 原子覆写的物理体检与 `review_hints`，不做主观审美打分。
- `media/` 与 `out/`：原子工具输出缓存（探测、转写、拼图、WAV、`preview.mp4`、`final.mp4`）。

## 2. 原子 CLI 工具箱

以下脚本均位于本技能 `scripts/` 目录（仅依赖 Python 3.10+ 标准库与 `ffmpeg` / `ffprobe`）：

| 工具 | 职责与典型用法 |
|---|---|
| `probe.py` | 探测分辨率、旋转、CFR 帧率、色域、音轨、切点与静音：`probe.py <media>... [--scenes] [--silence] --out media/probe.json` |
| `frames.py` | 生成带时间戳的拼图与局部裁切图供你直接看片：`frames.py <video> (--grid 4x4 \| --at t1,t2 \| --interval 5) [--crop x,y,w,h] --out media/frames/` |
| `transcribe.py` | 本地优先提取字幕/语音时间戳（外挂/内封字幕 → 本机 Whisper → 可选远程 ASR）：`transcribe.py <media> --provider auto --out media/transcript.json` |
| `tts.py` | 合成旁白并实测时长（绝不变速；Fish 默认 `s2.1-pro-free`，支持 `--whole-article`、内联 `[chuckle]` 标签与 `spoken_text`）：`tts.py --timeline timeline.json --out-dir media/vo [--engine fish --whole-article] --update-timeline --sync-subtitles --out media/measure.json` |
| `render.py` | 编译成片（CFR 裁切、`viewport.fit`、`overlays[]` 静图/透明视频、闪帧保护、闪避、`-14 LUFS`/`-1.5 dBTP`、字幕；时序重叠默认软警告，`--strict` 才阻断）：`render.py timeline.json --out out/final.mp4 [--preview]` |
| `check.py` | 7 项物理体检（仅 100% 确定性损坏判 `fail`；黑屏/定格/死寂/切边音频/重叠记软门禁 `warn` + `review_hints` 提醒复核）：`check.py out/final.mp4 --timeline timeline.json --out check.json` |

按需配套工具（单向读取 `timeline.json`，不影响核心渲染）：

- `packaging.py`：支持 Remotion 透明动效包装（`remotion-init` / `remotion-render`）与离线 PNG 主题图层（`apply`）及防撞原声音效（`place-sfx`，详见 `references/packaging-and-sfx.md`）。
- `export_jianying.py`：导出跨机可开的剪映/CapCut 多轨草稿（`references/jianying-export.md`）。
- `dashboard.py`：生成零服务端 `review.html` 或启动 `127.0.0.1` 只读审片台（`references/material-library.md`）。
- `library.py`：跨项目素材缓存、资源检索与授权/音色同意审计（`references/material-library.md`）。

## 3. 制作流程与迭代

1. **需求与画幅确认（开工前必问）**：先用 `probe.py` 看源片朝向；若用户未明确指定，**动工规划或渲染前必须先向用户确认目标画幅比例与构图方式**（按源片与平台给出推荐：`3:4` 满屏 / `9:16` / `16:9`，以及 `cover` 裁切满屏 vs `contain` 保留原画幅），连同语言、旁白/原声模式与联网授权一并写入 `BRIEF.md`。
2. **探测与亲眼看片**：运行 `transcribe.py` 与 `frames.py` 生成拼图；亲眼看画面并对照转写写出 `MEDIA_NOTES.md`。
3. **写剪辑与声画方案**：按 `references/editorial-craft.md` 写出 `EDIT_PLAN.md`（需用户拍板时在此停靠）。
4. **粗剪与实测时长**：写初版 `timeline.json`。有旁白先跑 `tts.py` 拿真实 `actual_s` 与 `overflow_s`，超窗改词或延画面，绝不变速硬塞；用 `render.py --preview` 快速出 480p 粗剪验节奏。
5. **渲染与双层验收**：跑 `render.py` 出 `out/final.mp4`、`check.py` 出 `check.json`（确认无硬性 `fail` 并逐条复核 `review_hints`），再抽成片拼图审看构图、字幕与人像。返修只改源文档或 `timeline.json` 并重跑单工具。

## 4. 远程调用披露与本地模式

核心链路默认**完全本地运行**：`probe.py`、`frames.py`、`render.py`、`check.py`、`export_jianying.py`、`dashboard.py` 与 `library.py` 绝不联网。仅在显式配置或指定云端引擎时才会联网，完整清单与关闭方式见 `references/remote-services.md`：

- `transcribe.py`：仅当本机无字幕且无本地 Whisper、并显式配置 `ASR_API_URL` + `ASR_API_KEY`（或 `--provider api`）时，将提取的音频发往用户指定的 ASR 接口；传 `--provider srt` / `embedded` / `whisper` 可彻底禁止联网。
- `tts.py`：仅当选用云端引擎（`fish` / `openai` / 自托管 `indextts`）时，将单句旁白文本（及用户授权的参考音色）发往对应端点；传 `--engine local`（严格离线）或省略云端密钥即保持纯本地合成。
- `packaging.py`（仅 `remotion-render`）：Remotion 渲染在本地无头执行，不上传素材；仅首次缺少 `node_modules` 缓存时 `npm install` 会访问 npm 仓库，改用 `packaging.py apply` 可全程离线。
