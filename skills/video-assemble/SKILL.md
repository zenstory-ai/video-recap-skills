---
name: video-assemble
user-invocable: false
description: 合成视频解说最终成片：把旁白音频铺到源视频上，按旁白窗口压低原声，生成 SRT / ASS 字幕并可烧录， 最后做响度标准化。作为最终合成阶段使用。输入源视频、tts_meta.json 与旁白位置； 输出 recap 成片和字幕。触发词：视频合成、混音、字幕、压字幕、assemble video、mux、ducking、subtitles、成片。
---

## 1. 定位

本技能负责把画面、旁白、原声、配乐与字幕合成为最终交付的解说成片：

1. 将逐段旁白音频精准铺上成片时间线。
2. 在旁白发声期间按包络压低原片音轨（旁白压住原片人声与落在原片环境音上各用一档压低音量），并在旁白留白处把原片音量推回原位。
3. 根据旁白时间线生成 `subtitles.srt`，默认同时生成并烧录 `subtitles.ass`（传 `--no-burn-subtitles` 可关闭烧录）。未烧录字幕时（主动关闭或缺少滤镜自动降级），会把 `subtitles.srt` 复制到成片同目录并命名为 `recap_<stem>.srt`；成功烧录时则会清理同名的旧外挂字幕。
4. 可选对最终成片音轨执行两遍 `loudnorm` 响度标准化：全程只施加一个恒定增益，不做动态范围压缩；当混音真峰值余量不足以承受该增益时，先经过 4 倍过采样的真峰值限幅器削峰（最多削去 `LOUDNESS_LIMITER_MAX_DB`，默认 `6 dB`），超出部分才下调目标响度。由于 AAC 有损编码会让成片真峰值比编码前的 PCM 混音再高出 `0.3–1.4 dB`，首次渲染会把限幅目标设在 `-1.5 dBTP`（比 `-1 dBTP` 红线多留 `0.5 dB` 余量），并在渲染完成后解码成片实测真峰值；若实测仍超过 `-1 dBTP`，则按超出量再压低 `0.1 dB` 仅重编码音轨（画面流直接复制），最多重压两次，仍超标则以 `delivered_true_peak_over_target` 阻断。实际执行的响度模式、限幅削峰量与成片实测读数都会写入 `assembly_qc.json` 的 `loudness_mode` 与 `loudnorm_final_pass`（`delivered`）。
5. 交付成片一律剥离源视频自带的容器元数据（如 `title`、`comment` 标签及章节信息）。

## 2. 声音收尾契约

合成阶段只忠实执行上游已经定好的声音设计，不在渲染时临时替画面配乐或改主意。Agent 在规划旁白落点前，已经在 `visual_audio_board.json` 中为每个叙事节点（beat）指定了主导音源 `audio_owner`：

- `original_dialogue`（原片狠话/关键对白）
- `action_sound`（动作撞击/现场动静）
- `ambience` / `music`（环境声/原片配乐）
- `silence`（刻意静默）
- `narration`（解说旁白）

旁白留白处严格按上面选定的声音分工让出听觉焦点。只有在确实承担情绪推进任务时才铺入 BGM，确保观众能听清每一句关键原声台词和每一次有分量的沉默。

当前渲染器不直接读取 `visual_audio_board.json`；Agent 需通过旁白时间戳、`overlaps_speech` 标记、原声留白窗口与混音参数把这些声音设计落实到位。

## 3. 输入契约

- `<video>`：源视频路径；在 `cut` 模式下传入剪辑阶段产出的 `edited_source.mp4`。
- `work_dir/tts_meta.json`：默认 `narration` 模式的必需输入，包含配音阶段生成的 `{segments: [...]}`（记录每段音频路径、起止时间、`pause_after_ms`、`overlaps_speech` 及混音/字幕落点）。显式指定 `source-mix` 或 `adopted-packet-copy` 模式时不读取该文件。
- 若使用外部已确认采纳的配音资产，需同时显式传入 `--tts-meta` 和 `--narration-adoption`：后者由调用方独立确认每段台词文本、请求的引擎/音色与语速策略，工具绝不会把待消费的 `tts_meta.json` 自动当成已批准文件；详细结构见 `references/narration-adoption.md`。
- 若同时使用已确认的完整声音底轨与逐段配音，还可额外传入 `--audio-mix-adoption`；其 schema、48 kHz 声道矩阵与双绑定事务规则见 `references/explicit-audio-mix.md`。

以下 `scripts/...` 路径均相对于本技能目录；若从仓库根目录启动，请补齐本技能的目录前缀。

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

- `recap_<stem>.mp4`：对外交付的成片文件（每次运行更新）。
- `work_dir/output.mp4`：工作目录内的成片副本。
- `subtitles.srt`：旁白字幕文件；启用字幕烧录时还会生成 `subtitles.ass`。
- `timeline.json`：与具体渲染后端解耦的多轨时间线描述文件，涵盖视频轨、原声轨、旁白轨、BGM 轨、字幕轨及音量压低（ducking）包络。
- `_placed_*.wav`：实际送入主混音器的逐段旁白 PCM 文件；`timeline.json` 与剪映草稿统一引用这些文件。
- `narration_input_binding.json`：记录旁白输入源、格式转换、实际落点、旁白总轨与最终成片音轨的参数及数据包计数，并区分 `UNADOPTED`（默认未绑定采纳文件）与 `BOUND_TO_ADOPTION`（已绑定采纳文件）两种状态；该记录仅证明消费了哪些音频文件，不替代声纹鉴定或人工听审。
- `audio_mix_binding.json`：显式完整混音分支（`--audio-mix-adoption`）的消费回执，记录画面帧钟、声音底轨、48 kHz 配音落点、预混母带（premaster）、固定主增益、最终 PCM/AAC 读数及关联的旁白绑定路径。
- `assembly_manifest.json`：记录输入源、剪辑片段来源身份（路径、大小、`mtime`）、渲染配置与最终成片路径。
- `assembly_qc.json`：涵盖旁白完整性、原声句末交接、时间线素材时长与成片交付质量的质检报告。
- 剪映草稿目录：仅在开启 `--export-jianying` 时生成，内含 `draft_content.json`、`draft_info.json` 与 `draft_meta_info.json`。

## 6. 合成规则

- **音频模式与冻结边界**：默认音频模式为 `narration`；`source-mix` 与 `adopted-packet-copy` 必须显式指定，三者的处理逻辑与数据包冻结边界见 `references/audio-modes.md`。
- **显式完整混音分支**：`--audio-mix-adoption` 必须与 `--tts-meta`、`--narration-adoption` 一同传入；它仍归属 `narration` 模式，但会跳过默认的语速适配、原声句末交接、环境 BGM 混音、自动闪避（ducking）、响度标准化与限幅器。
- **多轨独立混音**：原片音轨、可选 BGM 轨与解说旁白轨各自独立处理后再混流。
- **段落内气口收紧**：与前一段旁白留白超过 `1.6` 秒的段落首句，按稿件写的 `start` 时间入场（但不得早于前一段实际结束时间加上其 `pause_after_ms`，若前一段超时则会顺延往后推）；同一段落内部的后续句子则会贴着前一句的实际结束点紧凑跟进（保留 `0.35` 秒自然气口），最多可比稿件写的 `start` 提前 `1.2` 秒入场。由于这一提前动作发生在旁白门禁校验之后，提前的部分绝不能踩进原片对白里——前面若有原片对白，最多只能提前到最后一句对白收尾处。实际被提前的句子会在 `source_entry_status` 中标记为 `paragraph_tightened`，并在 `written_start` 中保留原稿填写的 `start` 时间。
- **严禁裁切旁白尾音**：合成阶段绝不为了塞进时间窗而硬切旁白末尾；在安全倍率内温和提速后若仍放不下，直接报 `no_safe_fit` 阻断。每段 `_placed_*.wav` 的实际时长不得超过 `timeline.json` 中对应区间的长度，否则以 `timeline_audio_mismatch` 阻断。
- **采纳配音的语速策略**：绑定了 `narration_adoption.json` 的配音严格按其中声明的 `tempo_policy` 执行（默认原速播放且禁止段内自动提速，也可在安全范围内显式声明其他倍速策略），环境变量里的默认 `1.15` 倍速不会覆盖采纳决定；显式完整混音分支则固定原速，不做任何自动提速。放不下时必须回头修改排期，绝不裁切尾音。严格运行要求使用全新的工作目录与输出路径；一旦输入变动或质检不过，立即清理临时候选文件，绝不覆盖或冒充成功成片。
- **原声句末平滑恢复**：一段旁白读完后，如果原片正好在说话中途，立刻把原声推满会蹦出难听的“半截残词”。因此，旁白结束后最多可继续把原声压低 `3` 秒，等待这段窗口内出现的下一个原片句末锚点：一直压到该锚点的停顿起点 `pause_start`，随后完全在实测停顿区间内平滑推起音量，在 `source_restore_at` 恢复满音量（若该锚点状态为 `unverified`，则标记为 `sentence_boundary_unverified`）。若 3 秒内没有任何句末锚点：距离全片结束不足 3 秒时直接压到底（`held_to_timeline_end`）；否则在旁白结束处直接恢复原声（`bounded_release`），避免为了等远处的句号而把大段原声一直死死压住。全片最长的压低等待时长记入 `assembly_qc.json` 的 `summary.max_source_duck_hold_seconds`。
- **剪映草稿导出**：传入 `--export-jianying` 或设置 `EXPORT_JIANYING=1` 可将 `timeline.json` 导出为剪映可编辑草稿。在 `cut` 模式下建议同时传入 `--source-video <orig>`，让剪映草稿直接关联原片与剪辑区间，方便在剪映里微调入出点。导出时默认把用到的视频、音频和图片复制进草稿目录的 `Resources/local/{video,audio,image}`，保证草稿拷走也能直接打开；只有确认原文件路径固定不变时才用 `--jianying-no-bundle-media`。若画面叠加物在时间上重叠，导出器会自动拆到编号轨道；目标草稿目录非空时不会覆盖旧草稿，而是自动新建带编号的同级目录。导出器只接受 `schema_version: 2` 的 `timeline.json`，仅映射视频、音频、字幕与图片叠层（`scale` / `position`），遇到手写添加的变速、转场、蒙版、富文本或特效轨会直接报错拒绝。由于剪映草稿直接引用未烧录字幕的源视频，原片自带的硬字幕仍会显示，可在剪映内按需加遮罩。
- **字幕样式与原片硬字幕遮罩**：字幕外观可通过 `SUBTITLE_FONT_SIZE`、`SUBTITLE_MARGIN_V`、`SUBTITLE_MAX_CHARS` 等参数调整。传入 `SUBTITLE_Y_TOP/BOT`（或 `--subtitle-y-top/--subtitle-y-bot`）可将 ASS 字幕基线精准落到实测的原片字幕带上；坐标采用自动旋转摆正后显示画布上的左闭右开行区间 `[top, bot)`，仅支持方形或近方形像素（SAR 与 `1:1` 偏差不超过 2%，未标注的 `0:1` 按方形处理）。通用按比例遮罩默认半透明（`SUBTITLE_MASK_OPACITY=0.6`），而传入实测字幕带坐标时默认改为完全不透明（除非显式覆盖 `SUBTITLE_MASK_OPACITY`），彻底挡住底下的原片硬字幕。遮罩触发时机 `SOURCE_SUBTITLE_MASK_TIMING` 默认为 `narration`（仅在有字幕时遮挡）。
- **原声闪避与 BGM 混音参数**：原片音轨在留白段恢复至 `IDLE_ORIG_VOLUME`，在旁白下方压低至 `SPEECH_DUCKING_VOLUME`，过渡时长由 `DUCK_FADE_SECONDS` 控制；还可通过 `DUCK_BRIDGE_SECONDS`、`ZONE_DUCKING_VOLUME`、`FINAL_LOUDNORM`、`TARGET_LUFS` 与 `LOUDNESS_LIMITER_MAX_DB` 精细调节。如需铺垫配乐，用 `BGM_PATH` 指定音频文件，工具会将其循环铺满全片并按 `BGM_VOLUME` / `BGM_DUCKING_VOLUME` 混音；没有明确情绪推进任务时不要挂通用 BGM。
- **字幕与花字滤镜预检**：烧录字幕依赖带 `subtitles`（`libass`）滤镜的 ffmpeg，合成前会先做预检。若用户显式要求烧录（传了 `--burn-subtitles` 或设置了 `BURN_SUBTITLES`），缺少 `libass` 会直接报错退出；若只是走默认开启逻辑，缺少 `libass` 时会自动降级为不烧录并输出外挂 `.srt`（留白处的 `「」` 原声对白字幕仅在提供了 `user_subtitles.*` 时写入外挂字幕；原本靠遮罩触发的原声重打字幕随遮罩一并关闭，直接露底显示原片硬字幕），同时在 `visual_qc.json` 与 `assembly_manifest.json` 的 `warnings` 中记入 `subtitle_burn_degraded` 并在 `subtitles.burn_degraded_reason` 写明原因。至于 `visual_overlays.json` 里的画面花字，则依赖 ffmpeg 的 `drawtext`（`libfreetype`）滤镜；由于花字是写稿阶段明确要求的包装元素，缺少 `drawtext` 时会在渲染前直接报错，绝不静默丢弃。
- **画面编码与色彩标记**：交付成片统一为 H.264 8-bit 4:2:0 并写入 `+faststart`。只有当全程无需挂载任何画面滤镜，且源视频本身已是偶数宽高的 H.264 8-bit 4:2:0（`yuv420p` 或全范围 `yuvj420p`）时，才会直接流复制画面；否则统一重编码为 `yuv420p`。色彩空间标记规则：源视频未标记或原本就是 BT.709 时统一标为 BT.709（`-colorspace/-color_primaries/-color_trc bt709`），源视频已声明其他色彩空间则原样保留；色彩范围上，全范围（`pc`）源保留 `pc`，其余标为 `tv`。对于 YUV 源视频只修正容器标记、不重算像素矩阵；对于 RGB 源视频（`ffprobe` 报 `gbr` 或 RGB/调色板像素格式，如 PNG 或 QuickTime RLE 封装的 MOV），因不存在现成的 YUV 矩阵，会按 BT.709 limited 转换像素，并在 `assembly_qc.json` 的 `delivery_qc.color_tags` 中额外记入 `from_rgb: true`。
- **原声留白对白字幕**：原声留白段的对白字幕优先读取 Agent 校对过的 `original_subtitles.json`，未提供时才保守回退到 ASR 转写文本。只有当遮罩挡住了原片硬字幕、或用户提供了外挂字幕要求替换时，才会在留白段烧录原声对白，并自动加上 `「」` 引号与解说旁白区分。

### 按原片区间重建纯净声音底轨

如果成片需要精细保留多段原片对白并搭配独立铺设的配乐，可先按 `references/source-score.md` 调用独立的 `source_score.py`，直接从源视频音轨按精确帧区间切出原声轨、配乐轨与合成底轨（`prepared_bed.wav`）及来源回执。随后再按 `references/explicit-audio-mix.md` 传入 `--audio-mix-adoption` 与已确认的逐段配音合成最终视频。不要把做好的底轨塞回旧入口做二次自动压低（ducking），更不要从已经混入旧版解说的成片里扒音轨冒充干净原声。

## 7. 字幕与可选包装

永远先锁死画面、切点、旁白和混音，最后再加字幕动效或边框包装；花哨的包装救不了断裂的叙事、闪帧的切点或糊成一团的声音。日常交付优先走内置的 ASS 字幕路径；整片固定不动的边框、标题条和角标直接用 `packaging_layers.json` 静态叠加；只有当用户明确要求逐句定制花字排版、弹跳动效或透明图层动画时，才在项目内引入代码渲染器（如 Remotion），生成透明前景层后合入锁定母版。推荐工序与抽帧检查清单见 `references/packaging.md`。

## 8. 能力边界

- 本技能不编写旁白文案、不调用 TTS 合成语音，也不重新跑视频转写。
- 字幕烧录默认开启；关闭烧录时不会为了字幕重编码画面。
- 显式输出轴字幕轨（`subtitle_track.json`）的独立契约、全量替换语义与当前限制见 `references/subtitle-track.md`。
