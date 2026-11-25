# Agent Note: 长篇渲染撞上的两处 ffmpeg 上限：恢复 `-/filter_complex` 探测、压低表达式分组

Status: implemented

## Problem

0.8.0 重写成原子 CLI 时，`_common.filter_script_args` 把长 filtergraph（超过 4096 字节）写进文件后写死了旧选项 `-filter_complex_script`。FFmpeg 9.0 已删除该选项（见 [[2026-09-23-ffmpeg9-filter-file-options]]），于是在 9.x 上：几百段镜头的长篇时间线 `render.py` 直接报 `Unrecognized option 'filter_complex_script'` 失败；loudnorm 首遍测量（`_audio.py`）在图变长时同样失败。现有渲染测试都只有几段镜头，图从未超过 4096 字节，所以没被发现。

## Decision

- `_common.ffmpeg_reads_option_files()` 沿用 0.7 的做法：用 `-/filter_complex <临时文件>` 实际调用一次 PATH 上的 ffmpeg，stderr 不含 `Unrecognized option` 即支持新写法，结果按进程缓存；没有 ffmpeg 时按不支持处理。
- `filter_script_args` 支持时一律用 `-/{flag}`，否则 `filter_complex`/`vf`/`af` 退回旧选项。
- 压低表达式分组：旁白一百多句时，合并后近百个压低窗口串成一个 `volume` 表达式（5.7 KB），ffmpeg 报 `Missing ')' or too many args`。`_audio.ducking_filters` 把合并后的窗口按 40 个一组生成多个串联 `volume` 滤镜（第一组带基础增益，其余按相对增益）；合并后的窗口连同淡入淡出互不重叠，乘积与原单一表达式逐点相同。原声压低与背景音乐压低都改用它；测试用 120 个窗口在真 ffmpeg 上跑通。
- 测试：固定探测结果分别核两种写法；真 ffmpeg 渲染 40 段短镜头（图超过 4096 字节），核成片时长等于整帧累计时长。两条测试在改动前的代码上都失败。

## Alternatives considered

- **解析 `ffmpeg -version` 主版本号**——最强理由：不用临时文件。否：git/nightly 构建没有可解析的版本号，0.7 的笔记已论证过。
- **只用 `-/option`**——最强理由：代码最少。否：Ubuntu 22.04/24.04 自带的 4.4、6.1 不认，会弄坏这些安装。

## Consequences

- **收益**：长篇时间线在 ffmpeg 4.x–9.x 上都能渲染；回归有测试兜住。
- **代价**：每个进程首次遇到长图时多一次很短的 ffmpeg 调用。
