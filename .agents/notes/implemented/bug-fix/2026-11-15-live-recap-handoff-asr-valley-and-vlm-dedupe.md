# Agent Note: 实片验证后的声画让口、ASR 谷值分窗与 VLM 去重修复

Status: implemented

## Problem

在《盘龙》5 分钟实片剪辑出片过程中，暴露出五处代码与简报层面的真实问题：

1. **混音保持窗口误吞紧随解说之后的原片短台词**：`audio_mix._apply_source_sentence_handoffs` 在旁白结束后的 `3.0s`（`SOURCE_HANDOFF_MAX_HOLD_SECONDS`）内寻找 `restore_anchor` 以避免半句残尾外露。但当旁白在角色开口**之前**已经落定（旁白收口处在静音窗/声学停顿内，或与后续锚点之间隔着实测静音），若紧随其后的原片台词较短（如 1.4 秒的“救我！”），其句末锚点仍落在 `run["end"] + 3.0s` 内，混音器会把整句新开口的短台词继续压低在 `0.12` 音量直至句末。此外，`cut` 模式的 `speech_boundary_anchors_output.json` 此前未重映射 `acoustic_pauses`，导致成片混音缺少短声学停顿证据。
2. **固定 15 秒网格硬切音频劈断台词并拉宽粗粒度边界**：`asr.py` 过去按固定 `asr_segment_seconds`（默认 15s）等距切分 `audio.wav`，实片中在 `180.0s` 与 `195.0s` 两次把整句台词从词中劈成两截（“救……”/“救我！”、“会”/“将不可估量”），且片尾或转场的纯静音边缘也被整窗送入远端 ASR。
3. **极短场景回退同帧造成重复 VLM 请求**：在 `1.5fps` 抽帧下，相邻极短闪切场景（如 `239.32–239.40s`、`239.40–239.44s`）均回退命中同一张最近邻帧 `frame_NNNNN.jpg`，`vlm.py` 对完全相同的帧元组重复发起多次 VLM 请求。
4. **源片内嵌硬字幕与片尾演职员表信号未在简报汇总提示**：国漫/短剧普遍带底部硬字幕且含片尾曲与预告。VLM 已识别出画面中的硬字幕与演职员表，但 `agent_narration_brief.md` 未汇总提示专有名词同音字互校、烧录字幕叠字遮罩/避让与封面选帧避字，也未标出片尾演职员表区间参考。
5. **macOS Homebrew 默认 `ffmpeg` 缺 `libass` 时未自动探测 `ffmpeg-full`**：Homebrew 主线 `ffmpeg` 已移除 `libass`，带 `libass` 的 `ffmpeg-full` 为 keg-only（位于 `<prefix>/opt/ffmpeg-full/bin`），不手动改 `PATH` 就无法烧录硬字幕；而若本机 `ffmpeg-full` 因依赖库升级损坏，盲目优先又会导致进程崩溃。

## Decision

1. **混音释放不跨实测静音吞新句**：
   - `briefing/timeline.py` 在单源与多源 `cut` 模式下将 `speech_boundary_anchors.json` 的 `acoustic_pauses` 一并重映射写入 `speech_boundary_anchors_output.json`。
   - `audio_mix._apply_source_sentence_handoffs` 过滤 `restore_anchor` 候选：若 `run["end"]` 本身已处在实测静音/声学停顿（`quiet_windows` 或 `acoustic_pauses`）内且该静音在 `anchor["pause_start"] - 0.05s` 前结束，或 `run["end"]` 与 `anchor["pause_start"] - 0.10s` 之间隔着 `>= 0.35s` 的实测静音段，说明该锚点属于静音之后新开口的台词而非当前残句尾巴，不再向后延长压低，改为按 `bounded_release` 平滑恢复原声。
2. **纯标准库声学谷值分窗与首尾纯静音裁剪**：
   - `asr.py` 基于 Python 标准库 `wave` + `array` 读取 16kHz 单声道 16-bit PCM `audio.wav` 的 `50ms` 帧峰值包络（3 帧 `150ms` 平滑），在 `[cur + 0.6 * segment_length, cur + 1.2 * segment_length]` 范围内寻找能量最低的声学谷值切分长音频，替代盲目等距硬切；同时裁剪各分窗首尾 `<= -35 dBFS` 的纯静音边缘（保留 `0.15s` 余量）并跳过全窗纯静音段（全片均静音时保留单个兜底窗口以维持 `EMPTY_UNKNOWN` 语义）。若音频非标准 PCM（如单测合成桩）则自动回退等距网格。
   - `understanding_cache._asr_cache_payload` 的 `schema_version` 升至 `2`。
3. **保留原始镜头切点，按抽帧签名去重 VLM 请求**：
   - 遵循 `simplification/2026-11-10-scripts-for-hands-and-floor-agent-for-craft.md` 不在 `detect_scenes` 源头抹除客观切点；在 `vlm.py` 中预先计算各待分析场景选中的帧路径元组 `tuple(scene_frames)`，命中完全相同帧元组的场景只发一次 VLM 请求，结果按各自 `scene_id` / `start` / `end` 复用，保持 `len(vlm_analysis) == len(scenes)` 与续传缓存契约不变。
4. **简报自动汇总内嵌硬字幕与片尾演职员表提示**：
   - `briefing/builder.py` 扫描 `brief_scenes` 与全片尾部场景：当 `>= 3` 处画面检出内嵌硬字幕（排除片尾滚动字幕词条）时，输出专有名词对照画面校准、烧录字幕遮罩/错位防叠字、封面选帧避字三条提示；当非剪后第 2 步且后段检出 `>= 3` 个片尾/演职员表/预告场景时，标出对应源片时间范围供选段剔除。
5. **CLI 入口安全探测 Homebrew `ffmpeg-full`**：
   - `lib.py` 新增 `prefer_homebrew_ffmpeg_full()` 与 `broken_homebrew_ffmpeg_full_path()`：仅当 `PATH` 解析到的 `ffmpeg` 是 `/opt/homebrew/bin/ffmpeg` 或 `/usr/local/bin/ffmpeg` 且缺少 `subtitles` 滤镜时，检查同前缀下 `opt/ffmpeg-full/bin/ffmpeg`；实测 `-filters` 退出码为 `0` 且包含 `subtitles` 时将其前置到 `PATH` 并清空滤镜文件参数探测缓存，若缺失或动态库损坏则保持原 `PATH` 不变。
   - 在 `recap_runner.py`、`assemble.py`、`doctor.py` 的 CLI 入口调用该探测；当磁盘存在损坏的 `ffmpeg-full` 时，`doctor.py` 在缺 `libass` 警告中附带重装提示。`recap.py --doctor` 遵循既有单入口决策（`simplification/2026-11-06-one-entry-per-capability.md`）不恢复。

## Alternatives considered

- **用固定分贝阈值（如 `-28 dBFS`）的 `ffmpeg silencedetect` 切分 ASR 窗口。** 最强理由：复用现有命令行模式，无需解析 WAV 字节。没采用：带配乐和打斗音效的国漫/影视原片在台词气口处底噪常在 `-15 ~ -22 dBFS`，固定阈值在整段打斗戏里找不到任何静音切点，仍会退化成硬切词中；而在 `50ms` 包络上做局部能量谷值搜索即使压着配乐也能稳定落在句间气口，且纯标准库扫描 5 分钟音频仅需数毫秒、不产生额外子进程。
- **在 `detect_scenes` 源头把短于 `0.5s` 的场景并入相邻场景，或把多个短镜头拼成宫格合并进单次 VLM 调用。** 最强理由：能减少 `scenes.json` 与 `vlm_analysis.json` 条目数并进一步压缩 `0.5s~1.2s` 快速对切戏的请求量。没采用：`simplification/2026-11-10-scripts-for-hands-and-floor-agent-for-craft.md` 已明确不在 `detect_scenes` 抹除原始切点证据，而多镜拼图会改变逐场景对应关系并增加帧时间戳错配风险；按 `tuple(scene_frames)` 去重既完整保留原始切点，又零损耗消除全部同帧重复调用。
- **恢复 `recap.py --doctor` 转发参数。** 最强理由：减少用户记不住独立 `doctor.py` 入口的摩擦。没采用：`simplification/2026-11-06-one-entry-per-capability.md` 已明确决议每项能力只留一个入口且将 `video` 固定为必填位置参数，不推翻既有收敛结论。

## Consequences

- **收益**：旁白为后续原声短台词留白时不再被 `3.0s` 保持窗口误压低；ASR 分窗贴合真实句间气口并跳过纯静音段，消除等距硬切词中问题并收窄句末锚点误差上界；消除闪切同帧重复 VLM 请求；国漫/短剧内嵌硬字幕与片尾区间在简报中直接可见；macOS 安装了健康 `ffmpeg-full` 的环境开箱即可烧录硬字幕，损坏时安全回退并在 `doctor.py` 给出明确修复命令。
- **代价**：`asr` 阶段缓存 `schema_version` 升至 `2`，升级后首次重跑未锁定 `--brief-only` 的旧工作目录会按谷值分窗重新转写一次 ASR。
