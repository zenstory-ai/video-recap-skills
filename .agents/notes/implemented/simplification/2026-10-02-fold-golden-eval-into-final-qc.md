# Agent Note: golden_eval 并入 final_qc

Status: implemented

## Problem

0.6.0 的 shift-left QC 在渲染后写两份报告：`final_qc.json`（成片探测、上游 assembly/visual QC 汇总）和 `golden_eval.json`。后者的设计是拿一份 golden fixture（期望时长区间、codec、必需产物）对照成片，但编排入口从不传 fixture（`recap_stage_qc._write_final_qc_reports` 只调 `final_qc.run(work_dir, final_output=...)`）。没有 fixture 时它唯一的检查是 `expected_final_qc_ok=True`，也就是把 `final_qc.ok` 原样再说一遍：

- `golden_eval.ok` 永远等于 `final_qc.ok`，`--require-final-qc` 却要同时检查两份摘要，失败信息、报告路径都成双出现。
- QC 契约为它多留了一个 artifact（`golden_eval.json`）和一个 stage（`golden`），dashboard 的 QC 页多一张「黄金评估」卡片，内容与成片 QC 卡片同步变红变绿。
- `final_qc.py --golden-fixture` / `--only` 是只为这份报告存在的手动入口，文档（data-schema.md）里写着"也可用 golden fixture 做断言"，但仓库内没有任何 fixture，也没有调用方。

0.6.0 把这两份报告一起作为 shift-left QC 的成片门禁发布（CHANGELOG 0.6.0「最终 QC 可选阻断」）；那次决定没有单独的笔记。架构审计（`.omc/plans/2026-10-02-skill-architecture-audit.md` S4）建议并入，[[2026-10-02-skill-ownership-function-parity-and-budgets]] 把它列为待 owner 拍板项，owner 选择并入。本篇翻转的是 0.6.0「渲染后写 final_qc + golden_eval 两份报告」这一部分。

## Decision

- `final_qc.py` 删除 `build_golden_eval`、`_load_or_build_final_qc`、`GOLDEN_EVAL_ARTIFACT` / `GOLDEN_STAGE` 以及 CLI 的 `--golden-fixture` / `--only`；`run()` 只写 `final_qc.json`，返回 `{"work_dir", "written": ["final_qc.json"], "final_qc": {ok, blocker_count}}`。`--probe-fixture` 保留，离线测试用它注入 ffprobe 结果。
- `recap_stage_qc._require_final_qc` 只检查 `final_qc` 摘要（`ok is True` 且整数 `blocker_count == 0`），判定与之前相同；`_print_final_qc_pointer` 只提示 `final_qc.json`。
- `qc_contract.ARTIFACTS` 只剩 `final_qc.json` / `preflight_qc.json`，`STAGES` 去掉 `golden`；`golden` 与 `golden_eval` 都在拒收的阶段名列表里（`test_stage_names_match_approved_gate_matrix_exactly`）。
- dashboard 的 `QC_FILES` 去掉「黄金评估」，空状态文案不再提 `golden_eval.json`；旧 work_dir 里残留的 `golden_eval.json` 被忽略（dashboard 夹具里放了一份残留文件锁住这一点）。
- 测试 `test_final_qc_golden_eval.py` 改名为 `test_final_qc.py`，只保留 final_qc 断言；`run()` 不再写 `golden_eval.json` 由 `test_probe_fixture_success_writes_only_a_valid_final_qc` 锁住。
- SKILL.md 的 `--require-final-qc` 段、`recap_cli` 帮助文本、`references/shift-left-qc-schema.md`、`references/data-schema.md` 同步只写 `final_qc.json`。

## Alternatives considered

- **保留 golden_eval，让编排入口真正传一份 fixture。** 最强理由：时长区间、codec、必需产物这些断言本身有价值，接上 fixture 后它就不再是复读。没采用：这些检查里有意义的部分 final_qc 已经在做（缺失/空成片、缺视频流、缺时长、缺 codec 都是 blocker）；剩下的"期望时长区间 / 期望 codec"需要每个项目维护一份期望值，没有人提出过这种需求，也没有任何 fixture 存在。真要做，应作为 final_qc 的输入参数加进去，而不是再造一份平行报告。
- **保留 `final_qc.py --golden-fixture` 作为手动工具，只从流水线和门禁里拿掉。** 最强理由：不破坏 data-schema.md 里写过的手动能力。没采用：没有调用方、没有 fixture，留着就得继续维护 `golden` stage、`golden_eval.json` artifact 和一组测试；门禁不读它，手动跑出的报告也没有消费者。

## Consequences

- 收益：`final_qc.py` 少约 115 行（525 → 411），QC 契约少一个 artifact 和一个 stage；`--require-final-qc` 的失败信息只有一条、只指向一份报告；dashboard QC 页少一张重复卡片。
- 代价：这是 breaking 变更——流水线不再写 `golden_eval.json`，读取它的外部脚本会找不到文件（`final_qc.json` 的 `ok` / `blocker_count` 就是它过去的结论）；`final_qc.py --golden-fixture` / `--only` 传入即报 argparse 错误；`qc_contract.build_report(artifact="golden_eval.json")` 或 `stage="golden"` 会被拒绝。`--require-final-qc` 的判定结果不变。
- 是否在默认路径：是（每次 full/cut 渲染后都写过）。谁读取产物：删除前只有 `--require-final-qc` 门禁、结束提示和 dashboard 卡片，结论都与 final_qc 相同。删除信号：本篇即删除。agent 表面增量：负一个产物、两个 `final_qc.py` 参数。
