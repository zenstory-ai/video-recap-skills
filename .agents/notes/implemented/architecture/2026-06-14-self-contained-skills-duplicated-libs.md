# Agent Note: 每个 skill 自包含，复制模块而不共享代码

Status: implemented

## Problem

六个 skill 要能在 Claude Code、Codex、OpenCode、OpenClaw 里被各自发现并单独安装，而这些宿主只保证 `skills/<name>/` 目录存在。任何跨 skill 的 `import` 或相对路径引用，在只装一个 skill 的机器上都会断。反过来，直接复制代码又会悄悄漂移，五份 `lib.py` 曾各自携带所有人的配置并集（155–173 个键，95–132 个从不读取）。

## Decision

- `skills/<name>/scripts/*.py` 只 import 自身目录里的模块；源码与 markdown 都不得出现兄弟 skill 的路径或名字（`test_skill_scripts_do_not_import_other_skill_scripts`、`test_stage_sources_never_point_to_a_sibling_skill_path`）。skill 之间只通过 `work_dir` 里的 JSON / MP4 产物通信，编排器 `video-recap` 以子进程调用各 skill 脚本。
- 刻意复制的模块 must 字节一致，由 `tests/orchestrator/test_brief_narration_parity.py` 守：video-understanding 的 `brief.py` 与 video-script 的 `narration.py`，以及两者共有的 `agent_brief / agent_text / brief_context / brief_inputs / brief_timeline / narration_lint / speech_ownership / timeline_fusion / deslop_qc`；video-recap 与 video-script 的 `creative-editing-playbook.md`。改一处必须同批改另一处。
- 每份 `lib.py` 只声明自己代码读取的 CONFIG 键（`test_no_skill_declares_config_it_never_reads`）；多个 skill 共同声明的音频与 tempo 键取值必须一致（`test_audio_policy_parity.py`）。never 为"方便"把别的 skill 的键加进来。
- 每个脚本模块 ≤ 800 行，skill 内 import 图无环（`test_test_suite_architecture.py`）。
- `file_identity`（size/mtime_ns）等在多份 `lib.py` 里各有一份的辅助函数保持同形；内容指纹已于 2026-09-20 整体移除（见 [[2026-09-20-no-content-hashing]]），跨 skill 只比较路径与 size/mtime_ns。

来源：080b22b、eff7db5、a5fa71b、e222cb8 (#67)、7d8f979 (#68)、c4da353 (#65)

## Alternatives considered

- **抽一个共享 lib / 共享 remap helper 模块** — 最强理由：一处修改，不会漂移，也不用 parity 测试。否：单独安装的 skill 找不到共享模块；eff7db5 与 a5fa71b 都明确拒绝，理由是违反"无跨 skill 运行时 import"约束。
- **`lib.py` 保持全量配置并集，用五份副本的 parity 断言防漂移** — 最强理由：任何 skill 都能读到任何键，配置面一致、简单。否：parity 只证明"复制得一样"，不证明有人在读；`zone_fade_seconds` 全仓库无读取点却靠它存活，`CLIP_PADDING` 在唯一实现方 video-cut 缺声明而环境变量整整失效。

## Consequences

- **收益**：任一 skill 可独立 clone / 安装运行；配置面少了 583 条死声明，"声明即被读"成为结构不变量。
- **代价**：共享逻辑要同步改多份副本，parity 测试会在忘记时变红；裁剪配置时静态 grep 和运行时插桩都看不到经 `dict(DEFAULT_CONFIG)` 副本发生的读取，#67 因此误删了 QC 报告依赖的 12 个键，#68 补回并改用行为测试（env → CONFIG → 请求链）兜底。裁配置 must 配行为测试，不能只靠结构检查。
- 重访信号：所有目标宿主都开始支持 skill 间共享包时，再考虑抽公共库。
- 2026-09-21：video-script 侧的 brief 链副本已删除，parity 清单缩到两 skill 仍共有的五个模块，见 [[2026-09-21-drop-script-brief-chain-and-orphan-references]]。

## Verification

`python3 scripts/test.py orchestrator` 覆盖上述全部守卫；`test_skill_scripts_do_not_import_other_skill_scripts` 出现违规即列出文件与被引用的兄弟 skill。
