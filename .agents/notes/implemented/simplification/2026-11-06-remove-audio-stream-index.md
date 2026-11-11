# Agent Note: 移除 --audio-stream-index，固定用第一条音轨

Status: implemented

`timeline.json` 的 `selected_stream` 后来被 [去掉 timeline 和 visual_qc 里恒定或可推导的键](2026-11-06-assemble-trim-derived-output-keys.md) 删除；`media._require_audio_stream` 并入 `_has_audio_stream`，见 [合成阶段删掉门禁之后的兜底](2026-11-06-assemble-drop-unreachable-fallbacks.md)。

## Problem

定稿交付路线移除后（[[2026-11-06-remove-delivery-family]]），非零音频流只在 full + source-mix 且不导出剪映这一条路线上可达：解说、cut 和剪映导出都在 recap 的解析器里拒绝非零值，assemble 又重复检查两遍。于是一个参数要穿过 recap、assemble、contract、settings 和 timeline，并带三条只为围住它而存在的报错。README 和 recap SKILL 都没有提它。owner 决定移除。

## Decision

- 删除 recap 与 assemble 的 `--audio-stream-index` 及上述守卫和报错。source-mix 固定映射 `0:a:0`，`media._require_audio_stream(path)` 固定探测 `a:0`，ffmpeg 与 ffprobe 的参数不变（等价测试逐条比对）。
- 落盘字段保留为常量 0：`recap_run_manifest.json` 的 `audio.selected_stream_index`、`assembly_manifest.json` 的 `selected_audio_stream_index`、`assembly_settings` 的 `audio.selected_stream_index`、`timeline.json` 的 `selected_stream`。续跑比较 `audio_binding`，字段没了会让每个在途 `work_dir` 不匹配；样例 `examples/guohuo-60s/assembly_manifest.json` 也带着它。
- 用过非零流的旧 `work_dir`：manifest 记录的值与 0 不等，续跑按既有的 `audio: expected ...` 报错，不会悄悄换一条音轨。
- 旧命令包括显式 `--audio-stream-index 0` 和旧 `work_dir` 打印的续跑命令，都由 argparse 报未知参数。

## Alternatives considered

- **保留 flag，只删 assemble 的重复检查**：最强理由是双语 MKV 接 source-mix 是真实输入，改动也更小。没采用：只有这一条路线用得上，而先用 ffmpeg 重封装一次就能解决；参数穿过的五层和解析器守卫才是成本，只删重复检查省不掉它们。
- **接受 `--audio-stream-index 0` 为无操作、只拒绝非零值**：最强理由是旧脚本和续跑命令不用改。没采用：要把 flag 和一条报错永久留在解析器里，只为一个默认值；未知参数的报错已经足够清楚。
- **连字段一起删，旧 manifest 做兼容**：最强理由是不留恒定字段。没采用：要在 `_manifest_mismatches` 加归一化，并改样例和下游对 manifest 的读取，比留一个常量代价大。

## Consequences

- **收益**：少两个 flag、三条报错和一层穿透参数。
- **代价**：想要的音轨不是第一条的多音轨源要先用 ffmpeg 重封装（`ffmpeg -i in.mkv -map 0:v:0 -map 0:a:1 -c copy out.mp4` 这类命令）。显式传 `--audio-stream-index 0` 的脚本要去掉参数。manifest 里留一个恒为 0 的字段。
