# Agent Note: 资源库第 5 期——静态包装图层直接在合成时叠加

Status: implemented

## Problem

`packaging` 模板（[[2026-09-27-resource-library-format]]）描述的是整片不动的包框、标题条、角标：几张图片各放在画布的一个矩形里。
唯一现成的叠加路径 `compose_foreground.py` 要求调用方渲染出与母版逐帧对齐的 RGBA 序列，并严格核对 AAC 包——它为动画包装设计，
套在静态图片上意味着写出成千上万张相同的 PNG，再做一遍整片复核。第 3 期（[[2026-09-27-project-binding-and-font-files]]）
只能校验和记录 `packaging` 绑定，成片里没有它。

## Decision

- video-assemble 新增 `scripts/packaging.py`，读取可选的 `work_dir/packaging_layers.json`（`canvas`、`layers[{name, path, rect}]`、`template`）。
  画布必须等于成片画布、`rect` 必须在画布内、图片必须存在，否则合成前报错。
- 渲染：每个图层用 `movie=filename=…,scale=w:h,format=rgba` 读入，按数组顺序 `overlay` 到遮原字幕之后、画面文字与解说字幕之前；
  整条滤镜仍是单输入单输出（`[in]…[out]`），沿用现有的 `-vf` / 滤镜脚本文件路径，不改输入与 `-map`。
- `timeline.json` 得到位置与缩放一致的全长 image 段（画布中心为原点、Y 轴向上、以半个画布为单位；缩放相对"按图片自身比例适配画布"的尺寸，
  用 ffprobe 读出的图片像素尺寸分别算 x、y，使拉伸到 `rect` 的渲染与剪映草稿一致），
  剪映草稿里的包框可单独编辑。`assembly_manifest.json` 的 `video_filters.packaging_layers` 记录每张图片的路径与 `{size, mtime_ns}`。
- video-recap：`--project` 绑定了 `packaging` 模板时，合成前写出该文件（带 `written_by: "video-recap --project"`）；
  不再绑定时只删除带此标记的文件，调用方手写的文件保留。`resource_lock.json` 把每个图层列为 `packaging_layer` 并对上图片资源。

## Alternatives considered

- **把模板渲染成 RGBA 序列交给 `compose_foreground.py`** — 最强理由：复用已有的严格前景合成与 AAC 冻结校验，不新增渲染路径。
  否：静态图片要展开成逐帧序列，磁盘与时间成本和片长成正比，还要求母版先锁定成 H264/CFR/AAC；动画包装仍然走那条路。
- **先把所有图层合成一张全画布 PNG，再叠一次** — 最强理由：滤镜图只多一个 `movie`。否：要多一次 ffmpeg 预合成和一个临时文件，
  而逐层 `overlay` 的滤镜文本本来就短；分层也让剪映草稿里每个图层可以单独编辑。
- **用额外的 `-i` 输入和 `-filter_complex`** — 最强理由：ffmpeg 最常见的叠图写法。否：会改变现有的输入序号与 `-map 0:v:0`，
  牵动音频输入、BGM 与封面图流的处理；`movie=` 源把改动限制在视频滤镜内部。

## Consequences

- **收益**：采用过的包装模板第一次真正进入成片与剪映草稿；叠加顺序明确，字幕永远在包框之上。
- **代价**：`movie=` 源在滤镜里按路径读图，路径转义沿用字幕滤镜的规则（路径含单引号时与既有的 `subtitles=` 一样不支持）；只支持静态图片，动画包装仍需逐帧序列；
  图层图片缩放到 `rect` 时不保持原始宽高比（模板作者负责给出匹配的图片）。

## Verification

`tests/assemble/test_packaging_layers.py`：真实 ffmpeg 渲染一段 320x240 蓝色画面，读出成片像素，底部包框处为红色、上方仍为蓝色（含带空格的图片路径），
并核对 `timeline.json` 的 image 轨；另有滤镜顺序、画布不符与越界拒绝、三种矩形的时间线坐标换算用例。
`tests/orchestrator/test_project_binding.py` 覆盖绑定写出、解绑删除自己的文件、保留手写文件。
