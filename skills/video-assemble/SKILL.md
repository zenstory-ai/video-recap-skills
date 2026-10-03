---
name: video-assemble
user-invocable: false
description: >
 合成视频解说最终成片：把旁白音频铺到源视频上，按旁白窗口压低原声，生成 SRT / ASS 字幕并可烧录，
 最后做响度标准化。作为最终合成阶段使用。输入源视频、tts_meta.json 与旁白位置；
 输出 recap 成片和字幕。触发词：视频合成、混音、字幕、压字幕、assemble video、mux、ducking、subtitles、成片。
---

## 1. 定位

本技能负责最终合成：

1. 把各段旁白音频放到视频时间线上。
2. 在旁白窗口内用固定包络压低原声（盖住原声对白时与落在安静段时各用一档音量），间隙恢复原声。
3. 根据旁白位置生成 `subtitles.srt`；默认同时生成并烧录 `subtitles.ass`，`--no-burn-subtitles` 可关闭。不烧录时（关闭或降级），`subtitles.srt` 复制到成片旁，名为 `recap_<stem>.srt`；烧录时删掉旧的同名外挂字幕。
4. 可选把最终响度标准化到目标 LUFS：两遍 loudnorm，只用一个恒定增益，不做动态压缩；混音的真峰值放不下这么大的增益时，先过 4 倍过采样的真峰值限幅器（最多削 `LOUDNESS_LIMITER_MAX_DB`，默认 6 dB），超出部分才下调目标响度。真峰值目标针对交付的 AAC 文件：AAC 编码会让真峰值比 PCM 混音高 0.3–1.4 dB，所以首次渲染瞄准 TP 下 0.5 dB，渲染后解码成片实测；仍超过 -1 dBTP 时按超出量再降 0.1 dB 只重编码音频（画面流复制），最多两次，仍超出则以 `delivered_true_peak_over_target` 阻断。ffmpeg 实际用的模式、限幅量与成片实测值记在 `assembly_qc.json` 的 `loudness_mode` 与 `loudnorm_final_pass`（`delivered`）。
5. 成片不带原片的容器元数据（`title`、`comment` 等标签与章节）。

## 2. 声音收尾契约

合成阶段只实现创作决定，不凭空制造决定。Agent 在写旁白位置前，已在 `visual_audio_board.json` 为每个 beat 指定 `audio_owner`：

- `original_dialogue`
- `action_sound`
- `ambience` / `music`
- `silence`
- `narration`

因此，旁白间隙是主动选择，不是必须填满的空白。不要为了“更满”而加入通用 BGM、压住必须听见的台词或消除有意义的沉默。

当前渲染器不解析 `visual_audio_board.json`；Agent 通过旁白时间、`overlaps_speech`、原声留白与现有混音参数落实这些决定。

## 3. 输入契约

- `<video>`：源视频；cut 模式下为 `edited_source.mp4`。
- `work_dir/tts_meta.json`：默认 `narration` 模式必需；配音阶段写出的 `{segments: [...]}`。每段包含 `audio_path`、时间、`pause_after_ms`、`overlaps_speech` 和用于混音/字幕的位置。显式 `source-mix` / `adopted-packet-copy` 模式不读取它。
- 已采用的配音使用显式 `--tts-meta` 和 `--narration-adoption`：后者由调用方独立确认文字、请求的引擎/声线和速度策略，不能从待消费元数据自动“批准”出来。完整格式与记录边界见 `references/narration-adoption.md`。
- 已采用的完整声音底轨与逐段配音可再传 `--audio-mix-adoption`；严格格式、48 kHz 声道矩阵和双 binding 事务见 `references/explicit-audio-mix.md`。

下面的 `scripts/...` 均相对于本技能目录。若执行器从仓库根目录启动，请给脚本路径加上本技能的绝对目录。

## 4. 运行命令

```bash
python3 scripts/assemble.py <video> --work-dir <work_dir> \
  [--audio-mode narration|source-mix|adopted-packet-copy] [--audio-stream-index <N>] \
  [--tts-meta <tts_meta.json> --narration-adoption <narration_adoption.json>] \
  [--audio-mix-adoption <audio_mix_adoption.json>] \
  [--recap-stem <name>] [--output-dir <dir>] [--no-burn-subtitles] \
  [--subtitle-y-top <inclusive-y> --subtitle-y-bot <exclusive-y>] \
  [--source-video <orig.mp4>] [--export-jianying [--jianying-out <dir>]]
```

## 5. 输出契约

- `recap_<stem>.mp4`：稳定的最终输出别名；每次运行覆盖更新。
- `work_dir/output.mp4`：工作目录内成片。
- `subtitles.srt`：旁白字幕；烧录时另有 `subtitles.ass`。
- `timeline.json`：后端无关的多轨模型，包含视频、原声、旁白、BGM、字幕和 ducking 自动化。
- `_placed_*.wav`：实际写入主混音的完整逐段旁白 PCM；时间线与剪映只引用这些文件。
- `narration_input_binding.json`：旁白输入、转换、实际放置、旁白总轨和最终音轨的消费记录（路径、PCM 参数、packet 计数）。区分未采用与已绑定采用决定两种状态；不等于声线鉴定或听审。
- `audio_mix_binding.json`：显式完整声音分支消费的画面时钟、底轨、48 kHz 配音放置、premaster、固定 master gain、最终 PCM/AAC 事实与 narration binding 路径的记录。
- `assembly_manifest.json`：输入来源、cut 来源标识（路径、大小、mtime）、渲染设置与最终输出路径。
- `assembly_qc.json`：旁白完整性、原声句末交接、时间线素材时长与交付质量的发布门禁。
- 剪映草稿目录：仅 `--export-jianying` 时生成，包含 `draft_content.json`、`draft_info.json` 与 `draft_meta_info.json`。

## 6. 合成规则

- 音频模式的处理与冻结语义见 `references/audio-modes.md`。默认仍为 `narration`；另外两种模式必须显式选择。
- `--audio-mix-adoption` 只与显式 `--tts-meta`、`--narration-adoption` 同时使用；它保留 `narration` 模式名，但跳过旧速度/适配、原声 handoff、环境 BGM、duck、loudnorm 和 limiter。
- 音频按轨道混合：原声、可选 BGM 与旁白各自独立。
- 段落首块（与上一块作者留白超过 1.6 秒）从写的 `start` 放置，但不早于上一块实际结尾加它的 `pause_after_ms`，上一块超时会把它往后推；段落内后续块紧接上一块的实际结尾（间隔 0.35 秒），最多比写的 `start` 提前 1.2 秒。这一提前发生在旁白校验之后，所以提前的那一段不能进入原声对白：有对白时最多提前到最后一段对白结束处。真的提前了的块在 `source_entry_status` 记 `paragraph_tightened`，并在 `written_start` 记写的 `start`。
- 旁白不做任何容差裁尾；温和加速后仍放不下即 `no_safe_fit`。每段 `_placed_*.wav`
  必须与序列化后的时间线区间等长或更短，否则 `timeline_audio_mismatch` 阻断。
- 已采用配音的 v1 合同只支持原速、禁止段内适速；不能让环境默认 1.15 倍速或旧缓存覆盖它。放不下就修订安排，不裁尾。严格运行使用新工作目录与新输出路径；输入/实际混音来源变动或 QC 失败时，不发布候选成片。没有采用文件的旧入口仍是兼容模式，不自动获得同等证据。
- 原声在旁白结束后最多再压低 3 秒，等这段时间内的下一个句末锚点：压到它的 `pause_start`，只在实测停顿内渐强，
  于 `source_restore_at` 回满（锚点为 `unverified` 时状态记 `sentence_boundary_unverified`）。3 秒内没有锚点时，
  离时间线末端不足 3 秒就压到末端（`held_to_timeline_end`），否则在旁白结束处直接回满（`bounded_release`），
  不为远处的锚点长时间压住原声对白。`assembly_qc.json` 的 `summary.max_source_duck_hold_seconds` 记录最长的压低延续。
- `--export-jianying` / `EXPORT_JIANYING=1` 可把 `timeline.json` 导出为可编辑草稿。cut 模式应传 `--source-video <orig>`，让草稿引用真实原片区间。
- 剪映导出默认把视频、音频与图片复制到 `Resources/local/{video,audio,image}`，保持草稿可搬迁；`--jianying-no-bundle-media` 只适合原路径始终可访问的情况。
- 重叠覆盖物会拆到编号轨道；非空目标目录不会覆盖，而会创建编号兄弟目录。
- 导出只接受 `schema_version: 2` 的 `timeline.json`，只映射视频、音频、字幕和图片叠层（`scale` / `position`）；变速、转场、蒙版、富文本、特效轨等手写扩展字段会被明确拒绝。
- 剪映草稿引用未烧录的源视频，因此原片硬字幕仍会保留，必要时在剪映内另行遮罩。
- 字幕外观可用 `SUBTITLE_FONT_SIZE`、`SUBTITLE_MARGIN_V`、`SUBTITLE_MAX_CHARS` 等控制。
- `SUBTITLE_Y_TOP/BOT` 把 ASS 基线放到测得的原片字幕区域，坐标为显示画布上的半开 `[top, bot)`，只接受方形或近方形像素（SAR 与 1:1 相差不超过 2%，未标注的 `0:1` 按方形）；显式遮罩策略下默认 `SUBTITLE_MASK_OPACITY=0.6`，`SOURCE_SUBTITLE_MASK_TIMING=narration`。
- 原声在旁白间隙回到 `IDLE_ORIG_VOLUME`，旁白下压到 `SPEECH_DUCKING_VOLUME`；`DUCK_FADE_SECONDS` 控制过渡。还可配置 `DUCK_BRIDGE_SECONDS`、`ZONE_DUCKING_VOLUME`、`FINAL_LOUDNORM`、`TARGET_LUFS` 与 `LOUDNESS_LIMITER_MAX_DB`。
- 可通过 `BGM_PATH` 指定 BGM；它会循环到成片长度，并按 `BGM_VOLUME` / `BGM_DUCKING_VOLUME` 混音。不要在没有创作依据时设置通用 BGM。
- 烧录字幕需要带 `subtitles` / libass 的 ffmpeg，合成阶段在渲染前预检。显式要求烧录（`--burn-subtitles` 或环境变量 `BURN_SUBTITLES`）时缺 libass 直接失败；只是默认开启时降级：不烧录，交付外挂 `.srt`（留白里的 `「」` 原声对白仅在有 `user_subtitles.*` 时照常写进去；原本由遮罩触发的对白随遮罩一起关闭，原片硬字幕可见），`visual_qc.json` 的 `warnings` 与 `assembly_manifest.json` 的 `warnings` 记一条 `subtitle_burn_degraded`，`subtitles.burn_degraded_reason` 写原因。降级后遮罩照旧关闭（`mask.trigger` 为 `burn_subtitles_degraded`）。
- `visual_overlays.json` 的文字叠加用 ffmpeg `drawtext`（libfreetype）。缺 drawtext 时合成在渲染前失败；叠加是写稿时明确加的内容，不会被静默丢掉。
- 成片画面一律是 H.264 8-bit 4:2:0 并带 `+faststart`：不需要滤镜、且源画面已是 H.264 8-bit 4:2:0（`yuv420p`，或全范围的 `yuvj420p`）、宽高为偶数时才流复制，原样交付；否则重编码为 `yuv420p`。色彩标记：源未标记或已是 BT.709 时标为 BT.709（`-colorspace/-color_primaries/-color_trc bt709`），其它已声明的色彩空间原样保留；全范围（`pc`）源保留 `pc`，其余标 `tv`。YUV 源只改标记，不转换像素；RGB 源（ffprobe 报 `gbr`，如 PNG/QuickTime RLE 封装的 MOV）没有 YUV 矩阵可保留，按 BT.709 limited 转换。结果记在 `assembly_qc.json` 的 `delivery_qc.color_tags`（RGB 源多一个 `from_rgb: true`）。
- 原声留白中的对白字幕优先读取 Agent 校对的 `original_subtitles.json`；否则保守映射 ASR。只有遮罩覆盖留白或用户字幕明确要求替换时才烧录原声对白，并用 `「」` 与旁白区分。

### 按原片区间准备声音，而不是整体压低旧成片

已有多段原声取舍和独立 BGM 决定时，先用 `references/source-score.md` 的独立
`source_score.py` 从原片声音流按精确帧区间重建原声轨、音乐轨及两者之和；它只输出
声音底轨和来源回执。要与逐段已采用配音合成，再由调用方提供 `references/explicit-audio-mix.md`
的严格 adoption；不要将底轨塞入旧入口自动 duck，也不要从含旧解说的成片取整条声音冒充干净原声。

## 7. 字幕与可选包装

先锁定画面、剪点、旁白和混音，再投入字幕动画或边框包装；字幕样式不能掩盖叙事、剪点或声音问题。
普通交付优先使用现有 ASS 路径；整片不动的包框、标题条和角标用 `packaging_layers.json` 静态叠加；
只有用户需要逐 cue 排版、动画或透明图层时，才用项目级代码渲染器，由该项目自己把透明层合成到锁定母版。
包装顺序与样帧抽检清单见 `references/packaging.md`。

## 8. 能力边界

- 不生成旁白文字，不合成 TTS，不重新转写视频。
- 字幕烧录默认开启；关闭时不会重编码绘制字幕区域。

显式输出轴字幕轨的独立合同、完整替换语义和当前边界见 `references/subtitle-track.md`。
