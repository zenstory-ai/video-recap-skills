# Agent Note: 多视频 cut 第二阶段由 understanding 生成剪后故事板

Status: implemented

## Problem

video-script SKILL.md 要求 cut 第二阶段“先查看 `edited_source.mp4` 与剪后故事板”再写 `narration.json`。单视频第二阶段，recap 调 `understand.py --brief-only` 重建 brief，video-understanding 看到 `clip_plan_validated.json` 就顺手写 `storyboard/edited_storyboard.*` 并在 brief 顶部加指引。多视频第二阶段由 recap 自己写 `agent_narration_brief.md`（`_write_multi_source_output_brief`），不调 understanding，所以从来没有剪后故事板，Agent 只能逐个打开帧。

不能直接对项目 work_dir 跑 `--brief-only`：它会用项目目录里并不存在的 VLM/ASR 产物重建 brief，覆盖 recap 写的多源 brief。现有 `build_edited_storyboard` 也只读 `work_dir/frames`，而多视频的帧在各来源的 `sources/<source_id>/frames/` 里，抽帧率随来源时长不同（≤60s 2fps、≤5min 1.5fps、更长 1fps）。

实测还发现：把不同来源的帧排进同一张 sheet 时，只要中途帧尺寸或像素格式变了（例如 4:4:4 与 4:2:0 的来源相邻），ffmpeg 会重建滤镜图，`tile` 里已排好的格子丢失，前几格变黑；只变尺寸时则按首帧尺寸硬拉伸。

## Decision

- video-understanding 新增 `understand.py --edited-storyboard-only`（与 `--brief-only` 互斥）：只按 `clip_plan_validated.json` 生成剪后故事板，已有 `agent_narration_brief.md` 时在顶部加与 `--brief-only` 相同的 storyboard 指引；不抽帧、不调 API，故事板失败只记日志。
- `_generate_edited_storyboard` 识别带 `sources` 的多源计划：按 `sources.<id>.source_work_dir`（相对项目 work_dir）找各来源的 `frames/`，fps 取该来源 `frames/frames_manifest.json` 的 `fps`；缺帧或缺清单的来源跳过并记日志。缓存键包含计划身份、每个来源的帧清单身份与 fps。单源计划的行为与缓存键不变。
- `build_edited_storyboard(..., source_frames=None)`：多源时每个 tile 带 `source_id`，标签为 `out mm:ss / S<n> mm:ss`，sidecar 增加 `sources: [{label, source_id, source_path}]`，`frame_file` 保留完整路径（帧文件名在各来源间重复），`source_video_path` 为 null。所有多源 tile 统一按第一个可探测来源的帧尺寸缩放补边并转 `yuvj420p`。
- brief 顶部的 storyboard 指引在多源时多一行 `来源标签: S1=…，S2=…`；重复执行时替换旧指引，不叠加。
- recap 多视频第二阶段在写完多源 brief、写账本前调用 `understand.py <首个视频> --work-dir <项目> --edited-storyboard-only`；子进程失败只打印警告，照常暂停。
- 测试：understanding 组覆盖按来源 fps 取帧、缓存与跳过缺帧来源、brief 指引幂等，以及一条真 ffmpeg 渲染（4:4:4 横屏与 4:2:0 竖屏混排时每一格都在）；orchestrator 组覆盖第二阶段的调用顺序、参数与失败时仍暂停。

## Alternatives considered

- **recap 自己读 `storyboard/edited_storyboard.json` 写指引。** 最强理由：brief 完全由 recap 写，understanding 不碰别人写的文件。没采用：recap 无法判断这份 JSON 是不是对当前计划生成的（缓存元数据格式属于 understanding），故事板被关掉或失败时会把旧计划的故事板当成当前的；而由 understanding 只对本次生成或缓存校验通过的结果加指引，与单视频完全同一条路径。
- **在 recap 里复制一份故事板生成代码。** 最强理由：不用新增 CLI 选项。没采用：帧编号↔时间、帧清单、缓存键都由 understanding 独家定义，复制后需要再加一组跨技能一致性测试，且技能之间不共享代码。
- **只对尺寸不同的来源做缩放补边。** 最强理由：同尺寸来源少一次 ffmpeg 调用。没采用：尺寸相同但色度采样不同的来源同样触发滤镜重建、丢格子；每格本来就要过一次 ffmpeg 烧时间戳，统一处理几乎没有额外成本。

## Consequences

- 收益：多视频第二阶段与单视频一样有剪后故事板，Agent 能按 `S<n>` 看出每段来自哪一集；混排不同画幅的来源时格子不丢、不变形。
- 代价：多源故事板的每一格都要重新编码一次（单源只在烧时间戳时编码）；素材库恢复的来源没有帧，它们的片段不出现在故事板里，仍需 `inspect clip-map` 核对；故事板按第一个来源的帧尺寸排版，其他画幅的来源会有黑边。
