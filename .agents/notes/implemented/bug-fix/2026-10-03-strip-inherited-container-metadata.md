# Agent Note: 交付文件不继承原片的容器元数据与章节

Status: implemented

## Problem

ffmpeg 默认把第一个输入的全局元数据、各流元数据和章节抄进输出。下载器（yt-dlp 等）给原片写的 `title`、`comment`（常是来源网址）、`purl`、`description`，以及原音轨的 `language=eng`、原片章节，因此会出现在 `edited_source.mp4`、最终 recap 和 dub 成片里。实测 ffmpeg 8.0：重编码加 `-filter_complex` 时 `title`/`comment` 照样被抄过去；流复制时连视频流的 `handler_name` 也原样保留；章节时间是原片的，剪辑后对不上画面。`final_qc` 早已不再把容器标签写进报告，但成片本身仍带着它们。

## Decision

- 交付文件的每个 mux 都加 `-map_metadata -1 -map_chapters -1`，不保留任何源标签与章节；我们自己写的只有色彩标记（`-colorspace` 等，作用于码流与 `colr`，不受影响）和 muxer 自动写的 `encoder`、brand 等。
  - video-cut `cut_render.build_edited_source_video`。
  - video-assemble `assemble.py` 的最终命令：narration、source-mix、adopted-packet-copy 与显式混音四条音频路径共用同一处，加在色彩参数之后。
  - video-voiceover `dub.py` 的 `_mux`。
- 实测（ffmpeg 8.0）：只给 `-map_metadata -1` 时章节仍被抄过去且标题变空，所以两个选项一起用；画面旋转在 ffmpeg 6 以后是 side data，不受 `-map_metadata` 影响，流复制后 ffprobe 仍读到 `rotation`。
- 内部中间文件（VLM 分片、TTS、ASR 切片）不交付，不改。

## Alternatives considered

- **只去掉全局标签（`-map_metadata:g -1`），保留流标签。** 最强理由：音轨语言、`handler_name` 有时是有用信息，流复制时完全不动流更保守。没采用：成片音轨是中文旁白混音，源的 `language=eng` 反而错；`handler_name` 也可能带来源站点名；ffmpeg 自己会写默认的 `handler_name`。
- **dub 输出保留章节。** 最强理由：dub 不改时间线，章节时间仍然对得上。没采用：章节标题同样来自下载器；只去标签不去章节会留下空标题的章节；dub 是实验模式，与其它交付保持一致更易维护。
- **显式写入我们自己的 `title`（如 recap 名）。** 最强理由：播放器里显示得更友好。没采用：任务只要求不继承，写什么标题属于产品决定，没有需求方。

## Consequences

- **收益**：成片不再暴露来源网址与原片标题，也没有错位的章节。
- **代价**：原片里有意义的标签（如真实的音轨语言、版权说明）也一并去掉；需要时由使用者在成片上另行写入。旋转只在 ffmpeg 8.0 上验证过；更早的 ffmpeg 还把旋转同时导出为 `rotate` 标签，流复制旋转过的手机原片时是否保留未验证（cut 模式的 `edited_source.mp4` 是重编码后的已转正画面，不受影响）。
- 测试：`tests/cut/test_edited_source_picture.py`（命令与真 ffmpeg：标题、注释、章节、语言都不在 `edited_source.mp4` 里），`tests/assemble/test_render_delivery.py`（narration 命令；source-mix 与 adopted-packet-copy 在流复制和重编码下的真渲染），`tests/voiceover/test_dub.py`（dub 命令）。
