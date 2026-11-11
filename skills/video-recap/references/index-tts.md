# 自托管 IndexTTS 端点

通过 `--tts-provider index-tts` 或 `TTS_PROVIDER=index-tts` 显式启用用户自行部署的 IndexTTS 协议服务（输入 JSON，返回 WAV）。`auto` 模式绝不会自动兜底到该服务。

## 环境变量配置

```bash
export TTS_PROVIDER=index-tts
export INDEX_TTS_ENDPOINT=http://127.0.0.1:<port>/tts
export INDEX_TTS_VOICE=<authorized-voice-name>
```

- 缺少 `INDEX_TTS_ENDPOINT` 或 `INDEX_TTS_VOICE` 时，程序会在查缓存与发请求前直接报错退出。
- `INDEX_TTS_ENDPOINT` 只接受不带用户名密码（userinfo）、查询参数（query）或锚点（fragment）的 `http://` 或 `https://` 地址，且请求全程禁止 HTTP 重定向，防止已定稿的旁白文本被意外转发到第三方主机。
- 该路径不接受 `--voice-ref` 或 `--mimo-voice`。

## 请求体契约

请求体固定为 `{"voice": voice, "text": spoken_text}`。若 `narration.json` 段落里带有可选的 `emotion` 字段、额外风格参数或非默认的语速/音高设置，程序会直接报错退出，绝不静默丢弃这些参数继续合成。服务端按自身默认语速生成原始音频，不根据标点、字数或段落位置自动调整语速；但这只代表原始合成阶段不调速，下游合成阶段仍会按 `NARRATION_SPEED` 与可用时间窗做有界提速适配。

## 回执与缓存复用

每段合成后都会在 `provider_receipt` 中记录本次调用的 `provider` 与请求的音色名 `requested_voice`。回执仅证明向哪个音色名发起了请求，不能替代声纹核验与人工听审；服务端本身不向本地工作目录写入任何文件。

命中本地缓存需同时满足三个条件：`tts_segments/cache/` 下存在按朗读正文与配置（含 `index_tts_voice`）算出的缓存项、缓存 WAV 的 `{size, mtime_ns}` 未变、同目录 sidecar 回执记录的音色与当前请求的 `INDEX_TTS_VOICE` 一致。命中缓存时直接沿用 sidecar 里保存的原始回执，绝不临时伪造新回执。由于客户端无法感知自托管服务端背后的模型权重或部署变更，一旦在服务端更换了声线实现，请手动清空 `tts_segments/cache/` 强制重新合成。
