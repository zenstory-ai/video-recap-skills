# Agent Note: 剪后输出证据按 clip_plan_validated.json 身份绑定，续跑不再失效

Status: implemented

## Problem

PR #126 把 `speech_boundary_anchors_output.json` 的新鲜度判断从内容哈希改成 `clip_plan_identity`（`clip_plan_validated.json` 的 `{size, mtime_ns}`，见 [[2026-09-20-no-content-hashing]]），video-script 的 `speech_ownership._output_payload_is_current` 直接按这个键取值。复核 2026-10-02 架构审计时确认了三处问题：

- recap 多源 cut 的写入方 `_write_multi_source_output_speech_evidence` 不写 `clip_plan_identity`。多视频 cut 第三遍续跑执行 `validate.py --mode cut_output` 时抛 `KeyError`。同一函数还直接取 `anchor["pause_start"]`，缺这个字段的锚点也会抛 `KeyError`；单源的重映射和 assemble 都默认 `time - 0.12`。
- 只补字段并不够。recap 每次续跑都会先调 `cut.py`；cut 复用 `edited_source.mp4` 时也会重写一遍内容不变的 `clip_plan_validated.json`，`mtime_ns` 随之改变。第二遍写下的输出证据在第三遍一律判为过期，lint 按 fail-closed 拿到空证据，任何不在 0.25s 冷开场的旁白都报 `source_sentence_anchors_unavailable`。单源 cut 也受影响；assemble 的 `audio_mix` 用"证据不早于计划"判断，同样会退回 require_measured。实测：对同一计划连跑两次 `cut.py`，计划文件 size 不变，mtime_ns 变了，`validate` 从通过变成阻断。
- 测试里 cut 与 validate 子进程全被 mock，测不到这条链路。

## Decision

- recap 多源写入方现在写 `clip_plan_identity: file_identity(work_dir / "clip_plan_validated.json")`（用 recap 自己的 `materials.file_identity`），缺 `pause_start` 的锚点默认 `time - 0.12`，与单源一致。
- `cut_cli._write_validated_plan(path, plan, raw_plan_paths)`：文件内容已逐字相同、且不早于任何原始计划（`--clip-plan` 指定的文件与 `work_dir/clip_plan.json`）时不重写。复用路径不再先写一份 `rendered=false` 的中间版本，只在最后写一次；渲染路径保持"先写计划事实、渲染后再写最终版"，渲染失败时磁盘上仍是本次的计划事实。
- 原始计划被重新保存（即使内容没变）时照常重写，"validated 早于 raw 即过期"的约定不受影响；此时 recap 的 ledger 本来就会要求重写旁白、重建证据。
- 测试：`tests/orchestrator/test_io_fixes.py` 新增两条参数化用例，经 `_run_multi_cut` 跑第二、第三遍，validate 用真子进程：一条断言 lint 无错误，一条断言半句切入时报 `interrupts_source_sentence`，`suggested_start` 取自 recap 写的锚点。`tests/cut/test_required_evidence_cli.py` 新增真 ffmpeg 用例：续跑后计划文件身份不变，重新保存原始计划后身份改变，且不早于原始计划。

## Alternatives considered

- **每次续跑都在 validate 前重写输出证据**：最强理由是不用改 cut，多源这边 recap 自己就能做。没采用，因为单源证据由 video-understanding 的 `--brief-only` 产出，续跑时重跑它会重建 brief、可能重生成 storyboard；assemble 的 mtime 判断也只能靠重写来续命。cut 不改，问题就留在源头。
- **消费方改为比对计划内容（clips 的 source/output 区间）**：最强理由是只要映射没变就永远有效，与写入时序无关。没采用，因为这会改动三个 skill 的落盘契约和 understanding/script 两份逐字节 parity 的 `speech_ownership.py`，实际上又走回了 [[2026-09-20-no-content-hashing]] 刚拿掉的内容比对。
- **只补 `clip_plan_identity`（审计原方案）**：改动最小。没采用，因为续跑时 cut 会刷新计划文件的 mtime，`KeyError` 只是换成了必然的 fail-closed 阻断。

## Consequences

- **收益**：单源和多源 cut 的第三遍续跑都能用第二遍写下的输出时钟证据做 lint 和 assemble 闪避；understanding 的 edited storyboard 按计划文件身份做缓存，也不再在每次续跑时失效。
- **代价**：cut 每次运行多读一次计划文件做比较；"同一计划未改动"现在以文件内容逐字相同为准，cut 输出里任何不稳定的字段（比如时间戳）都会让这个优化失效，仍然退回 fail-closed，不会误放行。
- 素材库白名单这次补了 `consolidation.status.json`，`asr_timing_evidence.json` 有意不入库：它按身份绑定 `asr_result.json` 与 `audio.wav`，恢复时前者被脱敏重写、后者不复制，恢复出的副本永远是 `MISSING_OR_STALE`。
