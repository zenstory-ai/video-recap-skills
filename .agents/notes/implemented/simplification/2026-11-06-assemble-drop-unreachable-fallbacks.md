# Agent Note: 合成阶段删掉门禁之后的兜底，合并音轨探测

Status: implemented

取代 [删除走不到的 ducking 模式](2026-10-02-drop-unreachable-ducking-modes-and-tts-dynamic-switch.md) 里“没有放置信息时恒定 `ducking_orig_volume`”这一分支。

## Problem

三处代码在防程序已经排除的状态：

- 解说模式里，`_block_before_render` 在时间线和混音图之前运行，任何零宽段都是 `skipped` 或 `no_safe_fit`，已被阻断。`audio_mix`、`audio_automation`、`timeline_emit` 里的“无放置信息”兜底和只为它存在的常量 `ducking_orig_volume` 走不到；source-mix 没有段，也不走这张图。
- `_placed_audio_matches_timeline` 的 ffprobe 回退是给已采用音频路线留的。现在 `placed_audio_path` 只有一个赋值点，同一次运行里用标准库 `wave` 写出单声道 16 bit，`wave.open` 不会因格式报错。
- `_require_audio_stream` 和 `_has_audio_stream` 是同一个 ffprobe 查询，在 source-mix 里对同一个输入跑两遍，前者只有一个调用方。

## Decision

- 删除 `_duck_envelope` 和 `_emit_timeline` 的零宽跳过、`ducking_expression` 和 `release_ducking_expression` 的空窗口返回 `None`、`_build_audio_filter_complex` 的两个恒定音量分支，以及 `lib.CONFIG["ducking_orig_volume"]` 与 `assembly_settings.audio_mix.ducking_orig_volume`。样例 `examples/guohuo-60s/assembly_manifest.json` 手工删掉这个键，`test_audio_policy_parity.py` 的 `MIX_KEYS` 同步去掉。
- 保留 `_apply_source_sentence_handoffs` 的过滤（它在门禁之前运行）、`release_ducking_keyframes` 的空返回（片段可能落在所有窗口之外）和 `build_timeline` 自己的过滤（有直接测试）。
- `_placed_audio_matches_timeline` 遇到 `wave.Error` 直接返回 `False`，仍是 `timeline_audio_mismatch` 阻断。
- `assemble_video` 在原 source-mix 探测的位置调用一次 `media._has_audio_stream`，source-mix 缺音轨时报 `找不到音频流 a:0: <path>`，文字不变；两种模式共用这个结果，后面不再探测。`_require_audio_stream` 删除。

## Alternatives considered

- **保留无放置兜底。** 最强的理由是它把故障变成平滑退化：日后有人挪动 `_block_before_render`，成片仍能出，只是原声恒定。不采用：那样会悄悄交付错误的混音；现在这种改动要么生成平坦包络，要么在 ffmpeg 里明确失败，更容易被发现。
- **保留 ffprobe 回退。** 最强的理由是 `wave` 读不了的文件还能得到准确时长。不采用：这条路径已经没有写入方，它只是把一个损坏的文件晚一点判成不匹配。
- **保留两个探测，只是抽公共函数。** 最强的理由是 ffprobe 自身出错时，`无法探测媒体` 的 stderr 更有诊断价值。不采用：`_probe_canvas` 已在它之前对同一文件报错，真正读不了的文件走不到这里。

## Consequences

- **收益**：少一个配置键、约五十行分支和一次 ffprobe 调用。解说模式下探测提前到放置之前，结果不变，渲染等价对比确认 ffmpeg 命令和成片帧哈希相同。
- **代价**：`assembly_manifest.json` 的 `assembly_settings.audio_mix` 少 `ducking_orig_volume`；这份设置只作审计，没有地方比较它，不影响缓存和续跑。source-mix 下 ffprobe 自身失败时，只会看到缺音轨的报错。
