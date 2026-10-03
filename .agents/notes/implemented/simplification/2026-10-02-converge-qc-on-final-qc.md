# Agent Note: QC 收敛到 final_qc，删除 preflight_qc.json 与 qc_contract

Status: implemented

## Problem

0.6.0（PR #58）的 shift-left QC 设计了一套通用 QC 契约 `qc_contract.py`：6 个 stage、4 种 severity、4 种 confidence、5 种 sample policy、语义/审美这类非确定性类别、每条 finding 18 个必填字段，外加递归的密钥与 URL 脱敏。它要同时服务 4 个生产者：`final_qc.json`、`golden_eval.json`、MiMo 建议型 QC 和逐阶段账本 `preflight_qc.json`。[[2026-10-02-delete-mimo-qc]] 和 [[2026-10-02-fold-golden-eval-into-final-qc]] 落地后，剩下的两个生产者都撑不起这套契约：

- `preflight_qc.json` 从来没有 finding。`recap_runner` 和 `recap_source` 调 `_write_shift_left_stage_qc` 的 11 处都只传 metadata，所以它永远是 `ok: true`、`blocker_count: 0`。metadata 不是整份拷贝别的产物（`tts_meta.json`、`assembly_manifest.json`、`clip_plan_validated.qc`），就是 `not_applicable` 之类的常量字符串。唯一的读取方是 `final_qc` 的 `metadata.artifacts`，而它只记一行摘要。dashboard 和 `recap_inspect` 都不读它。
- 这个账本还会让运行崩溃。narration 路径不清理旧文件，在 `pre_tts` 直接 `json.loads(...)["metadata"]["stages"]`。旧 work_dir 里只要有一份损坏或旧格式的 `preflight_qc.json`，运行就会在付费的解说评审调用之后报错退出。一个只做建议的账本，唯一可能的影响就是让运行失败。
- `final_qc` 会把 `assembly_qc.json` / `visual_qc.json` 的 blocking code 汇总成自己的 blocker（`_upstream_blockers`），但这在流水线里不可能触发：visual QC 阻断时 `assemble.py` 在渲染前就抛错，assembly QC 阻断时它的 `main` 以 `SystemExit` 退出，而 recap 的 `_run` 遇到非零退出码就整体失败。能走到 final_qc，说明两份上游报告一定是刚写出的非阻断结果。只有在已经失败的 work_dir 上手动跑 `final_qc.py`，这段汇总才会生效，而那时上游报告本来就能直接看。
- 剩下的唯一生产者 `final_qc._finding` 每次都写死 `severity=blocker`、`confidence=objective`、`sample_policy=deterministic`、`deterministic=True`、`blocking=True` 和固定的 `model_used`；`rule_id` / `decision_reason` / `id` 只是 `code` / `message` / `finding_id` 的复制。读取方只用到 `ok`、`blocker_count` 和 finding 的 `code` / `message` / `blocking`（dashboard `runs._findings`、`_require_final_qc`）。

[[2026-09-21-scripts-subpackages-jianying]] 第四批记录了"`qc_contract.py` 不动，因为 `shift-left-qc-schema.md` 按路径引用它"。本篇把这个模块和那份文档一起删掉，翻转的是那一条，以及 0.6.0 中"逐阶段写 preflight 账本"和"通用 QC 契约"这两部分。0.6.0 的 shift-left 决定本身没有单独的笔记（[[2026-10-02-fold-golden-eval-into-final-qc]] 已记过这一点）。

## Decision

- 删除 `preflight_qc.json`。`recap_stage_qc` 删除 `_write_shift_left_stage_qc`、`_load_preflight_stage_reports`、`_tts_qc_metadata`、`_post_render_qc_metadata`，只保留 `_write_final_qc_reports`、`_print_final_qc_pointer`、`_require_final_qc`。`recap_source` 删除 `begin_non_narration_qc` / `begin_local_adoption_qc`。`recap_runner` 去掉 `pre_tts` / `post_tts` / `pre_assemble` / `post_cut` / `post_render` 的全部写入点。`_render_cut` 照常调用 `_surface_cut_qc` 打印 cut QC 那一行。旧 work_dir 里残留的 `preflight_qc.json` 不再被读取，也不再被清理（`test_full_run_ignores_a_corrupt_leftover_preflight_ledger` 用一份损坏的残留文件跑完整个 full 模式来锁住这一点）。
- `final_qc` 删除 `_UPSTREAM_QC_ARTIFACTS` / `_upstream_blockers`。`assembly_manifest.json`、`assembly_qc.json`、`visual_qc.json` 仍列在 `metadata.artifacts` 中，摘要字段改为上游 QC 自己的 `schema_version` / `verdict` / `blocking` / `blocking_codes`；旧字段 `stage` / `ok` / `blocker_count` / `finding_count` 是为 QC 契约形状的报告设计的，在这三份文件上全为 null。
- 删除 `scripts/qc_contract.py` 和 `references/shift-left-qc-schema.md`。`final_qc.py` 内联最小报告形状，`schema_version` 升到 2：报告字段为 `schema_version`、`artifact`、`ok`、`blocker_count`、`finding_count`、`findings`、`metadata`；每条 finding 只有 `code`、`message`、`blocking`（恒为 true）、`evidence`、`next_action`。`next_action` 保留，因为它是 agent 读 `final_qc.json` 后可以直接执行的修复提示（如 `rerender_final_output_with_valid_fps`）。`evidence` 也保留。
- 不再做通用脱敏，改为裁剪探测结果。assemble 不传 `-map_metadata -1`，原片的容器标签（comment、purl 等可能带 URL）会出现在 ffprobe 输出里。`_probe_metadata` 现在只保留检查用到的流字段（codec、宽高、帧率、时长、采样率、声道等）和容器字段（`format_name`、`duration`、`size`、`bit_rate`、`nb_streams`），`tags`、`disposition`、`filename` 一律不写入报告（`test_probe_is_trimmed_so_source_tags_never_reach_final_qc`）。上游产物的摘要也只取固定的几个键。
- SKILL.md 的契约指引改为指向 `references/data-schema.md` 新的 `## final_qc.json` 一节，该节写出报告形状、全部阻断码、上游摘要和探测裁剪规则。原来误放在 QC 一节的 `cut_output` 评审说明移到 `narration_review.json` 一节。
- 测试：删除 `test_shift_left_qc_contract.py`（契约的值域、MiMo 阻断规则和 preflight 汇总），`test_final_qc.py` 改用本地 `_assert_valid` 检查 v2 形状，上游汇总的两个测试改成一个"只汇总、不转成阻断项"的测试。路由测试（`test_audio_routing`、`test_local_audio_adoption`、`test_source_routing_render`）去掉对账本 metadata 的断言，`test_strict_final_qc` 去掉对已删函数的 monkeypatch。

## Alternatives considered

- **保留 preflight 账本，修掉崩溃（损坏时重置）。** 最强理由：同一个文件就能看到一次运行的逐阶段轨迹（评审是否跑过、TTS 是否被采用而非生成、cut QC 的拷贝），排查时不用翻好几个产物。没采用：这些事实在 `narration_review.json`、`tts_meta.json`、`clip_plan_validated.json`、`assembly_manifest.json`、`recap_run_manifest.json` 里都有原件，账本只是拷贝；它从来不含 finding，也没有门禁或界面读它。保留下来，每次运行就要多 5 次整份 JSON 拷贝写入，还要维护一套 stage 值域。
- **保留 final_qc 的上游 blocker 汇总，作为纵深防御。** 最强理由：如果 assemble 将来在 QC 阻断时仍然退出 0，final_qc 还能兜住，`--require-final-qc` 也会随之失败。没采用：真出现这种回归，应该在 assemble 的退出码契约上修（`assembly_qc["blocking"]` 时 `SystemExit` 已有测试覆盖），而不是让下游再判一遍。汇总的代码和测试约 110 行，在流水线里走不到。上游 verdict 仍记在 `metadata.artifacts`，dashboard 也直接显示 `assembly_qc.json`。
- **保留一个瘦身后的 `qc_contract.py`（脱敏 + `build_report`/`validate_report` + 5 字段 `build_finding`）。** 最强理由：将来出现第二个 QC 生产者时可以复用，`redact_secrets` 也能挡住 metadata 里任何形状的密钥。没采用：只剩一个生产者时，"契约"就是 `final_qc.build_final_qc` 里那个字面量 dict，再验证一遍等于自己验证自己。脱敏真正要防的只有 ffprobe 标签这一处来源，从源头裁掉字段比用正则清洗任意 JSON 更可靠（白名单之外的字段根本不会写入，不靠猜哪些键像密钥）。真有第二个生产者时，再按它的实际需求抽取。
- **把 final_qc 的 finding 也裁到只剩 `code` / `message` / `blocking`。** 最强理由：这就是读取方实际用到的全部字段。没采用：`next_action` 和 `evidence` 是给读 `final_qc.json` 的 agent 用的修复线索，删掉之后，阻断信息只告诉你"坏了"，不告诉你"怎么修"。

## Consequences

- 收益：`qc_contract.py`（约 320 行）和 `shift-left-qc-schema.md` 删除，`recap_stage_qc.py` 从约 100 行降到约 40 行，`recap_source.py` 少 34 行，`final_qc.py` 少约 60 行；测试净减约 450 行。每次运行少写最多 5 次 `preflight_qc.json`，旧 work_dir 里的损坏账本不会再让运行在评审之后崩溃。`final_qc.json` 每条 finding 从 19 个键减到 5 个，报告里不会再出现原片的容器标签。
- 代价：这是 breaking 变更。流水线不再写 `preflight_qc.json`；`final_qc.json` 的 `schema_version` 变为 2，报告去掉 `stage`，finding 去掉 `finding_id` / `id` / `stage` / `severity` / `deterministic` / `confidence` / `rule_id` / `decision_reason` / `location` / `sample_policy` / `model_used` / `category` / `source` / `objective_corroboration`，`metadata.probe` 只保留白名单字段，`metadata.artifacts` 的摘要键改变。`ok`、`blocker_count`、`finding_count`、finding 的 `code` / `message` / `blocking` / `evidence` / `next_action` 不变，`--require-final-qc` 的判定不变。在失败的 work_dir 上手动跑 `final_qc.py`，不再把 assembly/visual 的 blocking code 转写成 final_qc 的 blocker，需要直接看 `assembly_qc.json` / `visual_qc.json`（或 `metadata.artifacts` 里的 verdict）。
- 是否在默认路径：是（每次 full/cut 运行都写过账本、都跑过契约校验）。谁读取产物：删除前 `preflight_qc.json` 只有 `final_qc` 的一行摘要在读；`final_qc.json` 的读取方只用不变的那几个字段。删除信号：本篇即删除。agent 表面增量：负一个产物、负一份 reference。
