# Agent Note: 移除 --preserve-approved-text

Status: implemented

取代 [[2026-10-02-voiceover-no-default-truncation]] 里“`--preserve-approved-text` 不变”的部分（默认路径的决定不变），[[2026-10-02-strict-full-mode-no-silent-rewrite]] 与 [[2026-10-03-tts-content-addressed-cache]] 的相关事实已就地改。

## Problem

这个 flag 的核心承诺是“不改稿”，而默认路径现在已经不改稿：validate 不再改写，voiceover 不再截短。两条路径用同一个 `raw_budget` 判断超窗，区别只是严格模式抛错、默认模式记日志并交给 assemble。它还剩四个窄行为：在 TTS 阶段而不是 assemble 的渲染前阻断处失败；拒绝 `--allow-partial-tts`；清理后为空的段报错；把旧 `tts_meta.json` 归档到 `tts_meta.history/`。这些带来一个 125 行的模块、缓存键分支、错误类和两个只有 voiceover 自己写的 `tts_meta.json` 字段（`authored_text`、`authored_text_policy`）。它来自刚移除的定稿交付这一脉，owner 决定一并移除。

## Decision

- 删除 recap 与 voiceover 的 `--preserve-approved-text`（含 `recap_source._TTS_OPTIONS` 里的条目）和 `approved_text_policy.py`。`write_json_atomically` 搬到 `tts_cache.py`：`tts_cache` 本来就要用它，放进 `voiceover.py` 会形成导入环。
- `_check_segment_window` 只记日志。合成和复用两条路径都不再因超窗失败，超窗统一交给 assemble：有界提速放得下就放，放不下在视频编码前以 `no_safe_fit` 阻断。
- `tts_meta.json` 各段去掉 `authored_text` 与 `authored_text_policy`，没有其他技能读它们。默认路径的 TTS 缓存键本来就不含策略，所以已有的缓存音频继续命中（baseline 与当前实现的键逐字节相同）。
- 严格策略写下的缓存条目键不同，不再被命中，也没有人清理，只占磁盘。旧的 `tts_meta.history/` 同样没有读者。
- `docs/production-boundaries.md` 的原则保留，改写成默认路径的事实：文本装不下就回到创作，不让 TTS 删改或强行变速。

## Alternatives considered

- **让默认路径也在 TTS 阶段对超窗报错**：最强理由是更早失败，少一次到 assemble 的路程。没采用：超出 `raw_budget` 但有界提速放得下的段本来就能出片，这样会拒绝今天能交付的默认运行。
- **保留成无操作的 flag**：最强理由是旧脚本和旧 `work_dir` 打印的续跑命令不报错。没采用：无操作 flag 会让人以为它还有保护作用，[[2026-10-02-strict-full-mode-no-silent-rewrite]] 对 validate 上的同名 flag 已经做过同样取舍。
- **保留 `tts_meta.history` 归档作为默认行为**：最强理由是失败后不会留下看似成功的旧元数据。没采用：默认路径从来没有归档，recap 在 voiceover 失败时直接停止；这是独立的另一个决定，不该借这次移除引入。

## Consequences

- **收益**：voiceover 只有一条文本路径；少一个模块、两个 flag、一个目录概念和缓存键分支。
- **代价**：超出原始预算但有界提速放得下的段现在会通过，之前严格模式会失败；清理后为空的段被跳过而不是报错；`--allow-partial-tts` 不再被任何策略拒绝。严格失败时写明原稿与窗口证据的那份结构化错误没有了，改看 assemble 的 `no_safe_fit` 与 `needed_tempo_factor`。需要严格路径用 v0.6.2。
