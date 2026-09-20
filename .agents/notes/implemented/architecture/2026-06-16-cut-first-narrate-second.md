# Agent Note: 剪辑模式先剪后配，解说按成片输出时间轴写

Status: implemented

## Problem

cut 模式初版让 Agent 用原片时间写 `narration.json`，再由 `map_narration_to_clips` 映射到输出时间轴。2 小时压成 30 分钟时，brief 按原片时长要求 ~1150 个 beat，mapper 静默丢掉约 75% 并裁剪跨片段边界的 beat，没有任何环节复查，解说和画面系统性失步。映射后加 lint 只能报告失步，不能消除。

## Decision

编排器 `recap_runner.py` 的 cut 模式是两次暂停：

1. PASS 1：理解完成后暂停，Agent 只写 `clip_plan.json`。
2. `cut.py --no-narration-map` 渲染 `edited_source.mp4` 并写 `clip_plan_validated.json`（含 `clip_id`、`source_start/end`、`output_start/end`）。
3. PASS 2：重建 OUTPUT 时间轴的 brief 后再次暂停，Agent 对着成片按输出时间写 `narration.json`；`validate.py --mode cut_output` 校验；voiceover 与 assemble 以 `edited_source.mp4` 为视频，不再做任何原片→输出映射。

守则：

- `recap_phase.json` 记录 `clip_plan` 与 `narration` 的 md5；`clip_plan.json` 变而 `narration.json` 未变时 must 拒绝继续，提示删稿重写。
- `clip_plan_validated.json` 是 cut 证据的权威来源：输出时间轴的 speech 证据（`speech_boundary_anchors_output.json` 等）must 携带匹配的 `clip_plan_fingerprint`；缺失、过期或畸形一律 fail closed，never 回退到原片时钟，never 信任 Agent 写入的 `overlaps_speech=false`。
- `review.py --timeline` 默认 `auto`，以 `recap_run_manifest.json` 的 `edit_mode` 为准判断评审时间轴；编排 full 模式显式传 `source`，避免复用目录里的旧 cut 产物误导。
- `--audio-mode source-mix|adopted-packet-copy` 的 cut 项目只有 PASS 1：剪完直接合成，不重建 brief、不等 `narration.json`，声音归属由 `recap_run_manifest.json` 的 `audio` 块记录（#103）。
- 例外：单独调用 `cut.py` 且不传 `--no-narration-map` 的旧版单阶段路径仍把原片时间的 `narration.json` 映射为 `narration_mapped.json`，voiceover 需显式 `--narration` 传入；编排路径 never 生成该文件。

来源：187dd2b (#16) Step 4–6、93864f5 (#23)、2c923cf、96af0a7 (#31)、affe325 (#64)

## Alternatives considered

- **原片时间写稿 + 机械映射**（e4c8c85 的初版指令"cut 模式解说保持原片时间，只在 clip_plan 校验后映射"）— 最强理由：Agent 面对的是原片 ASR / VLM 证据，原片时间是它最安全的写作坐标，而且只需一次暂停。否：映射会丢、裁、夹 beat，且 Agent 从未见过它写作的那条时间轴；改为先剪后配后 `map_narration_to_clips` 在编排路径上根本不被调用。
- **映射后 lint + 阻断式 preflight**（187dd2b Step 4）— 最强理由：不改流程即可在 TTS 前挡住严重失步。否：只能报告不能治本；作为独立 `cut.py` 旧路径的兜底保留。
- **`cut_output` 评审缺 validated 文件时回退 raw `clip_plan`** — 最强理由：更宽容，评审总能跑。否：raw 计划与吸附 / padding 后的实际剪辑不一致，会产生误导证据（93864f5 Rejected）。
- **立刻删除 legacy `narration_mapped.json` 路径** — 最强理由：少一条分支和文档。否：独立使用 video-cut 的用户仍依赖它（2c923cf Rejected），保留但要求显式传入。

## Consequences

- **收益**：解说与画面按构造对齐，没有映射就没有失步；brief 按成片时长给预算，不再要求上千个 beat。
- **代价**：两次暂停、两次 brief；任何 `clip_plan` 改动都意味着重写解说（ledger 会拒绝旧稿）；legacy 映射路径与其测试仍需维护；多视频项目的输出证据要逐源保留 `source_id` 与原片起止。
