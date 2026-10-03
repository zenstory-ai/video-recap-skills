# Agent Note: 峰值放不下线性增益时先过真峰值限幅器，成片达到 TARGET_LUFS

Status: implemented

接续并部分推翻 [[2026-10-03-loudnorm-linear-gain-cap]] 里"峰值余量不足就下调目标响度、不用 limiter"的决定。那篇的线性条件、`loudnorm_final_pass` 记录和下调目标的算法仍在，只在限幅器削到上限后才用。

## Problem

- 只下调目标时，峰值余量不足的混音交付得远比 `TARGET_LUFS` 安静。release 0.6.1 的真实 full 模式运行（`recap/release061/final-full`）首遍测得 -18.09 LUFS、+0.66 dBTP，按上篇算法目标降到 -19.76 LUFS（`gain_capped_db` 5.76），成片 -19.7 LUFS；拉高这类成片的音量就是这一步存在的原因。
- 压低目标的原因是少数瞬态（枪声、音乐重拍）顶到 0 dBTP。为了几十毫秒的峰值让整条片子安静 5 dB，代价不对等。

## Decision

- 响度代码从 `audio_mix.py` 移到新模块 `skills/video-assemble/scripts/loudness.py`（`audio_mix.py` 只剩 handoff、ducking 和混音图）。
- `loudness._peak_limiter_plan(measured)`：首遍测量下，若 `measured_TP + (TARGET_LUFS - measured_I) > TP`，规划一个限幅器：上限 `ceiling = TP - LIMITER_HEADROOM_DB`（-2 dBTP），先增益 `pre_gain = min(TARGET_LUFS - measured_I, ceiling + 最大削减 - measured_TP)`。最大削减是 `CONFIG["loudness_limiter_max_db"]`（环境变量 `LOUDNESS_LIMITER_MAX_DB`，默认 6 dB，`0` 关闭限幅）。线性增益已能达标、测量无效、测得 LRA 超出 loudnorm 线性上限（20）时不规划。
- `_peak_limiter_chain(plan)` 是 `volume=<pre_gain>dB,aresample=192000,alimiter=limit=<ceiling 线性值>:attack=5:release=100:level=false[:latency=true],aresample=48000`：4 倍过采样让样本峰值逼近真峰值（真实样片限幅后测得 -2.00 dBTP；同类混音不过采样时，样本峰值限到 -2 dBFS 而真峰值为 -1.0 dBTP）。ffmpeg 的 alimiter 有 `latency` 选项（较老的版本没有）时补偿 5 ms 的 lookahead 延迟，`_alimiter_compensates_latency()` 每个进程问一次 `ffmpeg -h filter=alimiter`。
- `loudness.plan_final_loudness(...)` 返回 `(measured, limiter)`：首遍测量混音；需要限幅时把限幅链接在混音后再测一遍（`_measure_loudness(..., pre_chain=...)`），`limiter` 带上这次测量 `measurement` 和实测削减 `reduction_db = measured_TP + pre_gain - 限幅后 TP`。限幅后测不到或仍无法线性时 `limiter` 为 None，回到上篇的下调目标。
- `final_loudnorm_filter(measured, limiter)` 在限幅链后接第二遍 loudnorm，`measured_*` 取限幅后的测量，目标仍由 `_linear_loudnorm_targets` 算：-2 dBTP 的上限给线性增益留 1 dB，补回限幅损失的响度（真实样片 0.6–0.9 LU）。损失超过 1 LU 或削减到上限时，余下部分照旧下调目标，记在 `target.gain_capped_db`。
- `assembly_qc.json` 的 `loudnorm_final_pass` 新增 `peak_limiter`（`pre_gain_db`、`ceiling_dbtp`、`required_reduction_db`、`max_reduction_db`、`reduction_db`、`measurement`；没限幅时为 null）。`loudness_mode` 新增 `two_pass_linear_peak_limited`（限幅后线性）；ffmpeg 报告动态时仍是 `two_pass_dynamic`。`assembly_settings.audio_mix` 记录 `loudness_limiter_max_db`。
- 在 `final-full` 工作目录的副本上重跑 assemble：先增益 3.34 dB、削去 6.0 dB（需要 6.75，上限截断），限幅后 -14.94 LUFS，第二遍线性补到 -14.0 LUFS，loudnorm 报告 linear、输出真峰值 -1.1 dBTP；AAC 成片 ebur128 测得 -14.0 LUFS、LRA 7.5、真峰值 -0.8 dBTP（AAC 编码比 PCM 阶段高约 0.3 dB，改动前动态归一的成片同样是 -0.7）。`single-full` 的同一素材（首遍 -18.13 LUFS、+0.29 dBTP）同样交付 -14.0 LUFS。`loudness_mode` 均为 `two_pass_linear_peak_limited`。

## Alternatives considered

- **维持只下调目标（上篇）。** 最强理由：完全不动原声瞬态，混音的峰值和动态原样保留。没采用：真实成片低 5 dB 以上，手机外放几乎听不清，等于让响度归一失去作用；被削的只是少数几十毫秒的瞬态。
- **动态 loudnorm（`linear=false` 或让它自己掉进动态模式）。** 最强理由：一遍就达标，ffmpeg 自带真峰值限制。没采用：逐 3 秒的自动增益会让对白和原声的相对音量随时间起伏（抽吸感），这是之前审计里列出的问题。
- **限幅器不设上限。** 最强理由：任何混音都能到 `TARGET_LUFS`。没采用：削 10 dB 以上时 alimiter 会明显改变枪声、鼓点等瞬态的质感，也说明混音本身的平衡有问题（BGM 或原声过响），应当在混音里解决；6 dB 在真实样片上响度损失约 0.6–0.9 LU，仍在 1 dB 补偿余量内，超出时记录下调量，让人看到。
- **只测一遍，按经验值补偿限幅损失。** 最强理由：省掉一遍音频解码（5 分钟片约 20–40 秒）。没采用：损失取决于素材，突发能量占比高的信号在 6 dB 削减下损失可达 1.9 LU；不测就只能猜，而第二遍 loudnorm 要求的 `measured_*` 也必须是限幅后的真实值，否则 ffmpeg 会按错误的测量判断线性条件。
- **不过采样的 alimiter。** 最强理由：速度快约 3 倍。没采用：样本峰值限到 -2 dBFS 时真峰值仍有 -1.0 dBTP，留给线性补偿的余量就没了。

## Consequences

- **收益**：峰值不再拖累整片响度，真实样片交付 -14.0 LUFS（之前 -19.7），全程恒定增益，没有动态归一的抽吸；`assembly_qc.json` 写明限幅削了多少、是否还下调了目标。
- **代价**：需要限幅的运行多一遍混音解码加 192 kHz 限幅（5 分钟片约 20–40 秒），最终渲染也多一级限幅；被限幅的瞬态最多削 6 dB。alimiter 没有 `latency` 选项的老 ffmpeg 上限幅链没有延迟补偿，音频比画面晚 5 ms。`two_pass_linear_peak_limited` 是 `loudness_mode` 的新取值。
- 测试：`tests/assemble/test_final_loudnorm.py`（限幅规划与上限、命令构造含 latency 两种、限幅后第二遍滤镜、`plan_final_loudness` 的测量顺序与回退、assemble 写入 `peak_limiter`；真 ffmpeg 下峰值合成信号到 -14 ±1 LU 且真峰值不超 -1 dBTP，超出上限时只下调余量）。
