# Agent Note: assemble / voiceover 未文档化调参改成常量，删掉旧版旁白入场延迟

Status: implemented

## Problem

第二轮精简清单（`.omc/plans/2026-10-02-slim-candidates-round2.md` 第 21、24、29、34、35 条，PR-B1a）在 video-assemble 和 video-voiceover 里找到一批只有作者自己会碰、却要一直维护的旋钮和状态：

- 约 20 个音频 / 字幕环境变量（`FADE_MS`、`TARGET_TRUE_PEAK`、`TARGET_LRA`、`FINAL_LIMITER_PEAK`、`NARRATION_RUN_GAP_SECONDS` 等）除了 `env-inventory-v1.json` 以外没有任何文档提到。其中段落收紧三项不进 `assembly_settings`，改了会改变成片，manifest 却看不出来。
- `NARRATION_TIGHTEN=0` 关掉段落内收紧，退回按 slot 锚定放置。生产里没人走这条路，只有一个“回归保护”测试守着它。
- `NARRATION_DELAY_SECONDS` 默认 0。config-playbook 自己写着“只给旧稿用，隐藏延迟会把校验过的句界入场推回原声里”，唯一用途就是一个已记录的风险；`narration_tail_pad_seconds` 在 assemble 里只为这个延迟算上限。
- `lib.CONFIG["foreign_source_audio"]` 是只写不读的派生键，靠 `test_audio_policy_parity` 的 allowlist 才活着。
- `media._load_cut_timeline_plan` 在 `clip_plan.json` 比 `clip_plan_validated.json` 新时改读原始计划，再把原始 start/end 首尾相接成输出时间轴：没有 padding、没有吸附。可 `edited_source.mp4` 是按 validated 计划渲染的，所以 `timeline.json` 和重映射后的原声 / 用户字幕描述的是画面里没有的片段。understanding 的 brief 和 script 的评审早已把过期的 validated 计划当失败，[[2026-06-16-cut-first-narrate-second]] 也规定 cut 证据缺失或过期一律 fail closed。
- `dub.py` 的 `--asr-window` / `--ref-start` / `--ref-dur` 只出现在 `add_argument` 行，recap 调 dub 只传 `--video` / `--work-dir`。
- voiceover 把 `--voice-ref` 转码后的字节缓存在 CONFIG 的四个进程级键里，外加一把模块锁、一个 reset 函数、线程池前后的 lock / unlock，以及两个按 locked 标记分支的身份函数。但 `voice_ref` 在一次调用里是固定的。

## Decision

- **常量化。** assemble `lib.CONFIG` 的 `fade_ms`、`ducking_orig_volume`、`narration_cumulative_tempo_max` / `_hard_max`、`tts_segment_tempo_max`、`narration_run_gap_seconds`、`narration_tight_pause_seconds`、`narration_max_pull_seconds`、`target_true_peak`、`target_lra`、`final_limiter_peak`、`subtitle_margin_l` / `_r` 改成字面值。voiceover `lib.CONFIG` 的 `mimo_tts_style`、三个 tempo 上限、`tts_segment_normalize`、`tts_segment_target_rms_dbfs`、`tts_segment_peak_limit` 也改成字面值。数值都不变，所以 `test_audio_policy_parity` 照样成立，TTS 段缓存不失效。键名保留，测试仍可 `setitem`。这些环境变量从 `env-inventory-v1.json` 删除；SKILL.md 和 config-playbook 不再列 `DUCKING_ORIG_VOLUME`，playbook 只说兜底值固定为 0.3。
- **保留的旋钮。** `SUBTITLE_ORIGINAL_IN_GAPS` 保留：CHANGELOG 把它当作「」原声字幕的开关公布过，也没有 CLI 参数能替代。`NARRATION_SPEED`、各 `*_DUCKING_VOLUME`、`DUCK_*`、`BGM_*`、`FINAL_LOUDNORM`、`TARGET_LUFS`、`OUTPUT_*`、`FOREIGN_SOURCE_AUDIO` 和 project binding 写的字幕样式变量不动。voiceover 的 `MIMO_DISABLE_THINKING` 也保留：understanding 和 script 读同名变量，只在 voiceover 常量化会让同一个环境变量在三个 skill 里表现不一致。
- **段落收紧只有一条路。** `narration_tighten` 键和 `narration_audio._build_timed_narration` 里的 `tighten and` 分支删除。段落内一律紧贴上一句，段落起点一律严格用作者写的 `start`。
- **删除入场延迟。** `narration_delay_seconds` 和 assemble 侧的 `narration_tail_pad_seconds` 删除，`start_sample` 就是 `seg["start"]`。`assembly_manifest.json` 的 `assembly_settings.narration_timing` 不再写 `delay_seconds` / `tail_pad_seconds`，`examples/guohuo-60s/assembly_manifest.json` 同步去掉这两个键。understanding / script 用来算字数预算的 `narration_tail_pad_seconds` 不受影响。
- **删除派生键。** `foreign_source_audio` 键和 parity 测试的 `allowed_unread` 删除；`FOREIGN_SOURCE_AUDIO` 仍然选择两个 ducking 默认值。
- **时间轴溯源只读 validated 计划。** `media._load_cut_timeline_plan` 合并进 `_plan_clip_spans`，规则如下：
  - 没有 `clip_plan_validated.json` 时返回 None，按整片模式走恒等映射，散落的 `clip_plan.json` 不再参与重映射。
  - validated 计划早于 `clip_plan.json` 时抛 `ValueError`（“clip_plan_validated.json 已过期；请先重新剪辑再组装”）。`assemble_video` 在参数检查后、渲染前先调用一次，过期计划不会白跑一遍渲染。
  - spans 直接取每个 clip 的 `source_start` / `source_end` / `output_start` / `output_end`，不再兼容 `start` / `end` 别名或首尾相接的推算。
- **dub CLI。** `--asr-window` / `--ref-start` / `--ref-dur` 删除。`stage_prepare(video, work, asr_window=6.0, ref_start=2.0, ref_dur=10.0)` 和 `stage_render(video, work, ref_start=2.0, ref_dur=10.0)` 把原默认值改成关键字默认值，测试照旧直接传参。
- **voice-ref 只在一次调用内有效。**
  - `synthesize_tts` 先探测缓存，有 miss 且引擎是 MiMo 时才调用 `_prepare_voice_reference` 转码一次，再把 base64 经 `_synthesize_segment(..., voice_ref_b64=)` → `_run_tts_engine` → `_tts_mimo` 传下去。全部命中缓存的重跑仍然不跑 ffmpeg。
  - 转码后再 stat 一次参考音频。如果它和 miss 缓存键里探测到的 `voice_ref_identity` 不同，就报“参考音频在配音期间被修改”，以免新音频用旧身份入缓存。
  - `tts_settings_payload` 直接用 `_voice_reference_signature`（resolved path + size + mtime_ns）。
  - 直接调用 `_tts_mimo` 而没传 base64 时，按当前文件转码。
  - CONFIG 的 `voice_ref_b64` / `voice_ref_snapshot_*` 四个键、`_VOICE_REFERENCE_LOCK`、`_reset_voice_reference_state`、`_voice_reference_identity` 和 `_cache_prepared_voice_reference` 都删除。`tts_meta.json` 的 `voice.reference` 身份格式不变。

## Alternatives considered

- **保留环境变量，只补文档。** 最强理由：真有人想调 true-peak 或 TTS 归一目标时，不用改代码。没采用：这些值是成片质量的一部分，没有一个出现在 SKILL.md 或 playbook 里，也没人报过要调；其中三项还不进 manifest。改一行常量比长期维护一个不可见的配置面便宜。
- **把收紧三项补进 `assembly_settings`，而不是常量化。** 最强理由：环境变量还在，manifest 也能如实记录。没采用：仍然是没人用的旋钮；常量化后成片只由代码版本决定，同样可追溯。
- **保留 `NARRATION_DELAY_SECONDS` 给旧稿。** 最强理由：0.4 时代的旧草稿按隐藏延迟写，换版本后入场会早一点。没采用：playbook 自己说非零值会把句界入场推回原声；旧稿重写 `start` 就能得到同样效果，而且是显式的。
- **过期的 validated 计划时回到“无 spans”，按整片处理。** 最强理由：不报错，assemble 总能跑完。没采用：`_map_asr_to_output` 把 None 当恒等映射，cut 成片上的用户字幕和原声字幕会落在原片时间戳上，比原来的 raw 近似更糟。与 understanding / script 一样硬失败，用户重新剪辑一次即可。
- **原始计划更新时继续读原始计划。** 最强理由：手改过 `clip_plan.json` 却没重新剪的 work_dir 也能出 timeline。没采用：这份溯源本来就和画面对不上，recap 编排器改计划后总会重跑 cut；没有 validated 计划却有原始计划时，旧逻辑还会在整片模式下误重映射。
- **dub 的三项改成模块常量。** 最强理由：参数表更短。没采用：测试直接调 `stage_render(ref_start=0.0, ref_dur=2.0)`，关键字默认值零改动；`--ref-start` 原本是把克隆参考挪离片头音乐的唯一入口，dub 是实验功能、recap 从不暴露，现在只能改调用参数。
- **voice-ref 保留锁定快照，只把它从 CONFIG 挪进模块变量。** 最强理由：改动更小。没采用：快照状态机的唯一用途是“一次调用只转码一次”，在 `synthesize_tts` 里准备好再传参就能做到，锁、reset 和按 locked 分支的身份函数都没有存在理由。

## Consequences

- 收益：
  - 脚本净少约 90 行。其中 voiceover 快照状态机约 45 行，`media` 原始计划回退约 15 行，`narration_audio` 少一条分支和延迟计算，dub CLI 少三个参数。常量化本身基本不省行数，省掉的是 19 个对外配置面。
  - env 清单少 19 个变量。
  - timeline 与字幕重映射不会再描述画面里没有的片段。
- 代价：
  - 导出这 19 个环境变量不再有效果，`NARRATION_TIGHTEN=0` 也恢复不了 slot 锚定放置。
  - `dub.py --asr-window/--ref-start/--ref-dur` 会报 argparse 错误。
  - 手改 `clip_plan.json` 后没重新剪就跑 assemble 会直接失败。
  - 配音过程中改动参考音频文件会报错，不再静默使用旧快照。
  - 外部脚本若读 `narration_timing.delay_seconds` / `tail_pad_seconds` 要改；仓库内没有读者（`resource_lock` 只读 bgm / packaging / subtitle_style）。
- 是否在默认路径：默认值全部不变，渲染结果、TTS 缓存键、QC 判定都不变；只有上面列的显式设置和过期计划会表现不同。

## Verification

- `ruff check .` 通过。
- `PATH=$HOME/.cache/video-recap-ffmpeg8:$PATH python3 scripts/test.py orchestrator voiceover assemble` 全绿。
