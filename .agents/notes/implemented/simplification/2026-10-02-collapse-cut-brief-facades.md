# Agent Note: 收掉 cut / brief 门面层，narration_mapping 改名 cut_qc

Status: implemented

## Problem

[[2026-10-02-skill-ownership-function-parity-and-budgets]] 的 Phase 1 要删"门面层"。[[2026-09-27-test-audit-residuals]] 已去掉入口对私有函数的再导出，但还剩两层只为测试存在的转发：

- `video-cut/scripts/cut.py` 再导出 13 个公开函数加 `main`。生产只经命令行跑它的 `main`（recap 以子进程调用 `cut.py`）；进程内唯一使用这些名字的是 `tests/cut/test_pure_cut.py`。
- `video-understanding/scripts/brief.py` 是 9 行再导出（`build_agent_brief`、`assess_understanding_substrate`），没有 `__main__`，SKILL.md 与 references 都没点名它。它不是纯测试便利：`understanding_runner.py` 和 `understanding_brief.py` 在生产里经它导入。
- `video-cut/scripts/narration_mapping.py` 只剩 `update_cut_qc`。旁白映射已在 cut-first-narrate-second 中删除（见 [[2026-06-16-cut-first-narrate-second]]），文件名和 `cut_contract.py` 两条"before mapping narration"报错仍在描述不存在的步骤。
- understanding 里四处注释仍说 brief 与 script 侧的 narration 是"字节孪生"（`understanding_storyboard.py`、`understanding_runner.py`、`briefing/builder.py`、`briefing/inputs.py`）。script 侧的 brief 链已在 [[2026-09-21-drop-script-brief-chain-and-orphan-references]] 删除，这些注释给出的理由不再成立。

## Decision

- `cut.py` 只剩 `from cut_cli import main`、`__all__ = ["main"]` 和 `__main__` 守卫，命令行入口与参数不变。`test_pure_cut.py` 直接从 `cut_contract` / `cut_render` / `sentence_boundaries` / `media_geometry` / `cut_qc` 导入被测函数，只有跑 CLI 的用例仍经 `cut.main()`。
- 删除 `video-understanding/scripts/brief.py`。`understanding_runner.py` 与 `understanding_brief.py` 改为 `from briefing.builder import build_agent_brief`、`from briefing.context import assess_understanding_substrate`；`PUBLIC_ENTRYPOINTS` 去掉它。
- `narration_mapping.py` 改名 `cut_qc.py`（`git mv`，内容不变），`cut_cli.py` 随之改导入。`cut_contract.py` 两条重叠报错的结尾改为"split or remove duplicate source footage in the clip plan"，测试匹配的"overlaps an earlier source range"前缀不变。
- 四处"字节孪生"注释删除或改写为现状：storyboard / runner 只说明会给返回的 brief 文件加 storyboard 头；builder 说明缺 ASR 证据时不能编造可用状态；inputs 说明 `_ASR_SPAN_TOL` 与 `consolidate._ASR_SPAN_TOL` 同值、由 `test_asr_span_tol_matches_across_files` 钉住。
- 不在本次范围：video-script `review.py` 的门面（另一条线处理）；`briefing/inputs.py` 与 `vlm.py` 的 MiMo 辅助函数去重（行为不等价，见审计 critique，归 Phase 2）；`cut_cli.py` 中 `--normalize-only` 的帮助文案（`validate --mode cut` 仍读 `clip_plan_validated.json`，文案暂时成立，随 Phase 3 删除 `--mode cut` 时一起改）。

## Alternatives considered

- **保留 `brief.py` 作为 understanding 的稳定 brief API。** 最强理由：外部脚本若 `import brief`，删除就会断；一个 9 行文件的维护成本几乎为零。不采用：它从未出现在 SKILL.md、references 或 README 里，宿主只通过 `understand.py` 命令行拿 brief；保留它还让 `briefing/` 子包有两个入口，读代码的人要多跳一层才找到实现。
- **保留 `cut.py` 的公开再导出，把它当 cut 的 Python API。** 最强理由：测试和潜在外部调用方只需记一个模块名。不采用：cut 是自包含技能，只以命令行交付；再导出让 `cut.py` 依赖全部子模块，测试经门面调用还会掩盖函数真正的归属模块，和 09-27 去掉私有再导出的理由相同。
- **只改文件名，不改报错文案。** 没有被认真考虑：文案里的"mapping narration"同样描述已删除的步骤，改动只有两行。

## Consequences

- **收益**：
  - 删掉一个文件和约 40 行转发；`cut.py` 不再在 import 时加载 cut 的全部子模块。
  - 模块名与报错文案和现有流程一致：cut 只做剪辑和时长 QC，不映射旁白。
  - 去掉四处会误导后来者"必须保持字节一致"的注释。
- **代价**：
  - 进程内 `import brief`、`from cut import normalize_clip_plan` 一类的外部脚本需改为从所属模块导入；`from narration_mapping import update_cut_qc` 要改成 `from cut_qc import update_cut_qc`。仓库内没有这样的调用方。
  - 依赖旧报错全文（含"before mapping narration"）的外部匹配会失配。
- **默认路径**：命令行、参数、产物与默认值都不变。
- **agent 表面**：SKILL.md 与 references 不变。
