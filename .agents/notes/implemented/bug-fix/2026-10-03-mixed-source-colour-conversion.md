# Agent Note: 多来源 cut 每段显式转换到同一色彩空间，RGB 也按像素格式识别

Status: implemented

接续并部分推翻 [[2026-10-02-delivery-yuv420p-and-color-tags]] 里"多来源标记不一致时只改标记"的决定。

## Problem

- `_output_color_tags` 只在 ffprobe 报 `color_space=gbr` 时把源当作 RGB。实测（ffmpeg 8.0）：PNG、libx264rgb、FFV1 RGB 报 `gbr`，但 QuickTime RLE（`argb`）报不出任何色彩空间，GIF（`pal8`）、raw `bgr24` 同理。这类源被当成未标记的 YUV：单源时 `setparams=colorspace=bt709` 打在 RGB 帧上，矩阵交给 ffmpeg 自动转换去挑；多源时不进逐段的 BT.709 显式转换。
- 多来源 cut 各源标记不一致时，旧规则按 BT.709 标记、不转换像素。实测 ffmpeg 8.0 更糟：concat 会在各输入之间协商同一个色彩空间和范围，自行把其它来源转换到其中一个来源的色彩空间，最后 `setparams` 只改标签。BT.601 源 + 未标记源：两段纯红都存成 BT.601（Y=81/Cb=90），文件标 BT.709（应为 63/102）；全范围源 + limited 源：两段都存成全范围（54/98）却标 `tv`；`argb` + BT.601 源：两段都是 BT.601 像素标 BT.709。

## Decision

- 两份 `_output_color_tags`（video-assemble `media.py`、video-cut `media_geometry.py`，函数级副本）在 `color_space == "gbr"` 之外，也把像素格式以 `_RGB_PIX_FMT_PREFIXES`（`rgb`、`bgr`、`gbr`、`argb`、`abgr`、`0rgb`、`0bgr`、`x2rgb`、`x2bgr`、`pal8`、`bayer_`）开头的源当作 RGB。`test_rgb_pix_fmt_prefixes_match_ffmpegs_rgb_family` 用真 ffprobe 的 `-show_pixel_formats` 核对：被标 `rgb` 或 `palette` 的格式都匹配，其它（含 `gray`、`xyz12`、各种 YUV）都不匹配。常量加进 `tests/orchestrator/test_render_format_parity.py` 的比对表。
- 选标签的规则不变：各源标签（RGB 去掉 `from_rgb` 后即 BT.709 limited，未标记即 BT.709 + 源的范围）完全一致就沿用，否则 BT.709 limited。所以只要有 RGB 源或未标记源与别的源不一致，输出就是 BT.709 limited；全部未标记且都是全范围时保留 `pc`，与单源一致。
- 多来源时先算出这个目标，再由 cut 自己的 `cut_render._clip_color_filter` 给每段生成显式转换，接在各段的缩放/补边之后：RGB 源 `scale=out_color_matrix=<目标>:out_range=<目标>`；YUV 源 `scale=in_color_matrix=<源>:in_range=<源>:out_color_matrix=<目标>:out_range=<目标>`（未标记按 BT.709，与它单独时得到的标签一致），再 `format=yuv420p`。各源一致时也写同样的 in=out 转换，好让每段帧属性相同，concat 不再自行协商转换。`_SCALE_COLOR_MATRIX` 只收 scale 认得的矩阵（bt709、bt470bg→bt470、smpte170m、fcc、smpte240m、bt2020nc→bt2020）；`ycgco`、`bt2020c`、`ictcp` 等转不了，只做 `format=yuv420p`，与目标不同时记一条日志。
- 只转换矩阵和范围；原色与传输特性（如 BT.2020/PQ 与 BT.709 混剪）仍只改标记。
- `cut_contract.EDITED_SOURCE_PICTURE_RULES` 升到 `yuv420p-color-tags-v2`，按旧规则缓存的 `edited_source.mp4` 会重剪一次。

## Alternatives considered

- **只扩大 RGB 识别，标签不一致时仍只改标记。** 最强理由：改动最小，单源渲染不受影响，多来源的错标本来就写在旧笔记的代价里。没采用：实测 ffmpeg 8 的 concat 会把"正确"的那一段也转错，错的不只是标签，而是两段像素都与标签不符。
- **用 `colorspace` 滤镜同时转换矩阵、原色、传输和范围。** 最强理由：一次把四项都对齐，BT.601 与 BT.709 的原色差异也能修。没采用：它对未声明的输入和 PQ/HLG 等传输直接报错，失败就会让整次剪辑失败；scale 覆盖了肉眼最明显的矩阵与范围错误且在所有 ffmpeg 版本都可用。HDR 转 SDR 需要色调映射，超出范围。
- **混剪时一律输出 BT.709 limited（即使各源一致）。** 最强理由：规则更简单，交付标签固定。没采用：各源一致时保留标签与单源行为一致，也不必对已经一致的像素做多余的范围压缩。
- **调用 `ffprobe -show_pixel_formats` 按 `rgb` 标志判断。** 最强理由：完全跟随本机 ffmpeg 的定义。没采用：每次渲染多一次进程调用；名字前缀在测试里对真 ffprobe 全量核对，同样可靠。

## Consequences

- **收益**：BT.601、全范围、RGB 来源与别的来源混剪时，每段像素都与 BT.709 标签一致；QuickTime RLE、GIF 等不报色彩空间的 RGB 源按 BT.709 矩阵转换。
- **代价**：多来源剪辑每段多一次 swscale 转换（原来就有 `format=yuv420p`，现在显式指定矩阵）；各源不一致时 BT.601 等来源被转成 BT.709 存储，不是原样。原色与传输特性不同的来源仍然只改标签。缓存的 `edited_source.mp4` 会重剪一次。
- 测试：`tests/cut/test_edited_source_picture.py`（标签选择与逐段转换的矩阵、命令构造、真 ffmpeg 下四种混剪组合两段像素都检查、单个 `argb` 源），`tests/assemble/test_render_delivery.py`（像素格式识别、前缀与真 ffprobe 全量核对、QuickTime RLE 源的真渲染）。
