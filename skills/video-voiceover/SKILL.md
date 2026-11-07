---
name: video-voiceover
user-invocable: false
description: 把带时间戳的 narration.json 合成为中文解说音频。使用 MiMo TTS（mimo-v2.5-tts）或 Fish Audio（s2.1-pro-free）或显式配置的通用 IndexTTS HTTP 服务逐段生成语音， 按时间窗动态适配语速并处理响度；输入输出时间线上的旁白，产出 tts_segments 与 tts_meta.json。 外发说明：每段旁白文字会发给所选 TTS 服务（MiMo / Fish Audio / 用户自托管的 IndexTTS），--voice-ref 的参考音频会发给 MiMo。 触发词：配音、语音合成、TTS、解说配音、 voiceover、text to speech、旁白配音。
---

## 1. 定位

本技能读取带时间戳的旁白稿，为每一段生成独立音频，并把语音适配到对应时间窗，随后记录下游合成所需的放置元数据。
默认引擎是 MiMo TTS（`mimo-v2.5-tts`）；也可显式选择 Fish Audio（默认模型 `s2.1-pro-free`）。

## 2. 远程服务与数据外发

本技能按所选 TTS 路径联网。表中的实读文本指 `narration.json` 段落去掉格式与舞台提示后的文字。

| 路径 | 服务与凭据 | 发送内容 |
|------|------------|----------|
| 默认解说 TTS（`--tts-provider auto\|mimo-tts`） | MiMo chat 接口 `<MIMO_TTS_API_URL 或 MIMO_API_URL>/chat/completions`，`MIMO_TTS_API_KEY` 或 `MIMO_API_KEY`，模型 `mimo-v2.5-tts` | 每段实读文本、一句自然语言语气/语速指令（由段的 `emotion` 与时间窗算出）、内置音色名（默认 `冰糖`）；`auto` 下 MiMo key 缺失且设置了 `FISH_API_KEY` 时改走 Fish Audio 行 |
| 解说声音克隆（`--voice-ref <audio>` / `VOICE_REF`） | 同上，模型 `mimo-v2.5-tts-voiceclone` | 上述内容，外加参考音频（转成 24 kHz 单声道 WAV，最长 30 秒）的 base64，每段请求都带 |
| Fish Audio（`--tts-provider fish-audio`） | `FISH_TTS_API_URL`（默认 `https://api.fish.audio/v1/tts`），`FISH_API_KEY` | 每段实读文本、数值语速、音色 ID `FISH_TTS_REFERENCE_ID`；不发送本地音频 |
| 自托管 IndexTTS（`--tts-provider index-tts`） | 用户自己部署、由 `INDEX_TTS_ENDPOINT` 指定的 HTTP(S) 服务 | `{"voice": INDEX_TTS_VOICE, "text": 实读文本}`；不发送本地音频 |

怎样关闭：不运行本技能就不外发（走 recap 时用 `--audio-mode source-mix`）；要让文字只到自己的机器，选 `--tts-provider index-tts` 并把 `INDEX_TTS_ENDPOINT` 指向自托管服务。

`--voice-ref` 会把一个真实人物的声音发给 MiMo 用于克隆：只在用户有权使用该音频、且声音主人同意被克隆时使用。

## 3. 环境要求

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

## 4. 输入契约

默认输入为 `work_dir/narration.json`。每段必须包含 `start`、`end` 与 `narration`，可选字段包括
`pause_after_ms` 和 `overlaps_speech`。时间统一表示音频最终放置的**输出时间线秒数**。

cut 流程先剪后配：`narration.json` 本身就是按剪后成片的输出时间写的，不存在另一份映射稿。

## 5. 运行命令

```bash
python3 scripts/voiceover.py --work-dir <work_dir> --narration <narration.json> \
  [--tts-provider auto|mimo-tts|fish-audio|index-tts] \
  [--mimo-voice 冰糖 | --voice-ref <reference-audio>] \
  [--allow-partial-tts]
```

单独运行且省略 `--narration` 时，默认读取 `work_dir/narration.json`；`--narration` 只用于指定其他路径的同格式稿件。

## 6. 输出契约

- `tts_segments/*.wav`：每段旁白对应一个音频文件。
- `tts_meta.json`：包含 `segments`、`engine`、`voice`（实际使用的 provider、模型、音色或参考音频）与 `narration`。每段记录 `audio_path`、时间、
  `pause_after_ms` 和放置字段。
- 干净运行写入 `partial: false` 与 `failures: []`。
- 使用 `--allow-partial-tts` 跳过失败段时，写入 `partial: true` 和
  `failures: [{index,start,end,text,error}]`，让缺失语音保持可见。

## 7. 运行规则

- 分段音频按内容缓存在 `tts_segments/cache/`：键是实读文本、实际发给供应商的语气请求（MiMo 是那句自然语言指令，语速只在 ≥+6% 或 ≤-3% 时改变措辞；Fish Audio 是数值 speed；index-tts 没有段级控制）与 TTS 设置，不含段序号和时间窗；因此段位变化让名义语速从 +5% 变成 -2% 时，MiMo 不重新合成；
  缓存 WAV 的 `size`/`mtime_ns` 变了即失效。`narr_NNN.wav` 是指向缓存的硬链接（不支持时为副本），`tts_meta.json`
  照旧引用它。删掉、插入或挪动某段后，只重生成文本或发给供应商的请求变了的段（名义语速随首段、末两段的位置变化）。
- `tts_meta.json` 通过同目录临时文件原子替换；失败的运行不改动已有文件。
- `auto` 优先使用已配置的 MiMo，MiMo key 缺失且设置了 `FISH_API_KEY` 时使用 Fish Audio；需要可复现的 provider 选择时显式传 `--tts-provider`。
- 自托管 index-tts 端点只能由 `--tts-provider index-tts` 或 `TTS_PROVIDER=index-tts` 显式选择，`auto`
  永不兜底选择它。协议、请求体、receipt 语义与缓存失效规则见 `references/index-tts.md`。
- Fish Audio 直接请求 WAV；默认使用“娱乐扒妹”音色（`5653cea4ac83480aaf2bf45406556185`），`FISH_TTS_REFERENCE_ID` 可覆盖。模型、音色 ID、API URL、归一化设置或按内容计算出的语速变化时会重新生成缓存（Fish 不接收音高和情绪，它们变了不重新生成）。当前免费模型无 SLA，受 Fair Use 和官方免费期限约束。
- `--voice-ref` 仅用于 full/cut 解说克隆，切换到 `mimo-v2.5-tts-voiceclone`。仅在确需新合成时惰性规范化一次；
  参考音频的路径、`size`/`mtime_ns` 或预处理版本变化会使旧缓存失效。仅在获得授权后使用，参考音频会发送到 MiMo。
- 合成出的段音频比按 `TTS_MIN_SPEECH_RATE`（默认 2.5 字/秒，英文按每词 1.5 字）读完全文、再加停顿与首尾静音的上限还长时，
  视为 TTS 幻读（读完原稿后又编出一段话），按失败重试，不缓存也不交给 assemble；重试用尽则该段失败，报错写明时长与上限，
  最后一次被拒的音频留在 `tts_segments/narr_NNN.rejected.wav` 供试听。数字（半角/全角）逐个计 1 字，`%` 计 3 字（百分之）。
  旧版本缓存下的这类 WAV 在重跑时不再复用，会重新合成。设为 `0` 关闭这道检查。
- `TTS_WORKERS`、`TTS_TIMEOUT`、`TTS_RETRIES` 用于调整并发、超时与重试；`--allow-partial-tts` 允许部分段失败后继续。

## 8. 能力边界

- 超窗时保留原稿并记录日志；assemble 有界提速放不下则在渲染前以 `no_safe_fit` 阻断。
- 不混流、不压低原声、不渲染字幕。
- 不分析视频，也不选择时间点；只为输入稿件中的既定分段配音。
- Fish Audio 与 IndexTTS 路径都不接受本地 `--voice-ref`；前者用已创建的 `FISH_TTS_REFERENCE_ID` 选择音色。
