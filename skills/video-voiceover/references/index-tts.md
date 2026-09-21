# 自托管 IndexTTS 端点

`--tts-provider index-tts` 或 `TTS_PROVIDER=index-tts` 显式选择自托管的 index-tts 协议服务（JSON→WAV）。
`auto` 永不兜底选择它。

## 配置

```bash
export TTS_PROVIDER=index-tts
export INDEX_TTS_ENDPOINT=http://127.0.0.1:<port>/tts
export INDEX_TTS_VOICE=<authorized-voice-name>
export INDEX_TTS_CACHE_REVISION=<operator-deployment-revision>  # 可选
```

- 缺 `INDEX_TTS_ENDPOINT` 或 `INDEX_TTS_VOICE` 时在缓存/请求前失败。
- endpoint 只接受无 userinfo/query/fragment 的 HTTP(S) URL，且请求禁止重定向，避免把批准文本转发至其他主机。
- 不接受 `--voice-ref` / `--mimo-voice`。

## 请求契约

请求体固定为 `{"voice": voice, "text": spoken_text}`。当前段 schema 的可选 `emotion`
以及额外 style、非默认 rate/pitch 控制会显式失败，不会静默忽略。原始合成使用 provider 默认速度，
不按标点、长度或位置自动计算 rate；这不等于下游放置阶段的端到端 tempo 锁。

## Receipt 与缓存

receipt 记录“请求的 voice”、返回原始 WAV SHA-256 与处理后 WAV SHA-256。它只证明
请求参数和收到的字节，不是该声线的声学验证，也不代表已做人耳听审或音色身份验证。
若归一化改变字节且未另存 raw WAV，receipt 明确标记 raw 不可由哈希重建；缓存命中必须复用
匹配 sidecar 中的 receipt，不能现场补造。

操作员可在部署或声线实现变化后提升 `INDEX_TTS_CACHE_REVISION` 使旧缓存失效；未配置时不声称
已记录或可重建服务端模型版本。
