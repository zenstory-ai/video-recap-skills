# 自托管 IndexTTS 端点

`--tts-provider index-tts` 或 `TTS_PROVIDER=index-tts` 显式选择自托管的 index-tts 协议服务（JSON→WAV）。
`auto` 永不兜底选择它。

## 配置

```bash
export TTS_PROVIDER=index-tts
export INDEX_TTS_ENDPOINT=http://127.0.0.1:<port>/tts
export INDEX_TTS_VOICE=<authorized-voice-name>
```

- 缺 `INDEX_TTS_ENDPOINT` 或 `INDEX_TTS_VOICE` 时在缓存/请求前失败。
- endpoint 只接受无 userinfo/query/fragment 的 HTTP(S) URL，且请求禁止重定向，避免把批准文本转发至其他主机。
- 不接受 `--voice-ref` / `--mimo-voice`。

## 请求契约

请求体固定为 `{"voice": voice, "text": spoken_text}`。当前段 schema 的可选 `emotion`
以及额外 style、非默认 rate/pitch 控制会显式失败，不会静默忽略。原始合成使用 provider 默认速度，
不按标点、长度或位置自动计算 rate；这不等于下游放置阶段的端到端 tempo 锁。

## Receipt 与缓存

每段的 `provider_receipt` 只记录 `provider` 与“请求的 voice”（`requested_voice`）。它说明请求参数，
不是该声线的声学验证，也不代表已做人耳听审或音色身份验证。endpoint 不写入任何文件。

缓存复用条件：sidecar 中的文本与设置（含 `index_tts_voice`）与当前相等、WAV 的 `size`/`mtime_ns` 未变，
且 sidecar 的 receipt 对应当前 voice；缓存命中复用 sidecar 中的 receipt，不能现场补造。
服务端模型或部署变化不会被自动察觉；更换声线实现后请删除 `tts_segments/*.cache.json` 强制重合成。
