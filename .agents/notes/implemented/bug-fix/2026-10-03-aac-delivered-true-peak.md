# Agent Note: 真峰值目标针对交付的 AAC 文件：首次渲染留编码余量，成片实测超出就只重编码音频

Status: implemented

接续 [[2026-10-03-loudnorm-true-peak-limiter]]：限幅与线性 loudnorm 的算法不变，只是它们瞄准的真峰值从 TP 改为"编码前目标"，并在渲染后按交付文件核对。

## Problem

- 限幅器上限 -2 dBTP、线性 loudnorm 再补约 1 dB，PCM 阶段的峰值落在 -1.1 到 -1.4 dBTP；AAC 192k 编码再把真峰值抬高 0.4–1.4 dB。release 0.6.1 的真实 full 模式运行（`recap/release061/last-full-capcase`）交付文件 loudnorm 实测 +0.31 dBTP，48.98 s 处有 +0.23 dBFS 的采样，16 bit 回放会削波；`single-full` 副本交付 -0.96 dBTP。
- `assembly_qc.json` 只记了 loudnorm 的 `Output True Peak`（编码前，-1.1），`final_qc` 不看响度，于是两者都 PASS。只有采纳路径（`adoption/audio_mix_binding.py`）解码过最终 AAC。
- 同一混音的 AAC 超出量并不稳定：上面那次是 1.41 dB，按本篇编码前目标重渲染后是 0.33–0.43 dB；方波一类的合成信号可达 4 dB。不实测就只能猜一个固定余量。

## Decision

- `loudness.py` 的 `_linear_loudnorm_targets`、`_peak_limiter_plan`、`final_loudnorm_filter`、`loudnorm_final_pass`、`plan_final_loudness` 都接受 `peak_target`（编码前真峰值目标，默认 TP）。限幅器上限是 `peak_target - LIMITER_HEADROOM_DB`。`plan_final_loudness(..., measured=...)` 复用已有的首遍测量，只测限幅后的信号。
- 首次渲染瞄准 `loudness.first_render_peak_target()` = TP - `CODEC_PEAK_HEADROOM_DB`（0.5 dB，即 -1.5 dBTP），narration 与 source-mix 两条路径都是；显式混音（固定 master gain）与 adopted-packet-copy 不动。
- 新模块 `skills/video-assemble/scripts/codec_peak.py`：
  - `delivered_loudness(path)` 解码成片第一条音轨，`ebur128=peak=true` 与 loudnorm 首遍各测一次真峰值取较高值（两个检测器在密集瞬态上相差可达约 1 dB），返回 `{integrated, true_peak}`，测不到为 None。
  - `deliver_under_true_peak(output, peak_target, reencode_audio)`：真峰值仍高于 TP 时把编码前目标降 `超出量 + CODEC_PEAK_MARGIN_DB`（0.1 dB），调用 `reencode_audio` 再测，最多 `CODEC_PEAK_MAX_CORRECTIONS`（2）次。
  - `reencode_audio_track(...)` 用渲染时的同一组输入和混音图重渲音频，把渲染好的成片作为最后一个输入，`-c:v copy` 复制画面，写到 `.<stem>.audio-reencode.mp4` 再原子替换；画面不重编码。
- `assemble.py` 在渲染后、成片校验前跑这一步，纠正后的 loudnorm 报告、测量与限幅计划替换原来的，`loudnorm_final_pass` 多一个 `delivered`：`integrated`、`true_peak`、`peak_target_dbtp`、`corrections`（`FINAL_LOUDNORM=0` 时不测）。
- `assembly_contract._build_assembly_qc`：`delivered.true_peak` 高于 TP 时加阻断码 `delivered_true_peak_over_target`，assemble 非零退出、不交付；测不到不阻断。
- 实测（ffmpeg 8，缓存的 TTS，不调 API）：`last-full-capcase` 副本重新组装，首次渲染限幅后目标降到 -14.45 LUFS，交付 -14.48 LUFS、-1.17 dBTP（ebur128 -1.2），没触发纠正；把 `CODEC_PEAK_HEADROOM_DB` 临时设为 0 复现了 +0.31 dBTP，纠正一次后交付 -15.4 LUFS、-1.98 dBTP，画面流的 MD5、帧数、色彩标记与时长和不纠正的渲染完全相同。

## Alternatives considered

- **只加一个固定的编码余量。** 最强理由：没有额外的解码和编码，行为可预测。没采用：超出量在同一素材上就从 0.3 变到 1.4 dB，余量给到 1.5 dB 才稳，所有需要限幅的成片都要白白安静 1 LU 以上，而且仍然没有交付文件的证据。
- **只实测、不留初始余量。** 最强理由：限幅器没削满的成片响度一点不损失。没采用：真实样片 3 次里 2 次会超，几乎每次都多一遍音频编码；0.5 dB 覆盖了常见的 0.3–0.45 dB 超出量。
- **超出时重新跑整次渲染。** 最强理由：一条命令出片，没有重封装。没采用：会把视频再编码一遍（5 分钟片几分钟），而画面根本没变；流复制实测画面包逐字节相同。
- **对已编码的 AAC 解码后降增益再编码。** 最强理由：不用重建混音图。没采用：二次有损编码，音质下降，且第二次编码同样会有超出量。
- **超出量只记 warning、不阻断。** 最强理由：极端素材不会在最后一步失败。没采用：-1 dBTP 是交付标准，两次纠正后仍超出说明测量或混音有问题，静默交付正是这次要修的缺陷。

## Consequences

- **收益**：交付文件本身满足 -1 dBTP，`assembly_qc.json` 记的是成片实测值，超出就阻断而不是 PASS。
- **代价**：每次组装多一遍成片音频解码；需要纠正时再加一遍限幅后测量和一遍音频编码。限幅器削满 6 dB 的混音比 `TARGET_LUFS` 低约 0.5 LU（编码余量），纠正时按测得的超出量再降，偏保守（上例降了 1.4 LU，结果离 TP 还有约 1 dB）。`delivered_true_peak_over_target` 是新的阻断码。
- 测试：`tests/assemble/test_codec_peak.py`（纠正量、上限两次、不超/测不到不纠正、QC 阻断；真 ffmpeg 下方波瞬态的 AAC 超出被纠正到 -1 dBTP 以下，画面包 MD5 不变）；`tests/assemble/test_final_loudnorm.py`（编码前目标下移各级目标、复用首遍测量、assemble 只重编码音频并记录 `delivered`）。
