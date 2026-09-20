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

# 或显式选择自托管 HTTP TTS 端点（index-tts 协议，JSON→WAV）；值只保留在本地环境
export TTS_PROVIDER=index-tts
export INDEX_TTS_ENDPOINT=http://127.0.0.1:<port>/tts
export INDEX_TTS_VOICE=<authorized-voice-name>
export INDEX_TTS_CACHE_REVISION=<operator-deployment-revision>  # 可选
```

下面的 `scripts/...` 均相对于本技能目录。若执行器从仓库根目录启动，请给脚本路径加上本技能的绝对目录。
脚本不从其他技能目录读取文件；外部输入仅限命令显式传入的稿件、音频、参数与 `work_dir` 产物。

## 3. 输入契约

默认输入为 `work_dir/narration.json`。每段必须包含 `start`、`end` 与 `narration`，可选字段包括
`pause_after_ms` 和 `overlaps_speech`。时间统一表示音频最终放置的**输出时间线秒数**。

cut 流程先剪后配：`narration.json` 本身就是按剪后成片的输出时间写的，不存在另一份映射稿。

## 4. 运行命令

```bash
python3 scripts/voiceover.py --work-dir <work_dir> --narration <narration.json> \
  [--tts-provider auto|mimo-tts|fish-audio|index-tts] \
  [--mimo-voice 冰糖 | --voice-ref <reference-audio>] \
  [--preserve-approved-text]
```

单独运行且省略 `--narration` 时，默认读取 `work_dir/narration.json`；`--narration` 只用于指定其他路径的同格式稿件。

## 5. 输出契约

- `tts_segments/*.wav`：每段旁白对应一个音频文件。
- `tts_meta.json`：包含 `segments`、`engine` 与 `narration`。每段记录 `audio_path`、时间、
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

- 重跑只复用内容与 TTS 设置均匹配的分段音频；修改旁白或合成参数后，只重生成受影响的 WAV。
- 批准稿保护策略属于缓存身份：严格模式不会命中旧的自动缩稿缓存；只有同一严格策略下、
  `spoken_text` 完整匹配且音频指纹有效的缓存才可离线复用。
- 严格 CLI 在本轮合成前把旧 `tts_meta.json` 按内容指纹归档至 `tts_meta.history/`，因此失败时
  当前路径不会继续冒充本轮成功；成功元数据通过同目录临时文件原子替换。
- `auto` 优先使用已配置的 MiMo，MiMo key 缺失且设置了 `FISH_API_KEY` 时使用 Fish Audio；需要可复现的 provider 选择时显式传 `--tts-provider`。
- 自托管 index-tts 端点只能由 `--tts-provider index-tts` 或 `TTS_PROVIDER=index-tts` 显式选择，`auto`
  永不兜底选择它；缺 `INDEX_TTS_ENDPOINT` 或 `INDEX_TTS_VOICE` 时在缓存/请求前失败。endpoint
  只接受无 userinfo/query/fragment 的 HTTP(S) URL，且请求禁止重定向，避免把批准文本转发至其他主机。
- IndexTTS 请求体固定为 `{"voice": voice, "text": spoken_text}`。当前段 schema 的可选 `emotion`
  以及额外 style、非默认 rate/pitch 控制会显式失败，不会静默忽略。原始合成使用 provider 默认速度，
  不按标点、长度或位置自动计算 rate；这不等于下游放置阶段的端到端 tempo 锁。
- IndexTTS receipt 记录“请求的 voice”、返回原始 WAV SHA-256 与处理后 WAV SHA-256。它只证明
  请求参数和收到的字节，不是该声线的声学验证。若归一化改变字节且未另存 raw WAV，receipt
  明确标记 raw 不可由哈希重建；缓存命中必须复用匹配 sidecar 中的 receipt，不能现场补造。
  操作员可在部署或声线实现变化后提升 `INDEX_TTS_CACHE_REVISION` 使旧缓存失效；未配置时不声称
  已记录或可重建服务端模型版本。
- Fish Audio 直接请求 WAV；默认使用“娱乐扒妹”音色（`5653cea4ac83480aaf2bf45406556185`），`FISH_TTS_REFERENCE_ID` 可覆盖。模型、音色 ID、API URL、动态语速或归一化设置变化时会重新生成缓存。当前免费模型无 SLA，受 Fair Use 和官方免费期限约束。
- `--voice-ref` 仅用于 full/cut 解说克隆，切换到 `mimo-v2.5-tts-voiceclone`。仅在确需新合成时惰性规范化一次；
- dub voiceclone 原始 WAV 也会用模型、提示、台词和参考音频指纹缓存；匹配重跑不再重复请求或计费，`dub_manifest.json` 逐行记录 `tts_cache=hit|miss`；
  参考音频内容或预处理指纹变化会使旧缓存失效。仅在获得授权后使用，参考音频会发送到 MiMo。
- `TTS_WORKERS`、`TTS_TIMEOUT`、`TTS_RETRIES`、`ALLOW_PARTIAL_TTS` 用于调整并发、超时、重试与部分成功策略。
- dub 模式有独立的确定性门禁：`dub_lint.json` 会在语音克隆前阻止空行、重叠或越界译文；
  `dub_review.json` 用于记录忠实度、语气、时长和平台适配复核。可通过
  `dub.py --stage lint|review` 或 `dub.py --print-schema` 单独调用。

## 7. 能力边界

- 默认兼容旧流程：超窗时仍可能在句界自动缩稿并在 `spoken_text/truncated` 留痕。
  对已批准、不可自动改写的文本必须显式使用 `--preserve-approved-text`；该策略只证明文本未被
  创意删改，不代表已完成直接听审、音色锁定或发音质量验收。
- 不混流、不压低原声、不渲染字幕。
- 不分析视频，也不选择时间点；只为输入稿件中的既定分段配音。
- Fish Audio 路径不接受本地 `--voice-ref`；使用已创建的 `FISH_TTS_REFERENCE_ID` 选择音色。
- IndexTTS 同样不接受 `--voice-ref`/`--mimo-voice`，也不声称已做人耳听审或音色身份验证。
