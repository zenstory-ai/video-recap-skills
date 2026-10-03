# 仓库架构

给改代码的人看的一页地图。用法与参数以各技能 `SKILL.md` 和 `--help` 为准；每条边界背后的取舍在
`.agents/notes/implemented/` 里，用 `rg --hidden <关键词> .agents/notes/` 检索。

## 技能与通信

```text
                         video-recap（编排器，子进程调用各阶段）
                                        │
video-understanding ─▶ Agent 按 video-script 写计划与旁白 ─▶ [video-cut] ─▶ video-voiceover ─▶ video-assemble
```

- video-reference 不在这条链上：按需对成片运行，导出的 `production_reference.json` 只给写稿 Agent 阅读，没有脚本按内容使用它；
  它可以登记成资源库模板，经 `--project` 绑定时 recap 只把副本复制进 `work_dir`。
- 七个技能各自可单独安装；`skills/<name>/scripts/`（含其子包）只 import 本技能的模块，阶段之间只交换 `work_dir` 里的
  JSON / MP4。自包含的理由与代价见 `architecture/2026-06-14-self-contained-skills-duplicated-libs.md`。
- 创作产物（`clip_plan.json`、`narration.json`、`dub_script.json`、两份创作计划）由 Agent 写；脚本只准备
  brief、做确定性校验、执行 TTS 与渲染（`architecture/2026-05-18-agent-owned-narration-cli-mechanical.md`）。
- 成片以 ffmpeg 渲染结果与 `timeline.json` 为准，剪映草稿是可选附件
  （`architecture/2026-06-14-timeline-json-canonical-jianying-optional.md`）。

## 各阶段拥有什么

| 技能 | 拥有 | 入口脚本 | 功能子包 |
|---|---|---|---|
| video-understanding | 场景 / ASR / VLM / 静音分析，以及写给 Agent 的 `agent_narration_brief.md`（含 cut 第二遍的输出时间 brief） | `understand.py`（`--brief-only` 只重建 brief）、`consolidate.py` | `briefing/` |
| video-script | 创作方法与产物格式；旁白确定性校验；建议型评审 | `validate.py`、`review.py` | — |
| video-cut | 按 `clip_plan.json` 渲染 `edited_source.mp4` 与 `clip_plan_validated.json`；修短残镜复核 | `cut.py`、`shot_review.py` | — |
| video-voiceover | 旁白 TTS（MiMo / Fish Audio / index-tts）与英译中克隆配音 | `voiceover.py`、`dub.py` | `providers/` |
| video-assemble | 混音、字幕、渲染、`timeline.json`、组装 QC、剪映导出、严格采用路径 | `assemble.py`、`export_jianying.py`、`source_score.py` | `subtitles/`、`adoption/`、`jianying/` |
| video-recap | 编排、断点续跑、素材库与资源库、成片 QC、只读 dashboard | `recap.py`、`recap_inspect.py`、`library.py`、`dashboard_server.py`、`doctor.py`、`final_qc.py` | `dashboard/`、`resources/` |
| video-reference | 按需，不在流水线上：成片的镜头/响度测量、事实与方法分离校验（R1–R8）、导出 `production_reference.json` | `reference.py` | — |

功能子包的划分规则见 `architecture/2026-09-21-scripts-subpackages-jianying.md` 与
`architecture/2026-09-21-adoption-family-stays-in-skill-as-subpackage.md`：顶层只留入口与每次都会走到的核心模块，
只在特定参数下才走到的一族模块（剪映导出、严格采用、dashboard……）收进子包；video-recap 的 `resources/`（项目绑定与资源锁）
按"资源库的消费方"成族归组，资源锁每次 full / cut 都会写。

## 编排路径

`--edit-mode × --audio-mode` 的合法组合与各自的暂停点列在 `skills/video-recap/SKILL.md` §1 的表里。代码里的对应关系
（`skills/video-recap/scripts/recap_runner.py`）：

- `_run_local_adoption`：已剪母版 + 三个采用 JSON，直接严格合成。
- `_run_dub`：dub 模式，一次暂停。
- `_run_single`：单视频 full / cut；`_run_multi_cut`：多视频 cut。
- 两者都在最后调用 `_deliver`：（有旁白时）评审 → TTS → 视觉叠层 → 合成 → 成片 QC。cut 两条路径共用
  `_render_cut`、`_reject_stale_cut_narration`、`_validate_cut_output_narration`。

## 刻意复制的模块

复制按函数算，不按文件算：同一个函数出现在两个技能里时 AST 必须一致，清单以
`tests/orchestrator/test_brief_narration_parity.py` 的 `SHARED_FUNCTIONS` 为准。目前只有 video-understanding 的
brief 预算与 video-script 的旁白 lint 共用的四个文本原语（`_recommended_char_budget`、`_scene_available_seconds`、
`_sentence_pieces`、`_text_units`），以及它们读取的四个预算 CONFIG 键；另有 video-cut、video-script、video-assemble 三份
语气词判定（`_interjection_only`、`_dialogue_speech_spans` 及其常量），由同一文件的 `test_interjection_rule_stays_identical_across_cut_script_assemble` 校验。每个副本都必须在本技能内被调用，
不为凑 parity 保留死副本。模块各归一个技能：brief 生成链与 `timeline_fusion` 只在 video-understanding，
`narration_lint`、`speech_ownership`、`deslop_qc` 只在 video-script；两边各有一个 `agent_text`，内容不同
（`simplification/2026-10-02-function-level-parity.md`）。

## 结构守卫

`python3 scripts/test.py orchestrator` 覆盖：禁止跨技能 import 与路径引用、每个脚本模块不超过 800 行、技能内 import 无环、
每份 `lib.py` 只声明自己读取的配置键、复制函数保持一致、剪映模块不进入核心渲染路径。
