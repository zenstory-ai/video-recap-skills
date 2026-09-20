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
2. 在旁白窗口内压低原声，支持 fixed / sidechain / zone 模式。
3. 根据旁白位置生成 `subtitles.srt`；默认同时生成并烧录 `subtitles.ass`，`--no-burn-subtitles` 可关闭。
4. 可选把最终响度标准化到目标 LUFS。

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
- 已采用的配音使用显式 `--tts-meta` 和 `--narration-adoption`：后者由调用方独立确认文字、WAV 指纹、请求的引擎/声线和速度策略，不能从待消费元数据自动“批准”出来。完整格式与证据边界见 `references/narration-adoption.md`。
- 已采用的完整声音底轨与逐段配音可再传 `--audio-mix-adoption`；严格格式、48 kHz 声道矩阵和双 binding 事务见 `references/explicit-audio-mix.md`。

下面的 `scripts/...` 均相对于本技能目录。若执行器从仓库根目录启动，请给脚本路径加上本技能的绝对目录。脚本不从其他技能目录读取文件；外部输入仅限命令显式传入的视频、参数与 `work_dir` 产物。

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
- `narration_input_binding.json`：旁白输入、转换、实际放置、旁白总轨和最终音轨的消费证据。区分旧输入未核、指纹匹配但未经独立采用、与采用决定绑定；不等于声线鉴定或听审。
- `audio_mix_binding.json`：显式完整声音分支的画面、底轨、48 kHz 配音、premaster、固定 master gain、最终 PCM/AAC 与另一 binding 的单向身份链。
- `assembly_manifest.json`：输入来源、cut 来源指纹、渲染设置与最终输出路径。
- `assembly_qc.json`：旁白完整性、原声句末交接、时间线素材时长与交付质量的发布门禁。
- 剪映草稿目录：仅 `--export-jianying` 时生成，包含 `draft_content.json`、`draft_info.json` 与 `draft_meta_info.json`。

## 6. 合成规则

- 音频模式的处理与冻结语义见 `references/audio-modes.md`。默认仍为 `narration`；另外两种模式必须显式选择。
- `--audio-mix-adoption` 只与显式 `--tts-meta`、`--narration-adoption` 同时使用；它保留 `narration` 模式名，但跳过旧速度/适配、原声 handoff、环境 BGM、duck、loudnorm 和 limiter。
- 音频按轨道混合：原声、可选 BGM 与旁白各自独立。
- 旁白不做任何容差裁尾；温和加速后仍放不下即 `no_safe_fit`。每段 `_placed_*.wav`
  必须与序列化后的时间线区间等长或更短，否则 `timeline_audio_mismatch` 阻断。
- 已采用配音的 v1 合同只支持原速、禁止段内适速；不能让环境默认 1.15 倍速或旧缓存覆盖它。放不下就修订安排，不裁尾。严格运行使用新工作目录与新输出路径；输入/实际混音来源变动或 QC 失败时，不发布候选成片。没有采用文件的旧入口仍是兼容模式，不自动获得同等证据。
- 原声在旁白结束后保持压低到下一可靠句末的 `pause_start`，只在实测停顿内渐强，
  于 `source_restore_at` 回满；无后续锚点时保持压低到时间线末端，而不是放出半句。
- `--export-jianying` / `EXPORT_JIANYING=1` 可把 `timeline.json` 导出为可编辑草稿。cut 模式应传 `--source-video <orig>`，让草稿引用真实原片区间。
- 剪映导出默认把视频、音频与图片复制到 `Resources/local/{video,audio,image}`，保持草稿可搬迁；`--jianying-no-bundle-media` 只适合原路径始终可访问的情况。
- 重叠覆盖物会拆到编号轨道；非空目标目录不会覆盖，而会创建编号兄弟目录。
- 常速、倒放、变换、富文本、转场、蒙版、LUT、绿幕复合草稿及显式特效轨道通过 timeline v2 扩展表达。需要素材包的功能只接受调用方合法提供的离线资源。
- 剪映草稿引用未烧录的源视频，因此原片硬字幕仍会保留，必要时在剪映内另行遮罩。
- 字幕外观可用 `SUBTITLE_FONT_SIZE`、`SUBTITLE_MARGIN_V`、`SUBTITLE_MAX_CHARS` 等控制。
- `SUBTITLE_Y_TOP/BOT` 把 ASS 基线放到测得的原片字幕区域，坐标为半开 `[top, bot)`；显式遮罩策略下默认 `SUBTITLE_MASK_OPACITY=0.6`，`SOURCE_SUBTITLE_MASK_TIMING=narration`。
- 原声在旁白间隙回到 `IDLE_ORIG_VOLUME`，旁白下压到 `SPEECH_DUCKING_VOLUME`；`DUCK_FADE_SECONDS` 控制过渡。还可配置 `DUCKING_MODE`、`ZONE_DUCKING_VOLUME`、`FINAL_LOUDNORM` 与 `TARGET_LUFS`。
- 可通过 `BGM_PATH` 指定 BGM；它会循环到成片长度，并按 `BGM_VOLUME` / `BGM_DUCKING_VOLUME` 混音。不要在没有创作依据时设置通用 BGM。
- 烧录字幕需要带 `subtitles` / libass 的 ffmpeg；合成阶段会预检并在缺失时明确失败。
- 原声留白中的对白字幕优先读取 Agent 校对的 `original_subtitles.json`；否则保守映射 ASR。只有遮罩覆盖留白或用户字幕明确要求替换时才烧录原声对白，并用 `「」` 与旁白区分。

### 按原片区间准备声音，而不是整体压低旧成片

已有多段原声取舍和独立 BGM 决定时，先用
`references/source-score.md` 的独立 `source_score.py` 操作，从原片声音流
按精确帧区间重建原声轨、连续音乐轨及两者之和。原片完整解码一次再切样本，
分别执行保留对白、低位原声和明确静音；渐变也必须显式给定。
已处理的音乐轨走 `frozen`，不能再次偏移、调增益或加渐变。
若采用的原声底轨本身已包含完整音乐决定且不再叠加配乐，使用严格的
`score:{"kind":"none"}`；它生成真实全零 score，并保持 source 与 prepared
的 canonical PCM payload 相同，不伪造静音音乐资产。

这一步仅输出声音底轨和来源回执，不是最终视频。要与逐段已采用配音合成，
必须再由调用方提供 `references/explicit-audio-mix.md` 的严格 adoption；不要将
底轨塞入旧入口再自动 duck，也不要从含旧解说的成片取整条声音来冒充干净
原声。保留旧入口兼容行为，并区分“底轨已验证”“配音已验证”和“完整混音已验证”。

## 7. 字幕与可选包装

先锁定画面、剪点、旁白和混音，再投入字幕动画或边框包装。字幕样式不能掩盖叙事、剪点或声音问题。

普通交付优先使用现有 ASS 路径。只有用户需要更精细的逐 cue 排版、动画或透明图层时，才使用 Remotion 或其他代码渲染器作为**项目级可选实现**，不要把特定框架、字体、颜色或黄字写成核心依赖。推荐顺序：

1. 输出无包装的锁定母版，确认音画内容不再变化。
2. 从实际 TTS / 时间线生成 captions；TTS 块保持连续思路，字幕可以按阅读宽度拆 cue。
3. 先抽检开头、亮背景、暗背景、人物近景和长字幕样帧，确定字号、安全区、描边、阴影及是否需要底板。
4. 渲染完整透明字幕层，再 overlay 到锁定母版；合成后确认音轨未被意外改写。
5. 完整播放实际最终文件，并复查字幕遮脸、跳字、断行、首尾帧和边界处残影。

已有项目级渲染器能输出精确包装时，使用 `references/foreground-compose.md`
将它生成的 RGBA 序列叠到锁定母版，而不是用通用白字黑框近似品牌样式。
该操作保留实际帧钟和 AAC 包，不生成字体或文案；只有通过验证的新文件才写入新目录。
片名卡有渐显或动画时须提供完整序列，不能冻结最后一张图代替。

包装价值来自稳定、可读、与内容一致的排版，不来自效果数量。先建立统一字体、颜色、描边/阴影和轻量动效；底板、边框、花字与音效只有解决具体可读性或叙事任务时才加入。一个样帧好看不代表全片成立。

## 8. 能力边界

- 不生成旁白文字，也不合成 TTS。
- 不重新转写视频，不擅自改变 Agent 的时间决定。
- 字幕烧录默认开启；关闭时不会重编码绘制字幕区域。

显式输出轴字幕轨的独立合同、完整替换语义和当前边界见 `references/subtitle-track.md`。

画面回原片重建后，若需保留另一文件中的已采用完整混音，先按
`references/pair-media.md` 显式配对独立画面与音轨。配对只复制流，不补字幕或片名卡；
后续字幕轨必须重新绑定配对后的容器与 `a:0`，不能继续沿用旧版本身份。
