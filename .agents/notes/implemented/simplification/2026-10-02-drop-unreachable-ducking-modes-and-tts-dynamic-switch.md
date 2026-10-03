# Agent Note: 删除走不到的 ducking 模式与 tts_dynamic_params 开关

Status: implemented

## Problem

[[2026-10-02-skill-ownership-function-parity-and-budgets]] 把"没有调用方的模式"和"只有测试在改的开关"列为该删的东西，本篇落地其中两项：

- video-assemble 的 `CONFIG["ducking_mode"]` 写死为 `"fixed"`，没有环境变量、CLI 参数或任何生产代码改它。`audio_mix._build_audio_filter_complex` 里 `sidechaincompress` 和 `none` 两个分支、`timeline_emit` 里 `ducking_mode != "none"` 的判断，以及只给 sidechain 用的 `ducking_threshold` / `ratio` / `attack` / `release` / `level_sc` / `makeup` 六个键，只有测试用 `monkeypatch.setitem` 才会碰到。sidechain 分支还会在 `source_duck_end` 超出旁白窗口时自己退回 fixed，说明它本来就保不住句末交接契约。
- video-voiceover 的 `CONFIG["tts_dynamic_params"]` 写死为 `True`，同样没有入口能改。`_prepare_tts_segment` 里它为 False 时的 `"+0%", "+0Hz"` 分支只被 7 处测试用来拿到确定的语速。`voiceover.py` 顶部还有一行带 `noqa: F401` 的转导出，其中 `_normalize_tts_wav_rms` 只有测试通过 `voiceover` 去拿。

`ducking_narr_weight` 不在此列：默认 fixed 路径用它算旁白音量（`_amix_tail`），保留。

## Decision

- `audio_mix._build_audio_filter_complex` 只剩 gap-fill 包络一条路：原声按旁白窗口压到 `speech_ducking_volume` / `zone_ducking_volume`，间隙回到 `idle_orig_volume`，没有放置信息时恒定 `ducking_orig_volume`；旁白音量仍是 `ducking_narr_weight`。
- video-assemble `lib.CONFIG` 删去 `ducking_mode` 和六个 sidechain 参数；`assembly_settings_payload` 的 `audio_mix` 同步删去这七个键。`timeline_emit` 在 `narration` 模式且没有显式混音时总是写 ducking 自动化。
- video-voiceover `lib.CONFIG` 删去 `tts_dynamic_params`，`tts_settings_payload` 不再带这个键。MiMo 与 Fish 段落总是走 `_compute_tts_params`（按位置与标点给语速/音高），index-tts 仍用 provider 默认值。
- `voiceover.py` 只从 `tts_audio` 导入自己调用的 `_maybe_normalize_tts_wav`，不再转导出；归一化测试直接从 `tts_audio` 取 `_normalize_tts_wav_rms`。
- 测试：删掉 `test_build_audio_filter_complex_explicit_modes`，去掉各处对 `ducking_mode` / `tts_dynamic_params` 的 `setitem`；唯一写死 `"+0%"` 期望的用例改为按 `_compute_tts_params` 算期望值；新增 `test_mimo_preparation_always_derives_rate_and_pitch_from_content` 断言三段的具体语速/音高，并断言设置载荷里没有该键。`test_audio_policy_parity.py` 的 `MIX_KEYS` 去掉七个键。
- `examples/guohuo-60s/assembly_manifest.json` 是历史运行的产物，不改。

## Alternatives considered

- **保留 sidechain 作为备用混音策略。** 最强理由：sidechaincompress 按旁白电平自动压原声，不依赖放置信息，理论上对没有准确对白边界的素材更稳。没采用：没有任何入口能选它，而且它在需要句末交接时已经自动回退 fixed，保不住 [[2026-06-17-block-delivery-full-volume-original-audio]] 的块交付契约；真要恢复，应作为一项带 E2E 验证的新功能重新设计。
- **保留 `tts_dynamic_params` 作为可调开关，只补一个环境变量。** 最强理由：有人可能想要完全平直的语速做对比。没采用：没有用户提过，补环境变量等于为测试便利新增产品面；测试改成断言动态值反而覆盖了真实默认路径。
- **在 `tts_settings_payload` 里保留常量 `"tts_dynamic_params": True` 以免缓存失效。** 最强理由：已有 work_dir 重跑时不必重新合成 TTS。没采用：这是一个不再对应任何设置的假字段，会一直留在缓存键里误导读者；一次性重新合成的代价可接受，并已写进 CHANGELOG。

## Consequences

- 收益：assemble 与 voiceover 脚本净少约 50 行，混音入口只剩一条可读的路径，配置表少八个无人能改的键。
- 代价：TTS 段缓存按设置载荷整体比较（`_load_tts_segment_cache`），载荷少了 `tts_dynamic_params` 后，升级前生成的 `tts_segments/*.cache.json` 全部失效，已有 work_dir 重跑 voiceover 会重新合成一次 TTS（Fish Audio 会产生一次 API 费用）。assembly 侧的 `assembly_settings` 只写进 `assembly_manifest.json` 供 `resource_lock` 读 BGM / 包装 / 字体，不参与任何缓存比较，所以合成不受影响。
- 是否在默认路径：渲染结果不变；默认路径本来就是 fixed 包络与动态语速。
- 直接 `import voiceover` 并取 `voiceover._normalize_tts_wav_rms` 的外部代码需要改从 `tts_audio` 导入。

## Verification

- `ruff check .` 通过。
- `PATH=$HOME/.cache/video-recap-ffmpeg8:$PATH python3 scripts/test.py orchestrator voiceover assemble` 全绿（orchestrator 404 passed，voiceover 128 passed，assemble 474 passed）。
