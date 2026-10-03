# Agent Note: parity 从整文件改为函数级，复制模块按执行方归属

Status: implemented

## Problem

[[2026-06-14-self-contained-skills-duplicated-libs]] 规定 skill 之间不共享代码、刻意复制的模块逐字节一致。[[2026-09-21-drop-script-brief-chain-and-orphan-references]] 删掉 video-script 的 brief 链后，parity 清单还剩 5 个整文件副本（`agent_text / narration_lint / speech_ownership / timeline_fusion / deslop_qc`，两边各 1,470 行）。按文件做 parity 带来两个问题：

- understanding 里 `timeline_fusion.py` 在模块顶层 import `narration_lint`，只为 `_align_narration_to_quiet`，而这个函数只有 video-script 的 `validate.py` 调用。结果 understanding 每次运行都带着约 1,060 行从不执行的 lint / speech ownership / deslop 代码；script 也带着约 240 行从不执行的 brief 代码（`_chunk_asr_for_writing` 一族）和整份 `timeline_fusion.py`。
- parity 只证明"两份抄得一样"，不证明有人在用。被它守护的恰好是最少被执行的副本。改 lint 的 PR 必须把同一处改动逐字节同步到 understanding 那份死代码里。

understanding 真正用到的共享部分只有 `briefing/builder.py` 的 brief 半边（`_chunk_asr_for_writing`、`_format_frame_facts`、`_build_timeline_fusion` 等）和它们依赖的几个文本原语。

## Decision

- video-understanding 删除 `narration_lint.py`、`speech_ownership.py`、`deslop_qc.py`。`agent_text.py` 只保留 brief 半边（ASR 写作分块、帧动作格式化）和预算原语，`_sentence_pieces`、`_text_units` 从 `deslop_qc` 搬进来；lint/改写半边（`_text_char_count`、`_truncate_at_sentence`、`_post_dedup_narration`、`_normalise_narration_segment` 等）删除。`timeline_fusion.py` 不再 import `narration_lint`，`_align_narration_to_quiet` 从这里删除。
- video-script 删除 `timeline_fusion.py`；`_align_narration_to_quiet` 和它用到的 `_quiet_windows` 并入 `narration_lint.py`，`validate.py` 从那里 import。`agent_text.py` 删除 brief 半边，只保留 lint 用的文本处理和预算原语。
- CONFIG 跟着收缩（由 `test_no_skill_declares_config_it_never_reads` 强制）：understanding 的 `lib.py` 删除只有 lint 读取的 `narration_coverage_min/max`、`original_block_min_seconds`、`narration_block_min_chars`、`quiet_overlap_min_ratio`、`visual_beat_max_seconds/facts`；script 的 `lib.py` 删除 `asr_chunk_min/max_chars` 和随之没有调用者的 `env_int`。`ASR_CHUNK_*` 环境变量仍由 understanding 读取，行为不变。
- `tests/orchestrator/test_brief_narration_parity.py` 改为函数级：
  - `SHARED_FUNCTIONS` 只列 `_recommended_char_budget`、`_scene_available_seconds`、`_overlap_seconds`、`_sentence_pieces`、`_text_units`，按 `ast.dump` 比对；`_sentence_pieces`、`_text_units` 在 understanding 的 `agent_text.py` 与 script 的 `deslop_qc.py` 之间比对。`_text_char_count` 不在清单里，understanding 只在已删除的 lint 半边用过它。
  - 每个共享函数必须在本 skill 内有调用点，不为凑 parity 留死副本。
  - 预算函数读取的四个 CONFIG 键（`speech_rate`、`speech_safety_margin`、`narration_speed`、`narration_tail_pad_seconds`）在两份 `lib.py` 里的默认表达式必须一致，否则 brief 的字数预算和 lint 的预算会悄悄分叉。
  - 曾经整文件复制的四个模块各自只能出现在一个 skill 里。
- 测试跟着归位：understanding 组里那段 `lint_narration` 断言改成 `tests/script/test_pure_script.py::test_cut_lint_suggests_output_clock_anchor_from_brief_evidence`，用与 understanding 写出的同形 `speech_boundary_anchors_output.json` 做夹具；`_build_timeline_fusion` 的测试从 script 组搬到 `tests/understanding/test_pure_understanding.py`，断言不变。
- `docs/architecture.md` 的"刻意复制的模块"一节改成函数级描述，去掉"只复制入口用到的模块"的说法（实际上 understanding 一直复制着入口用不到的 lint 链）。

## Alternatives considered

- **保留整文件 parity，只把 `timeline_fusion.py:5` 的顶层 import 改成延迟 import**：最强理由是改动最小，understanding 运行时不再加载 lint 代码，parity 继续用字节比较，简单可靠。没采用：死代码仍在两份副本里，改 lint 仍要同步改 understanding 那份，parity 仍在守护没人执行的代码，问题的根源（复制单位过粗）没动。
- **在 understanding 里新建 `briefing/brief_text.py` 承接 brief 半边，删掉 understanding 的 `agent_text.py`**：最强理由是 brief 文本处理和 `briefing/` 子包放在一起，归属更直观。没采用：这样要多改 `briefing/builder.py`、`timeline_fusion.py` 和测试的 import，而收益只是换个文件名；两边都叫 `agent_text` 也方便读者对照 parity 清单。以后整理 briefing 子包时可以再搬。
- **合并 understanding 与 script，彻底消除复制**：最强理由是 parity 问题自然消失。没采用：前者是昂贵、可缓存的 API 阶段，后者是 agent 创作阶段，暂停点不同，单独安装 script 做 validate/review 的用法也会消失。拆完后共享面只剩五个函数，复制成本已经很低。

## Consequences

- **收益**：两个 skill 的脚本合计少约 1,440 行（understanding 删 4 个文件的大部分，script 删 `timeline_fusion.py` 和 brief 半边）；understanding 运行时不再加载 lint 链；lint、speech ownership、deslop 的改动只改 video-script 一处。parity 现在同时检查"副本一致"和"副本在用"，还覆盖了之前没人守的预算 CONFIG 默认值。
- **代价**：两个 `agent_text.py` 同名但内容不同，读者要看 parity 清单才知道哪些函数是共享的；函数级 parity 只比较函数本体，共享函数改了依赖的全局名（例如新读一个 CONFIG 键）时，测试只能靠"预算键一致"和"声明即被读"两条间接兜底，新增共享依赖时要同步扩 `SHARED_BUDGET_KEYS`。
- 用户可见行为不变：落盘产物、CLI、默认值都没变；`ASR_CHUNK_*` 只在 video-understanding 里生效，这点和之前一样（script 那份从来没被读过）。
- 重访信号：如果某个共享函数开始只在一边被调用，parity 测试会变红，这时删掉那一边的副本并把它移出 `SHARED_FUNCTIONS`，而不是为了过测试造调用点。
