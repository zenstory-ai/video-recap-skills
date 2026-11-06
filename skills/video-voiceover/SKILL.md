---
name: video-voiceover
user-invocable: false
description: 把带时间戳的 narration.json 合成为中文解说音频。使用 MiMo TTS（mimo-v2.5-tts）或 Fish Audio（s2.1-pro-free）或显式配置的通用 IndexTTS HTTP 服务逐段生成语音， 按时间窗动态适配语速并处理响度；输入输出时间线上的旁白，产出 tts_segments 与 tts_meta.json。 外发说明：每段旁白文字会发给所选 TTS 服务（MiMo / Fish Audio / 用户自托管的 IndexTTS），--voice-ref 的参考音频会发给 MiMo。 另含实验性的英译中 dub 路径：只在显式选择 dub 模式并传 --confirm-voice-rights 时运行，会把源视频音频发到 MiMo ASR， 并以原说话人的声音为参考经 MiMo voiceclone 克隆配音；只可用于用户有权使用、且说话人同意被克隆声音的内容。 触发词：配音、语音合成、TTS、解说配音、 voiceover、text to speech、旁白配音。
---

## 1. 定位

本技能读取带时间戳的旁白稿 `narration.json`，逐段合成解说语音并适配到对应的时间窗口，同时输出供下游合成使用的放置元数据 `tts_meta.json`。默认使用 MiMo TTS（`mimo-v2.5-tts`），也可显式切换为 Fish Audio（默认模型 `s2.1-pro-free`）或自托管 IndexTTS 服务。

## 2. 远程服务与数据外发

本技能根据所选的配音或 dub 路径发起网络请求。下表中的“朗读正文”（`spoken_text`）指 `narration.json` 各段去掉排版符号与括号舞台提示后、实际送交语音合成的纯文字。

| 路径 | 服务与凭据 | 外发内容 |
|---|---|---|
| 默认解说配音（`--tts-provider auto\|mimo-tts`） | MiMo chat 接口 `<MIMO_TTS_API_URL 或 MIMO_API_URL>/chat/completions`，使用 `MIMO_TTS_API_KEY` 或 `MIMO_API_KEY`，模型 `mimo-v2.5-tts` | 每段朗读正文、由该段 `emotion` 与时间窗算出的自然语言语气/语速提示、内置音色名（默认 `冰糖`）。`auto` 模式下若未配 MiMo 密钥但配置了 `FISH_API_KEY`，会自动改走 Fish Audio |
| 解说声音克隆（`--voice-ref <audio>` / `VOICE_REF`） | 同上，模型切换为 `mimo-v2.5-tts-voiceclone` | 除上述文本与提示外，每次请求还会附带参考音频（预处理为 24 kHz 单声道 WAV、最长截取 30 秒）的 base64 编码 |
| Fish Audio（`--tts-provider fish-audio`） | `FISH_TTS_API_URL`（默认 `https://api.fish.audio/v1/tts`），使用 `FISH_API_KEY` | 每段朗读正文、数值语速、音色 ID `FISH_TTS_REFERENCE_ID`；不外发任何本地音频 |
| 自托管 IndexTTS（`--tts-provider index-tts`） | 用户自行部署、由 `INDEX_TTS_ENDPOINT` 指定的 HTTP(S) 服务 | `{"voice": INDEX_TTS_VOICE, "text": 朗读正文}`；不外发任何本地音频 |
| 实验性英译中 dub（见 §8） | MiMo ASR（`MIMO_API_URL`、`MIMO_API_KEY`、`mimo-v2.5-asr`）与 MiMo voiceclone（TTS 接口与凭据、`mimo-v2.5-tts-voiceclone`） | 准备阶段将源视频整条音轨按 6 秒分窗发给 ASR；渲染阶段将每句中文译文连同从源音轨截取的约 10 秒原说话人干声一并发给 voiceclone |

`--voice-ref` 与 dub 都会把真人声音发往 MiMo 进行音色克隆，**仅限用户有权使用该音频、且声音本人已明确同意克隆时使用**。

## 3. 环境要求

```bash
export MIMO_API_KEY=***  # 也可单独配置仅供配音使用的 MIMO_TTS_API_KEY

# 或改用 Fish Audio
export TTS_PROVIDER=fish-audio
export FISH_API_KEY=***
export FISH_TTS_REFERENCE_ID=<voice-model-id>  # 可选；覆盖默认的“娱乐扒妹”音色

# 或显式启用自托管 index-tts 服务（配置说明见 references/index-tts.md）
export TTS_PROVIDER=index-tts
```

以下 `scripts/...` 路径均相对于本技能目录；若从仓库根目录启动，请补齐本技能的目录前缀。

## 4. 输入契约

默认读取 `work_dir/narration.json`。每段必须包含 `start`、`end` 与 `narration`，可选字段包括 `pause_after_ms` 和 `overlaps_speech`。所有时间戳一律表示音频在**最终成片时间线上的秒数**。

剪辑式解说（`cut` 模式）遵循先剪画面、后写旁白的顺序：`narration.json` 本身就是对照剪后成片时间线直接编写的，不存在从原片时间映射过来的第二份底稿。

## 5. 运行命令

```bash
python3 scripts/voiceover.py --work-dir <work_dir> --narration <narration.json> \
  [--tts-provider auto|mimo-tts|fish-audio|index-tts] \
  [--mimo-voice 冰糖 | --voice-ref <reference-audio>] \
  [--preserve-approved-text] [--allow-partial-tts]
```

单独调用且省略 `--narration` 时，默认读取 `work_dir/narration.json`；`--narration` 仅用于指定其他路径下的同格式旁白稿。

## 6. 输出契约

- `tts_segments/*.wav`：每段旁白各自生成的语音文件。
- `tts_meta.json`：汇总 `segments`、`engine`、`voice`（实际调用的供应商、模型、音色或参考音频）与 `narration`，每段记录 `audio_path`、起止时间、`pause_after_ms` 及混音放置参数。
- 全部合成成功时写入 `partial: false` 与 `failures: []`。
- 传入 `--allow-partial-tts` 允许跳过失败段落时，写入 `partial: true` 与 `failures: [{index, start, end, text, error}]`，把缺失语音显式暴露给下游。
- `--preserve-approved-text` 用于锁定已定稿文本：每段同时保留原始书面文本 `authored_text` 与剥离舞台提示后的朗读正文 `spoken_text`。一旦生成的完整语音超出该段时间窗与累计提速上限，命令会直接报错退出，并打印超窗段落的序号、原稿、朗读正文、语音时长和窗口上限，不写出成功的 `tts_meta.json`。开启该保护后，任何段落合成失败（含接口报错）都不能靠 `--allow-partial-tts` 降级放行；仅含 `[停顿]` 等舞台标记、剥离后无字可读的空段落同样按必需段报错处理。

## 7. 运行规则

- **分段内容缓存**：合成出的音频按内容缓存在 `tts_segments/cache/`。缓存键由朗读正文、实际发给供应商的语气/语速参数（MiMo 为那句自然语言指令，仅当语速偏移达到 `≥+6%` 或 `≤-3%` 时才会改变措辞；Fish Audio 为数值 `speed`；IndexTTS 无段级语速参数）以及全局 TTS 配置共同决定，不包含段序号和时间窗起止点。因此，调整段落顺序导致名义语速从 `+5%` 变为 `-2%` 时，MiMo 不会重复请求；只有文本或发给接口的指令真正变化时才会重新合成。`narr_NNN.wav` 是指向缓存文件的硬链接（文件系统不支持时退化为文件副本），一旦缓存 WAV 的 `{size, mtime_ns}` 改变即判失效；旧版 `narr_NNN.wav.cache.json` 不再读取。
- **定稿保护缓存隔离**：开启 `--preserve-approved-text` 时，缓存键会额外纳入定稿保护策略名与原始文本 `authored_text`，与默认模式（`report-over-budget-v2`）互不混用；只有在同一严格策略下、`spoken_text` 完全一致且 WAV 非空的缓存才允许复用，且复用时仍会按当前时间窗重新核算时长，放不下照样报错。
- **元数据安全替换**：严格模式下每次启动合成前，都会先把已有的 `tts_meta.json` 按时间戳归档到 `tts_meta.history/`，防止本轮失败后残留的旧文件冒充成功；新生成的元数据通过同目录临时文件原子替换。
- **供应商选择**：`auto` 优先走已配置密钥的 MiMo，未配 MiMo 但配置了 `FISH_API_KEY` 时自动改走 Fish Audio；需要严格锁定供应商时请显式传入 `--tts-provider`。自托管 IndexTTS 只能通过 `--tts-provider index-tts` 或 `TTS_PROVIDER=index-tts` 显式启用，`auto` 绝不会自动兜底到 IndexTTS；接口协议与缓存细节见 `references/index-tts.md`。
- **Fish Audio 说明**：直接返回 WAV 音频，默认使用“娱乐扒妹”音色（`5653cea4ac83480aaf2bf45406556185`），可通过 `FISH_TTS_REFERENCE_ID` 覆盖。模型、音色 ID、接口地址、响度归一化参数或计算出的语速变化时会触发重合成；Fish 接口不接收音高与情绪字段，修改这两项不会使缓存失效。免费模型无 SLA 保障，受官方 Fair Use 与免费额度策略约束。
- **声音克隆（`--voice-ref`）**：仅支持 `full` 与 `cut` 模式的解说克隆，自动切换到 `mimo-v2.5-tts-voiceclone`，并在真正需要发起新合成请求时才对参考音频做一次性规范化预处理；参考音频的路径、`{size, mtime_ns}` 或预处理版本变化都会使旧缓存失效。
- **dub 语音缓存**：dub 模式下 voiceclone 生成的原始 WAV 同样按模型、提示词、译文和参考音频的 `{size, mtime_ns}` 缓存，重跑时命中的句子不再重复请求或计费，并在 `dub_manifest.json` 中逐句标记 `tts_cache=hit|miss`。
- **防 TTS 幻读截断**：若某段合成出的语音时长，超过了按最低语速 `TTS_MIN_SPEECH_RATE`（默认每秒 2.5 个汉字，英文单词按每词 1.5 字折算，数字每个计 1 字，`%` 计 3 字）读完全文再加上标点停顿与首尾留白的时间上限，工具会判定模型发生了幻读（读完正文后自己多编了一截话或怪声），自动丢弃重来，绝不写入缓存或交给下游合成；重试耗尽后该段报错退出，并把最后一次被拒的音频保存在 `tts_segments/narr_NNN.rejected.wav`（dub 模式保存在 `dub_tts/line_NNN_raw.rejected.wav`）供人工试听排查。将 `TTS_MIN_SPEECH_RATE` 设为 `0` 可关闭此项检查。
- **并发与重试参数**：可通过 `TTS_WORKERS`、`TTS_TIMEOUT`、`TTS_RETRIES`、`ALLOW_PARTIAL_TTS` 调整并发数、单次请求超时、重试次数与部分成功策略。

## 8. 实验性 dub 配音（英译中、克隆原声）

`dub.py` 是独立于解说配音的实验性功能：把英文原片对白翻译成中文，并用原片说话人的克隆音色替换整条人声轨。`voiceover.py` 不会调用它。

- **触发方式**：仅当用户明确要求做英译中配音时，通过编排入口传入 `--edit-mode dub --confirm-voice-rights` 启动；编排入口会将确认参数原样透传给 `dub.py` 的准备与渲染两个阶段。
- **授权确认门禁**：`dub.py` 的准备阶段与渲染阶段都必须显式传入 `--confirm-voice-rights`；漏传时程序会在抽取音频或发出任何网络请求前直接退出，并打印即将外发的数据说明。编排入口在 `dub` 模式下同样强制要求该参数，而在非 `dub` 模式下传该参数则会报错拒绝。
- **外发数据明细**：准备阶段把源视频音轨按 6 秒分窗发往 MiMo ASR（`mimo-v2.5-asr`）转写英文台词；渲染阶段把每句中文译文连同从源音轨第 2 秒起截取的约 10 秒原说话人音频（`dub_reference.wav`）一并发往 MiMo voiceclone（`mimo-v2.5-tts-voiceclone`）。
- **权利与知情同意**：仅限用户拥有视频与音频的合法使用权、且原说话人本人已同意其声音被克隆时使用。Agent 动手前必须先向用户确认这两项前提，收到用户明确肯定后才能加上 `--confirm-voice-rights`；无法确认时严禁运行 dub，更不得用它伪造他人发言。
- **译文确定性门禁**：渲染阶段在发起语音克隆请求前会先生成 `dub_lint.json`，一旦发现空译文、时间重叠或越界台词立即中止，避免白白消耗克隆请求。
- **本地产物**：`dub_source.wav`、`dub_transcript.json`、`dub_brief.md`、`dub_reference.wav`、Agent 编写的 `dub_script.json`、`dub_lint.json`、`dub_tts/`、`dub_manifest.json` 以及最终成片 `dub_<name>.mp4` 全部保存在 `work_dir` 内。

## 9. 能力边界

- 默认模式下若某段语音略微超出时间窗，本阶段会保留完整音频并记录日志，交由下游合成阶段在安全倍率内温和提速；若提速后仍塞不下，下游会在渲染前以 `no_safe_fit` 阻断。若开启 `--preserve-approved-text`，超窗段落会在本阶段直接报错拦截。
- 本技能只生成旁白音频，不混流、不压低原片音轨、不烧录字幕，也不分析视频画面或自行挑选时间戳。
- Fish Audio 与 IndexTTS 路径均不支持本地参考音频参数 `--voice-ref`；Fish Audio 请使用已在云端建好的 `FISH_TTS_REFERENCE_ID`。
