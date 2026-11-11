# Agent Note: 收窄旁白放置循环，放置字段只算一遍

Status: implemented

## Problem

`_build_timed_narration` 里有一个走不到的重叠分支和多份冗余状态：

- `actual_start` 由 `last_written_end + max(0, pause)` 或 `last_written_end + tight_pause` 算出，之后只会被抬高；被 `end_boundary` 钳住时可用空间为 0，先走 `no_room`。所以重叠分支只有未经 lint 的负 `pause_after_ms` 才能进入，而 `narration_lint.py` 把负值判为错误。
- `prev_pause_samples` 在六个出口各赋值一次；`placed_count == 0` 等同于 `last_written_end == 0`；结尾检查等同于所有段都是 `skipped`；`available_samples` / `available`、`audio_samples` / `write_samples` 是别名；`needs_resample` 先算再用 `original_wav_path` 撤销。
- 放置状态算了三遍：voiceover 写占位值，`_adjust_tts_speed` 组一个 13 键的 meta，调用方的 else 分支再算一遍。meta 里 `tempo_factor`、`truncated`、`tts_rate_offset`、`audio_duration`、`placed_audio_duration`、`cumulative_tempo_*` 都没有读取方。

## Decision

- 循环顶部算一次 `prev_pause_samples, pause_samples`，`pause_samples` 用 `max(0, ...)` 钳住负值。
- 循环顶部（缺 WAV 分支之前）用 `narration_tempo_budget` 设好 `fit_status=fits`、`segment_tempo_factor=1.0`、`global_narration_speed`、基础 `effective_tempo`、`blocking=False`。`_build_assembly_qc` 按键直读这些字段，所以缺 WAV 的段也必须有。
- `_adjust_tts_speed` 的 meta 只留 `fit_status`、`segment_tempo_factor`，`no_safe_fit` 时再加 `truncate_reason` 和 `needed_tempo_factor`。调用方只在实际加速时覆盖 `fit_status`、`segment_tempo_factor`，并把 `effective_tempo` 乘上加速系数，算术顺序不变，结果逐位相同。
- `needs_resample` 放到拟合之后判断：`_adjust_tts_speed` 的输出读回来就是 44100 Hz、单声道、16 bit，得到相同结论。
- 删除重叠分支、三个计数器、别名和走不到的 `if s < 0: break`。“全部 N 段解说均被跳过”的报错文字不变，`跳过 N 段` 用段数。
- `tts_meta.json` 的段契约和 `skills/video-voiceover` 不变：voiceover 仍写占位字段和 `truncate_reason`，assemble 不再依赖这些占位字段。响度、峰值、帧栅和吸附逻辑都不动，`tts_settings_payload` 和缓存键也不动，已付费的音频不失效。

## Alternatives considered

- **保留重叠分支，防未经 lint 的负停顿。** 最强的理由是 assemble 可以单独运行，不一定经过 lint，负值确实能到这里。不采用：钳住负值后这个状态消失，块从上一块结尾开始、走正常拟合，同样不会重叠；专门为非法输入留 19 行分支不划算。
- **连 voiceover 的占位字段一起删。** 最强的理由是三份预算计算才真正收敛成一份，`tts_meta` 也更干净。这一轮不做：它改变跨技能的段契约，旧 assemble 读新 `tts_meta` 会 `KeyError`，两个 ClawHub 条目还要同步升版；所以只落地 assemble 内部这一半。

## Consequences

- **收益**：`narration_audio.py` 净少 70 行，放置状态只有一处来源；非负停顿下放置结果逐段不变（1500 例模糊对比，比较每段全部字段、`narration.wav` 哈希和报错文字，差异为 0；800 例负停顿无重叠）。
- **代价**：未经 lint 的负停顿下，完全被上一块覆盖的段不再得到 `no_room`，而是接在上一块之后放置。缺 WAV 的段现在记 assemble 当前配置算出的速度，而不是 voiceover 写入时的值；两边 `NARRATION_SPEED` 不一致时数值不同，但这种段本来就会以 `skipped_segments` 阻断。
- 占位字段的重复还留在 voiceover 一侧，没有读取方，等决定改 `tts_meta` 契约时再删。
