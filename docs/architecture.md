# 仓库架构说明

本仓库包含两个自包含技能（`1 + 1` 结构）：生产主技能 `skills/video-recap/` 将视频理解、故事与选镜策划、画面剪辑、旁白创作校验、TTS 配音和音画混音合成收敛在同一技能内一气呵成完成；按需拆片技能 `skills/video-reference/` 独立于日常生产流水线之外，用于将优秀成片拆解为可复用的 `production_reference.json`。各技能的完整命令与参数见对应目录下的 `SKILL.md` 及 `--help` 输出；历次架构取舍笔记可通过 `rg --hidden <关键词> .agents/notes/` 检索。

## 技能拓扑与数据交接

```text
                               skills/video-recap/（统一生产技能，recap.py 串联全流程）
                                                        │
understand.py ─▶ Agent 制定方案并写 clip_plan.json / narration.json ─▶ [cut.py] ─▶ validate.py ─▶ voiceover.py ─▶ assemble.py
```

- **创作一气呵成**：导演立意（`recap_story_plan.json`）、分镜与声画让位（`visual_audio_board.json`）、选镜剪辑（`clip_plan.json`）和解说撰稿（`narration.json`）同属一个连续创作过程，不再拆成多个技能目录来回跳转（见 `architecture/2026-11-10-converge-production-skills-into-video-recap.md`）。
- **按需参考技能独立于主链之外**：`video-reference` 不在日常生产流水线上，仅在用户需要拆解优秀成片时按需运行；它导出的 `production_reference.json` 仅供写稿 Agent 阅读参考，无任何生产脚本机械消费该文件（登记为资源库模板并通过 `--project` 绑定时，编排器也只是把副本拷进 `work_dir`）。
- **每个技能彻底自包含**：两个技能均可单独拷贝安装；`skills/<name>/scripts/`（含子包）只允许 `import` 本技能内部的模块，源码与文档中严禁引用兄弟技能的路径。
- **创作拍板权归 Agent，机械执行与底线防守归脚本**：`clip_plan.json`、`narration.json` 以及 `recap_story_plan.json`、`visual_audio_board.json` 全部由 Agent 编写；Python 脚本只负责生成创作简报、执行确定性底线门禁（防残镜/碎镜、防台词截断、防旁白抢话、防时间窗超载与超长削尾、防必保画面丢失、防占位符泄漏）、调用 TTS 接口与运行 ffmpeg 渲染（见 `architecture/2026-05-18-agent-owned-narration-cli-mechanical.md` 与 `simplification/2026-11-10-scripts-for-hands-and-floor-agent-for-craft.md`）。
- **单一时间线真值**：最终交付以 ffmpeg 渲染出的成片与后端无关的 `timeline.json` 为准，剪映草稿只是消费同一份 `timeline.json` 的可选导出物（见 `architecture/2026-06-14-timeline-json-canonical-jianying-optional.md`）。

## 技能与内部模块划分

| 技能 / 阶段 | 核心职责 | 顶层入口脚本 | 内部功能子包 |
|---|---|---|---|
| `video-recap`（编排与公共底座） | 跨阶段编排、断点续跑校验、文件素材库与制作资源库管理、成片终审质检、环境自检、只读本地看板（dashboard） | `recap.py`、`recap_inspect.py`、`library.py`、`dashboard_server.py`、`doctor.py`、`final_qc.py` | `dashboard/`、`resources/` |
| `video-recap`（理解阶段） | 场景切点检测、ASR 语音转写、VLM 抽帧分析、静音与句末锚点检测，以及生成供 Agent 写稿的 `agent_narration_brief.md`（含 `cut` 模式二阶段的输出时间轴简报） | `understand.py`（传 `--brief-only` 仅重建简报） | `briefing/` |
| `video-recap`（创作校验阶段） | 导演/剪辑/旁白创作规范与 schema 约定、解说词确定性底线校验、Agent 自检清单 | `validate.py` | — |
| `video-recap`（剪辑阶段） | 按 `clip_plan.json` 吸附句界并渲染 `edited_source.mp4` 与 `clip_plan_validated.json`；成片短镜头与密集切点扫描复核 | `cut.py`、`shot_review.py` | — |
| `video-recap`（配音阶段） | 解说旁白分段语音合成（支持 MiMo、Fish Audio、自托管 IndexTTS） | `voiceover.py` | `providers/` |
| `video-recap`（合成阶段） | 多轨混音、原声闪避（ducking）、字幕生成与烧录、`timeline.json` 输出、组装质检、剪映草稿导出 | `assemble.py`、`export_jianying.py` | `subtitles/`、`jianying/` |
| `video-reference`（独立按需技能） | 成片镜头节奏与响度实测、原片事实与可迁移方法分离校验（R1–R8）、导出脱敏 `production_reference.json` | `reference.py` | — |

子包划分原则（见 `architecture/2026-09-21-scripts-subpackages-jianying.md`）：`scripts/` 顶层只保留 CLI 入口与主干模块；仅在特定阶段或按需功能下触发的模块族（如简报生成 `briefing/`、TTS 后端 `providers/`、字幕生成 `subtitles/`、剪映草稿导出 `jianying/`、只读看板 `dashboard/`、资源库绑定与资源锁 `resources/`）各自收进独立子包。

## 编排器内部路由

`--edit-mode` 与 `--audio-mode` 的合法组合及暂停点定义在 `skills/video-recap/SKILL.md` §1 的总表中，对应 `skills/video-recap/scripts/recap_runner.py` 中的两条执行分支：

- `_run_single` 与 `_run_multi_cut`：分别处理单视频 `full / cut` 与多视频 `cut` 主流程。
- 两条路径收尾时共用 `_deliver`：（有旁白时执行 TTS 合成与画面花字转换）→ 音画组装 → 成片确定性 QC；所有 `cut` 路径共用 `_render_cut`、`_reject_stale_cut_narration` 与 `_validate_cut_output_narration`。

## 自动化架构守卫

运行 `python3 scripts/test.py orchestrator` 会自动校验以下架构红线：禁止跨技能 `import` 或路径引用、单个 Python 模块不超过 800 行、技能内部 `import` 图无环、`lib.py` 只声明本技能实际读取的配置项，以及剪映导出子包绝不被核心渲染主链反向依赖。
