# Agent Note: TTS 段缓存按内容寻址，不再按段序号

Status: implemented

## Problem

voiceover 的段缓存是每段一个 `narr_NNN.wav.cache.json` sidecar，键里有 `segment_index`、`start`、`end`、`pause_after_ms`。assemble 报 `no_safe_fit` 时建议删掉或改短一段，删掉第 k 段后，k 之后每一段的序号都减一，缓存全部不命中，没改过的段被重新请求 TTS 并重新计费（Fish Audio 按次收费，MiMo 也要等）。插入一段同理。时间窗也在键里，只挪动时间不改文字同样重合成，而时间窗根本不影响合成出来的音频。

## Decision

- 新模块 video-voiceover `scripts/tts_cache.py`：条目在 `tts_segments/cache/<sha256 前 32 位>.wav` + `.json`。键是 `voiceover._tts_segment_cache_inputs(engine, seg, text, rate, pitch)`：引擎、实读文本、rate、pitch、emotion、`tts_settings_payload`（供应商/模型/声线/参考音频身份/归一化等），严格模式另加策略名与原稿；不含段序号与时间窗。sidecar 记 `settings`（整份键，取出后再比一次相等）、缓存 WAV 的 `{size, mtime_ns}`、`spoken_text`、`audio_duration`、`tts_rate_offset`、`normalization`、`provider_receipt`。自己写的 sidecar 解析不了照旧报 `TTS 缓存 sidecar 损坏`。
- 下游读的仍是 `narr_NNN.wav`（`tts_meta.json` 的 `audio_path` 不变）。命中时 `tts_cache.materialize` 把它换成指向条目的硬链接（同目录临时名 + `os.replace`；`os.link` 失败时 `shutil.copy2` 复制）；已是同一文件（samefile）就不动，其余一律重新链接或复制：`{size, mtime_ns}` 分不清同一时间戳刻度内写入的两段等长音频，按身份跳过曾让删段后的槽位留着上一段的声音。新合成的段在归一化之后 `tts_cache.store` 把它链进缓存（临时名取身份再 `os.replace`，并发写同一键时 sidecar 与 WAV 不一致只会安全地不命中）。
- 因为 `narr_NNN.wav` 可能与缓存共用 inode，`_synthesize_segment` 在调用引擎前先删掉它，供应商永远写新文件，不会原地改写别段的缓存音频。有人原地改写了 `narr_NNN.wav` 时缓存 WAV 的身份随之改变，下次不命中、重新合成；只是替换了这个文件则从缓存恢复。
- 语速/音高仍按位置计算（首段 +5%，末段 -5%，倒数第二段 -2%）并留在键里：删段让某段变成倒数第二段时，这一段会重新合成，其余不动。
- 时间窗不在键里，所以严格模式（`--preserve-approved-text`）在复用时按当前时间窗重新做 `enforce_duration`：`_check_segment_window` 同时服务合成与复用两条路径；探测阶段抛出的 `ApprovedTextDurationError` 记进 `failures`，与合成阶段的冲突走同一个 `_finish_tts` 汇总后抛出。不在严格模式时，复用也记同一条“超出预算”日志。
- 不迁移旧缓存：旧的 `narr_NNN.wav.cache.json` 不再读取，`_cleanup_partial_tts_outputs`（`tts_cache.legacy_sidecar_path`）在该段重新合成时删掉它。0.6.0 的缓存键含 `tts_dynamic_params`，本版本已删掉这个键（见 [[2026-10-02-drop-unreachable-ducking-modes-and-tts-dynamic-switch]]），旧缓存反正命中不了。
- 测试：`tests/voiceover/test_tts_content_cache.py` 覆盖删中间段零调用且各 `narr_NNN.wav` 内容对上新序号、插入+改写只调用两段、位置改变语速的段会重合成、挪时间窗零调用、严格模式复用到放不下的窗口不调用即失败、`narr_NNN.wav` 与缓存同 inode、替换文件从缓存恢复而原地改写触发重合成、等长且同 mtime 的另一段音频不会被当成已就位；`test_pure_voiceover.py` 钉住任何形态的旧 sidecar 都只是一次未命中并被删除。

## Alternatives considered

- **保留每段 sidecar，只从键里去掉序号，未命中时去别的 `narr_*.wav` 里找同键的音频复制过来。** 最强理由：不新增目录和模块，测试与文档改动最小。没采用：探测时边找边覆盖 `narr_NNN.wav`，插入一段时第 k+1 段要的是旧第 k 段的文件，而旧第 k+1 段的文件已被覆盖、第 k+2 段就找不到了；要做对得先把所有文件快照到临时名，复杂度不比独立的内容寻址目录低，而且每段文件仍兼任“缓存”和“交付视图”两个角色。
- **`narr_NNN.wav` 一律复制，不用硬链接。** 最强理由：没有共享 inode，原地改写不会波及缓存。没采用：每次复用都要多一份 WAV（10 分钟旁白约 29 MB），而唯一的写入方（`_run_tts_engine`）已先删再写，`_synthesize_segment` 也显式先删；硬链接不可用的文件系统仍退回复制。
- **把语速/音高从位置规则里拿掉，让删段永远零重合成。** 最强理由：缓存完全与位置无关。没采用：首尾段的语速变化是有意的节奏设计，不属于这次修复；它们只影响首段和末两段，删段最多多合成一两段。
- **把旧 sidecar 去掉位置键后迁入新缓存。** 最强理由：升级后第一次运行不用重合成。没采用（写过又删了）：0.6.0 的键含本版本删掉的 `tts_dynamic_params`，迁移进来的键永远对不上当前输入，只对未发布的中间版本有用，不值得多一段迁移代码和测试。
- **保留时间窗在键里，只去掉序号。** 最强理由：严格模式不用在复用路径上重新检查。没采用：时间窗不改变音频，挪窗口就重新计费是同一类浪费；复用时重新检查只是几次算术。

## Consequences

- 收益：按 `no_safe_fit` 建议删段、插段、挪时间窗后重跑，只合成真正变了的段；同一句话在不同位置（语速相同）也共用一份音频。
- 代价：`tts_segments/cache/` 里被删段的条目不会自动清理，长期反复改稿的 work_dir 会留下一些不再引用的 WAV（每条通常几十到几百 KB）；需要时整目录删掉即可，下次只是重新合成。
- 代价：升级后已有 work_dir 的第一次 voiceover 全部重新合成（与删 `tts_dynamic_params` 的那次失效是同一次）；降级回旧版本同样全部重合成。
- 代价：`narr_NNN.wav` 与缓存共用 inode，任何下游如果原地改写它会让该条缓存失效（不会交付错音频，只会多合成一次）；目前下游（assemble 的 `_adj` 等）都写新文件。
- index-tts 的“更换声线实现后删缓存”的说明改为删 `tts_segments/cache/`。
