# 仓库架构说明

本仓库采用 `1 + 1` 自包含技能结构：通用视频剪辑与解说主技能 `skills/video-recap/`（6 支核心本地 CLI + 4 支单向配套工具，围绕同一份 `timeline.json` v3 协作），加上按需参考拆片技能 `skills/video-reference/`（1 支本地测量与抽帧 CLI，产出脱敏 Markdown 参考笔记）。架构决策记录见 `.agents/notes/implemented/architecture/2026-11-21-atomic-primitives-and-timeline-bus.md`。

## 技能拓扑与统一时间线

```text
源视频 ─▶ probe.py / frames.py / transcribe.py ─▶ Agent 看图定主线，写 MEDIA_NOTES.md & EDIT_PLAN.md
                                                          │
                                                          ▼
             timeline.json (v3 统一时间线契约) ◀── tts.py (配音测时) / packaging.py (按需包装与音效)
                 │            │              │              │
                 ▼            ▼              ▼              ▼
             render.py     check.py   export_jianying.py  dashboard.py / library.py
```

- **创作判断与本地执行分离**：选材、立意、节奏、原声留白、旁白文案与画面构图由宿主多模态 Agent 对照抽帧拼图和字幕稿完成，记录在人类可读的 Markdown（`BRIEF.md`、`MEDIA_NOTES.md`、`EDIT_PLAN.md`、`PROMO.md`）中；Python 脚本只负责无状态的媒体探测、抽帧、本地优先转写/配音、帧精确渲染、物理质检与配套导出。
- **统一 `timeline.json` v3 契约，无中间状态锁**：不再维护跨阶段中间 JSON 和 `{size, mtime_ns}` 指纹锁。修改任意镜头、字幕、配音或图层后，直接局部重跑对应的单个 CLI 即可。
- **核心与配套单向隔离**：核心模块（`_common.py`、`_audio.py`、`_subtitles.py` 及 6 支核心 CLI）不导入配套工具（`export_jianying.py`、`dashboard.py`、`packaging.py`、`library.py`）；配套工具之间互不导入，统一通过 `timeline.json` 与工作区文件交接（`export_jianying.py` 与 `render.py` 共用 `_audio.py` 的闪避曲线计算）。
- **技能彼此自包含**：`skills/video-recap/` 与 `skills/video-reference/` 均可独立拷贝安装，互不导入、不引用对方路径；`video-reference` 产出的 `REFERENCE_NOTES.md` 仅供 Agent 阅读参考，不被生产脚本强制消费。

## 模块划分

| 技能 | 层次 | 脚本与资产 | 职责 |
|---|---|---|---|
| `video-recap` | 核心内部模块（3） | `_common.py`、`_audio.py`、`_subtitles.py` | 命令封装/原子 JSON 写入/错误脱敏、闪避曲线与响度处理、SRT/ASS 字幕生成 |
| `video-recap` | 核心本地 CLI（6） | `probe.py`、`frames.py`、`transcribe.py`、`tts.py`、`render.py`、`check.py` | 媒体探测、拼图抽帧、本地优先转写、配音测时、多轨渲染、7 项物理质检 |
| `video-recap` | 配套工具（4） | `packaging.py`、`export_jianying.py`、`dashboard.py`、`library.py` | Remotion/PNG 包装与防撞音效、剪映草稿导出、审片页与本地审片台、跨项目素材库与授权审计 |
| `video-recap` | 静态资产与模板 | `assets/dashboard.html`、`assets/themes/*.json`、`assets/remotion/*`、`references/jianying/*` | 审片页模板、竖横屏包装主题、Remotion 透明包装工程、剪映协议骨架 |
| `video-reference` | 独立拆片技能（1） | `analyze_reference.py` | 本地测量参考成片的切点分布、镜头时长、响度密度并生成拼图 |

## 自动化架构约束与预算检查

运行 `python3 scripts/test.py orchestrator` 自动核验以下约束：

- **技能文档预算**：单份 `SKILL.md <= 4,000` 字符、单份参考 `<= 2,500` 字符、全仓 `skills/**/*.md <= 22,000` 字符。
- **运行时 Python 预算**：单文件 `<= 550` 行、全仓 `skills/*/scripts/**/*.py <= 5,000` 行、单技能 `<= 20` 个 `.py` 文件、环境变量总数 `<= 25` 个。
- **隔离与代码规范**：禁止跨技能导入或路径引用、技能内 import 图无环、核心模块不导入配套工具、所有脚本可在 `python -I` 隔离拷贝下独立导入、所有文本 I/O 显式指定 `encoding="utf-8"`。
