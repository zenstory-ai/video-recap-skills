# Agent Note: voiceover 默认不再截短旁白，放不下在渲染前阻断

Status: implemented

## Problem

[[2026-10-02-strict-full-mode-no-silent-rewrite]] 退役了 validate 的静默改稿，但写明"voiceover 的 `approved_text_policy.py` 和默认句界缩稿作为最后保险保留"。复核时发现这层保险从来保不住任何东西：

- `voiceover.py` 在 TTS 时长超过 `slot - pause_after` 换算的原始预算时，按句界截短文本（`lib._truncate_at_sentence`）再合成一次，结果里 `truncated: true`。
- assemble 的 `assembly_contract._build_assembly_qc` 对任何 `truncated` 段无条件加 `truncated_speech` 阻断码。所以默认路径上被截短的段一定会在最后被阻断：截短只换来第二次 TTS 调用和一次注定作废的完整渲染。
- assemble 只在严格 `tempo_policy` 路径上于编码前对 `no_safe_fit` 报错；默认路径要等完整视频编码后，`main` 读到 `assembly_qc["blocking"]` 才 `SystemExit`。评审时测得 pass5 类运行需要 ×1.31 的提速才放得下，照样先渲染完整个视频再失败。

`assemble_video` 只有 `main` 一个调用方，而 `main` 本来就在 `assembly_qc` 阻断时退出，所以把同一组阻断码提前到编码前只改变触发时间，不新增门禁。

## Decision

- `voiceover.py` 删除截短并重合成的分支。超预算时记日志 `段 N: 超出预算 Xs > Ys，保留原稿，交由 assemble 有界提速或在渲染前阻断`，原稿照常交给 assemble。`--preserve-approved-text`（`enforce_approved_text_policy`）不变，超窗仍在 TTS 阶段抛 `ApprovedTextDurationError`。结果里的 `truncated` / `truncate_reason` 字段保留，默认路径上恒为 `False` / `"none"`。`lib._truncate_at_sentence` 和只有它在用的 `_text_char_count` 一并删除。
- `approved_text_policy.LEGACY_TEXT_POLICY` 改名为 `report-over-budget-v2`。策略名是 TTS 分段缓存键的一部分，改名让按旧策略缓存的截短音频失效。
- `assemble.py` 在 `_apply_source_sentence_handoffs` 之后、`seal_render_inputs` 之前调用 `_block_before_render`：用当前分段构建一次 `_build_assembly_qc`（`delivery_qc.video_encode_passes = 0`、`reencode_reason = ["blocked_before_render"]`），有阻断码就删掉工作目录里旧的 `output.mp4` 和 `timeline.json`、写 `assembly_qc.json`，并抛 `AssemblyBlockedBeforeRender`（`RuntimeError` 子类），消息列出阻断码、段号和 `needed_tempo_factor`；`main` 把它转成 `SystemExit(消息)`，和编码后阻断一样只打印一行，不出 traceback。默认路径和严格路径都走这一步，取代原来只在严格路径上的 `no_safe_fit` 报错。阻断码不变（`no_safe_fit`、`skipped_segments`、`unsafe_source_handoff` 等）。
- `narration_audio._build_timed_narration` 在 `no_safe_fit` 时把 `needed_tempo_factor` 写回分段，供上面的错误消息使用。
- 文档：`video-voiceover/SKILL.md` 的能力边界与缓存说明同步更新。

## Alternatives considered

- **保留默认截短，只去掉 `truncated_speech` 阻断**：最强理由是 Agent 写超的稿子能自动出片，少一轮返工。没采用：这等于恢复刚被退役的静默改稿，成片与 Agent 认可的稿子不一致，违背 `docs/production-boundaries.md` 的"文本装不下窗口时回到创作，不让 TTS 静默缩稿"。
- **把 cut_output 的超预算 lint 升级为 error，或把 3.9 字/秒的估算重新标定**：能更早失败，连 TTS 都不用调。没采用：这会是一道建立在估算上的新门禁，而这次实测估算乐观了 1.7 倍（MiMo 实测 2.1–3.7 字/秒）；标定是后续工作。

## Consequences

- **收益**：默认路径不再出现 `truncated_speech`；每段只调一次 TTS；放不下的段在视频编码前失败，不再白跑完整渲染，错误里直接给出需要的提速倍数，Agent 知道该删多少字。pass4 类运行的全文要么经 assemble 有界提速放下（评审估算原始音频最多可超 4.39 秒），要么在编码前阻断。
- **代价**：以前"截短后其实也会被阻断"的段，现在同样被阻断，但报的码从 `truncated_speech` 变成 `no_safe_fit`，依赖旧码的脚本需要更新。旧缓存的截短音频会被重新合成一次。工作目录里上一次的 `output.mp4` 和 `timeline.json` 在编码前阻断时会被删除，以免和 FAIL 的 QC 放在一起被误用（`assembly_manifest.json` 与编码后阻断时一样不动）。
- 只有单测证据；真实重跑需要确认 `tts_meta.json` 里没有 `truncated: true`、每段 TTS 只调用一次、所有 assemble 失败的 `assembly_qc.delivery_qc.video_encode_passes == 0` 且没有 `output.mp4`，以及用 pass5 的稿子重放时以 `no_safe_fit ×1.31` 在编码前阻断。
