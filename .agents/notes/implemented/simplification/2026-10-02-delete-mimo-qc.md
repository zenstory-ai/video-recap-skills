# Agent Note: 删除 MiMo 多模态建议型 QC

Status: implemented

## Problem

video-recap 带着一个可选的 MiMo 多模态 QC：`--mimo-qc pre-assemble|post-render|both` 在合成前 / 成片后各发一次 MiMo 请求，把语义 / 审美观察写进 `mimo_qc.json`。它默认关闭，按契约永远不能阻断、不能自动修复，所有异常都被吞成 `failed` / `unavailable` 状态。代价却是 recap 里成本最高的建议型功能：

- 代码约 1,200 行：入口 `mimo_qc.py`、`scripts/qc/` 七个模块（[[2026-09-21-scripts-subpackages-jianying]] 第二批从 `mimo_qc_*.py` 分包而来）、`lib.mimo_qc_api_call`、`recap_stage_qc` 的 MiMo 半边，以及 `lib.CONFIG` 里只为它存在的 11 个键（`mimo_qc_model`、`mimo_disable_thinking`、`mimo_media_resolution` 等，靠 CONFIG 拷贝读取，grep 看不见）。
- 测试约 900 行（`test_mimo_qc_adapter.py`、`test_recap_mimo_qc.py`、`test_mimo_qc_config_resolution.py`），全部是 mock 响应。
- 三个 flag / 环境变量（`--mimo-qc`、`--mimo-qc-refresh`、`MIMO_QC_MODEL`），以及 local adoption、source 音频模式、续跑命令里为它写的互斥与回传逻辑。
- `final_qc.json`、QC 契约和 dashboard 都要认识 `mimo_qc.json`，关闭时还得在每次运行开头 `clear_report`，否则陈旧建议会被当成本轮结果。

架构审计（`.omc/plans/2026-10-02-skill-architecture-audit.md` S11）把去留列为 owner 决定；owner 选择了选项 A（整体删除）。[[2026-10-02-skill-ownership-function-parity-and-budgets]] 的提案也把它列为"一个版本周期没人用就删"的候选。

## Decision

- 删除 `skills/video-recap/scripts/mimo_qc.py` 与整个 `scripts/qc/` 子包；`lib.py` 删除 `MiMoQCRequestError`、`mimo_qc_api_call` 及随之不用的 `socket` / `urllib` 导入，CONFIG 删除只有 QC 读取的 11 个键（由 `test_no_skill_declares_config_it_never_reads` 强制）。
- `recap_cli` 删除 `--mimo-qc` / `--mimo-qc-refresh`；传入时 argparse 报 unrecognized arguments（`test_removed_mimo_qc_flags_are_rejected`）。`MIMO_QC` / `MIMO_QC_REFRESH` / `MIMO_QC_MODEL` 不再被读取，也从 `env-inventory-v1.json` 删除。
- `recap_runner` 不再在 `pre_assemble` / `post_render` 调 MiMo，也不再在运行开头清理 `mimo_qc.json`；`recap_stage_qc` 只剩 shift-left 与 final QC。`recap_source` 去掉 `--mimo-qc` 相关的 adoption 冲突项和 source 模式检查，`recap_timeline` 的续跑命令不再回传这两个 flag。
- 没有任何环节再读 `mimo_qc.json`：`final_qc._COLLECT_ARTIFACTS` 去掉它，`qc_contract.ARTIFACTS` 只剩 `final_qc.json` / `golden_eval.json` / `preflight_qc.json`，非确定性类别只剩通用的 `semantic` / `aesthetic`；dashboard 的 `QC_FILES` 去掉「MiMo 复核」卡片。旧 work_dir 里残留的 `mimo_qc.json` 被忽略，测试用残留文件锁住这一点（`test_leftover_mimo_qc_from_an_older_run_is_not_read`、dashboard 夹具）。
- SKILL.md、`references/{shift-left-qc-schema,data-schema,config-playbook,audio-routing}.md`、两份 README 与 `docs/architecture.md` 删掉 MiMo QC 段落；README 进阶请求只保留"导出剪映草稿"。
- 旧笔记里把 MiMo QC 写成现状的地方各加了一行指向本篇的事实注记，决定本身没有改写。

## Alternatives considered

- **选项 B：保留，只把 `import mimo_qc` 改成延迟导入，SKILL.md 留一行指引。** 最强理由：零行为变化，偶尔需要模型看片意见的人仍有入口，不用发 breaking 变更。没采用：导入本来就便宜，延迟导入省不了什么；1,200 行代码和 900 行 mock 测试照样要维护，`clear_report` 也必须照常运行，`final_qc`、契约、dashboard 仍要认识这个产物。功能不能阻断、不能修复，留下的价值只是一份建议 JSON。
- **只删 CLI flag，保留 `mimo_qc.py --live` 作为独立脚本。** 最强理由：流水线变简单，需要时手动跑一次。没采用：独立脚本仍要带 `qc/` 全部模块、`mimo_qc_api_call` 和 CONFIG 键，测试量不减；而且它只读 work_dir 证据，Agent 直接看成片或跑 `review.py` 就能拿到同类意见。

## Consequences

- 收益：recap 脚本少约 1,300 行（含 lib 与 stage_qc），orchestrator 测试少约 900 行；`recap.py --help` 少两个 flag；QC 契约和 dashboard 只认确定性产物；每次运行开头不再需要清理陈旧文件。
- 代价：这是 breaking 变更——脚本或别名里写了 `--mimo-qc` 的调用会直接报错退出，需要删掉该参数；设了 `MIMO_QC` 环境变量的用户不会收到任何提示，只是不再有 `mimo_qc.json`。想要模型看片意见的人只能用解说评审（`review.py`）或 Agent 自己看成片，暂时没有基于抽帧的成片级多模态评审。
- 是否在默认路径：否（原来就默认关闭）。谁读取产物：删除前只有 `final_qc` 元数据汇总和 dashboard 卡片，都不影响结论。删除信号：本篇即删除。agent 表面增量：负两个 flag、三个环境变量、一个入口脚本。

## Verification

- `ruff check .` 通过。
- `python3 scripts/test.py orchestrator inspect` 全绿（orchestrator 407 passed，inspect 23 passed）。
