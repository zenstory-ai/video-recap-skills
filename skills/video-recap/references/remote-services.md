# 远程调用披露与数据隐私说明

本技能默认本地优先；所有可能产生网络请求的路径均列于下表，未显式配置或指定云端引擎时绝不发起外发请求。

## 数据外发与联网清单

| 脚本 | 触发条件 | 发出的数据 / 行为 | 接收方 | 如何彻底关闭 |
|---|---|---|---|---|
| `transcribe.py` | 传入 `--provider api`，或 `--provider auto` 在本地无字幕且无本机 Whisper 时检测到 `ASR_API_URL` 与 `ASR_API_KEY` | 从源视频抽取的音频流（及可选语种参数） | 用户配置的 `ASR_API_URL` 兼容端点 | 不设置 `ASR_API_URL` / `ASR_API_KEY`，或显式传 `--provider srt` / `embedded` / `whisper` |
| `tts.py` | `--engine` 为 `fish`（默认模型 **`s2.1-pro-free`**，免费额度下 `s1`/`s2-pro` 会返 HTTP 402；读 `FISH_API_KEY`/`FISH_AUDIO_API_KEY`，可选 `FISH_VOICE_ID`/`FISH_MODEL`/`FISH_TTS_URL`，默认 `temperature=0.9`/`top_p=0.9`，支持 `--whole-article` 整篇合成、内联 `[chuckle]`/`[sigh]` 标签与 `spoken_text`）、`openai`（`OPENAI_API_KEY`）或自托管 `indextts`（`INDEX_TTS_ENDPOINT`）；`--sync-subtitles` 仅本地同步字幕 | 旁白朗读文本（单句或 `--whole-article` 整篇）、语速/采样参数与音色 ID；仅显式克隆时附参考音频 | 对应云端 TTS API 或用户自托管端点 | 传 `--engine local`（严格离线调用本机 `say` / `espeak-ng` / `espeak`）或仅做无旁白剪辑 |
| `packaging.py`（仅 `remotion-render`） | 显式运行 Remotion 渲染且工程目录尚无本地缓存 `node_modules` | 仅 `npm install` 拉取 Remotion 依赖包；实际视频渲染在本地无头完成，**不上传任何视频、台词或项目文件** | npm 包仓库（仅首次安装依赖） | 复用本地已缓存的 `node_modules`（`--node-modules`），或改用纯离线静态命令 `packaging.py apply` |

## 严格离线（绝不联网）的脚本

`probe.py`、`frames.py`、`render.py`、`check.py`、`export_jianying.py`、`dashboard.py`（仅监听 `127.0.0.1` 只读回环）与 `library.py` 全程离线，绝不上传任何素材、帧图或文稿。

## 密钥与授权安全约定

- 仅通过环境变量读取 API 密钥（`ASR_API_KEY`、`OPENAI_API_KEY`、`FISH_API_KEY` / `FISH_AUDIO_API_KEY`），严禁写入 `timeline.json`、`BRIEF.md` 或任何工作区文件。
- 错误日志统一经脱敏处理，自动隐藏 `data:<mime>;base64,...` 载荷与 `sk-` / `tp-` 形态密钥。
- 使用真人参考音频克隆音色前须获声音所有者明确授权，并在 `timeline.json` 的 `meta.voice.consent` 中如实登记。
