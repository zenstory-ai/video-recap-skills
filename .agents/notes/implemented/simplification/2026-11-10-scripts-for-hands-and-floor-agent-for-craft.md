# Agent Note: 脚本只做“手”和“底线体检”，删除替模型预消化、品味规则、自家产物校验与旧兼容层

Status: implemented

## Problem

仓库技能脚本曾累积约 3.2 万行代码与 3.3 万行测试。随着基础模型能力增强，视频剪辑与解说中“怎么把视频做好看”无法靠工程规则穷举；而代码里大量替模型做预消化/归纳/出建议（P）、用词表与阈值写死品味规则（T）、校验自家中间产物契约与保留旧版 schema 兼容层（C），不仅越来越尴尬，甚至在源头抹掉或改写客观证据（如 `detect._merge_short_scenes` 合并掉 4 秒以内的碎镜、`asr._correct_text_with_glossary` 静默改写 ASR 原始转写），或把风格偏好做成硬阻断（如 `deslop_qc` 的破折号阻断、`cut_qc` 的时长比例阻断）。

[[2026-05-18-agent-owned-narration-cli-mechanical]] 已确立“创作归 Agent、脚本做机械活”，需要将脚本职责收束回 **H（确定性执行与测量）** 与 **F（客观底线体检：残镜/碎镜、台词截断、旁白压台词、定稿删字、必保画面、占位符泄漏）**。

## Decision

清理并删除确认随模型增强越发尴尬的四类代码（替模型预处理/归纳/出建议、代码写死的品味规则、自家产物校验、旧兼容层），保留全部 H（执行与测量）与 F（底线门禁）：

1. **删除替模型预消化、归纳与出建议（P）**：
   - 删除 `skills/video-understanding/scripts/consolidate.py`、`index_normalize.py`、`timeline_fusion.py`，以及 `--consolidate` / `--no-consolidate` / `--consolidate-asr` 参数、`understanding_index.{json,md}`、`asr_clean.json`、`consolidation.status.json`、`timeline_fusion.json`、`asr_writing_chunks.json`（翻转并取代 [[2026-10-03-ordinal-plot-time-reads-unit-start]]、[[2026-10-03-reference-reads-consolidate-asr-mentions]] 中关于 `consolidate` 与 `understanding_index.json` 的约定）。
   - 删除 `detect._merge_short_scenes`（保留原始检测切点，不再把短于 4 秒的镜头并入相邻镜头）与 `asr._correct_text_with_glossary`（保留 ASR 原始观测文本，不再用人名表静默改写转写与绑定 `glossary` 缓存键）。
   - 删除 `briefing/context.py` 的素材薄厚打分与调研指令生成（`assess_understanding_substrate`、`_format_substrate_warning`、`_format_research_directive`）、`builder.py` 的硬编码块数配额 `target_count` 及重复静态写作教条、`recap_timeline.py` 的 `CUT_TIMELINE_CRAFT_BULLETS` / `MULTI_SOURCE_NARRATION_CRAFT_BULLETS` / `_brief_excerpt`、`lint_summary._HINTS` 的固定改法后缀，以及 `audio_mix.py` 的口播段落自动前拉紧凑启发式（`_dialogue_free_pull_start` / `_paragraph_pull_evidence`）。
2. **删除代码写死的品味与启发式规则（T）**：
   - 删除 `skills/video-script/scripts/deslop_qc.py`（翻转并取代 [[2026-10-02-drop-write-only-artifacts]] 中保留 `deslop_qc` 的部分）；仅在 `narration_lint.py` 内联保留 5 行 `placeholder_leakage`（拦截照抄 `【主角】` / `TA要赌上` 示例占位符）。
   - 删除 `narration_lint.py` 的场景边界跨切点、画面节拍对齐、旁白覆盖率区间（`under_narrated` / `over_narrated` / `fragmented_beats` / `no_original_blocks`）及 `metrics` 统计，同步清理 `lib.py` 中 13 个未读配置项。
   - 删除 `voiceover._compute_tts_params` 按句首/句尾位置与标点增减语速和音高的启发式规则。
   - `cut_qc.py` 的 `target_duration_drift` 统一记为 `warning`，不再因时长比例越出 `[0.60, 1.40]` 阻断渲染。
3. **删除自家产物校验与旧版兼容层（C）**：
   - 删除 `detect.py` 的 schema-1 句末锚点就地升级兼容层（`_upgrade_schema1_anchors`、`anchors_current`、`ensure_speech_boundary_anchors`）。
   - 删除 `tts_cache.py` 的 `legacy_sidecar_path`、`understanding_cache.py` 的 `_LEGACY_ASR_SETTINGS`、`understanding_storyboard.py` 的旧绝对路径重定位 `_relocated` 与旧标题前缀替换、`recap_runtime._settings_for_compare` 的旧 manifest 键补齐，以及多处 `asr_clean.json` 回退分支。
   - 精简 `shot_review.py` 的 `_protect_declared_sources` 与 `UnsafeReportTarget` 包装。

## Alternatives considered

- **维持现状，只把阻断降级为 warning** — 最强理由：改动最小，表面不改产物契约。不采用：3.3 万行测试与冗余模块负担仍在，且代码产出的机械建议与预消化索引会继续诱导 Agent 照单抄写而非直接阅读原始画面与对白证据。
- **连底线门禁（F）一起删除，只留 ffmpeg/API 调用（H）** — 最强理由：代码最薄。不采用：残镜、掐断半句原声、旁白抢话、定稿删字属于客观可测的基础硬伤，仅靠提示词在长链路与多轮返修中会被绕过（见 `docs/production-boundaries.md`），必须保留确定性门禁。

## Consequences

- **收益**：
  - 净删约 4,600+ 行生产与测试代码（删除 `deslop_qc.py`、`consolidate.py`、`index_normalize.py`、`timeline_fusion.py` 及其测试），`video-understanding` 少跑一轮串行 LLM 汇总调用，原始镜头切点与 ASR 转写不再被代码静默涂改。
  - `narration_lint.json` 与 `clip_plan_validated.json` 只报客观底线错误，不再因破折号、覆盖率配额或剪辑时长比例拦截创作。
- **代价**：
  - 移除 `--consolidate` / `--no-consolidate` / `--consolidate-asr` 参数及 `understanding_index.*`、`asr_clean.json`、`timeline_fusion.json`、`asr_writing_chunks.json`、`narration_lint.json` 的 `metrics`/`deslop_qc` 字段；读取这些旧产物的外部脚本需改读 `vlm_analysis.json`、`asr_result.json` 与 `silence_periods.json`。
  - 旧版 schema-1 锚点与旧版 TTS 缓存 sidecar 不再做兼容迁移，升级后首次运行会按当前 schema 重算。
