# 配置参数速查表（按需覆盖）

配好必需密钥与本机 `ffmpeg` 后，默认参数即可跑通全流程。以下环境变量与 CLI 参数用于按需覆盖默认值；也可通过 `--project recap_project.json` 绑定资源库与模板（见 `./resource-library.md`，显式参数与项目绑定冲突时会报错停机）。

## 1. 模型、理解与 TTS 配音

| 配置项 | 环境变量 / CLI 参数 | 默认值 | 说明 |
|---|---|---|---|
| MiMo 密钥 | `MIMO_API_KEY` | — | 默认全链路必需，驱动 ASR、VLM 与默认 MiMo TTS。`tp-*` 自动走 Token Plan 集群地址，`sk-*` 走按量计费地址 |
| Token Plan 集群 | `MIMO_TOKEN_PLAN_CLUSTER` | `cn` | 可选 `cn` / `sgp` / `ams`（仅对 `tp-*` 密钥生效） |
| 画面理解模型 | `MIMO_MODEL` | `mimo-v2.6-flash` | 场景抽帧 VLM 模型（可选 `mimo-v2.6-pro` 等）；并发数由 `VLM_WORKERS`（默认 `8`）控制，遇限流可调小 |
| 语音转写模型 | `MIMO_ASR_MODEL` / `MIMO_ASR_LANGUAGE` | `mimo-v2.5-asr` / `auto` | 语言可选 `auto` / `zh` / `en` |
| ASR 切片时长 | `ASR_SEGMENT_SECONDS` | `15` | 秒；调小（如 `5`）可获得更细粒度对白时间窗 |
| 场景切分阈值 | `--scene-threshold` | `0.1` | 理解阶段初始镜头切分灵敏度 |
| TTS 供应商 | `TTS_PROVIDER` / `--tts-provider` | `auto` | 可选 `auto`、`mimo-tts`、`fish-audio`、`index-tts`。`auto` 优先选已配置的 MiMo，其次 Fish Audio，不自动选 `index-tts` |
| MiMo 配音音色 | `MIMO_TTS_MODEL` / `MIMO_TTS_VOICE` / `--mimo-tts-voice` | `mimo-v2.5-tts` / `冰糖` | 仅 `mimo-tts` 生效 |
| 解说音色克隆 | `VOICE_REF` / `--voice-ref` | 关闭 | 仅 `mimo-tts` 支持，与 `--mimo-tts-voice` 互斥；将参考音频切片发往 `mimo-v2.5-tts-voiceclone`，使用前须获声音授权 |
| Fish Audio | `FISH_API_KEY` / `FISH_TTS_MODEL` / `FISH_TTS_REFERENCE_ID` / `FISH_TTS_API_URL` | — / `s2.1-pro-free` / `5653cea4ac83480aaf2bf45406556185` / `https://api.fish.audio/v1/tts` | 选 `fish-audio` 时需配置 `FISH_API_KEY`（默认音色“娱乐扒妹”）；密钥不落盘 |
| 自建 IndexTTS | `INDEX_TTS_ENDPOINT` / `INDEX_TTS_VOICE` | — | 显式设 `TTS_PROVIDER=index-tts` 或传 `--tts-provider index-tts` 时启用；兼容 8/16/24/32-bit PCM 与 32/64-bit float WAV，固定请求体拒绝段级 `emotion`（详见 `./index-tts.md`） |
| TTS 并发与超时 | `TTS_TIMEOUT` / `TTS_WORKERS` / `TTS_RETRIES` | `300` / `4` / `3` | 单请求超时秒数、并发段数与重试次数 |
| 异常慢读检测 | `TTS_MIN_SPEECH_RATE` | `2.5` | 字/秒下限；合成音频明显超出正常朗读时长时判定为异常合成并重试，失败后留存 `*.rejected.wav`（`0` 关闭） |
| 全局旁白语速 | `NARRATION_SPEED` | `1.15` | 旁白整体轻度提速倍率；长片深度解说可设为 `1.0` |

## 2. 剪辑切点与解说校验

| 配置项 | 环境变量 / CLI 参数 | 默认值 | 说明 |
|---|---|---|---|
| 原声句界检测 | `SOURCE_BOUNDARY_NOISE_THRESHOLD` / `SOURCE_BOUNDARY_MIN_PAUSE` / `SOURCE_BOUNDARY_MAX_ALIGNMENT_ERROR` | `-18dB` / `0.12` / `2.1` | 将 ASR 句末标点对齐短声学停顿并写出 `speech_boundary_anchors.json`，用于拦截切词与抢话 |
| 硬切边缘吸附 | `SCENE_CUT_SNAP` / `SCENE_CUT_SNAP_MARGIN` / `SCENE_CUT_DETECT_THRESHOLD` | 开启 / `0.5` / `0.32` | `cut` 模式下将片段首尾吸附到 `0.5s` 内的原片硬切点，避免切出邻镜残帧（`SCENE_CUT_SNAP=0` 关闭） |
| 句末吸附与尾垫 | `SNAP_CLIP_LINE_END` / `CLIP_START_SNAP_MAX_PREPEND` / `CLIP_START_SNAP_MAX_TRIM` / `CLIP_SNAP_MAX_EXTEND` / `CLIP_END_PAUSE_PAD` / `MIN_CLIP_DURATION` | 开启 / `1.8` / `0.35` / `2.0` / `0.30` / `0.8` | 硬切吸附后将出点向停顿窗延伸最多 `0.30s` 留出反应气口；单段短于 `0.8s` 报错，踩中对白中途报 `unsafe_clip_sentence_boundary`，硬切与台词无法兼顾时报 `shot_speech_conflict` |
| 成片残帧门禁 | `CUT_SHOT_REVIEW` / `SHOT_REVIEW_THRESHOLD` / `FLASH_SLIVER_MAX_SECONDS` | `true` / `0.35` / `0.20` | `cut.py` 自动扫描 `edited_source.mp4` 写出 `shot_review.json`：对剪辑切点附近的 `<= 0.20s` 残帧（`flash_slivers`）做硬阻断，原片单镜头内部自带的闪白/雷电记为 `SOURCE_INTERNAL_FLASH_ADVISORY` |
| 留白节奏提示阈值 | `MAX_COLD_OPEN_SILENCE_SECONDS` / `MAX_DEAD_AIR_SECONDS` | `2.5` / `5.0` | `validate.py` 对开场静默超过 `2.5s`（`weak_cold_open_silence`）或连续无旁白无对白超过 `5.0s`（`excessive_dead_air`）输出非阻断提示（`warning`） |

## 3. 混音、响度、字幕与导出

| 配置项 | 环境变量 / CLI 参数 | 默认值 | 说明 |
|---|---|---|---|
| 原声闪避音量 | `IDLE_ORIG_VOLUME` / `SPEECH_DUCKING_VOLUME` / `ZONE_DUCKING_VOLUME` | `1.0` / `0.12` / `0.12` | 原声留白段恢复 `1.0`，旁白覆盖段压低至 `0.12`；半句中途抢断切入原声会触发 `unsafe_source_handoff` 阻断 |
| 外语源片闪避 | `FOREIGN_SOURCE_AUDIO` | 关闭 | 外语原片配中文解说时开启，旁白下原声自动压低至 `0.05` 避免双语互扰，留白段仍恢复 `1.0` |
| 闪避过渡与桥接 | `DUCK_FADE_SECONDS` / `DUCK_BRIDGE_SECONDS` | `0.3` / `1.5` | 音量渐变时长与句间桥接阈值：相邻旁白间隔短于 `1.5s` 时保持压低，避免句间原声忽大忽小 |
| 片尾音频淡出 | `ENDING_AUDIO_FADE_MS` | `350` | 毫秒；片尾原声与 BGM 平滑淡出时长（`0` 关闭） |
| 背景配乐 | `BGM_PATH` / `BGM_VOLUME` / `BGM_DUCKING_VOLUME` | 关闭 / `0.18` / `0.10` | 循环铺底配乐路径及常规/旁白压低音量 |
| 终混响度控制 | `FINAL_LOUDNORM` / `TARGET_LUFS` / `LOUDNESS_LIMITER_MAX_DB` | `true` / `-14` / `6` | 两遍线性等增益响度归一（不改变动态比例），配合超采样真峰值限制器守住 `-1.0 dBTP` 上限，结果记入 `assembly_qc.json` |
| 字幕烧录与样式 | `BURN_SUBTITLES` / `--burn-subtitles` / `--no-burn-subtitles` / `SUBTITLE_ORIGINAL_IN_GAPS` / `SUBTITLE_FONT_SIZE` / `SUBTITLE_MARGIN_V` / `SUBTITLE_MAX_CHARS` / `SUBTITLE_OUTLINE` / `SUBTITLE_FONT_NAME` / `SUBTITLE_FONT_FILE` | `true` / `true` / `42` / `48` / `18` / `2.5` / 系统默认黑体 / — | 旁白默认暖金（`#FFE08A`），`「原声台词」` 为纯白（`#FFFFFF`）；指定字体文件时 `SUBTITLE_FONT_NAME` 需与字体族名一致 |
| 原片字幕遮罩 | `MASK_SOURCE_SUBTITLES` / `SOURCE_SUBTITLE_MASK_RATIO` / `SOURCE_SUBTITLE_MASK_TIMING` / `SUBTITLE_MASK_OPACITY` / `SUBTITLE_MASK_PADDING` / `--subtitle-y-top` + `--subtitle-y-bot` | 关闭 / `0.14` / `narration` / `0.6` / `4` / 关闭 | 开启遮罩且烧录字幕时生效；默认仅旁白区间半透明遮罩（`narration`，不透明度 `0.6`），设 `SOURCE_SUBTITLE_MASK_TIMING=all` 可全片遮罩；成对传入实测 `[top, bot)` 像素行会自动开启 `opacity=1.0` 的不透明像素带遮罩（默认仍仅在旁白区间生效，若需全片遮盖硬字幕并启用 `original_subtitles.json` 再加设 `SOURCE_SUBTITLE_MASK_TIMING=all`；要求近方形像素 SAR 偏差 `<= 2%` 且底部对齐 `SUBTITLE_ALIGNMENT` 为 `1/2/3`） |
| 视频编码参数 | `OUTPUT_CRF` / `OUTPUT_PRESET` / `OUTPUT_MAX_HEIGHT` / `FORCE_VIDEO_REENCODE` | `18` / `veryfast` / `0` / `false` | H.264 `yuv420p` 重编码质量、速度预设、最大输出高度（`0` 保持原分辨率）与无滤镜时是否强制重编码 |
| 素材库目录 | `VIDEO_RECAP_MATERIAL_LIBRARY_DIR` / `--material-library-dir` | — | 跨集理解缓存与五维资源库根目录（配合 `--save-materials` / `--use-materials` 或 `library.py` 使用） |
| 剪映草稿导出 | `--export-jianying` / `JIANYING_DRAFT_DIR` / `JIANYING_BUNDLE_MEDIA` / `--jianying-no-bundle-media` | 关闭 / `work_dir` / 开启 | 从后端无关的 `timeline.json` 导出剪映草稿，默认打包媒体到 `Resources/local/*` 保证草稿可迁移 |
| 源视频引用 | `--source-video` | — | 仅 `assemble.py` 单阶段入口参数（`recap.py` 自动传递），让 `cut` 模式的 `timeline.json` 与剪映草稿直接指向源片区间 |

各阶段完整命令行选项可通过 `python3 scripts/<stage>.py --help` 查看。
