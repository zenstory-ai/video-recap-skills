# Agent Note: 再硬编码五个未公开的环境变量

Status: implemented

## Problem

沿用 [[2026-10-02-hardcode-tuning-knobs-drop-narration-delay]] 的标准复查：`CLIP_JOIN_AUDIO_FADE_MS`、`MIMO_ASR_BASE64_MAX_MB`、`MIMO_MEDIA_RESOLUTION`、`SPEECH_SAFETY_MARGIN`、`MIMO_DISABLE_THINKING` 在任何文档、CHANGELOG 和测试里都没有以环境变量形式出现。测试改这些值用的是 `monkeypatch.setitem(CONFIG, ...)`。

`MIMO_MEDIA_RESOLUTION` 更特殊：视频分片概览移除后（[[2026-11-06-remove-mimo-video-overview]]）没有请求再用它，只剩下 VLM 缓存键里的一个值。

## Decision

- 五个名字不再读取，CONFIG 键保留，值写死为原默认值：30.0、10.0、`"default"`、0.85、`True`。`MIMO_DISABLE_THINKING` 与 `SPEECH_SAFETY_MARGIN` 的多份副本同时改，副本间不产生差异。
- `mimo_media_resolution` 冻结在 VLM 的两处缓存键里，不从键中删除。删掉这个键会让所有已付费的 VLM 缓存未命中，只为少一行不值得。`tests/understanding/test_io_fixes.py` 钉住这个值。
- 保留 `ASR_THROTTLE_SECONDS`（真实运行用它避开 Token Plan 集群限流，CHANGELOG 公布过）、`SUBTITLE_ORIGINAL_IN_GAPS`（行为开关，没有 CLI 可替代）和 `VLM_MAX_FRAMES` / `VLM_SECONDS_PER_FRAME`（CHANGELOG 公布过）。

## Alternatives considered

- **把 `mimo_media_resolution` 从缓存键里一起删掉。** 最强理由：键里留一个恒定值看起来是死代码。没采用：用户要为此重付全部 VLM 费用，换来的只是少一行。
- **连 `ASR_THROTTLE_SECONDS` 一起硬编码。** 最强理由：文档没有写，节流也可以按默认值固定。没采用：真实长片运行靠它躲限流，没有替代手段。

## Consequences

- 收益：少 5 个设置和 8 处环境读取，缓存键的取值范围变窄。
- 代价：私下设过这些名字的运行，变量会静默失效，且相应阶段重算一次；因为没有公开过，不加报错。
- 是否在默认路径：默认值不变，已有缓存继续命中。
