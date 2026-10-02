# Agent Note: 不再写出没有读者的产物

Status: implemented

## Problem

架构审计（`.omc/plans/2026-10-02-skill-architecture-audit.md`，subtraction S2 / boundaries B12）在 [[2026-10-02-skill-ownership-function-parity-and-budgets]] 的 Phase 1 里列出三处"只写不读"的契约，复核后确认没有任何读者：

- **`deslop_qc_requirements.json`**：video-understanding 的 brief 每次都写 `{"schema_version": 1, "style_card_required": false}`。video-script 的 `deslop_qc.py` 读它来决定缺少 `style_card.json` 是 blocker 还是 advisory，但仓库里只有测试会写 `true`，所以 blocker 分支在生产里走不到；文件缺失时的默认值也是 advisory。两份 data-schema 各用 17 行描述这个契约。
- **cut 的交付 QC**：`cut_render.update_delivery_qc` 把编码次数、重编码原因、stream copy 风险、ffprobe 探到的采样率写进 `clip_plan_validated.json` 的 `qc.delivery_qc`，`write_cut_delivery_qc` 再写一份 `cut_delivery_qc.json`。写入点有三个（`cut_cli` 规划时、复用缓存时、`build_edited_source_video` 渲染后），`cut_cli` 还要在 normalize-only 和 required_evidence 预检时删掉陈旧的 `cut_delivery_qc.json`。仓库内没有读者：recap 的 `_surface_cut_qc` 和 dashboard 只读 `target_duration_status`、`output_geometry`、`blocking` 等键；assemble 的 `assembly_qc.json["delivery_qc"]` 是 assemble 自己算的成片交付检查，与 cut 无关。cut 的 SKILL.md 本来就写明它只做一次中间编码、不承担最终交付。
- **`plan["warning"]`**：`normalize_clip_plan` / `normalize_multi_source_clip_plan` 在总时长超出目标 15% 时写一个顶层 `warning` 字段并打日志。没有代码读这个键；`narration_mapping.update_cut_qc` 已经用同样的 1.15 阈值产出 `target_duration_drift`，并决定是否 blocking。

## Decision

- video-understanding 删除 `briefing/timeline.py` 的 `_write_deslop_qc_requirements` 和 `briefing/builder.py` 的调用，brief 不再写 `deslop_qc_requirements.json`。
- video-script 的 `deslop_qc.py` 删除 `_style_card_requirement`；`_style_card_issue` 只产出 advisory（`missing_style_card` / `malformed_style_card`），不再读 requirements 文件。报告删掉 `style_card_required` 和 `style_card_requirement_source` 两个键，其余字段与 blocker 规则（破折号、占位符泄漏）不变。旧 work_dir 里残留的 `deslop_qc_requirements.json` 被忽略。
- 两份 data-schema 删除 `deslop_qc_requirements.json` 小节；recap 的 `deslop_qc.json` 示例改用 `em_dash` blocker，并把缺少 / 空的 `style_card.json` 写进 advisories。
- video-cut 删除 `cut_render` 的 `_probe_audio_sample_rate`、`_delivery_reencode_reason`、`update_delivery_qc`、`write_cut_delivery_qc`，以及 `build_edited_source_video` 和 `cut_cli` 里的全部写入与清理点。`clip_plan_validated.json` 不再有 `qc.delivery_qc`，cut 不再写 `cut_delivery_qc.json`。渲染参数（libx264 / yuv420p / AAC 48 kHz / faststart）不变，`edited_source.mp4.meta.json` 的缓存判定不变。
- `cut_contract` 的两个 normalize 函数不再写 `plan["warning"]`，超时长只由 `update_cut_qc` 的 `target_duration_drift` 表达。
- `narration_review_override.md` 不在本次范围：video-script SKILL.md 把它定义为设计上只写不读的审计记录。

## Alternatives considered

- **保留 `deslop_qc_requirements.json`，作为将来 opt-in 硬性要求 `style_card.json` 的开关。** 最强理由：契约和读取逻辑都已经写好，哪天要把文风卡变成硬门禁只需改一个布尔值。不采用：从引入到现在没有任何生产路径写过 `true`，它只让两份 data-schema 多出 34 行、让 deslop 报告多出两个恒定字段。真要做硬门禁时，从 CLI flag 或 recap 编排层显式传入，比靠 brief 侧写文件、script 侧读文件的跨 skill 隐式约定更清楚。
- **把 cut 的交付 QC 留在 `clip_plan_validated.json`，只删 `cut_delivery_qc.json`。** 最强理由：采样率和重编码原因是排查中间编码问题的线索，放在已有文件里不增加产物数量。不采用：写入它要多跑一次 ffprobe，还在 `cut_cli` 里留了三处写入和两处清理逻辑；这些字段里除采样率外都是常量字符串，采样率由渲染命令的 `-ar 48000` 固定。成片的交付兼容性由 assemble 的 `assembly_qc.json` 检查，cut 不该有第二份。
- **保留 `plan["warning"]`，只是不再写日志。** 没有被认真考虑：字段没有读者，`target_duration_drift` 已覆盖同一信息。

## Consequences

- **收益**：
  - 净删约 160 行代码和约 90 行文档 / 测试。
  - cut 每次运行少一次 ffprobe；`cut_cli` 的主流程少了三处交付 QC 写入和两处陈旧文件清理。
  - understanding 与 script 之间少了一条靠文件名维系的隐式契约。
- **代价**：
  - 外部若有脚本读 `cut_delivery_qc.json`、`qc.delivery_qc`、`deslop_qc_requirements.json`，或 deslop 报告里的 `style_card_required` / `style_card_requirement_source`，会读不到。仓库内没有这样的读者。
  - 想把 `style_card.json` 变成硬门禁，需要另做一次设计。
- **默认路径**：三项都在默认路径上被写出，删除后默认路径产物减少两个文件，`clip_plan_validated.json` 少一个键。
- **agent 表面**：data-schema 少两个小节，SKILL.md 不变。
