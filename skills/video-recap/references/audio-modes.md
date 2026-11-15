# 声音模式与音频路由（`--audio-mode`）

`recap.py` 与 `assemble.py` 默认使用 `--audio-mode narration`（解说配音模式），可通过 `--audio-mode source-mix` 切换为不带解说的原声混音成片。源视频始终读取第一条音频流（FFmpeg `0:a:0`）；若多音轨素材的目标音轨不在第一轨，请先在外部重封装选轨后再输入。

## 1. 两种声音模式对比

| 音频模式 | 解说校验 (`validate.py`) | TTS 配音 (`voiceover.py`) | 合成输入与混音行为 (`assemble.py`) |
|---|---|---|---|
| `narration`（默认） | 执行 | 执行 | 读取非空 `tts_meta.json`，铺放旁白轨，对原声做动态闪避（压低至 `SPEECH_DUCKING_VOLUME=0.12` / `ZONE_DUCKING_VOLUME=0.12`，在有意留出的原声对白段回升至 `IDLE_ORIG_VOLUME=1.0`），混入可选纯器乐 `BGM_PATH`；若源视频无音轨则自动生成 48 kHz 立体声静音底轨混音 |
| `source-mix` | 跳过 | 跳过 | 不读 `tts_meta.json` 也不生成旁白；按 `IDLE_ORIG_VOLUME` 保留原声 `0:a:0`（若源视频缺少 `a:0` 音轨则直接报错退出），混入可选 `BGM_PATH`（声明的 `BGM_PATH` 文件不存在时直接报错；`narration` 模式则告警跳过） |

- **解说配乐纪律**：`narration` 模式下若提供 `BGM_PATH`，应选用无歌词的纯器乐且贴合场景情绪；若源片已带无法避开的大音量人声插曲，请勿再加外部配乐。
- **全流程组合路由**（与 `--edit-mode` 组合的五条合法路线详见 `SKILL.md` §1）：
  - `--edit-mode full --audio-mode source-mix`：直接进入 `assemble.py` 合成整段输入视频，不跑视频理解与 ASR。
  - `--edit-mode cut --audio-mode source-mix`：正常跑视频理解并暂停等待 `clip_plan.json`，剪出 `edited_source.mp4` 后直接合成原声成片，不再暂停等待 `narration.json`。

## 2. 工作目录绑定与参数互斥

- **清单隔离**：`recap_run_manifest.json` 单独记录 `audio: {mode, selected_stream_index}`（`selected_stream_index` 固定为 `0`）。切换 `--audio-mode` 必须换用新的 `--work-dir`；缺少 `audio` 字段的旧清单或已含 `narration.json` 的未绑定目录均不可直接改跑 `source-mix`，防止旧 TTS 产物串入原声成片。
- **显式 TTS 参数拦截**：在 `source-mix` 模式下显式传入 `--tts-provider`、`--mimo-tts-voice`、`--voice-ref`、`--allow-partial-tts` 会直接报错拒绝；环境变量中的全局 TTS 默认值因不运行配音而自动忽略，不会写入续跑命令。
- **包装图层兼容**：`packaging_plan.json` 与 `packaging_layers.json` 在两种音频模式下均正常生效。

## 3. 音频响度、淡出与重编码说明

两种模式均属于后处理混音交付，不会原样拷贝（stream copy）源音频数据包：

- 默认开启 `FINAL_LOUDNORM=true`：将终混响度统一归一至 `-14 LUFS`，并通过真峰值限制与编码器复验守住 `<= -1.0 dBTP` 上限。
- 最终音轨统一重编码为 48 kHz、192 kbps AAC，按视频总时长裁齐，并在片尾对原声与 BGM 施加平滑淡出（`ENDING_AUDIO_FADE_MS`，默认 `350ms`），实际执行的处理步骤记录在 `assembly_qc.json`。
- 如需最接近原声等增益重渲画面的设置，可使用 `--audio-mode source-mix` 并设 `IDLE_ORIG_VOLUME=1.0`、`ENDING_AUDIO_FADE_MS=0`、`FINAL_LOUDNORM=0`（峰值限制器与 AAC 编码仍会运行）。
- `timeline.json` 在 `source-mix` 下将当前输入记录为单条完整片段（`base_gain` 取 `IDLE_ORIG_VOLUME`）。
- 已移除的实验性外语翻译配音模式（`--edit-mode dub`）不再支持；若需旧路径请检出历史版本 `v0.6.2`。
