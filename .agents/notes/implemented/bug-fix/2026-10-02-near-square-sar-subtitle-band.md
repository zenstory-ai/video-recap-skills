# Agent Note: 测得的字幕带接受近方形像素，并在显示画布与解码帧之间换算行

Status: implemented

## Problem

测得的原片字幕带（`--subtitle-y-top/--subtitle-y-bot`、`SUBTITLE_Y_*`）由三处把关：`tools/measure_subtitle.py` 测量、recap 的 `_probe_display_size_or_raise(require_square_pixels=True)` 预检、assemble 的 `subtitles.core._measured_subtitle_band`。三处都要求 SAR 与 1:1 的差小于 `1e-9`。真实片源常见 `64:63`、`101:100` 这类近方形 SAR，于是工具直接报错、recap 拒绝坐标，用户只能关掉字幕带：遮罩无法贴合原字幕，`「」` 原声字幕也因遮罩不覆盖空隙而被跳过。

另外两处不一致：assemble 用 `_ratio_to_float(sar, 0.0)` 判断，把未标注的 `0:1` 读成 0 而拒绝，可同一模块算画布时按 1.0 处理；recap 预检把 `0:1` 读成 0，算出宽 1 像素的画布。

坐标本身的语义：未旋转时，显示画布只按 SAR 改宽度，行与解码帧完全一致；旋转 90°/270° 时，存储宽度变成画面高度并被 SAR 缩放，显示行与解码帧（ffmpeg 自动旋转后、drawbox 实际作画的帧）的行相差一个 SAR 倍数。旧的“只认 1:1”回避了这件事，而不是算对它。

## Decision

- 三处共用容差 `SUBTITLE_BAND_SAR_TOLERANCE = 0.02`（含边界），`0:1`/`N/A` 按方形；`tests/orchestrator/test_measure_subtitle.py` 用 AST 钉住三份副本同值。
- `tools/measure_subtitle.py`：`_probe_video` 另读旋转信息；显示画布由解码帧尺寸、SAR 和旋转算出（与 recap、assemble 的画布一致），检测到的像素行按 `显示高度 / 解码帧高度` 换算；写出的 `canvas` 是显示画布。行比例不为 1 时打印说明（预览红框仍按解码帧像素画）。
- recap 预检参数改名 `require_near_square_pixels`，报错写明容差。
- assemble：`_frame_row(canvas, y)` 把显示画布行换算成解码帧行（帧高取旋转后的存储宽/高，与画布同高时原样返回），源字幕遮罩用它；ASS 侧本来就按 PlayRes 比例映射，不需要换算。
- 方形、未旋转视频的所有输出与以前逐字节相同。
- 真实验证：用 ffmpeg 生成 `SAR 64:63` 的横屏片与带旋转元数据的竖屏片，工具分别测出 `650×360` 画布上的 `[300, 324)`、`360×650` 画布上的 `[569, 593)`（解码帧行 560–584 按 650/640 换算）；recap 与 assemble 对两份文件探测到的画布相同。

## Alternatives considered

- **只放宽容差，不做行换算。** 最强理由：改动最小；未旋转视频本来就行行一致。没采用：旋转的近方形视频会有最多 2% 画高的错位（1920 行上约 38 像素），遮罩恰好偏离原字幕；换算只有几行代码。
- **任何 SAR 都接受。** 最强理由：行换算已经精确，容差看起来多余。没采用：ffmpeg 的 subtitles 滤镜按存储像素渲染 ASS，SAR 明显偏离 1 时烧进去的字会被横向拉伸；近方形时拉伸不可见，变形片源仍应先转成方形像素再测。
- **测量工具按存储尺寸输出画布、让下游自己换算。** 最强理由：工具更简单。没采用：recap 与 assemble 都按显示画布校验坐标，坐标域必须在生产端就统一。

## Consequences

- 收益：近方形与未标注 SAR 的片源可以测字幕带、贴合遮罩并补 `「」` 原声字幕；旋转片源的遮罩落在原字幕上。
- 代价：三份容差常量要同步（有测试守着）；SAR 偏差在 2%–明显变形之间的片源仍被拒绝；视觉叠加（drawtext 坐标）在旋转的非方形片源上没有做同样的行换算，本次不涉及。
