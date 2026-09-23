# Agent Note: 长滤镜脚本按本机 ffmpeg 支持的写法传参

Status: implemented

## Problem

长 filtergraph 写进文件再交给 ffmpeg，是为了不让命令行超过 Windows 的 32K 上限（375 段遮罩即可触发）。四处调用都写死了旧选项：`assemble.py` 的 `-filter_complex_script` 与 `-filter_script:v:0`、`audio_mix.py` 的 loudnorm 首遍测量、`cut_render.py` 的剪辑渲染。FFmpeg 7.0 引入 `-/option 文件` 通用写法并弃用旧选项，8.0 仍保留，9.0 删除（`fftools/ffmpeg_opt.c` 在 n9.0 已无 `filter_complex_script`）。Homebrew 现装 9.x，于是：

- cut 的 filtergraph 超过 7000 字节（片段多）时，`cut.py` 渲染直接失败；
- 旁白段多或遮罩长时，`assemble.py` 最终渲染直接失败；
- loudnorm 首遍测量每次都失败，静默降级为单遍 target + limiter。

CI 的三个 runner 都没有 ffmpeg，真渲染测试全部跳过，所以没被发现。

## Decision

- video-assemble 与 video-cut 的 `lib.py` 各有一份 `filter_file_args(option, path)`：返回 `-/{option} path`，或在老 ffmpeg 上返回 `_LEGACY_FILTER_FILE_OPTIONS` 里的旧写法。按自包含约定两份各自维护。
- 写法由 `_ffmpeg_reads_option_files()` 决定：用 `-/filter_complex <临时文件>` 实际调用一次 PATH 上的 ffmpeg，stderr 含 `Unrecognized option` 即为老版本；结果按进程缓存。PATH 上没有 ffmpeg 时返回旧写法，随后的渲染照常报 ffmpeg 缺失。
- 四处调用全部改走该函数。
- 测试：两种写法各跑一遍参数化的长滤镜路径；用伪造 stderr 覆盖探测分支；另有 `shutil.which` 守卫的真 ffmpeg 测试，其中一条证明 loudnorm 首遍在本机 ffmpeg 上能返回测量值（回退修复即失败）。在 ffmpeg 4.2.2 与 9.0.1 两个真实二进制上都跑过。

## Alternatives considered

- **解析 `ffmpeg -version` 的主版本号**：一次调用、无需临时文件。没采用，因为 BtbN 的 `N-xxxxx-g…`、gyan.dev 的日期版等 git/nightly 构建没有可解析的版本号；按行为探测能覆盖这些构建。
- **探测时加 lavfi 输入，以返回码 0 判定**：判据更直接。没采用，因为它同时依赖 libavdevice 的 lavfi，精简构建会被误判为老版本，进而在 9.x 上用回已删除的选项；只看 `Unrecognized option` 回答的恰好是「认不认这个选项」。
- **只支持 `-/option`，要求 ffmpeg ≥ 7**：代码最简单。没采用，因为 Ubuntu 22.04 自带 4.4、24.04 自带 6.1，README 只要求 PATH 上有 ffmpeg，这样会直接弄坏这些安装。
- **长图也直接内联 `-filter_complex`**：两种文件选项都不用依赖。没采用，因为改用文件本来就是为了避开 Windows 命令行长度上限。

## Consequences

- **收益**：ffmpeg 4.x 到 9.x 都能渲染长剪辑与长旁白；ffmpeg ≥ 7 上重新启用两遍 loudnorm。
- **代价**：每个 skill 进程多一次短 ffmpeg 调用；探测依赖 cmdutils 的 `Unrecognized option` 文案（4.x 至 9.x 未变）；两份 lib 各一份实现。CI 仍没有 ffmpeg，真 ffmpeg 测试在 CI 上跳过，这类兼容问题只能靠本地或日后给 runner 装 ffmpeg 来发现。
