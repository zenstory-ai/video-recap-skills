# Agent Note: cut 边界对齐帧网格，edited_source.mp4 恒定帧率

Status: implemented

## Problem

cut 把吸附后的原片秒（毫秒精度）直接写进 `trim` / `atrim`。边界落在两帧之间时，一段的视频帧数是 `[start, end)` 内的网格点数，音频却是 `end - start`；当网格点数取到下整（入点刚过一帧、出点刚过一帧之前）时音频比视频长不到一帧，concat 按较长的流推进，下一段就从网格外开始。mp4 按 VFR 写入，编码后每个这样的接点少一个帧位：25fps 下一帧停 80 ms。替身国货片的成片就是这样（`avg_frame_rate` 不再是 25/1），final_qc 只看容器与时长，看不出来。

本地复现（ffmpeg 8，25fps 源，片段 1.01–3.03 / 4.51–6.53 / 7.01–9.0）：151 帧应有，实得 150 帧，帧间隔出现 0.08 s，`avg_frame_rate=3750/151`。多源时 `fps` 滤镜把每段重采样到画布帧率，但段长不是整数个画布帧时同样会让下一段偏离网格。

## Decision

- **帧网格事实随画布一起探测**（`media_geometry.py`）：ffprobe 多取视频流 `start_time` 与容器 `start_time`，`facts` / `output_geometry.sources[]` 记录 `video_start_offset`（首帧相对 ffmpeg 输入零点的秒数）和精确的 `frame_rate`（`r_frame_rate` 原串；它恰好是 `avg_frame_rate` 两倍时是隔行素材报的场频，改用 `avg_frame_rate`，否则 50i 会被 `fps` 滤镜逐帧复制成 50fps；它比 `avg_frame_rate` 高出 1.5 倍以上时是可变帧率手机素材报的标称帧率（60/1 而实际约 29.6），改用离 `avg_frame_rate` 最近的常用帧率，否则单源会按 60fps CFR 重渲染、帧数与编码量翻倍；`r_frame_rate` 不可用（`0/0`）而 `avg_frame_rate` 可用时同样取离平均最近的常用帧率，不把源当成帧率未知）。画布帧率桶 `fps` 也取同一个帧网格，多源时隔行或手机素材不会把画布推到场频或标称帧率。`output_geometry.frame_rate` 是成片帧率：单源沿用源的精确帧率，多源用画布帧率桶，NTSC 桶映射为 `24000/1001` / `30000/1001` / `60000/1001`（以前多源写 `fps=29.97`，即 2997/100）。
- **画布在吸附前选定**（`cut_cli.py`）：帧对齐要知道输出帧率，所以 `_select_output_geometry` 改在 normalize 之后、吸附之前调用一次，同一份几何写入 `clip_plan_validated.json` 并用于渲染。多源的方向与帧率桶按吸附前的片段时长加权。
- **帧对齐是吸附的最后一步**（`frame_grid.snap_edges_to_frames`，由 `sentence_boundaries.snap_source_clips` 在句界吸附之后、门禁之前调用）：入点取源网格上相邻的两个帧边界，出点取 `入点 + n / 输出帧率` 的相邻两个 `n`。候选排序依次是：句界门禁本身的判定（`sentence_gate._edge_classifier` 闭包传入，`safe` → `unchecked` → `blocking`；门禁把讲话区间 ±50 ms 内都算讲话，自己另写一套"严格在区间外"的排序会把门禁放行的出点对齐到容差内，例如讲话 [0,2]、出点 2.055 在 25fps 下被移到 2.04 而阻断）→ 严格落在停顿窗内（不带门禁的 50 ms 容差）→ 不切掉必保证据节点的边缘（`required_evidence.nodes` 按源路径传入的 `keep_ranges`；证据覆盖是精确比较）→ 距离最近 → 更早。不允许重叠时，向外扩的候选不得进入其他片段的原片区间。与上一段同源无损连续的入点直接取上一段对齐后的出点，接缝仍然无损。源帧率未知（`0/0`、大于 120 的时基）时入点不动，时长仍对齐到整数输出帧。首帧晚于文件起点（`video_start_offset`）的源，入点 0 会对齐到首帧；首帧偏移不超过 `_VIDEO_START_WAIVER_SECONDS = 0.2` 秒（常见 mp4 只有几毫秒）时，门禁把不晚于首帧（加容差）的入点仍算 `source_start`，偏移更大（音频先开始的 TS / 广播录制）时只有 0（加容差）算源头，对齐到首帧的入点照常按讲话判定，落在句中就阻断，不再默默放过"声音优先"。门禁 `enforce_clip_sentence_boundaries` 判定的是对齐后的边界，每次对齐写进 `qc.boundary_status.frame_snaps`。
- **记录**：每个 clip 多 `frame_count`，渲染直接用它（缺失时才按时长重算），文件帧数与记录只有一个来源；`record_frame_grid` 按累计帧数重写 `output_start/end` 与 `total_duration`（= `qc.frame_grid.duration`），不再逐段毫秒取整累加，NTSC 下长计划的输出时间轴不会漂离实际渲染。`qc.frame_grid` 记录 `output_frame_rate`、总帧数、对应时长与各源帧率 / `start_snapped`。`frame_count` 在 clip 里，渲染缓存 sidecar 比较的 `plan` 随之变化，旧的 VFR 成片会自动重渲染一次。
- **渲染精确出帧**（`cut_render.py`）：单源与多源走同一个循环。每段视频是 `trim`（起止各提前半个源帧，毫秒取整和容器时间戳抖动都选不错帧）→ `setpts` →（多源：缩放 / 补边 / setsar / format）→ `fps=输出帧率` → `tpad=stop_mode=clone:stop=-1`（无限克隆末帧）→ `trim=end_frame=frame_count`；音频裁 `frame_count / 输出帧率` 秒。末帧克隆不限帧数：音轨比视频流长时（容器时长取较长的流，吸附和帧对齐都允许出点到容器尾），越过视频流末尾的片段会短好几帧，只克隆 1 帧时在 9.8 s 视频 / 10 s 音频的源上 100 帧只出 96 帧、中间留 0.2 s 空洞。每段都按整帧推进 concat，成片 CFR，帧数等于 `qc.frame_grid.frame_count`。单源也经过 `fps` 滤镜，所以可变帧率的手机素材也会输出 CFR。同源无损连续的下一段从上一段实际渲染的终点（`start + frame_count / 输出帧率`，不取整）开始裁剪，而不是计划里取整到毫秒的 `source_start`：24fps 下上一段 atrim 止于 2.541667、下一段若从 2.542 开始会跳过 16 个采样（48 kHz），接点又没有淡入淡出，会有咔声。渲染后用 `ffprobe -count_packets` 数一次视频帧，与 `qc.frame_grid.frame_count` 不符时记警告：这套滤镜行为只在 ffmpeg 8 与 9.0.1 上验证过，CI 没有 ffmpeg，老版本 ffmpeg 行为不同时不至于悄悄产出短片或 VFR；只警告不阻断，final_qc 只承载阻断项，也不写进 `clip_plan_validated.json`（复用缓存的运行没有这个数，写进去会让文件内容来回变化，打断下游按 `{size, mtime_ns}` 绑定的证据）。
- 旁白不受影响：cut 在第二遍写稿之前完成，输出时间轴（`output_start/end`）由对齐后的区间重算，写稿 Agent 读的就是它，输出时间按累计帧数计算，与渲染一致（只差毫秒取整）。

## Alternatives considered

- **只在输出加 `-fps_mode cfr` / `-r`**：一行改动就能得到 CFR 文件。没采用：它靠复制或丢帧把时间戳拉回网格，正是要消除的那一帧停顿，只是换成编码器替我们丢；源区间与 `clip_plan_validated.json` 的时间也对不上。
- **渲染时用 `start_frame` / `end_frame` 按帧号裁剪**：不需要改计划时间。没采用：帧号从流开头数起，可变帧率源、首帧不在 0 的源都会选错帧；计划里的原片秒和实际画面也不再一一对应。
- **把帧对齐放在门禁之后**：门禁逻辑完全不动。没采用：门禁就会检查一个不会被渲染的边界；半帧的移动可能把停在停顿起点的出点拉回到最后一个字上，而门禁的 50 ms 容差察觉不到。
- **多源时入点也对齐到画布网格而不是源网格**：所有数字都在一个网格上。没采用：源帧率与画布不同时，入点会落在两源帧之间，`fps` 滤镜的取帧位置就取决于舍入；入点放在源网格、长度用输出网格，配合末尾 `tpad` + `trim=end_frame`，每段帧数都精确。
- **帧对齐保留自己的"严格在讲话区间外"排序，只把门禁容差抄过来**：frame_grid 不依赖门禁的闭包。没采用：两套规则迟早会再分叉（未验证锚点、源头源尾、首帧偏移都是门禁里的规则）；把门禁的 `classify` 直接传进来，对齐永远不会把门禁放行的边界变成阻断。
- **出点夹到视频流末尾，而不是无限克隆末帧**：每一帧都是真实画面。没采用：要再探测视频流时长，并且会裁掉视频流之后仍有的原声；越过视频流的部分本来就没有画面，克隆末帧与 ffmpeg 播放这类源时的表现一致，`trim=end_frame` 仍把帧数封顶。
- **吸附后重新选一次画布**：画布按最终时长加权，与以前一致。没采用：帧率可能因此翻转，帧对齐就得重跑；加权只在接近持平时受不到一秒的时长变化影响，不值得引入循环。

## Consequences

- **收益**：每个接点都整帧推进，成片 CFR 且帧数可预知；真实 ffmpeg 测试覆盖单源 25fps 与多源 30+24fps（一个无音轨），断言帧数等于 `round(duration × fps)` 且帧间隔恒定。多源 29.97 不再被近似成 2997/100。
- **代价**：首帧偏移超过 0.2 秒的源，片头入点不再自动算源头，开头有讲话时要 Agent 自己挪；边界最多移动一帧（25fps 下 40 ms），输出时间轴与以前相差不到一帧；`clip_plan_validated.json` 多 `frame_count`、`qc.frame_grid`、`frame_snaps`；旧缓存重渲染一次；每段多 `fps` / `tpad` / `trim` 三个廉价滤镜。片段越过视频流末尾（音轨比视频长）时，越过的部分是末帧的静止克隆画面，可能有数帧；帧数仍与计划一致。
- `skills/video-recap/scripts/final_qc.py` 的 `_probe_fps` 读了 `avg_frame_rate` / `r_frame_rate`，但只检查是否为正数，不判断成片是否恒定帧率。cut 产出的 edited_source 现在按构造是 CFR，但 assemble 拼接后的成片仍可能变成 VFR。没有在本次加检查：final_qc 按设计只承载阻断项（`data-schema.md`：只提示的项不进 final_qc），而 VFR 阻断在没有真实端到端验证的情况下可能误拦非 cut 模式下直接沿用 VFR 原片的成片。0.6.1 之后的后续：先在真实成片上量出 `avg_frame_rate` 与 `r_frame_rate`、`nb_frames` 与 `duration × fps` 的实际偏差，再决定是阻断还是另设提示通道。
- 相关：[[2026-06-16-cut-first-narrate-second]]（cut 先于写稿，旁白读 validated 计划的输出时间）。
