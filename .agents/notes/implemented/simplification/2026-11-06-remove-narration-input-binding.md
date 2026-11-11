# Agent Note: 移除 narration_input_binding 产物

Status: implemented

翻转 [移除声音定稿交付路线](2026-11-06-remove-delivery-family.md) 里“`narration_input_binding.json` 与 `assembly_qc` 的同名字段保留、内容不变”的决定。

## Problem

定稿路线移除后，这个文件的每个字段都是常量或副本：`identity_status` 恒为 `UNADOPTED`，`requested_provider`、`requested_voice`、`snapshot`、`placed`、`tempo_policy` 恒为 `None` 或定值，`voice_authentication`、`direct_listening` 恒为 `NOT_CHECKED`，`conversion` 恒为 `{applied: false}`，逐段行抄 `tts_meta`，另外两个路径恒为 `work_dir/narration.wav` 和 `work_dir/output.mp4`。SKILL.md 自己写着它不防旧 WAV，也不等于声线鉴定或听审。它的摘要还写了三遍：manifest、QC 和 `assembly_settings`。保留时的理由是外部读取方在用，但全仓搜不到任何读取方。

## Decision

- 删除 `skills/video-assemble/scripts/narration_input_binding.py` 和它唯一的依赖 `artifacts.write_json_atomic`；解说运行不再写 `narration_input_binding.json`。
- `assembly_manifest.json`、`assembly_qc.json` 去掉顶层键 `narration_input_binding`，`assembly_manifest.json` 的 `assembly_settings` 去掉同名键。
- 两处按文件名读取的拒绝逻辑不动：`assembly_contract.reject_removed_delivery_inputs` 和 `recap_source.py` 仍拒绝 `identity_status` 为 `BOUND_TO_ADOPTION` 的旧文件。旧 `work_dir` 里 `UNADOPTED` 的文件被忽略，也不会被覆盖或删除。
- `test_unadopted_narration_tone_is_audible_in_final_mix` 保留，它验证的是混音而不是记录；所在文件改名为 `tests/assemble/test_narration_final_mix.py`，并断言解说运行不写这个文件。
- `examples/guohuo-60s/skill-runbook.md` 不再提这个字段名。

## Alternatives considered

- **继续保留文件，只去掉三处摘要。** 最强的理由是产物保持不变，未知的外部脚本继续能读到逐段路径。不采用：路径可以从 `tts_meta.json` 和 `work_dir` 直接得到，文件本身不多提供一条信息，而这些常量字段还在暗示有鉴定或绑定。

## Consequences

- **收益**：少一个模块、一个产物和三个 JSON 键；每次运行的产物里不再出现被删路线的概念。
- **代价**：仓库外读取这个文件或这些键的脚本会失效，因此记入 CHANGELOG 的 `Removed`。
