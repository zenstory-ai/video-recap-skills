# Agent Note: 六个生产 skill 合一为 video-recap，保留 video-reference 形成 1 + 1 结构

Status: implemented

## Problem

仓库此前将一条连续的视频解说制作链路拆成了六个生产 skill（`video-recap`、`video-understanding`、`video-script`、`video-cut`、`video-voiceover`、`video-assemble`）加一个按需参考 skill（`video-reference`）。为了维持 [[2026-06-14-self-contained-skills-duplicated-libs]] 的“每个 skill 自包含”，这种拆分带来了三层结构性负担：

1. **创作体验被人为割裂**：视频理解、定叙事视角与节拍、选镜剪辑、留原声与写旁白本来是一气呵成的创作判断，却被拆散到六份 `SKILL.md`（合计 900+ 行）和两份微调措辞的 `research-guide.md`、`data-schema.md` 中；`CREATE / DIRECTED / REVISION`、五项创作角色、`clip_plan.json` 七段 `reason`、密集闪帧判别、TTS 供应商和 `dub` 授权说明在多处重复，且因为“不能点名兄弟 skill”衍生出大量绕口暗语。
2. **同源基础设施被迫复制与守护**：六份 `lib.py`（合计 1,559 行）各自重复维护 `api_call`、`env_*`、`file_identity`、`get_video_duration`、`narration_tempo_budget`、`filter_file_args`；`agent_text.py` 拆成两半；`media.py` 与 `media_geometry.py` 复制色彩标签函数；`sentence_boundaries.py`、`speech_ownership.py` 与 `audio_mix.py` 复制三份语气词豁免规则，再用 `test_brief_narration_parity.py`、`test_render_format_parity.py`、`test_audio_policy_parity.py` 看守。
3. **单 skill 分发反而卡死主入口**：其中四个阶段 skill 本就是 `user-invocable: false` 的内部子阶段，用户从不单独安装；而主入口 `video-recap` 因为要跨目录调用五个兄弟 skill，在 `.clawhub/publish.json` 中长期处于 `disposition: blocked`（伴随安装阻断）。

## Decision

将六个生产 skill 合并为单一自包含生产 skill `skills/video-recap/`，保留按需拆片学习的 `skills/video-reference/`，形成 **1 + 1** 双 skill 结构：

- **脚本层合一（`skills/video-recap/scripts/`）**：
  - 将原 `video-understanding`、`video-script`、`video-cut`、`video-voiceover`、`video-assemble` 的脚本与子包（`briefing/`、`providers/`、`adoption/`、`jianying/`、`subtitles/`、`dashboard/`、`resources/`）统一收入 `skills/video-recap/scripts/`。各阶段 CLI 入口（`recap.py`、`understand.py`、`validate.py`、`review.py`、`cut.py`、`shot_review.py`、`voiceover.py`、`dub.py`、`assemble.py`、`source_score.py`、`doctor.py`、`library.py`、`dashboard_server.py`）保持不变，`recap_runtime._entry` 直接解析本技能 `scripts/<script>`。
  - 六份生产 `lib.py` 合并为单一 `skills/video-recap/scripts/lib.py`，两份 `agent_text.py` 合并为单一 `skills/video-recap/scripts/agent_text.py`。
  - 消除原本仅因跨 skill 隔离而复制的同源逻辑：`cut_render.py` 直接从 `media.py` 导入色彩空间标签表与辅助函数（移除 `media_geometry.py` 中的副本）；`sentence_boundaries.py` 与 `audio_mix.py` 直接从 `speech_ownership.py` 导入纯语气词判定与对白区间函数。
  - 删除失去存在前提的跨生产 skill 副本比对测试 `tests/orchestrator/test_brief_narration_parity.py` 与 `tests/orchestrator/test_render_format_parity.py`；保留 `test_audio_policy_parity.py` 守护 `video-recap` 的 `lib.py` 只声明实际读取的配置项。
- **指令与参考层合一（`skills/video-recap/SKILL.md` 与 `references/`）**：
  - 原六个生产 skill 的 `SKILL.md` 合并为一篇一气呵成的 `skills/video-recap/SKILL.md`（理解 → 策划选镜与写旁白 → 剪辑/配音/合成 → 成片复核），完整保留远程调用与数据外发说明（MiMo ASR/VLM/`review.py`/TTS、Fish Audio、IndexTTS 及 `dub` 模式 `--confirm-voice-rights` 硬门禁）。
  - 所有生产 `references/` 迁入 `skills/video-recap/references/`；两份 `research-guide.md` 与两份 `data-schema.md` 各自合一，彻底消除跨阶段暗语。
  - 删除 `skills/video-{understanding,script,cut,voiceover,assemble}/` 五个目录。
- **分发与清单同步**：
  - `tests/orchestrator/test_plugin_manifest.py` 更新为校验 `video-recap` 与 `video-reference` 两个公开可调用（`user-invocable: true`）技能。
  - `.release/policy.json` 的 `requiredPaths` 同步收敛为两个技能的 `SKILL.md`。

## Alternatives considered

- **方案 B（2 + 1）：保留 `video-recap`（全流程生产）、`video-script`（纯文案/宣发）、`video-reference`（拆片参考）三个 skill** — 最强理由：只改文案或宣发标题时可单独触发 `video-script`。不采用：视频解说的选镜、留原声与写词高度咬合，保留 `video-script` 仍要维护两套创作角色说明、`research-guide.md` 与 `lib.py`/`agent_text.py` 副本；把宣发修订作为 `video-recap` 的轻量分支（直接读 `references/promotional-copy.md`）即可覆盖该场景。
- **方案 C：保留七个 skill 目录，只在内部软链接或跨目录共享 `_common`** — 最强理由：外部目录名不变。不采用：四个 `user-invocable: false` 的阶段 skill 本就不是独立产品，保留空壳目录既不解决 `SKILL.md` 碎片化，也无法解除单包安装的伴随依赖。
- **把 `video-reference` 也并入 `video-recap`（1 个全能 skill）** — 最强理由：彻底只剩一份 `lib.py`。不采用：`video-reference` 是对外部成片做逆向拆解的独立工作流（见 [[2026-10-02-video-reference-skill]]），与把源片做成解说成片的生产主线方向相反，分开能避免拆片指令污染每次解说创作的上下文。

## Consequences

- **收益**：
  - 生产链路收敛为单一自包含 `skills/video-recap/`，剪辑与解说从看片、定拍、选镜到写词、配音、合成一气呵成，不再跨六个技能跳转。
  - 删除 5 份生产 `SKILL.md`、5 份 `lib.py`、1 份 `agent_text.py`、重复的 `research-guide.md` / `data-schema.md` 以及跨 skill 复制的色彩标签与语气词函数，大幅减少重复代码与文档。
  - `video-recap` 本身包含全部生产脚本，单目录复制即可独立运行。
- **代价**：
  - `skills/video-recap/scripts/` 下的模块数量增加，依靠现有的子包结构（`briefing/`、`providers/`、`adoption/`、`jianying/`、`subtitles/`、`dashboard/`、`resources/`）与无环导入测试保持清晰边界。
  - 现有测试套件中指向 `skills/video-{understanding,script,cut,voiceover,assemble}/scripts` 的路径统一更新到 `skills/video-recap/scripts`。
