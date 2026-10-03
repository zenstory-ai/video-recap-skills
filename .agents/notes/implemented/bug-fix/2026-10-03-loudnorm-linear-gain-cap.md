# Agent Note: 两遍 loudnorm 压低目标以保持线性增益，并记录 ffmpeg 实际用的模式

Status: implemented

后续由 [[2026-10-03-loudnorm-true-peak-limiter]] 部分推翻：峰值放不下增益时先过真峰值限幅器，本篇的下调目标只用于限幅器削到上限（`LOUDNESS_LIMITER_MAX_DB`）之后的余量，或限幅后的测量不可用时。

## Problem

- assemble 的成片响度走两遍 loudnorm：首遍测量混音，第二遍 `linear=true` 带上测量值。ffmpeg（`af_loudnorm.c`）只在 `measured_TP + (I - measured_I) <= TP` 且 `measured_LRA <= LRA` 时用一个恒定增益；否则悄悄改用动态模式（逐 3 秒的自动增益），把混音的动态压扁。
- `assembly_qc.json` 的 `loudness_mode` 只看有没有首遍测量，有就记 `two_pass_linear`。本地 6 次真实运行的首遍测量（I -12.9 到 -17.9 LUFS、TP -2.2 到 0 dBTP、LRA 5 到 16.9）里 5 次不满足上面的条件，也就是成片实际是动态归一，记录却是线性。`loudnorm_measurement.normalization_type` 是首遍的值，首遍永远是 `dynamic`，看不出第二遍做了什么。
- source-mix 与首遍失败时是单遍 loudnorm，没有测量值，一定是动态模式，记为 `equivalent`。

## Decision

- `loudness._linear_loudnorm_targets(measured)`（原在 `audio_mix.py`，现在在 `skills/video-assemble/scripts/loudness.py`） 按首遍测量算出能让 ffmpeg 保持线性的第二遍目标：目标响度取 `min(TARGET_LUFS, TP - measured_TP + measured_I)`，再向下取到 0.01 并多留 0.01 LU（ffmpeg 用浮点比较）；LRA 目标取 `max(11, measured_LRA)`，它只在动态模式生效，放宽不影响线性增益。测量无效（`-inf`、ffmpeg 的 `99`/`-70`/`0` 哨兵）、目标低于 -70 LUFS 或测得 LRA 超过 20（旧 ffmpeg 的 LRA 上限）时返回 None，第二遍沿用配置目标。`final_loudnorm_filter(measured, limiter)` 用这组目标（有限幅器时按限幅后的测量算），结果里带 `gain_capped_db`（比 `TARGET_LUFS` 降了多少）。目标被压低时日志写明降到多少。
- 最终渲染的 loudnorm 一直是 `print_format=summary`。`loudness.loudnorm_final_pass(stderr, measured, limiter)` 从最终渲染的 stderr 读 `Normalization Type`、`Output Integrated`、`Output True Peak`，assemble 把它写进 `assembly_qc.json` 的新字段 `loudnorm_final_pass`（`normalization_type`、`target`、`output_integrated`、`output_true_peak`、`peak_limiter`；adopted-packet-copy、显式混音和 `FINAL_LOUDNORM=0` 时为 null）。
- `loudness_mode` 有首遍测量时按 ffmpeg 报告记 `two_pass_linear`、`two_pass_linear_peak_limited`（限幅后线性，见后续笔记）或新值 `two_pass_dynamic`；没读到报告时按目标是否可线性推断。无测量的单遍仍记 `equivalent`（`loudnorm_final_pass.normalization_type` 会是 `dynamic`）；`limiter_only`、`not_run`、`fixed_master_gain_no_loudnorm` 不变。`assembly_settings.audio_mix.loudness_mode` 仍是不带测量的配置描述，不变。

## Alternatives considered

- **只如实记录，不改目标。** 最强理由：响度不变，成片依旧到 -14 LUFS，改动最小。没采用：那样多数成片仍是动态归一；动态 loudnorm 的抽吸感在之前的成片质量审计里已列为问题，`linear=true` 本来就是要避免它。
- **先用 limiter 压掉峰值再线性增益到 `TARGET_LUFS`。** 最强理由：响度达标，只牺牲少数瞬态，是常见的母带做法。没采用：要压的峰值可达 4 dB 以上，硬限幅会改变枪声、音乐重拍等原声瞬态；而且要给 loudnorm 传一个与实际不符的 `measured_TP`。任务给出的方向是限制增益。
- **测量后把 `linear=false` 显式写成动态模式。** 最强理由：行为与记录一致，响度达标。没采用：动态模式正是要避免的。
- **按 ffmpeg 版本放宽 LRA 到 50。** 最强理由：ffmpeg 8.0 实测 LRA 上限是 50（旧版是 20），更宽 LRA 的混音也能线性。没采用：要多一次版本探测；本地运行测得的 LRA 最大 16.9，超过 20 的情况如实记为 `two_pass_dynamic`。

## Consequences

- **收益**：两遍路径真的是恒定增益，混音的相对动态保持原样；`assembly_qc.json` 记录的是 ffmpeg 实际用的模式，外加实际目标和输出响度，可以核对。
- **代价**：峰值余量不足的混音比 `TARGET_LUFS` 安静；按那 5 次的测量，目标落在 -14.2 到 -18.2 LUFS。需要更响时，在混音里给峰值留余量（降低原声或 BGM 音量）比调 `TARGET_LUFS` 有效。`two_pass_dynamic` 是 `loudness_mode` 的新取值。
- 测试：`tests/assemble/test_final_loudnorm.py`（目标计算、第二遍滤镜、summary 解析、assemble 按 ffmpeg 报告写 QC，以及真 ffmpeg 下原目标落到动态、压低后的目标保持线性且真峰值不超 -1 dBTP）。
