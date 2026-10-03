# Agent Note: 段音频时长超出合理上限视为 TTS 幻读

Status: implemented

## Problem

0.6.1 候选的真实回归（single-full，Homebrew ffmpeg 那一轮）里，MiMo TTS 对 35 字的「范闲替丫鬟出了这口气，一巴掌把管家扇倒在地，脸上还落下一片古怪的红斑。」返回了 18.08 秒音频；同一句在另一轮是 8.64 秒，重新生成是 8.2 秒。MiMo ASR 听回来是原句后面又接了一段编出来的话。

- `voiceover._synthesize_segment` 不检查内容，只在超出原始预算时记一句"超出预算"（[[2026-10-02-voiceover-no-default-truncation]] 的设计：放不下交给 assemble 判），随后把 WAV 写进段缓存。
- assemble 报 `no_safe_fit`（needed_tempo_factor 1.85），错误让人缩稿或挪时间窗，真正原因不可见。
- 按文档的续跑命令重跑，缓存键（文本、设置、WAV 身份）全都匹配，复用同一个坏 WAV，于是每次都阻断。
- 时间窗够宽时，编出来的话会放进成片，没有任何检查发现。

## Decision

- video-voiceover `tts_audio.max_plausible_tts_seconds(text)`：按 `TTS_MIN_SPEECH_RATE`（`lib.CONFIG["tts_min_speech_rate"]`，默认 2.5 单位/秒；CJK 字与数字各 1 单位，拉丁词 1.5）读完全文，加 1.5 秒首尾静音、每个停顿标点 0.4 秒、每个 `……` / `...` / `——` 0.8 秒。设为 0 时不检查。`implausible_tts_duration(text, duration)` 超过上限时返回写明时长、上限、字数和关闭方法的中文原因。
- `voiceover._run_tts_engine` 在时长有效性检查之后调用它，超限按一次失败处理：清理 WAV 与 sidecar、按 `TTS_RETRIES` 重试；用尽后抛 `合成失败: <原因>`，和其他供应商失败一样进 `failures`（默认中止运行，`--allow-partial-tts` 时缺这一段）。超限音频永远不写缓存，也到不了 assemble。
- `voiceover._reuse_tts_segment_cache` 对 sidecar 里的 `spoken_text` / `audio_duration` 做同一检查，超限就当缓存未命中并记日志，旧版本缓存下的坏 WAV 在重跑时自动重新合成，不需要手动删文件。
- 阈值依据：回归里的幻读段是上限的 1.17 倍；本机各次真实运行留下的约 100 个 MiMo 段里，最慢的忠实朗读是上限的 0.86 倍（短句带停顿时实际语速低到 1.8–2.4 字/秒，所以不能只按字数除以常规语速）。
- 测试：`tests/voiceover/test_tts_hallucination.py` 钉住真实句子的上下界、短句/省略号/英文不误判、0 关闭、幻读后重试成功只留下好音频、持续幻读时整段失败且 `tts_segments/` 为空、旧缓存的坏 WAV 重跑时重新合成且新缓存下次命中。

## Alternatives considered

- **合成后用 MiMo ASR 回听比对文本。** 最强理由：能抓到时长没有明显变长的幻读，也能抓到读错字。没采用：每段多一次付费调用和一次网络往返；人名、生僻字的 ASR 误差会让比对本身需要一个容差，误判时同样要停跑；这次的故障形态（原句之后追加整段）用时长就能稳定区分。如果以后出现时长正常的幻读，再在这道检查之上加 ASR。
- **按“预期时长的 2 倍”判断（回归报告的建议）。** 最强理由：简单。没采用：按写稿用的 3.9 字/秒估算，这句的预期是 9 秒，2 倍是 18 秒，18.08 秒只多出 0.08 秒；而短句带停顿的忠实朗读本身就可能是估算的 1.5–2 倍。显式的最慢语速加停顿/静音余量两侧都留出了约 15% 的空间。
- **只在 assemble 报 `no_safe_fit` 时提示“可能是 TTS 幻读”。** 最强理由：不碰 voiceover。没采用：坏 WAV 仍在缓存里，重跑仍卡住；时间窗够宽时幻读内容仍然会被交付。

## Consequences

- 收益：回归里那种幻读在 voiceover 当场重试掉，不进缓存；重试都失败时报错直接说是幻读；已经缓存的坏 WAV 重跑即自愈。
- 代价：这是一道新的失败条件。语速特别慢的音色（自定义参考音频、Fish / index-tts 的慢速音色）或大段留白的朗读可能被误判，整段失败并中止运行，需要调低 `TTS_MIN_SPEECH_RATE` 或设 0。阈值只用约 100 个 MiMo 段标定过，Fish 与 index-tts 没有实测数据；旧的 edge-tts 运行里有一段（24 字 12.05 秒）会超限，edge-tts 已不再受支持。
- 只抓“变长”的幻读：替换了内容但时长正常的错误读法仍然发现不了。
