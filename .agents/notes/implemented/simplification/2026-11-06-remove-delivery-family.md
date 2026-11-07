# Agent Note: 移除声音定稿交付路线

Status: implemented

取代 [已采用声音家族留在 adoption/ 子包](../architecture/2026-09-21-adoption-family-stays-in-skill-as-subpackage.md) 关于保留这一族的决定，以及 [删除 pair_media / compose_foreground](2026-10-02-drop-pair-media-compose-foreground.md) 里关于 `adoption/av_clock.py` 和“换画面走 `adopted-packet-copy`”的部分。

其中保留 `narration_input_binding.json` 的决定已被 [移除 narration_input_binding 产物](2026-11-06-remove-narration-input-binding.md) 翻转。

## Problem

“声音已定稿，只改画面或包装”这条路线占着 `--audio-mode adopted-packet-copy`、recap 与 assemble 上的采用三件套、`source_score.py`、`subtitle_track.json` 显式字幕轨和整个 `adoption/` 子包，约两千多行代码和四篇 references。owner 决定不再维护它，v0.6.2 是需要这条路线的人的退路。

这一族还长进了默认路径：`narration_binding` 和 `strict_publish` 用一个 `active` 标志同时处理严格和默认两种状态，所以每次默认渲染都要调用 adoption 代码，而这些代码在默认路径上几乎什么都不做。`assemble_video` 因此多出大量只为被删路线存在的分支。

## Decision

删除：

- `--audio-mode adopted-packet-copy`；recap 的 `--tts-meta`、`--narration-adoption`、`--audio-mix-adoption` 与 `_run_local_adoption`、`validate_local_adoption`；assemble 的后两个参数。assemble 的 `--tts-meta` 是默认路径的普通输入，保留。
- `source_score.py`、`subtitles/track.py`、`subtitles/track_binding.py`、整个 `adoption/` 子包，以及 `narration-adoption.md`、`explicit-audio-mix.md`、`source-score.md`、`subtitle-track.md`。
- `assembly_qc.json`、`assembly_manifest.json`、`timeline.json` 里默认路径上恒为空的键（`adopted_audio`、`audio_mix_binding`、`packet_copy`、`adopted_gain`、`output_start_sample`、`output_end_sample`、`packet_frozen`）。

默认路径还要用的部分搬到顶层：

- `scripts/narration_input_binding.py`：`prepare_binding`、`finalize_binding`、`record_of`、`binding_record`、`current_record`。每次解说运行都走到它，所以不进子包。
- `artifacts.write_json_atomic`，以及 `media._require_audio_stream`（`ffprobe -select_streams a:0 -show_entries stream=index`，保留原有报错文字；后来 `--audio-stream-index` 移除，固定探测 `a:0`，见 [[2026-11-06-remove-audio-stream-index]]）。

保留的产物与防线：

- `narration_input_binding.json` 内容不变，包括恒为 `UNADOPTED` 的 `identity_status`，`assembly_qc` 的同名字段也不变，外部读取方不受影响。
- `seal_render_inputs` 在默认路径上是空操作（`active` 为假时直接返回），记录里的 `placed` 也恒为 `null`，所以它从未防过“混音引用旧 WAV”。真正的防线是由当前输入重建 `_placed_*.wav` 和 `_placed_audio_matches_timeline`（`timeline_audio_mismatch`），两者都保留。`production-boundaries.md` 那条旧 WAV 反例继续成立。

旧输入明确失败，不改走其他路线：

- 墓碑沿用 `recap_cli._edit_mode` 的写法：`--audio-mode` 用 `type=_audio_mode`，被删参数用隐藏的 `_RemovedFlag`。`_RemovedFlag` 也拦住从旧 manifest 回放的 `argv`。没有 `AUDIO_MODE` 环境变量，不需要环境墓碑。
- `recap_source.reject_removed_delivery_workdir` 和 `assembly_contract.reject_removed_delivery_inputs` 在开工前检查工作目录，单视频和多视频都查：`subtitle_track.json`、`subtitle_track_validation.json`、`audio_mix_binding.json`、`.narration_input_snapshots/`、`.explicit_audio_mix/`、`BOUND_TO_ADOPTION` 的 binding，以及记录了被删模式的 manifest 或 QC。recap 另查 `recap_run_manifest.json` 的 `audio.local_adoption` 与 `local_adoption_run`。JSON 损坏不算标记，与 dub 墓碑一致。两边清单重复是有意的，每个 skill 自包含。

系列功能全部保留。`TEMPLATE_STATUSES`、模板的 `adoption` 记录、`changed_since_adoption` 是模板生命周期，与这条路线同名但无关，不动。`views.js` 的 `AUDIO_LABEL` 保留 `adopted-packet-copy`，因为 dashboard 只读，还要显示 v0.6.2 跑出来的旧运行。唯一删掉的是 `project_binding.py` 里“冻结音轨不能绑定 BGM”的检查，它的条件已不可能成立。

owner 在没有向这条路线的提交者（同事）确认的情况下决定移除，这一步不会再做。

## Alternatives considered

- **保留这一族。** 最强的理由：它是唯一能保证音频逐包不变的路线。系列片定稿后常常只改包装或画面，已通过听审的声音不应多过一代有损编码，也不应被响度和采样率处理改动；逐包核验还给“声音确实没动”提供机械证据，这是 source-mix 给不了的。`production-boundaries.md` 原本把“声音被重新编码”列为必须保留的反例。不采用：owner 判断这条路线的维护成本高于它的使用量，它的默认路径纠缠也让每次改合成都要带着严格分支；真正需要的人可以留在 v0.6.2。
- **保留 `seal_render_inputs` 作为防旧 WAV 的检查。** 最强的理由：`production-boundaries.md` 点名要防旧 WAV，函数名看上去正是做这件事。不采用：跟踪显示它在默认路径上立即返回，留着只会让读者以为有这层防线。真正的防线已保留。
- **不做工作目录标记检查，只拦参数。** 代码更少。不采用：遗留的 `audio_mix_binding.json` 此前会把默认渲染的设置标成 `explicit_adopted_full_sound`，文件被静默忽略则续跑结果与用户预期不同；`subtitle_track.json` 被忽略则字幕会静默换来源。
- **把 `frozen_audio.probe_audio_packets` 搬到顶层继续做 source-mix 的音频流检查。** 最强的理由：报错面完全不变。不采用：source-mix 只需要知道流存在，长片上逐包探测很慢，也要多带约 50 行只为被删路线写的代码。
- **QC、manifest、timeline 里恒空的键原样保留。** 最强的理由：输出逐字节不变，外部读取方一个都不用改。不采用：留着只是让被删路线的名字继续出现在每次运行的产物里；`narration_input_binding.json` 与 `assembly_qc` 的同名字段是读取方真正在用的，所以保留，恒空键则删除并记入 `Changed`。

## Consequences

- 收益：`assemble_video` 明显变短，默认路径不再调用 adoption 代码；少一个子包和四篇 references；`SKILL.md` 的路线表少一行。15 个场景与移除前对比：ffmpeg 参数、解码后的音视频帧、文件清单都一致，JSON 只差上面列出的恒空键。
- 代价：只换画面或包装的重渲没有路线能让音频保持原样，多过一代 AAC（192 kbps、48 kHz），失去音频包身份、原采样率与时间基、AAC 起始和尾部的包、原样响度以及逐包核验。最接近的设置是 `--audio-mode source-mix`、`IDLE_ORIG_VOLUME=1.0`、`FINAL_LOUDNORM=0`（限幅器仍会运行）；逐包不变只能用 v0.6.2。
- 代价：依赖严格路线的调用方必须停在 v0.6.2，其旧 `work_dir` 和命令都不能续跑。读 QC、manifest、timeline 里被删键的外部脚本要改。
- 代价：解说模式遇到 `subtitle_track.json` 现在在 TTS 前就报错，比之前早；这是收紧，已记入 `Changed`。
- 留下两份墓碑代码和一个名字过时的字段（`identity_status: "UNADOPTED"`、`tempo_policy: null`、`tempo_source`），要长期留着：前者防静默改走其他路线，后者不打断外部读取方。
- source-mix 不再报告畸形音频包和缺 `time_base` 的错误：新的探测只确认音频流存在，这两类错误只对逐包比较才有意义。
