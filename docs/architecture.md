# 仓库架构说明

本仓库采用 `1 + 1` 自包含技能结构：通用视频剪辑与解说主技能 `skills/video-recap/`（6 支核心原子 CLI + 4 支单向配套叶工具，围绕单一 `timeline.json` v3 协作），加上按需拆片参考技能 `skills/video-reference/`（1 支本地测量与抽帧 CLI，产出脱敏 Markdown 笔记）。架构演进见 `.agents/notes/implemented/architecture/2026-11-21-atomic-primitives-and-timeline-bus.md`。

## 技能拓扑与单一数据总线

```text
源视频 ─▶ probe.py / frames.py / transcribe.py ─▶ Agent 亲眼看片并写 MEDIA_NOTES.md & EDIT_PLAN.md
                                                          │
                                                          ▼
             timeline.json (v3 单一机器契约) ◀── tts.py (实测时长) / packaging.py (按需主题图层)
                 │            │              │              │
                 ▼            ▼              ▼              ▼
             render.py     check.py   export_jianying.py  dashboard.py / library.py
```

- **判断归 Agent，机械归原子 CLI**：选材、立意、节奏、原声留白、旁白文案与审美复核全由宿主多模态 Agent 直接看拼图与转写完成，记录在普通 Markdown（`BRIEF.md`、`MEDIA_NOTES.md`、`EDIT_PLAN.md`、`PROMO.md`）；Python 脚本只做无状态媒体探测、抽帧、本地优先转写/配音、帧精确渲染、物理体检与配套导出。
- **单一 `timeline.json` v3 契约，零清单锁**：删除旧版 50 个跨阶段 JSON 与 `{size, mtime_ns}` 指纹锁。修改任意镜头、字幕、配音或图层后，直接重跑受影响的单个 CLI 即可。
- **核心与配套严格单向隔离**：核心模块（`_common.py`、`_audio.py`、`_subtitles.py` 及 6 支核心 CLI）绝不导入配套叶工具（`export_jianying.py`、`dashboard.py`、`packaging.py`、`library.py`）；叶工具之间互不导入，统一通过 `timeline.json` 与工作区文件交互（`export_jianying.py` 与 `render.py` 共用 `_audio.py` 中的闪避曲线计算）。
- **每个技能彻底自包含**：`skills/video-recap/` 与 `skills/video-reference/` 均可独立拷贝安装，互不导入、不互相引用路径；`video-reference` 产出的 `REFERENCE_NOTES.md` 仅供 Agent 阅读，无任何生产脚本机械消费。

## 模块划分

| 技能 | 层次 | 脚本与资产 | 职责 |
|---|---|---|---|
| `video-recap` | 核心内部模块（3） | `_common.py`、`_audio.py`、`_subtitles.py` | 通用命令/原子 JSON/错误脱敏、闪避与响度闭环、SRT/ASS 字幕生成 |
| `video-recap` | 核心原子 CLI（6） | `probe.py`、`frames.py`、`transcribe.py`、`tts.py`、`render.py`、`check.py` | 探测、拼图抽帧、本地优先转写、实测时长配音、多轨渲染、7 项物理体检 |
| `video-recap` | 配套叶工具（4） | `packaging.py`、`export_jianying.py`、`dashboard.py`、`library.py` | 数据驱动主题包装与防撞音效、剪映草稿导出、静态/本地审片台、跨项目素材库与合规审计 |
| `video-recap` | 静态资产与骨架 | `assets/dashboard.html`、`assets/themes/*.json`、`references/jianying/*` | 审片页模板、竖横屏 JSON 主题、剪映协议骨架 |
| `video-reference` | 独立按需技能（1） | `analyze_reference.py` | 本地实测参考成片切点、节奏曲线、响度密度与拼图 |

## 自动化架构与防膨胀守卫

运行 `python3 scripts/test.py orchestrator` 自动守卫以下红线：

- **技能散文预算**：单份 `SKILL.md <= 4,000` 字符、单份参考 `<= 2,500` 字符、全仓 `skills/**/*.md <= 22,000` 字符。
- **运行时 Python 预算**：单文件 `<= 550` 行、全仓 `skills/*/scripts/**/*.py <= 5,000` 行、单技能 `<= 20` 个 `.py` 文件、环境变量总数 `<= 25` 个。
- **隔离与纯度**：禁止跨技能导入/引用、技能内 import 图无环、核心模块禁止导入配套叶工具、所有脚本可在 `python -I` 隔离拷贝下独立导入、所有文本 I/O 显式声明 `encoding="utf-8"`。
