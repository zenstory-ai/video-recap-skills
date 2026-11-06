# 仓库架构说明

本仓库包含七个自包含技能：编排器按阶段以独立子进程调用各个生产技能，各阶段之间仅通过工作目录（`work_dir`）下的 JSON 与 MP4 文件交接数据。各技能的完整命令与参数见对应目录下的 `SKILL.md` 及 `--help` 输出；历次架构取舍笔记可通过 `rg --hidden <关键词> .agents/notes/` 检索。

## 技能拓扑与数据交接

```text
                          video-recap（轻量编排器，以子进程调用各阶段）
                                         │
video-understanding ─▶ Agent 按 video-script 制定方案并写稿 ─▶ [video-cut] ─▶ video-voiceover ─▶ video-assemble
```

- **按需参考技能独立于主链之外**：`video-reference` 不在日常生产流水线上，仅在用户需要拆解优秀成片时按需运行；它导出的 `production_reference.json` 仅供写稿 Agent 阅读参考，无任何生产脚本机械消费该文件（登记为资源库模板并通过 `--project` 绑定时，编排器也只是把副本拷进 `work_dir`）。
- **每个技能彻底自包含**：七个技能均可单独拷贝安装；`skills/<name>/scripts/`（含子包）只允许 `import` 本技能内部的模块，源码与文档中严禁引用兄弟技能的路径（背景与代价见 `architecture/2026-06-14-self-contained-skills-duplicated-libs.md`）。
- **创作拍板权归 Agent，机械执行归脚本**：`clip_plan.json`、`narration.json`、`dub_script.json` 以及 `recap_story_plan.json`、`visual_audio_board.json` 全部由 Agent 编写；Python 脚本只负责生成创作简报、执行确定性门禁校验、调用 TTS 接口与运行 ffmpeg 渲染（见 `architecture/2026-05-18-agent-owned-narration-cli-mechanical.md`）。
- **单一时间线真值**：最终交付以 ffmpeg 渲染出的成片与后端无关的 `timeline.json` 为准，剪映草稿只是消费同一份 `timeline.json` 的可选导出物（见 `architecture/2026-06-14-timeline-json-canonical-jianying-optional.md`）。

## 各技能职责与模块划分

| 技能目录 | 核心职责 | 顶层入口脚本 | 内部功能子包 |
|---|---|---|---|
| `video-understanding` | 场景切点检测、ASR 语音转写、VLM 抽帧分析、静音与句末锚点检测，以及生成供 Agent 写稿的 `agent_narration_brief.md`（含 `cut` 模式二阶段的输出时间轴简报） | `understand.py`（传 `--brief-only` 仅重建简报）、`consolidate.py` | `briefing/` |
| `video-script` | 导演/剪辑/旁白创作规范与 schema 约定、解说词确定性门禁校验、建议型/严格型语义评审 | `validate.py`、`review.py` | — |
| `video-cut` | 按 `clip_plan.json` 吸附句界并渲染 `edited_source.mp4` 与 `clip_plan_validated.json`；成片短镜头与密集切点扫描复核 | `cut.py`、`shot_review.py` | — |
| `video-voiceover` | 解说旁白分段语音合成（支持 MiMo、Fish Audio、自托管 IndexTTS）及实验性英译中原声克隆配音 | `voiceover.py`、`dub.py` | `providers/` |
| `video-assemble` | 多轨混音、原声闪避（ducking）、字幕生成与烧录、`timeline.json` 输出、组装质检、剪映草稿导出、严格采用三件套合成 | `assemble.py`、`export_jianying.py`、`source_score.py` | `subtitles/`、`adoption/`、`jianying/` |
| `video-recap` | 跨阶段编排、断点续跑校验、文件素材库与制作资源库管理、成片终审质检、只读本地看板（dashboard） | `recap.py`、`recap_inspect.py`、`library.py`、`dashboard_server.py`、`doctor.py`、`final_qc.py` | `dashboard/`、`resources/` |
| `video-reference` | 成片镜头节奏与响度实测、原片事实与可迁移方法分离校验（R1–R8）、导出脱敏 `production_reference.json` | `reference.py` | — |

子包划分原则（见 `architecture/2026-09-21-scripts-subpackages-jianying.md` 与 `architecture/2026-09-21-adoption-family-stays-in-skill-as-subpackage.md`）：`scripts/` 顶层只保留 CLI 入口与每次运行必经的主干模块；仅在特定模式下触发的功能族（如剪映草稿导出 `jianying/`、严格音频采用 `adoption/`、只读看板 `dashboard/`）各自收进独立子包；`video-recap` 下的 `resources/` 则按“资源库消费与加锁”归为一族，在每次 `full` 或 `cut` 合成后写出 `resource_lock.json`。

## 编排器内部路由

`--edit-mode` 与 `--audio-mode` 的合法组合及暂停点定义在 `skills/video-recap/SKILL.md` §1 的总表中，对应 `skills/video-recap/scripts/recap_runner.py` 中的四条执行分支：

- `_run_local_adoption`：传入已剪好的画面母版与本地采用三件套 JSON，直接调用 `video-assemble` 完成严格合成。
- `_run_dub`：实验性英译中克隆配音模式，中间暂停一次等待 `dub_script.json`。
- `_run_single` 与 `_run_multi_cut`：分别处理单视频 `full / cut` 与多视频 `cut` 主流程。两条路径收尾时共用 `_deliver`（语义评审 → TTS 合成 → 画面花字转换 → 音画组装 → 成片确定性 QC）；所有 `cut` 路径共用 `_render_cut`、`_reject_stale_cut_narration` 与 `_validate_cut_output_narration`。

## 跨技能同名函数的同步守卫（Function-level Parity）

为了保证每个技能目录彻底自包含，极少数必须在多个技能间保持行为完全一致的底层纯函数采用“源码复制 + 自动化 AST 一致性测试”维护，具体清单以 `tests/orchestrator/test_brief_narration_parity.py` 中的 `SHARED_FUNCTIONS` 为准：

1. **字数预算与分句原语**（`video-understanding` 的简报预算计算与 `video-script` 的旁白校验共用）：`_recommended_char_budget`、`_scene_available_seconds`、`_sentence_pieces`、`_text_units` 及对应的 4 个预算配置项。
2. **纯语气词过滤规则**（`video-cut`、`video-script`、`video-assemble` 三方共用，确保对白避让尺度一致）：`_interjection_only`、`_dialogue_speech_spans` 及其关联常量，由 `test_interjection_rule_stays_identical_across_cut_script_assemble` 守护。

每一个复制函数都必须在所在技能内部被真实调用，绝不为了凑测试而保留死代码（见 `simplification/2026-10-02-function-level-parity.md`）。

## 自动化架构守卫

运行 `python3 scripts/test.py orchestrator` 会自动校验以下架构红线：禁止跨技能 `import` 或路径引用、单个 Python 模块不超过 800 行、技能内部 `import` 图无环、每份 `lib.py` 只声明本技能实际读取的配置项、跨技能复制函数 AST 完全一致，以及剪映导出子包绝不被核心渲染主链反向依赖。
