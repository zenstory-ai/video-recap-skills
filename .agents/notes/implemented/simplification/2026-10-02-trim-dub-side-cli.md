# Agent Note: 删除 dub 的手动侧 CLI 与 dub_review.json

Status: implemented

## Problem

PR #49 把 dub 的确定性 lint 和一份 review scaffold 收进 video-voiceover：`dub.py` 除了编排入口调用的 `--stage prepare|render`，还有 `--stage lint|review`（无需视频，单独检查 `dub_script.json`）和 `--print-schema`（打印全部 dub 产物契约）。渲染阶段在克隆前写 `dub_lint.json`，同时写 `dub_review.json`。那次收编没有单独的笔记。

审计（`.omc/plans/2026-10-02-skill-architecture-audit.md` S9）复核后：

- `dub_review.json` 没有任何读取方。它完全由 lint 结果确定性派生（error → FAIL、warning → REVISE），`faithful_to_source` / `speaker_tone` / `platform_fit` 三项恒为 `needs_agent_review`，等于把 `dub_lint.json` 换个形状再写一遍。
- `--stage lint|review` 与 `--print-schema` 没有调用方：recap 只调 `prepare` / `render`，仓库内只有单元测试调用它们。
- 文档自相矛盾：`dub.py` 模块注释写"由编排入口调用，不手动运行"，voiceover SKILL.md 却宣传手动用法；`dub_brief.md` 让译者按"约 5 字/秒"写，lint 却在 7 字/秒才警告，两个数字没有任何说明；dub 翻译规则在 `dub.py._brief_md`、recap SKILL.md §5、`recap_runner._run_dub` 的暂停提示里各写了一遍。

owner 批准删除侧 CLI。

## Decision

- `dub.py` 删除 `stage_lint`、`stage_review`、`build_dub_review`、`print_schemas` 及 `DUB_LINT_SCHEMA` / `DUB_REVIEW_SCHEMA` / `DUB_ARTIFACT_SCHEMAS`；`--stage` 只接受 `prepare|render`，`--video` / `--work-dir` / `--stage` 均为必填。`lint_dub_script` 保留，`stage_render` 通过 `work_dir=work` 让它写 `dub_lint.json`，lint 非 PASS 仍在克隆前中止。渲染不再写 `dub_review.json`。
- 语速口径统一：新增 `DUB_TARGET_CPS = 5.0`（brief 要求的目标），`DUB_FAST_SPEECH_CPS = 7.0`（lint 警告阈值）；`dub_brief.md` 第 3 条用这两个常量同时写出目标与警告阈值，`test_brief_speech_rate_agrees_with_the_lint_threshold` 锁住两者一致。
- dub 翻译规则只在 `dub_brief.md`（由 `_brief_md` 生成）写一份：recap SKILL.md §5 改为"按 `dub_brief.md` 的要求写 `dub_script.json`"并保留格式示例，`_run_dub` 的暂停提示只指向 brief。
- voiceover SKILL.md 删掉手动调用说明，写明 `dub.py` 只由 `--edit-mode dub` 调用；recap `references/data-schema.md` 的 dub 节只保留 `dub_lint.json`。
- voiceover `lib.py` 里只为 dub 存在的 ASR 配置不动：只有整个 dub 被移除时才删除。

## Alternatives considered

- **只修文档矛盾，保留侧 CLI 和 dub_review.json。** 最强理由：这是 PR #49 刻意收编的人工复核脚手架，已在 SKILL.md 与 data-schema.md 公开；dub 是实验功能，删掉约 160 行收益不大（审计评审给的就是这个结论）。没采用：owner 批准删除；脚手架从未被读取，手动入口除测试外没有调用方，留着就要继续维护两份契约和对应测试，而文档矛盾正是这些多余入口造成的。
- **保留 `dub_review.json`，只删手动 CLI。** 最强理由：渲染后 work_dir 里有一份带"建议修改"的人类可读清单。没采用：它的每一条都直接来自 `dub_lint.json.issues`，语义判断项恒为待定；同样的信息 Agent 读 `dub_lint.json` 就有。

## Consequences

- 收益：`dub.py` 少约 105 行（623 → 516），dub 测试少一组 review/print-schema 用例；dub 只剩编排入口实际走的两个阶段，规则与语速口径各只有一个出处。
- 代价：这是 breaking 变更——`dub.py --stage lint|review` 与 `--print-schema` 传入即报 argparse 错误；渲染不再写 `dub_review.json`，读取它的外部脚本会找不到文件。想在克隆前单独检查译稿，只能直接重跑 `recap.py --edit-mode dub`（lint 失败时同样在克隆前停下，不计费）。
- 是否在默认路径：否（dub 是实验模式）。谁读取产物：删除前没有任何读取方。删除信号：本篇即删除。agent 表面增量：负两个手动阶段、一个 flag、一个产物。
