---
name: video-voiceover
user-invocable: false
description: >
 把带时间戳的 narration.json 合成为中文解说音频。使用 MiMo TTS（mimo-v2.5-tts）或
 Fish Audio（s2.1-pro-free）或显式配置的通用 IndexTTS HTTP 服务逐段生成语音，
 按时间窗动态适配语速并处理响度；输入输出时间线上的旁白，产出 tts_segments 与 tts_meta.json。
 触发词：配音、语音合成、TTS、解说配音、
 voiceover、text to speech、旁白配音。
---

## 1. 定位

本技能读取带时间戳的旁白稿，为每一段生成独立音频，并把语音适配到对应时间窗，随后记录下游合成所需的放置元数据。
默认引擎是 MiMo TTS（`mimo-v2.5-tts`）；也可显式选择 Fish Audio（默认模型 `s2.1-pro-free`）。

## 2. 环境要求

```bash
export MIMO_API_KEY=***  # 也可使用仅供 TTS 的 MIMO_TTS_API_KEY

# 或改用 Fish Audio TTS
export TTS_PROVIDER=fish-audio
export FISH_API_KEY=***
export FISH_TTS_REFERENCE_ID=<voice-model-id>  # 可选；覆盖内置“娱乐扒妹”音色

# 或显式选择自托管 index-tts 端点，配置见 references/index-tts.md
export TTS_PROVIDER=index-tts
```

下面的 `scripts/...` 均相对于本技能目录。若执行器从仓库根目录启动，请给脚本路径加上本技能的绝对目录。

## 3. 输入契约

默认输入为 `work_dir/narration.json`。每段必须包含 `start`、`end` 与 `narration`，可选字段包括
`pause_after_ms` 和 `overlaps_speech`。时间统一表示音频最终放置的**输出时间线秒数**。

cut 流程先剪后配：`narration.json` 本身就是按剪后成片的输出时间写的，不存在另一份映射稿。

## 4. 运行命令

```bash
python3 scripts/voiceover.py --work-dir <work_dir> [--narration <path>] \
  [--tts-provider auto|mimo-tts|fish-audio|index-tts] \
  [--mimo-voice 冰糖 | --voice-ref <reference-audio>] \
  [--preserve-approved-text]
```

## 5. 输出契约

- `tts_segments/*.wav`：每段旁白对应一个音频文件。
- `tts_meta.json`：包含 `segments`、`engine`、`voice`（实际使用的 provider、模型、音色或参考音频）与 `narration`。每段记录 `audio_path`、时间、
  `pause_after_ms` 和放置字段。
- 干净运行写入 `partial: false` 与 `failures: []`。
- 使用 `--allow-partial-tts` 跳过失败段时，写入 `partial: true` 和
  `failures: [{index,start,end,text,error}]`，让缺失语音保持可见。
- `--preserve-approved-text` 是显式的批准稿保护策略。每段保留原始 `authored_text`
  证据；TTS 实际读取的 `spoken_text` 只经过既有的格式/舞台提示清理。若完整语音超过时间窗及
  累计语速预算，命令失败并报告段序号、原稿、实读文本、语音时长和窗口证据，不写成功的
  `tts_meta.json`。严格模式下任何必需段失败（包括供应商失败）都不能被
  `--allow-partial-tts` 降级为可交付的部分成功；异常记录标为 `required: true` 并带策略 ID。
  仅含 `[停顿]` 等清理标记、清理后无实读文本的作者段也属于必需段错误。

## 6. 运行规则

- 修改旁白或合成参数后只重生成受影响分段；严格模式失败时不留成功的 `tts_meta.json`。
- `auto` 优先使用已配置的 MiMo，MiMo key 缺失且设置了 `FISH_API_KEY` 时使用 Fish Audio；需要可复现的 provider 选择时显式传 `--tts-provider`。
- Fish Audio 直接请求 WAV；默认使用“娱乐扒妹”音色（`5653cea4ac83480aaf2bf45406556185`），`FISH_TTS_REFERENCE_ID` 可覆盖。当前免费模型无 SLA，受 Fair Use 和官方免费期限约束。
- `--voice-ref` 仅用于 full/cut 解说克隆，切换到 `mimo-v2.5-tts-voiceclone`。仅在获得授权后使用，参考音频会发送到 MiMo。
- `TTS_WORKERS`、`TTS_TIMEOUT`、`TTS_RETRIES`、`ALLOW_PARTIAL_TTS` 用于调整并发、超时、重试与部分成功策略。
- dub 模式有独立的确定性门禁：`dub_lint.json` 会在语音克隆前阻止空行、重叠或越界译文；
  `dub_review.json` 用于记录忠实度、语气、时长和平台适配复核。可通过
  `dub.py --stage lint|review` 或 `dub.py --print-schema` 单独调用。dub 语音克隆按参考音频与台词缓存。

## 7. 能力边界

- 超窗时默认在句界自动缩稿并在 `spoken_text/truncated` 留痕；批准稿加 `--preserve-approved-text`。
- 不混流、不压低原声、不渲染字幕。
- 不分析视频，也不选择时间点；只为输入稿件中的既定分段配音。
- Fish Audio 不接受本地 `--voice-ref`，用已创建的 `FISH_TTS_REFERENCE_ID` 选择音色。
