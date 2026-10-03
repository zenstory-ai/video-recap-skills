# Agent Note: 成片每条路径都是 H.264 yuv420p，并写 BT.709 色彩标记

Status: implemented

## Problem

- assemble 的最终编码只在有滤镜（遮罩、叠加、烧录字幕、包装图层、缩放）或 `FORCE_VIDEO_REENCODE` 时重编码并加 `-pix_fmt yuv420p`；否则 `-c:v copy` 原样复制源画面。默认烧录开着时几乎总有滤镜，所以问题不显眼；不烧录（`--no-burn-subtitles`，以及缺 libass 降级后的默认运行）时，10-bit、4:2:2、HEVC 或奇数宽高的源会原样交付，微信、Safari 和很多手机放不了。`+faststart` 在四条输出路径（默认 narration、严格身份约束渲染、显式 adopted 混音、source-mix / adopted-packet-copy）上本来都有。
- 最终编码和 cut 的 `edited_source.mp4` 编码都不写色彩标记。未标记的源出来仍是未标记，播放器各自猜（多数按 BT.601 或 BT.709），同一条片子在不同播放器上颜色不同。
- 实测 ffmpeg 8.0 与 9.0.1：重编码时只给 `-colorspace/-color_primaries/-color_trc` 输出参数，ffprobe 读到的成片只有 `color_space` 生效，primaries 与 transfer 仍是 unknown，因为帧自带的（未标记）属性覆盖了编码器参数；在滤镜末尾加 `setparams` 才能写进码流。流复制时输出参数写进容器（`colr`），ffprobe 能读到。全范围（yuvj420p / `pc`）源经 `-pix_fmt yuv420p` 重编码后，ffmpeg 8/9 保留 `pc` 范围，像素也未被压到 limited。

## Decision

- video-assemble `media.py` 新增 `_probe_video_format`（ffprobe 读 codec、pix_fmt、宽高和四个色彩字段，读不到返回 `{}`）、`_output_color_tags`、`_color_tag_filter`、`_color_tag_args`、`_video_copy_safe`。video-cut `media_geometry.py` 有前四个函数和三张常量表的同名副本，`tests/orchestrator/test_render_format_parity.py` 按函数与常量比对 AST，保持一致（技能之间不共享代码文件）。
- 色彩标记规则：`color_space` / `color_primaries` / `color_transfer` 三项都是未标记（`unknown`、空、`reserved` 等）或 `bt709` 时，三项都标 `bt709`；否则只保留源里已声明且在白名单 `_KNOWN_COLOR_VALUES` 里的值，其余不写。`color_range` 源是 `pc` 就保留 `pc`，其它一律 `tv`。YUV 源只改标记，不做任何色彩空间或范围转换。
- 白名单只收 setparams、`-colorspace/-color_primaries/-color_trc` 输出参数都接受、且 libx264 真的写回码流的 ffprobe 名字；`test_every_known_colour_value_is_accepted_by_ffmpeg` 用真 ffmpeg 逐项跑过并读回。实测（ffmpeg 8.0 / 9.0.1）：ffmpeg 拒绝 `-colorspace gbr`；BT.470 传输特性 ffprobe 打印 `bt470m` / `bt470bg`，setparams 只认这两个名字，而 ffmpeg 8 的 `-color_trc` 只认 `gamma22` / `gamma28`（9 两者都认），所以 `_color_tag_args` 经 `_COLOR_OPTION_SPELLING` 换成后者；`jedec-p22` 两边都接受但 libx264 不写，已从白名单去掉。
- RGB 源（ffprobe `color_space=gbr`：PNG / QuickTime RLE 封装的 MOV、libx264rgb）：交付画面总是 YUV，没有源矩阵可保留，所以按 BT.709 limited 转换，`_output_color_tags` 把 colorspace 记为 `bt709`、range 记为 `tv`，并加 `from_rgb: true`；`_color_tag_filter` 据此在 setparams 前插入 `scale=out_color_matrix=bt709:out_range=tv,format=yuv420p`。实测：在 RGB 帧上直接 `setparams=colorspace=bt709` 会让 ffmpeg 之后的自动转换用 BT.601 矩阵而文件标 BT.709（纯红 Y=81 而不是 63），显式转换后像素与标签一致。RGB 源 pix_fmt 不是 4:2:0，从不流复制。
- assemble：重编码路径在滤镜链最后追加 `setparams=...`（在 even/缩放之后、包装图层合成的尾段里），所有路径（含流复制）都追加 `-colorspace/-color_primaries/-color_trc/-color_range` 输出参数。
- 流复制只在源画面"已经是重编码会交付的样子"时保留：H.264、`yuv420p` 或 `yuvj420p`、宽高均为偶数。否则即使没有滤镜也走 `-vf scale=偶数,setparams=... -c:v libx264 -pix_fmt yuv420p`，`delivery_qc.reencode_reason` 记 `normalize_source_format`（`FORCE_VIDEO_REENCODE` 仍记 `force_video_reencode`）。`assembly_qc.json` 的 `delivery_qc.color_tags` 记写入的标记。
- cut：`build_edited_source_video` 在 concat 输出后接 `[v]setparams=...[vtagged]`，映射 `[vtagged]` 并加同样的输出参数。多来源时各源算出的标记一致就用它，不一致就按 BT.709 标记并打日志（一条拼接画面只能有一个标签）。
- 显式 adopted 混音的 `packet_identity` 比较去掉四个色彩字段：未标记的画面流复制后多了 BT.709 标签，包与时钟没变，仍应报 `EXACT`。`explicit-audio-mix.md` 写明这一点。
- cut 的 `edited_source_render_cache_payload` 带 `picture_rules: "yuv420p-color-tags-v1"`（`cut_contract.EDITED_SOURCE_PICTURE_RULES`），升级前缓存的无标记 `edited_source.mp4` 因此重新剪辑，不会被最终渲染当成未标记源改标 BT.709。这些规则再变时要改这个版本串。
- 范围：这条保证覆盖 assemble 的四条输出路径和 cut 的 `edited_source.mp4`。dub 模式（video-voiceover `dub.py` 的 `_mux`）仍 `-c:v copy` 原样复制源画面，不做 yuv420p 与色彩标记归一化，只加了 copy 安全的 `-movflags +faststart`；dub 是实验模式，输入多是已能播放的成片，归一化留到需要时再加（届时按函数级副本加平行测试）。

## Alternatives considered

- **流复制路径也一律重编码。** 最强理由：最简单，交付格式完全由我们决定。没采用：源已经是 H.264 8-bit 4:2:0 时重编码只多一代压缩损失和编码时间，而 adopted-packet-copy 与显式混音依赖画面包不变（`EXACT`）。
- **流复制时用 `h264_metadata` 比特流过滤器把标签写进 VUI。** 最强理由：标签进码流，比容器 `colr` 更普遍被解码器认。没采用：要按编码器分别处理（h264 / hevc 各一个过滤器），会改写包内容使 `EXACT` 不再成立；而流复制只发生在 H.264 源上，容器标签已能被 ffprobe 和主流播放器读到。
- **未标记时按分辨率猜（SD 标 BT.601，HD 标 BT.709）。** 最强理由：这是部分播放器对未标记视频的实际猜法，对老的 SD 素材更准。没采用：任务要求未标记一律按 BT.709 标；成片几乎都是 HD 网络视频，猜测规则还会让同一来源在缩放前后得到不同标签。
- **全范围源也转成 limited（`scale=out_range=tv`）。** 最强理由：交付统一 limited 范围更符合广播与平台惯例。没采用：任务要求非 BT.709/未标记以外的情形保留源标记，范围同理；实测 ffmpeg 8/9 重编码保留 `pc` 范围且像素一致，标记与数据不会错位。

## Consequences

- **收益**：不烧录或降级的成片也一定是 H.264 8-bit 4:2:0 加 `+faststart`；RGB 源不再在最后一步因 `-colorspace gbr` 失败（此前一版白名单含 `gbr`，TTS 已花钱后才崩）；成片与 `edited_source.mp4` 都带色彩标签，播放器不再各猜各的；已声明 BT.601、BT.2020/PQ 等标签的源不会被错标；交付标签记在 `assembly_qc.json` 里可核对。
- 全范围 `yuvj420p` 的 H.264 源满足流复制条件，原样以 `yuvj420p` 交付（ffprobe 报 `yuvj420p`，不是 `yuv420p`），所以文档写的是“H.264 8-bit 4:2:0”。
- **代价**：不是 H.264 8-bit 4:2:0 的源在无滤镜时多一次重编码（时间与一代损失）；最终编码多一次 ffprobe；cut 每个来源多一次 ffprobe。多来源 cut 标签不一致时统一标 BT.709，其中非 BT.709 的那段颜色标签不准（像素本来就没有统一转换）。叠加的 RGBA 图层（drawtext、包装 PNG）转 YUV 时用的矩阵不在本次范围。
- 测试：`tests/assemble/test_render_delivery.py`（标签规则、流复制判定、命令构造、白名单逐项过真 ffmpeg，以及真 ffmpeg 渲染后 ffprobe 读到 `yuv420p` + BT.709/BT.601 标签 + moov 在 mdat 前，RGB 源按 BT.709 矩阵转换），`tests/assemble/test_explicit_mix_boundaries.py`（流复制报 `EXACT`、重编码报 `REENCODED_CLOCK_MATCH`），`tests/cut/test_pure_cut.py` 的 edited_source 标签测试（含真 ffmpeg），CI 无 ffmpeg 时这些真渲染测试跳过。
- 相关：[[2026-10-02-libass-optional-sidecar-degrade]]。
