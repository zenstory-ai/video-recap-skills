# Agent Note: 删除只被 import、从不运行的字节一致副本

Status: implemented

## Problem

[[2026-06-14-self-contained-skills-duplicated-libs]] 让 video-understanding 与 video-script 各持一份 `agent_text / narration_lint / speech_ownership / timeline_fusion / deslop_qc`，由 parity 测试保证字节一致。审计（2026-10-20）发现其中三份副本在所在 skill 里没有运行路径：

- video-understanding 只用 `timeline_fusion` 的 `_build_timeline_fusion / _quiet_windows_for_scene / _scene_asr_lines`（brief 构建）；`timeline_fusion` 顶部 `from narration_lint import _validate_narration_budget` 只为 script 侧服务。于是 understanding 的 `narration_lint.py`（707 行）与 `speech_ownership.py`（143 行）只被 import、从不执行；它自己的 data-schema 也写明本技能"既不生成也不校验" narration 相关产物。
- video-script 只用 `timeline_fusion._align_narration_to_quiet`（`validate.py`），其余约 110 行 brief 时间线函数无人调用。

## Decision

- `_align_narration_to_quiet`（含内联的安静窗口筛选）移入 video-script 的 `narration_lint.py`；删除 `video-script/scripts/timeline_fusion.py`。
- 删除 video-understanding 的 `narration_lint.py` 与 `speech_ownership.py`；understanding 的 `timeline_fusion.py` 不再 import `narration_lint`。只被这两份 lint 读取的七个 understanding CONFIG 键一并删除（无环境变量来源）。
- parity 清单缩到两边都运行的 `agent_text` 与 `deslop_qc`（`tests/orchestrator/test_brief_narration_parity.py`）；`_build_timeline_fusion` 的测试进 understanding 组，输出时间线 lint 断言进 script 组。
- `docs/architecture.md` 的"刻意复制的模块"一节同步改写。

## Alternatives considered

- **保留副本，只靠 parity 测试防漂移** — 最强理由：零改动，未来 understanding 若要自检解说可直接用。否：约 960 行无运行路径的代码每次改 lint 都要同步两份，parity 测试证明的是"复制一致"而不是"有人用"；这正是上一篇笔记对 brief 链做过的判断（[[2026-09-21-drop-script-brief-chain-and-orphan-references]]）。
- **把 `timeline_fusion` 整份留在 script、删掉 understanding 那份** — 最强理由：script 不用改 import。否：understanding 才是 `_build_timeline_fusion` 的运行方，删它那份等于删掉 brief 的依赖。

## Consequences

- **收益**：两个 skill 各自只保留会运行的代码；parity 清单从五对降到两对；改 lint 不再需要同步一份不运行的副本。
- **代价**：未来若 understanding 需要在 brief 阶段跑解说 lint，要重新引入副本并恢复 parity 对。

## Verification

understanding、script、orchestrator 三组全绿；`git grep` 确认 understanding 不再 import `narration_lint` / `speech_ownership`，script 不再 import `timeline_fusion`。
