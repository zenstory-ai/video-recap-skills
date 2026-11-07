# Agent Note: storyboard 只保留 wrapper 一层门禁

Status: implemented

相关事实更新：[[2026-10-02-storyboard-relative-paths]]。

## Problem

`understanding_storyboard` 的 wrapper 和 `storyboard` 的 builder 对同一份数据做同样的检查：有没有帧、哪些来源被剪辑用到、`clips` 是否为空。builder 里还有 `_ffmpeg_available`，它只被测试设为 True；缺 ffmpeg 时后面的调用本来就会报错，落进 builder 的宽 `except` 返回 None。

`work_dir_relative_pages` 和它的分隔符正则是给读旧 sidecar 路径用的，读旧路径的 `_relocated` 已经删了，现在两个调用方传的都是 `_tile_pages` 刚生成的 `Path`。`_nearest_existing_frame` 手写了钳位和带提前退出的线性扫描。

## Decision

- 保留 wrapper 的门禁：`frames/` 检查、JSON 解析保护、多来源 `clips` 校验都不变。缓存键不变。
- 删除 builder 里的重复检查：两个 `if not paths`、`_ffmpeg_available`、`used` 过滤、`if not clips`、逐 clip 的 `try/continue` 和 `source_end <= source_start`。直接调用 builder 且条件不满足时，由 builder 的宽 `except` 或 `tiles` 为空的分支返回 None，结果不变，日志文字可能不同。
- `_multi_source_frame_sets` 的 `clips` 变成必传；"没用到的来源不进 sheet、不进缓存键"的约束都在这里。`source_id` 没有帧的 clip 仍在 builder 里跳过（素材库恢复时是真实情况）。
- `page_images` 直接写 `f"storyboard/{Path(page).name}"`。`_nearest_existing_frame` 改成 `min(..., key=距离)`：数字有序且唯一时，`min` 在距离相等时取较早的帧，与原逻辑一致。`fps<=0` 由 `frame_number_for_time` 抛错，落进 builder 的 `except`，结果同样是 None。
- `build_source_storyboard` 里 `if not tiles` 随之删除：`anchors` 非空时 `tiles` 不会为空。

## Alternatives considered

- **反过来保留 builder 的检查、去掉 wrapper 的。** 最强理由：builder 是更底层的一层，直接调用也安全。没采用：会改变行为。`frames/` 被删而 manifest 还在时会复用缓存的 sheet；没有 `clips` 的计划会在 `try` 之外报错，而这一步承诺过只是建议性的。
- **`_nearest_existing_frame` 保留提前退出。** 最强理由：不用扫完整个列表。没采用：极端 200 clip、7200 帧的 sheet 也不到一秒，而且只在建议性步骤里跑。

## Consequences

- 收益：门禁只在一个地方，builder 专心拼图。
- 代价：单来源计划 `clips` 为空时，日志从"clip_plan_validated 无 clips"变成"未解析到任何帧"，结果都是跳过。
- 代价：手工改坏的 `clip_plan_validated.json`（比如某个 clip 缺 `source_start`）现在让整张 edited storyboard 跳过，以前只跳过那个 clip。`cut_cli` 写出的计划不会这样，第二轮 brief 对这种计划本来就会报错。
- 代价：完整扫描比提前退出慢，只影响建议性的步骤。
