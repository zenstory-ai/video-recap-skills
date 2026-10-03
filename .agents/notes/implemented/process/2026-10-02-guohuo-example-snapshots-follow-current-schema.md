# Agent Note: guohuo-60s 的 assemble 快照只保留当前仍写的字段，runbook 写明复现时的项目级步骤

Status: implemented

## Problem

[[2026-10-02-drop-unreachable-ducking-modes-and-tts-dynamic-switch]] 与 [[2026-10-02-assemble-drop-qc-mirrors-dead-env-version-stamps]] 都决定 `examples/guohuo-60s/assembly_manifest.json` / `assembly_qc.json` 是历史运行产物、保留旧形状、不改。精简合并后，对这个样例做了一次静态契约核对和一次替身素材的端到端复现（原片与 Fish 音色不在本机），发现这个决定和样例文档互相矛盾：

- README 与 `skill-runbook.md` 把这两份文件写成“采用版应达到的轨道结构和门禁”“发布门禁”，复现者会拿新运行的产物去对照。可快照里仍有 7 个 `ducking_*` 键（含 `ducking_mode: "fixed"`）、`qc_*` 镜像、`segment_audio_schema_version`、`assembly_settings.version` / `subtitle_text_normalize`、`release_gate`、`visual_rollup`、`delivery_rollup`、约 20 个字段的 `visual_qc` rollup 和 `summary.fit_failed_segments`，当前 video-assemble 一个都不写。
- `tests/orchestrator/test_guohuo_example.py` 只检查文件之间是否自洽，从不对照技能代码，所以这些漂移在 429 个 orchestrator 测试全绿的情况下没被发现。
- 端到端复现还暴露出 runbook 没写、只能靠项目级步骤完成的几件事：叠 Remotion 字幕条前要加 `--no-burn-subtitles`，否则烧录的 ASS 字幕和透明层会叠成两套；1080p / BT.709 conform 不是任何技能能做的；Remotion 的总帧数、四条花字、片名复现窗口和片名文字写死在 TSX 里，只换 `captions.json` 会得到偏短、错位的透明层；7 个解说块都超出推荐字数 1.5–2.3 倍，cut_output 只给 warning，换声音后某块可能装不下，assemble 会在编码前以 `no_safe_fit` 阻断；样例记录的 `ducking_narr_weight: 2.4` 无法用技能复现（代码固定 1.5，且从未有开关）。

## Decision

- 两份 assemble 快照删掉当前写入方不再产出的键，其余键和全部数值不动；写入方后来新增的键（`audio_mode`、`narration_input_binding`、`audio_mix_binding`、`source_video_identity`、`tempo_source`、逐段 `output_*_sample` / `adopted_gain` 等）不补写，因为原运行没有记录它们，补写就是编造。
- 新增 `tests/assemble/test_guohuo_example_schema.py`：用 `_assembly_manifest_payload` / `_build_assembly_qc` 生成当前形状，断言样例的嵌套键路径都是它的子集。以后 assemble 再删字段而忘了改样例，测试会失败；新增字段不影响。
- README（样例与仓库根）把“发布门禁”“字幕溢出”改为 `assembly_qc.json` 的 `verdict` / `blocking_codes`。`skill-runbook.md` 补上：项目级 `background_research.json` 的放置、粗 ASR 窗口导致的边界阻断与处理方式、`--no-burn-subtitles`、超预算块装不下时的处理（`no_safe_fit` / `needed_tempo_factor`、`--preserve-approved-text`）、`ducking_narr_weight` 2.4 不可复现、Remotion 需要重定时的常量与无锁文件的传递依赖、1080p / BT.709 conform 属于项目级、10 MB 码率按新时长重算。
- 上面两篇笔记里“不改”的一行事实改为指向本篇。

## Alternatives considered

- **维持“历史快照不改”，只在 README 标注为旧版本存档。** 最强理由：样例记录的是一次真实制作，任何改动都会让它和当时实际写出的文件不一致。没采用：README / runbook 把它当作复现的对照目标，存档标注挡不住复现者按不存在的字段核对；删掉的键都是当时就恒定或重复的副本（`release_gate.delivery_qc` 恒为 PASS、rollup 是 `visual_qc.json` 的拷贝），删掉不丢失任何只在这里有的测量值。
- **按当前代码重新生成两份文件。** 最强理由：形状与新运行完全一致。没采用：原片和 Fish 音色都不在仓库里，只能用替身素材重跑，得到的就不是这次制作的数据了。
- **把 Remotion 常量改成 JSON props / `calculateMetadata`。** 最强理由：复现时只改数据、不改代码。没采用：这需要在有无头 Chrome 的环境里重新渲染验证，本次只能静态改动；先在 runbook 写清要改哪几个常量。（该决定已由 [[2026-10-02-guohuo-remotion-overlay-data-driven]] 翻转：常量移入 `overlay.json`，并做了真实渲染验证。）

## Consequences

- 收益：样例展示的字段与当前 assemble 输出一致，并有测试守住；复现者按 runbook 能知道哪些事要在项目里自己做，不会再因为默认烧录字幕或超预算块而反复返工。
- 代价：两份快照不再是原运行逐字节的文件（只少了键，没有改值）；旧版本留下的 visual rollup 细节（如 1920×1080 画布、25 条字幕的逐条宽度）从样例里消失，需要时看 `delivery-qc.json`。
- 新测试放在 assemble 组，因为它要导入 video-assemble 的模块；orchestrator 组的样例测试保持不导入技能代码。
