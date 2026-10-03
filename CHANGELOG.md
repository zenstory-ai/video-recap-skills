# Changelog

All notable changes to this project are documented here.

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

小节名统一使用 Keep a Changelog 的六个英文类别（`Added` / `Changed` / `Deprecated` /
`Removed` / `Fixed` / `Security`），正文为中文。收紧到会拒绝旧输入的改动记入 `Changed`。

## [Unreleased]

## [0.6.1] - 2026-10-03

升级前先从脚本与环境里删掉本版移除的参数和环境变量（见 Removed，如 `--mimo-qc`、`validate.py --preserve-approved-text`、`cut.py --clip-padding`、`dashboard_server.py --host` 和 19 个调参变量），读 `golden_eval.json`、`mimo_qc.json`、`preflight_qc.json` 的脚本按 Removed 改读 `final_qc.json` 或对应产物。
full 模式下超预算的解说稿和任何模式下未按 `start` 排序的解说稿现在 lint 失败，要改稿后重跑；默认开启的 `TTS_MIN_SPEECH_RATE` 会让语速过慢的段失败，可调低或设为 `0`。
旧 work_dir 续跑会重剪 `edited_source.mp4`、重新合成 TTS、重建故事索引各一次，缺 ASR 时间证据的还会重跑 ASR。
虽是补丁号，本版改了契约：校验、导出与清单读取不再静默改写或兼容旧形状，而是报错并写明改法（见 Changed），没有读取方的产物与开关一并删除。

### Added

- **缺 drawtext 时，带画面文字叠加的运行在配音前停下。** 旁白 `visual_overlays`（`top_title` / `inline_label_or_callout`）用 ffmpeg `drawtext` 渲染，Homebrew 自带的 ffmpeg 没有它，以前要跑完理解、写稿和 TTS 才在最后渲染失败。现在 recap 在评审与 TTS 之前检查（source 模式检查已有的 `visual_overlays.json`），`assemble.py` 在渲染前再查一次，报错写明删掉叠加或换带 drawtext 的 ffmpeg；叠加是写稿时明确加的内容，所以不做静默跳过。`--doctor` 新增 `ffmpeg_drawtext_filter` / `visual_overlays_ready` 和对应 warning。
- **新增按需技能 video-reference：把成片拆成可复用的制作参考。** `reference.py measure` 用一次 ffmpeg（`scdet` 逐帧分数 + `ebur128`）测镜头切点、镜长分布与响度，按成片 `{size, mtime_ns}` 缓存；切点只认孤立峰（固定阈值会在暗场漏掉硬切、在运动镜头里误报），被压下的候选由 `reference.py frames --review` 逐帧拼图给 Agent 复核，复核结果写进 `labels.cut_fixes`，导出物用 `cut_detection` 记录检测参数与增删数；Agent 在 `reference_breakdown.json` 标注音轨归属与叙事段落，并分开写原片事实与可迁移方法；`check` 用 R1–R8 机械分离两者（封闭 schema、事实锚点、方法证据、target 只写测量路径、实体名/台词引文/绝对时间/路径泄漏扫描、五维覆盖），`export` 零 error 时才写不含原片事实的 `production_reference.json`。写稿 Agent 只在 work_dir 有这个文件时阅读，并可在 `recap_story_plan.json` 写可选的 `reference_methods`。不调用 MiMo，不进默认生产路径，recap 不加参数，不新增 QC。
- **制作参考可以登记进资源库并经 `--project` 绑定。** 资源库新增模板 kind `production_reference`：版本目录里放导出的 `production_reference.json`，`template.json` 的 `params` 恰好是 `{"reference": {"path": "production_reference.json"}}`；`library.py check` 核对导出物 schema、methods 与五维枚举，任何层级出现 `source_facts`/`labels`/`evidence`/`entities`/`statement`/`from`/`path` 键都报错（与导出复扫同一组键）。项目 `bindings.production_reference` 只接受 adopted 版本，recap 在第一次暂停前把带 `written_by` 与 `template {id, version}` 的副本写进 `work_dir`，暂停提示多一行说明，`resource_lock.json` 记下 `id@vN`；只绑定参考时不探测成片画布，不加门禁，没有脚本读它。`work_dir` 已有不是 `--project` 写的同名文件时，内容与导出物相同就原样保留，不同则运行停止；解除绑定后只删自己写的副本。示例库加一份合成的 `demo-pacing@v1`。
- **新增 `TTS_MIN_SPEECH_RATE`（默认 2.5 字/秒，`0` 关闭）。** 段音频超过按这个语速读完全文再加停顿与首尾静音的上限时视为 TTS 幻读，按失败重试、不写缓存（见 Fixed）；默认开启的影响见 Changed。
- **新增 `LOUDNESS_LIMITER_MAX_DB`（默认 6 dB，`0` 关闭限幅）。** 成片响度的真峰值限幅器最多削这么多，超出部分改为下调目标响度（见 Changed）。
- **新增 `understand.py --edited-storyboard-only`。** 只按 `clip_plan_validated.json` 生成剪后故事板，与 `--brief-only` 互斥；recap 多视频第二阶段会调用它（见 Fixed）。
- **guohuo-60s 样例新增 `remotion/sync_overlay.py` 与 `npm run typecheck`。** 前者从运行的 `subtitles.srt` 重建 Remotion 透明层数据并列出越过母版结尾的元素（见 Fixed）。
- **cut 被阻断的边界会给出最近的安全边界。** `unsafe_clip_sentence_boundary` 的边界在 `qc.boundary_status.sentence_checks`（和 `qc.blocking`）里多一个 `nearest_safe: {"before", "after"}`：前后 5 秒内用同一门禁复核过的最近时间，报的是帧对齐后的落点、已在原片帧网格上，对齐后仍安全才报（原片秒，带 `reason` 与 `delta`），没有就是 `null`；Agent 按它改 `clip_plan.json` 的入/出点，不必再猜。重跑时切镜头避让（`SCENE_CUT_SNAP`，默认开）也不会再把建议的边界拉回讲话内，见 Fixed。

### Changed

- **ffmpeg 缺 libass 时，默认烧录字幕降级为外挂 `.srt`，不再在开跑前退出。** Homebrew 自带的 ffmpeg 已去掉 libass，以前默认运行一律停在预检。现在只有显式要求烧录（`--burn-subtitles`，或设置了取真值的 `BURN_SUBTITLES`）时才在开跑前报错；默认运行照常完成，成片不带字幕，`subtitles.srt` 复制到成片旁（`recap_<stem>.srt`，烧录运行会删掉旧的同名文件）；显式 `--no-burn-subtitles` 的运行同样输出这个外挂字幕，没有任何字幕条目的运行（如 source-mix 不带用户字幕）既不写空 `.srt` 也不记警告，留白里的 `「」` 原声对白在 work_dir 有 `user_subtitles.*` 时照常写进 `.srt`、`timeline.json` 和剪映草稿；原本由遮罩触发的 `「」` 对白随遮罩一起关闭（原片硬字幕可见）。降级是机器可读的：`visual_qc.json` 与 `assembly_manifest.json` 的 `warnings` 各有一条 `subtitle_burn_degraded`（`assembly_manifest.json` 另有 `subtitle_sidecar` 路径，`assembly_settings.subtitle_burn_degraded` 记原因），`final_qc.json` 在 `metadata.warnings` 转载它（仅当 `assembly_manifest.json` 的 `final_output` 就是本次成片，dub 运行不会带上同一 work_dir 里旧运行的警告），`final_qc.py` 的摘要多出 `warnings` 列表，recap 完成时打印警告和 `.srt` 路径。降级不阻断、不改 `ok` / `blocker_count`。原字幕遮罩仍然只在烧录时生效，降级时 `mask.trigger` 记为 `burn_subtitles_degraded`；显式 `--no-burn-subtitles` 时记 `burn_subtitles_disabled`。`--doctor` 的 `system_tools.subtitle_delivery` 给出 `burned` / `sidecar_srt` / `fails_explicit_burn` / `unavailable`（找不到 ffmpeg）。
- **full 模式校验不再静默改写解说稿（破坏性变更）。** `validate.py --mode full` 不再截短超预算文本、丢弃过短段、合并相似相邻段、补句末标点或按时间重排，也不再丢掉白名单之外的字段；它和 cut_output 一样只做 lint，再用同一套声音归属算法（原声对白区间减去安静窗口）回写 `overlaps_speech`，因此 full 模式的 `overlaps_speech` 和随之的原声闪避可能与以前不同。字数超过该时间窗推荐字数（先扣约 0.45 秒 TTS 首尾静音：每块是一次 TTS 合成，自带这段静音，取 MiMo 实测中位数）1.25 倍的段现在是 `over_budget` error（以前会被静默截短或丢弃），`narration_lint.json` 写明段号、时间窗、`budget_chars`、`limit_chars`、`actual_chars`、`over_chars` 与 `tts_overhead_seconds`，Agent 改稿后重跑；cut_output 的超预算仍是 warning，估计朗读时长同样计入这段静音。扣掉静音后 `slot_too_short` warning 在默认配置下对约 1.9 秒以下的窗口出现（以前约 1.4 秒）。brief 里每个窗口标的字数还没扣这一项，2–3 秒的短窗口要比它少写一两个字。未按 `start` 排序的段在所有模式下都报 `out_of_order` error。`validate.py` 的 `--preserve-approved-text` 已删除（见 Removed）；voiceover 默认也不再缩稿，见下方 Fixed 的“默认路径不再截短旁白”。
- **TTS 段时长超过 `TTS_MIN_SPEECH_RATE` 推出的上限时判为失败，默认开启。** 以前能通过的慢速旁白或慢速克隆声线现在会被重试，重试用尽则该段失败；旁白本来就慢时把 `TTS_MIN_SPEECH_RATE` 调低，或设为 `0` 关闭（判定细节见 Fixed）。
- **暂停时的续跑命令改为回显原命令。** 不再由 `recap_timeline` 按 flag 逐个重组，而是回显用户输入的参数：保留原写法（如 `--style=悬疑`、`--project` 写的是目录），视频与 `--work-dir` / `--output-dir` / `--voice-ref` / `--material-library-dir` / `--project` 等路径转为绝对路径，补上 `--work-dir` 与来自 `EDIT_MODE` / `TARGET_DURATION` / `TTS_PROVIDER` / `VOICE_REF` / `SUBTITLE_Y_*` 的设置。`recap_run_manifest.json` 新增 `argv`，记录这条续跑参数。续跑命令从任何目录都能用。
- **`final_qc.json` 改为最小报告形状（`schema_version: 2`），QC 只收敛到这一份报告。** 通用 QC 契约 `scripts/qc_contract.py` 与 `references/shift-left-qc-schema.md` 删除，字段说明并入 `data-schema.md` 的 `final_qc.json` 一节。报告去掉恒为 `post_render` 的 `stage`；每条 finding 只剩 `code`、`message`、`blocking`、`evidence`、`next_action`，去掉 `finding_id` / `severity` / `confidence` / `sample_policy` / `model_used` / `rule_id` / `decision_reason` / `location` / `category` / `source` 等恒定或重复字段。`ok`、`blocker_count`、`finding_count` 与 `--require-final-qc` 的判定不变。`metadata.probe` 只保留检查用到的流与容器字段，原片带来的容器标签（comment、purl 里的 URL 等）不再写进报告；`metadata.artifacts` 对 `assembly_qc.json` / `visual_qc.json` 改为记录它们的 `verdict` / `blocking` / `blocking_codes`。
- **`final_qc` 不再把 assembly / visual QC 的阻断码转写成自己的 blocker。** 这两份 QC 阻断时 video-assemble 已经非零退出，流水线走不到 final_qc；只有在失败的 work_dir 上手动跑 `final_qc.py` 时，结果才会不同（这时直接看 `assembly_qc.json` / `visual_qc.json`）。
- **cut 账本 `recap_phase.json` 只记 `clip_plan_identity`。** 写稿时剪辑计划的身份是它唯一被读取的字段；`edited_source_rendered`、`narration_written`、`multi_source`、`audio_mode`、`audio_stream_index` 不再写入，不带解说的 cut 运行也不再写账本。旧账本里多出的字段在下次写入时丢弃。
- **素材库按 `material_id` 直接恢复。** recap 把保存时用的 `material_id` 传给恢复，不再扫描 `materials/*/material.json` 按源路径查找；手工改过名的素材目录因此不会再被找到，需改回原名或重新沉淀。素材白名单去掉从未有生产者的 `reference_profile.json` / `reference_match_report.json`。
- **剪映导出只接受 `schema_version: 2` 的 `timeline.json`。** v0.4.0 起流水线就只写 v2；0.3 时代的 v1 时间线不再静默迁移，`export_jianying.py` 直接报 `unsupported timeline schema_version 1`，把版本号改成 2 即可导出。
- **`subtitle_track.json` 的 binding 不再容忍 `sha256` / `edit_sha256`。** 这两个摘要键按未知字段拒绝（`unknown field(s)`），删掉即可；没有任何已发布版本写过带这两个键的字幕轨。
- **assemble 的时间线溯源只认 `clip_plan_validated.json`。** `clip_plan.json` 比它新（改了计划却没重新剪）时，assemble 在渲染前报 `clip_plan_validated.json 已过期`，不再改用原始计划拼出与画面不符的 `timeline.json` 和重映射字幕；重新剪辑即可。没有 validated 计划时按整片处理，散落的 `clip_plan.json` 不再参与字幕重映射。
- **voiceover 的参考音频每次运行只转码一次，并按参数传给各段。** 有段落需要合成时才转码，全部命中缓存的重跑仍不调用 ffmpeg；配音期间参考音频被改动会报 `参考音频在配音期间被修改`，不再静默沿用旧快照。`tts_meta.json` 与 TTS 段缓存键不变。
- **cut 与 understanding 的脚本模块收窄导出。** `cut.py` 只导出 `main`，video-understanding 删除再导出用的 `brief.py`；video-cut 的 `narration_mapping.py` 改名 `cut_qc.py`；进程内导入这些名字的脚本需改从所属模块导入。cut 计划里源区间重叠的报错结尾改为 `split or remove duplicate source footage in the clip plan`。命令行、参数与产物不变。
- **video-recap 的 dashboard 与资源库模块归入子包。** `dashboard_{data,io,runs,templates}.py` 移到 `scripts/dashboard/`，`project_binding.py`、`resource_lock.py` 移到 `scripts/resources/{project_binding,lock}.py`；文件身份与 id 函数从 `materials.py`、ffmpeg 字幕滤镜探测从 `doctor.py` 移进 `lib.py`，`TTS_PROVIDERS` 只剩一份。进程内导入这些模块的脚本需改从新路径导入；入口脚本（`recap.py`、`dashboard_server.py`、`library.py`、`doctor.py` 等）、产物与默认值不变。
- **ASR 缓存缺少 `asr_timing_evidence.json` 时重跑 ASR。** video-understanding 删除 `LEGACY_UNVERIFIED` 状态：sidecar 被删掉、或重跑 ASR 时被 Ctrl-C 中断的 work_dir 不再离线复用旧转写，旧版本写下的 `LEGACY_UNVERIFIED` sidecar 也视为未命中。VLM prompt 模板缺失时直接报错，不再静默换用一份已过时的两段式兜底 prompt。
- **video-cut 的 `--sources-manifest` 只接受一种形状。** 清单必须是 `{"sources": [{"source_id", "source_path"[, "duration", "source_work_dir"]}]}`（即 recap 写出的 `multi_source_manifest.json`）；裸数组、以 `source_id` 为键的映射，以及 `id` / `name`、`path` / `video_path` / `video` / `file`、`duration_seconds` / `source_duration` 等别名一律报错，报错写明期望形状。多源 `clip_plan.json` 的片段必须写 `source_id`，不再把 `id` 当来源；顶层目标时长只认 `target_duration`，不再认 `target_duration_seconds`。形状写进了 video-cut SKILL.md。
- **多源 cut 只把计划里相邻的同源片段当作无损连接。** 按来源分组吸附时，计划中间夹着别的来源片段的 `a:0–2` 与 `a:2–4` 以前被判为 `continuous_source_join`，讲话中间的切点因此放行；现在只有计划里相邻（`clip_id` 连续）的同源片段才算连续，这类边界照常阻断。
- **成片响度改为恒定增益加真峰值限幅，交付文件真峰值仍超标时阻断。** 以前测得的真峰值放不下所需增益时，ffmpeg 会改用动态归一（逐 3 秒自动增益，会压扁混音），`loudness_mode` 却仍记 `two_pass_linear`。现在混音先整体增益，再经 4 倍过采样的 lookahead 限幅器把真峰值压到目标下 1 dB，最后按限幅后信号的测量做线性 loudnorm 补足剩余响度。限幅器最多削 `LOUDNESS_LIMITER_MAX_DB`（默认 6 dB），超过时只把余下的增益下调目标响度（`0` 关闭限幅，回到只下调目标）；只在动态模式生效的 LRA 目标放宽到测得值。真峰值目标针对交付的 AAC 文件（192k 编码会把真峰值抬高 0.3–1.4 dB）：首次渲染瞄准 -1.5 dBTP，渲染后解码成片测量（loudnorm 与 ebur128 取较高的真峰值）；仍超过 -1 dBTP 时把编码前目标再降“超出量 + 0.1 dB”，只重编码音频、画面流复制，最多两次，仍超出则以新阻断码 `delivered_true_peak_over_target` 阻断。限幅器削满 6 dB 的混音比 `TARGET_LUFS` 低约 0.5 LU，作为编码余量。长片的组装多花两遍音频解码的时间，需要纠正时再加一遍音频编码。`assembly_qc.json` 新增 `loudnorm_final_pass`（ffmpeg 报告的 `normalization_type`、实际目标与 `gain_capped_db`、编码前的输出响度与真峰值、`peak_limiter`：先增益、上限、需要与实际削去的 dB，以及 `delivered`：成片实测的 `integrated` / `true_peak`、最终的编码前目标 `peak_target_dbtp` 与纠正次数 `corrections`）；`loudness_mode` 新增 `two_pass_linear_peak_limited`，仍落到动态模式时记 `two_pass_dynamic`。source-mix 的单遍 loudnorm 同样按交付文件纠正。
- **ASR 读不到音频文件时报错。** 不再当作空转写继续。
- **没有 MiMo key 且理解缓存失效时运行停下。** `asr_result.json` 已有转写而 ASR 缓存未命中时停下并保留转写，VLM 缓存未命中时在 ASR 之前退出；以前会把转写写成 `[]` 后报成功。恢复办法见 Fixed。
- **报错与 brief 里给人看的段号、场景号、块号统一从 1 数。** 此前 `no_safe_fit` 的段号、validate 的 lint 摘要、brief 的 `### Fusion scene` 与 `ASR chunk` 场景号从 0 数；解析这些文字的脚本要减 1 对回 JSON。`narration_lint.json` 的 `index`、`narration_review.json` 的 `segment`、`timeline_fusion.json` / `asr_writing_chunks.json` 的 `scene_id` 仍从 0 数（细节见 Fixed）。

### Removed

- **`validate.py` 删除 `--preserve-approved-text`。** 传入即报 unrecognized arguments，recap 不再把它传给校验；`recap.py --preserve-approved-text` 照旧传给 voiceover。
- **删除 MiMo 多模态建议型 QC。** `recap.py` 不再接受 `--mimo-qc` / `--mimo-qc-refresh`（传入即报 unrecognized arguments），`MIMO_QC` / `MIMO_QC_REFRESH` / `MIMO_QC_MODEL` 环境变量不再被读取；入口 `mimo_qc.py` 与 `scripts/qc/` 子包删除，流水线不再写 `mimo_qc.json`。`final_qc.json` 的 `metadata.artifacts` 不再汇总 `mimo_qc.json`，dashboard 的 QC 页去掉「MiMo 复核」卡片；旧 work_dir 里残留的 `mimo_qc.json` 不再被任何环节读取。确定性的 `final_qc.json` 与解说评审不受影响。
- **`golden_eval.json` 并入 `final_qc.json`。** 渲染后只写 `final_qc.json`，不再写 `golden_eval.json`；没有 golden fixture 时它只是复述 `final_qc.ok`，而流水线从不传 fixture。`--require-final-qc` 只检查 `final_qc.json` 的摘要（`ok: true` 且整数 `blocker_count: 0`），判定结果不变；`final_qc.py` 去掉 `--golden-fixture` / `--only`（传入即报错），dashboard 的 QC 页去掉「黄金评估」卡片；旧 work_dir 里残留的 `golden_eval.json` 不再被读取。
- **删除 dub 的手动侧 CLI 与 `dub_review.json`。** `dub.py` 只剩编排入口调用的 `--stage prepare|render`：`--stage lint|review` 与 `--print-schema` 传入即报错，渲染不再写没有任何读取方的 `dub_review.json`（它只是把 `dub_lint.json` 换个形状重写）。`dub_lint.json` 照常在语音克隆前写出，lint 非 PASS 仍中止。dub 翻译规则只保留在 `dub_brief.md` 一处，brief 同时写出目标语速（约 5 字/秒）和 lint 警告阈值（7 字/秒）。
- **`--doctor` 去掉能力清单。** `recap.py --doctor --json` 不再输出 `capability_menu` 键，人读输出不再有 `[capability menu]` 分组段；`ok`、`checks`、`failures` 不变。清单里独有的两条信息改进 `warnings`：选中的 mimo-tts / fish-audio 未配置密钥时提示要设的变量，MiMo VLM 未配置时提示设 `MIMO_VIDEO_API_KEY` 或 `MIMO_API_KEY`；缺 libass 的 warning 说明默认运行改为交付外挂 `.srt`、显式 `--burn-subtitles` 会失败。
- **`dashboard_server.py` 删除 `--host`。** 服务固定绑定 127.0.0.1（原来也只接受回环地址），传 `--host` 即报 unrecognized arguments。
- **删除逐阶段账本 `preflight_qc.json`。** 流水线不再在 `pre_tts` / `post_tts` / `pre_assemble` / `post_cut` / `post_render` 写它：它不含 finding，内容是 `tts_meta.json`、`assembly_manifest.json`、cut QC 的拷贝。`final_qc.json` 的 `metadata.artifacts` 不再列出它；旧 work_dir 里残留的文件不再被读取（损坏的也不再让 narration 运行在解说评审之后崩溃），也不会被清理。
- **解说评审只产出 verdict 与 findings。** `review.py` 的 prompt 不再要求 16 维 1-5 分 scorecard 和 hook 候选、留存风险、最高回报改动、信息增量、口语改写、断言来源六个建议列表，`narration_review.json` / `.md` 也不再含这些键与小节；它们从不改变 verdict、不进门禁，也没有任何环节读取。评审 prompt 不再嵌入 deslop 报告（其 blocker 在评审前已是 lint error）。模型返回的 `OK` 记为 `PASS`，verdict 词表只剩 `PASS|REVISE|FAIL`。严格评审的门禁不变，仍只看 `findings` 里的 error 和解析失败。
- **评审、video-understanding 与校验不再写 `grounding_qc.json`、`silence_periods.qc.json`、`deslop_qc.json`。** recap 成片后也不再打印「🧭 Grounding QC」一行（评审关闭或失败时它会打印旧运行留下的文件）；每个安静窗口的判定依据仍在 `silence_periods.json`，去 AI 味报告保留在 `narration_lint.json` 的 `deslop_qc` 字段。
- **删除剪映 timeline-v2 手写扩展面。** `timeline.json` 不再支持变速、倒放、透明度 / 旋转 / 翻转、转场、蒙版、LUT、绿幕复合草稿、富文本样式（`style` / `words` / `style_presets`）以及 `sound` / `sticker` / `text_template` / `video_effect` / `face_effect` 资源轨与 `resource_packages` 离线资源包；导出遇到这些字段或轨道会明确报错，不再静默生成草稿。`timeline.build_timeline` 去掉 `resource_packages` / `style_presets` / `extra_tracks` 参数。流水线从不写这些字段，默认的 `timeline.json` 与导出的草稿（视频、旁白、BGM、字幕、包装图层的 `scale` / `position`）不变；这类效果请在剪映里直接加。
- **删除 assemble 的独立命令 `pair_media.py` 与 `compose_foreground.py`。** 两者只能手写计划 JSON 单独调用，编排器从不调用：前者把独立画面与已采用音轨按流复制配对成 `paired.mp4`，后者把调用方渲染的 RGBA 序列（可选片尾卡）叠到锁定母版。对应的 `references/pair-media.md`、`references/foreground-compose.md` 一并删除；动画或透明包装改由项目级渲染器自己合成到锁定母版，整片不动的包装继续用 `packaging_layers.json`。`--audio-mode adopted-packet-copy` 与显式混音用到的画面帧钟、AAC 包区间检查移入 `scripts/adoption/av_clock.py`，判定不变，只是三条报错不再以 “Pairing” 开头。
- **删除走不到的 ducking 模式与 `tts_dynamic_params` 开关。** video-assemble 的混音只保留默认的 fixed 包络：`sidechaincompress` / `none` 两种模式以及 `ducking_mode`、`ducking_threshold` / `ducking_ratio` / `ducking_attack` / `ducking_release` / `ducking_level_sc` / `ducking_makeup` 配置键删除（它们没有任何环境变量或参数能打开），`assembly_manifest.json` 的 `assembly_settings.audio_mix` 不再写这七个键。video-voiceover 删除恒为开启的 `tts_dynamic_params`，MiMo 与 Fish 段落总是按内容计算语速/音高。渲染结果不变；TTS 段缓存的设置载荷少了这个键，已有 work_dir 重跑时会重新合成一次 TTS。
- **assemble 产物不再镜像 QC 结论，删掉无效的 `SOURCE_VIDEO`。** `assembly_manifest.json` 去掉从 `assembly_qc.json` 抄来的 `qc_verdict` / `qc_blocking_codes` / `qc_loudness_mode` / `qc_loudnorm_measurement` / `audio_operations` / `adopted_audio`（`qc_path` 仍指向它），以及顶层和每段的 `segment_audio_schema_version`；`assembly_qc.json` 去掉恒等的 `release_gate` 块，`visual_qc` 只保留 `verdict` 与 `blocking_codes`，视觉细节请读 `visual_qc.json`。`subtitle_track_validation.json` 不再写 `validation_schema` / `projector_version`。环境变量 `SOURCE_VIDEO` 不再被读取（`assemble.py` 一直用 `--source-video` 覆盖它）。QC 判定、渲染结果和缓存都不变。
- **删除 19 个未文档化的调参环境变量和旧版旁白入场延迟。** video-assemble 不再读取 `FADE_MS`、`DUCKING_ORIG_VOLUME`、`TARGET_TRUE_PEAK`、`TARGET_LRA`、`FINAL_LIMITER_PEAK`、`NARRATION_RUN_GAP_SECONDS`、`NARRATION_TIGHT_PAUSE_SECONDS`、`NARRATION_MAX_PULL_SECONDS`、`SUBTITLE_MARGIN_L`、`SUBTITLE_MARGIN_R`、`NARRATION_TIGHTEN`、`NARRATION_DELAY_SECONDS`；video-voiceover 不再读取 `MIMO_TTS_STYLE`、`TTS_SEGMENT_NORMALIZE`、`TTS_SEGMENT_TARGET_RMS_DBFS`、`TTS_SEGMENT_PEAK_LIMIT`；两者都不再读取 `NARRATION_CUMULATIVE_TEMPO_MAX`、`NARRATION_CUMULATIVE_TEMPO_HARD_MAX`、`TTS_SEGMENT_TEMPO_MAX`。它们的默认值成为固定值，设置后不再生效；`NARRATION_TIGHTEN=0` 的按 slot 锚定放置和 `NARRATION_DELAY_SECONDS` 的隐藏入场延迟随之删除，段落起点一律采用作者写的 `start`，只在上一块超时时顺延到上一块结尾加停顿之后（段落内与上一块相隔不到 1.6 秒的后续块仍紧接上一块播放，最多提前 1.2 秒，这一点不变）。`assembly_manifest.json` 的 `assembly_settings.narration_timing` 不再写 `delay_seconds` / `tail_pad_seconds`。`SUBTITLE_ORIGINAL_IN_GAPS` 等文档列出的变量不变；默认渲染结果与 TTS 缓存都不变。
- **删除 `dub.py` 的 `--asr-window` / `--ref-start` / `--ref-dur`。** 传入即报 unrecognized arguments；ASR 窗口 6 秒、克隆参考从第 2 秒取 10 秒的默认值不变。
- **video-cut 不再写 `cut_delivery_qc.json`，brief 不再写 `deslop_qc_requirements.json`。** `clip_plan_validated.json` 的 `qc` 里不再有 `delivery_qc`，也不再有顶层 `warning`（超出目标时长仍由 `qc.target_duration_drift` 报告），每次剪辑少一次 ffprobe；成片交付检查仍在 `assembly_qc.json`。`narration_lint.json` 里的 `deslop_qc` 报告去掉恒为 `false` 的 `style_card_required` / `style_card_requirement_source`，缺少或为空的 `style_card.json` 一律是建议项（与此前默认行为一致）。渲染参数与 lint 阻断规则不变。
- **删除 video-cut 的 `--clip-padding` 与 `CLIP_PADDING`。** recap 从不传这个参数、默认值是 0，而片段边界本来就会吸附到句末与自然停顿；现在 `cut.py --clip-padding` 报 unrecognized arguments，`CLIP_PADDING` 环境变量不再被读取。需要前后余量时直接在 `clip_plan.json` 里把入出点写宽。
- **`shot_review.py` 删除三个未写进文档的召回参数。** `--max-short-seconds` / `--dense-window-seconds` / `--min-dense-cuts` 传入即报错；召回规则固定为 1 秒短镜、2 秒内 4 个切点；`--max-short-frames`、`--threshold`、`--roi`、`--plan` 不变。

### Fixed

- **删掉或插入一段旁白不再让后面所有段重新合成、重新计费。** voiceover 的段缓存以前按段序号（连同时间窗）记键，按 `no_safe_fit` 的建议删掉一段后，其后每段序号都变了，全部重新请求 TTS。现在缓存按内容寻址（文本、实际发给供应商的语气请求，以及供应商/模型/声线等设置），存在 `tts_segments/cache/`，`narr_NNN.wav` 改为指向缓存的硬链接（不支持硬链接的文件系统上为副本），`tts_meta.json` 与 assemble 读取的路径不变；只有内容或发给供应商的请求变了的段才重新合成。语气请求按供应商实际收到的内容记：MiMo 是那句自然语言指令，名义语速只在 ≥+6% 或 ≤-3% 时改变措辞，所以删段让某段从 +5% 变成倒数第二段的 -2% 时不再重新合成，复用的段按新位置的语速记 `tts_rate_offset`；Fish Audio 是数值 speed（语速变了就重合成，音高和情绪它不接收，不进键）。批准稿严格模式下，复用的段如果被挪进放不下的时间窗，照样在 TTS 阶段失败，不再为此重新合成。旧版的 `narr_NNN.wav.cache.json` 不再读取，该段重新合成时删除。
- **TTS 幻读按失败重试，不再写进段缓存。** MiMo TTS 偶尔读完原稿后再编一段话（35 字的句子返回 18.1 秒，正常 8.2–8.6 秒）；以前这段音频会进段缓存，时间窗够宽时编出来的话会进成片，否则 assemble 报 `no_safe_fit`。现在段音频超过按 `TTS_MIN_SPEECH_RATE`（默认 2.5 字/秒；半角/全角数字逐个计、`%` 按“百分之”计 3 字）读完全文再加停顿与首尾静音的上限，就按失败重试、不写缓存；重试用尽则该段失败，报错写明时长与上限，并把最后一次被拒的音频留在 `tts_segments/narr_NNN.rejected.wav` 供试听。dub 的 voiceclone 台词走同一道检查（被拒的留在 `dub_tts/line_NNN_raw.rejected.wav`）。旧缓存里的这类 WAV 重跑时重新合成，按 `no_safe_fit` 缩稿后的重跑不再复用同一段坏音频。`TTS_MIN_SPEECH_RATE=0` 关闭。
- **剪辑接点不再丢帧，`edited_source.mp4` 恒定帧率。** 片段边界不在源帧网格上时，concat 让下一段从网格外开始，每个这样的接点丢一个帧位（25fps 下画面停 80 ms），成片变成可变帧率。现在句界吸附之后，入点对齐到源帧网格、时长对齐到整数个输出帧（只移动不到一帧，优先选句界门禁仍判为安全、仍在停顿内、不切掉必保证据的一侧，两侧同样安全时选不跨过原片硬切的一侧，入点不会停在硬切前一帧闪一下旧镜头），句界门禁检查对齐后的边界；渲染按每段 `frame_count` 精确出帧，多源时按画布帧率重采样（NTSC 用 `30000/1001` 而不是 `29.97`）；片段越过比音轨短的视频流末尾时用末帧补足帧数，不再留空洞；标称帧率远高于实际平均帧率的可变帧率手机素材按实际帧率输出，不再翻倍成 60fps。输出时间轴按累计帧数计算，与渲染一致。`clip_plan_validated.json` 每段多 `frame_count`，`qc.frame_grid` 记录输出帧率与总帧数，`qc.boundary_status.frame_snaps` 记录每次对齐。`r_frame_rate` 为 `0/0` 但 `avg_frame_rate` 可用时按平均帧率对齐入点；同源无损连续的接点音频按上一段实际渲染的终点续接，采样连续。首帧比文件起点晚 0.2 秒以内的源，入点 0 仍算源头；晚得更多（如音频先开始的 TS 录制）时，对齐到首帧的入点照常按讲话判定。渲染后若实际帧数与 `qc.frame_grid.frame_count` 不符，cut 会记一条警告（只提示，不阻断）。旧的 `edited_source.mp4` 缓存会重渲染一次；输出时间轴可能比以前差不到一帧，旁白照旧按 validated 计划写。
- **切镜头避让不再把片段边界拉进原声讲话。** `SCENE_CUT_SNAP`（默认开）会把出点最多回收 `SCENE_CUT_SNAP_MARGIN`（默认 0.5 秒）到原片硬切上；附近没有停顿窗（ASR 空档里是音乐或环境声而不是静音）时，句末吸附修不回来，边界会停在讲话内被阻断。现在这类移动在原位置能过句界门禁时会被撤回（不与其他片段重叠时），按 `nearest_safe` 改后重跑可以收敛；`qc.boundary_status.shot_snaps` 记 `reverted_unsafe`、被撤回的切点与原因；声音优先于画面，片段边界可能离原片硬切不到半秒，开头或结尾带一小段别的镜头。
- **成片（dub 模式除外）在每条渲染路径上都是 H.264 8-bit 4:2:0 并带色彩标记。** 不烧字幕、不遮罩、不缩放时成片曾直接流复制源画面，10-bit、4:2:2、HEVC 或奇数宽高的源会原样交付，微信、Safari 和不少手机放不了；ffmpeg 缺 libass 降级后这条路径会变得常见。现在只有源画面已是 H.264 8-bit 4:2:0（`yuv420p`，或全范围 `yuvj420p` 原样复制）且宽高为偶数时才流复制，否则重编码为 `yuv420p`，`delivery_qc.reencode_reason` 记为 `normalize_source_format`。最终编码和 cut 的 `edited_source.mp4` 都写色彩标记：源未标记或已是 BT.709 时标为 BT.709 limited（`-colorspace/-color_primaries/-color_trc bt709 -color_range tv`，并在滤镜末尾用 `setparams` 打到每一帧，因为 ffmpeg 8/9 重编码时只给输出参数会丢掉 primaries/transfer），其它已声明的色彩空间原样保留（含 PAL 源的 BT.470 传输特性），全范围源保留 `pc`；单个 YUV 源只改标记，不转换像素。RGB 源按 BT.709 limited 显式转换，识别时既看 ffprobe 的色彩空间 `gbr`（PNG、libx264rgb），也看像素格式，所以不报色彩空间的 QuickTime RLE（`argb`）、GIF（`pal8`）同样按 BT.709 转换。多来源 cut 各源标记一致时保留，不一致（含 RGB、未标记与已标记的源混剪）时标为 BT.709 limited，并把每段的矩阵和范围显式转换过去：ffmpeg 8 的 concat 会自行把各段统一到其中一个来源的色彩空间，BT.601 源与未标记源混剪时两段都按 BT.601 存储却标成 BT.709；原色与传输特性仍只改标记。升级前缓存的 `edited_source.mp4` 会重新剪辑一次。dub 模式（`--edit-mode dub`）仍原样复制源画面，不做这项归一化，只补上 `+faststart`。`assembly_qc.json` 的 `delivery_qc.color_tags` 记录写入的标记；显式混音的 `packet_identity` 比较时不计这四个色彩字段，流复制的画面仍报 `EXACT`。
- **成片和 `edited_source.mp4` 不再继承原片的容器元数据。** 下载来的原片带的 `title`、`comment`（网址）等标签和章节此前会被 ffmpeg 原样抄进 `edited_source.mp4` 与最终成片，章节时间也对不上剪辑后的画面；现在 cut 渲染、assemble 的所有音频路径（narration、source-mix、adopted-packet-copy、显式混音）和 dub 输出都用 `-map_metadata -1 -map_chapters -1` 不带走任何源标签与章节（原音轨的语言标签也一并去掉）。
- **guohuo-60s 的 Remotion 透明层按新运行重定时只需改数据。** 总帧数、片名文字与两段显示窗口、四条花字此前写死在 TSX 里，换了配音时长只替换 `captions.json` 会渲染出偏短、错位的透明层。现在它们在 `remotion/src/overlay.json`，与 `captions.json` 一起作为 props 传入，`calculateMetadata` 按 props 定时长，`--props=<文件>` 可整体替换；标准库脚本 `remotion/sync_overlay.py`（见 Added）从运行的 `subtitles.srt` 重建 Remotion 读取的 `remotion/src/captions.json`（每次都写，`--out` 只额外写副本）、按母版时长设 `durationInFrames`，并列出越过母版结尾的字幕、片名窗口和花字（退出码 1）；新增 `tsconfig.json` 与 `npm run typecheck`。runbook 第 5 节改为按这条流程复现。
- **近方形像素的视频可以用测得的原字幕带。** `tools/measure_subtitle.py`、recap 的 `--subtitle-y-top/--subtitle-y-bot` 预检和 assemble 的字幕带校验此前只认精确的 SAR 1:1，`64:63` 这类近方形视频直接被拒，字幕遮罩用不了，`「」` 原声字幕也因此跳过；未标注 SAR（`0:1`）在 assemble 被当成非方形拒绝，在 recap 预检里还会算出 1 像素宽的画布。现在三处都接受与 1:1 相差不超过 2% 的 SAR（`0:1`/`N/A` 按方形），并按显示画布换算：测量工具写出的 `canvas` 宽度按 SAR 缩放，旋转 90°/270° 的近方形视频把解码帧的像素行换算成显示画布行，红框预览也先缩放到显示画布再画网格和红框，从网格读到的值就是提示里要填的行；assemble 画遮罩时再换回解码帧的行。方形、未旋转的视频结果不变；SAR 偏离更大的视频照旧拒绝。
- **多视频 cut 第二阶段也生成剪后故事板。** video-script 要求写稿前先看剪后故事板，单视频第二阶段由 `understand.py --brief-only` 生成 `storyboard/edited_storyboard.*`，多视频第二阶段却只写 brief，从不生成。现在 recap 在写完多源 brief 后调用 `understand.py --edited-storyboard-only`（见 Added）：按 validated 计划从各来源 `sources/<source_id>/frames/` 取帧，每个来源用自己 `frames_manifest.json` 记录的 fps（不同时长的来源抽帧率不同），tile 标 `out mm:ss / S<n> mm:ss`，sidecar 的 `sources` 与 brief 顶部列出 `S<n>` 对应的 `source_id`。所有 tile 统一缩放、补边并转成同一像素格式：混入不同分辨率或色度采样的来源时，ffmpeg 的 tile 会在中途重置，已排好的格子变黑；统一的格子尺寸取第一个被用到的来源的帧尺寸并向下取偶数（帧按存储分辨率抽取，853×480 这类奇数尺寸否则会让每一格都失败）。计划里登记了但没有片段用到的来源不占 `S<n>` 标签、不列入来源表。素材库恢复的来源没有抽帧，其片段不进故事板；故事板失败只打印警告，不影响暂停。
- **粗粒度 ASR 的句末锚点按实际精度标注。** understanding 按 ASR 窗口推出每个锚点的最坏误差 `timing_bound_seconds`，15 秒窗口里的锚点如实标为 `low` 并带 `boundary_use: unverified`，而不是凭字数比例猜出的 `high`；`speech_boundary_anchors.json` 升到 schema 2，旧文件有 `audio.wav` 时重新检测；素材库恢复（不带音频）时按 `asr_result.json` 原地重标、锚点时间不变，标不了就保持原样，不会被空的 `unavailable` 文件覆盖；没有 `boundary_use` 的 schema 1 锚点一律按 `unverified` 读。cut、script、assemble、brief 按 `boundary_use` 选锚点，选中的集合和门禁结果不变，状态与 brief 改写为 `unverified`（brief 显示 `[unverified ±N s]`）。剪后输出时钟的锚点也映射了 `pause_end` 和 `expected_time`（原片值保留在 `source_pause_end` / `source_expected_time`）。
- **只有语气词或 ASR 杂音的窗口（"啊！"、"Hi."）不再让整段窗口都不能下刀或切入旁白。** cut 门禁、script 的旁白入口检查和 assemble 的入口检查都不再把这类窗口当作对白，只在紧挨真实对白的一侧保留 1 秒保护；"救我！"这类短台词和只有标点的窗口（"……"）仍算对白。
- **full 模式的旁白 lint 与 cut、assemble 读同一份转写。** 有 `asr_clean.json` 时，video-script 的声音归属和入口检查改用它（以前只读 `asr_result.json`），cut 门禁和 assemble 入口检查本来就优先读它；清洗把"啊！"还原成真实台词（或反过来）时，lint 不再放行一个 TTS 之后才被 assemble 以 `unsafe_entry` 阻断的入口。单视频的解说评审也改为优先引用清洗后的台词，与多视频评审一致。
- **文本为空的 ASR 行不再算作对白。** 三份 `_dialogue_speech_spans` 统一把空白文本的行当作只有时间的证据：不算对白，也不在相邻对白一侧留 1 秒保护；没有 `text` 字段的行仍按对白处理。video-script 的整段归属同样不再把只有空白的行算作讲话；assemble 回退读 ASR 时，空白行不再让入口检查误判为打断原声。
- **解说评审不再把有意留给原声的拍报成漏写。** 评审 prompt 新增「计划内留给原声的区间」：逐条列出 `visual_audio_board.json`（缺项时看 `recap_story_plan.json`）里 `audio_owner` 为原声/沉默或 `narration_job: none` 的拍，以及 `original_subtitles.json` 的字幕块，按评审时钟标时间，并要求不得据此报跳过、`incomplete` 或 `no_throughline`。以前计划 JSON 在 prompt 里只截前 3000 字，靠后的原声拍被截掉，评审看到无旁白的 beat 就报"skipped"。
- **旁白结束后原声 3 秒内回满。** assemble 只等 3 秒内的句末锚点，否则在旁白结束处回满（`bounded_release`），不再把原声压到远处的锚点或片尾；`assembly_qc.summary.max_source_duck_hold_seconds` 记录最长的压低延续。
- **默认路径不再截短旁白。** voiceover 超预算时保留原稿只记日志（截短后的产物以前总会被 `truncated_speech` 阻断）；assemble 有界提速仍放不下的段在视频编码前以 `no_safe_fit` 阻断，错误里写明段号（`段 N`，按 narration.json 顺序从 1 起，此前从 0 起）与 `needed_tempo_factor`，不再白跑一遍完整渲染、也不再多调一次 TTS；显式 adopted full-sound 路径同样在编码前阻断。旧版截短合成的音频不会被复用：TTS 段缓存改为按内容寻址后，旧版的逐段缓存不再读取（默认策略名改为 `report-over-budget-v2`，只记在 `tts_meta.json` 各段的 `authored_text_policy` 里，不在缓存键中，改名本身不使任何缓存失效）。
- **MiMo 内容审核拒绝不再被当成对白或画面描述。** MiMo 把审核拦截作为一句英文回复（"The request was rejected because it was considered high risk"）返回，此前这句话会写进 `asr_result.json` 和逐场景 VLM 描述，再流进 brief，cut 与旁白校验还会把那个 ASR 窗口当成有人说话。现在 ASR 把它记为无文本，VLM 记为 `(VLM 无法识别此场景画面)` 并带 `analysis_status: "moderation_refused"`。只匹配 MiMo 自己的这句措辞，提到“违规”“风险”的真实台词不受影响；旧 work_dir 里已写下的拒绝文本需重跑 ASR / VLM 才会消失。
- **多视频运行会用上项目级 `background_research.json`。** 理解阶段对每个来源在 `sources/<source_id>/` 里运行，只读那里的调研文件。现在 recap 在每个来源理解前把按 SKILL.md 写在项目 `work_dir` 下的调研复制过去（来源目录里没有、或副本比项目文件旧时），项目级调研因此进入 VLM 上下文、ASR 人名纠错和索引；比项目文件新的来源副本保留，作为单集专用调研。
- **段落收紧提前的旁白块不再闯进没校验过的原声对白。** 段落内与上一块相隔不到 1.6 秒的块会紧接上一块播放、最多比写的 `start` 提前 1.2 秒，但旁白校验只检查过写的 `start`。assemble 现在只让它提前到不含原声对白的位置（ASR 对白区间减去实测安静窗口），有对白时停在最后一段对白结束处；真的提前了的块在 `assembly_manifest.json` 记 `source_entry_status: "paragraph_tightened"` 和新字段 `written_start`，此前这里是 `null`，看不出它被挪过。
- **旁白入口落在只有语气词的窗口里时记 `non_dialogue_source`。** 此前尖叫或 "Hi." 上的入口也记 `quiet_source`，像是原声安静；判定与是否阻断都不变。整段都不压低原声的块现在也记入口状态（此前为 `null`）。
- **assemble 退回读 ASR 时，空文本行也不再算作原声讲话。** 上面的空白行规则只管入口是否打断对白；full 模式下 assemble 从 `asr_clean.json` / `asr_result.json` 取的整段讲话区间此前仍包含没识别出文字的窗口，只落在这种窗口上的旁白也会压低原声并做句末交接。现在与 cut、script 一样先去掉空文本行。
- **故事索引被截断时不再报 ok。** consolidate 的索引调用此前上限 3000 token，5 分钟的解说素材就会被截断（`finish_reason=length`），解析出空列表后照样写出空的 `understanding_index.json`，`consolidation.status.json` 仍是 `ok`，brief 拿到 0 个角色。现在索引与 ASR 清洗两次调用的上限都是 8000 token，被截断时加倍预算重试一次；仍被截断或返回的不是 JSON 时不写产物，`consolidation.status.json` 记为 `failed` 并写明原因，brief 照常提示。索引 prompt 要求更紧凑的输出（每条描述不超过 40 字、剧情节点最多 20 条等），已有索引会按新 prompt 重建一次。
- **cut 续跑不再让剪后输出证据失效。** `cut.py` 复用 `edited_source.mp4` 时会重写内容不变的 `clip_plan_validated.json`，绑定其 `{size, mtime_ns}` 的 `speech_boundary_anchors_output.json` 因此过期，第三遍续跑的 `validate --mode cut_output` 对冷开场以外的旁白一律报 `source_sentence_anchors_unavailable`，assemble 的原声闪避也只能退回保守模式。现在计划未改动时不重写，`clip_plan.json` 被重新保存时照常重写。
- **cut 输出时间轴上的句末锚点不再落进相邻片段或成片之外。** 句末锚点（停顿结束）比片段出点晚几毫秒、或比入点早几毫秒时，`speech_boundary_anchors_output.json` 把它映射到下一段（多视频时是另一个来源）或上一段的输出时间里，却仍标着本段的来源，旁白校验和原声闪避会把别处的讲话当成这里的句末；首尾片段帧对齐后（如 181.42 → 181.40）还会映射到片头之前或成片结尾之后。现在落在 `[0, 成片时长]` 之外的锚点丢掉（brief 也不再列出），片段接缝处的锚点钉在本段的入点或出点上（另一段本身播放该时刻时只归那一段），`pause_start`、`expected_time` 和讲话/安静区间同样不越出本段；`source_time` 仍记实测的原片时间。单视频与多视频相同。
- **多视频 cut 的旁白校验不再以 `KeyError` 崩溃。** recap 写的多源 `speech_boundary_anchors_output.json` 现在带 `clip_plan_identity`；源锚点缺 `pause_start` 时与单源一样按 `time − 0.12` 处理。
- **Windows 上经管道运行 recap 时，阶段脚本不再因中文日志崩溃。** recap 调用各阶段脚本时设置 `PYTHONIOENCODING=utf-8`；此前 stdout 被管道捕获（Agent 宿主、CI）时子进程默认 cp1252，第一行中文日志就抛 `UnicodeEncodeError`。
- **`MIMO_VIDEO_API_KEY` 被拒（401）时报错点名它本身。** 不再让用户去检查 `MIMO_API_KEY`。
- **素材库保存并恢复 `consolidation.status.json`。** 从素材库恢复的 work_dir 重建 brief 时，仍会提示 consolidate 失败或缺索引。
- **cut 终轮先判断解说是否过期，再重剪。** `clip_plan.json` 在写稿后改过时，单视频与多视频 cut 现在在调用 `cut.py` 之前就以"clip_plan.json 已改变"退出，不再先重新归一化、吸附、甚至重编码 `edited_source.mp4` 之后才报错。
- **各技能 SKILL.md 补齐漏写的选项并改正错误说明。** video-cut §4 补上 `--allow-duration-drift`、`--normalize-only` 的用法和多源清单格式，命令块补上 `--clip-plan`、`--review-shots`、`--shot-scene-threshold`、`--shot-roi`，并列出 cut 阻断时 `qc.blocking` 里的错误码（`unsafe_clip_sentence_boundary`、`target_duration_drift`、`REQUIRED_EVIDENCE_*`）及各自的明细位置；`cut.py --normalize-only` 的帮助不再声称会“剪枝”计划。video-understanding §4/§5 改成两张表，列全 `understand.py` 的 13 个参数（含此前未写的 `--style`、`--edit-mode`、`--target-duration`、`--brief-only`、`--no-consolidate`、`--consolidate-asr`）和默认运行写出的产物（含此前未列的 `speech_boundary_anchors.json`、`understanding_index.*`、`consolidation.status.json`、`storyboard/*`）。video-voiceover 的命令示例补上 `--allow-partial-tts`，并把粘在一起的 `--voice-ref` 与 dub 缓存两条说明拆开。video-script 与 video-assemble 写明段落内相隔不到 1.6 秒的旁白块会紧接上一块播放、最多比写的 `start` 提前 1.2 秒，这一提前发生在校验之后。
- **参考文档去掉不存在的选项与环境变量。** `data-schema.md` 不再提 `--step script`，`config-playbook.md` 删除把写死常量 `NARRATION_COVERAGE_TARGET` / `NARRATION_BLOCK_SECONDS` 当成环境变量的一行。
- **不设 `MIMO_API_KEY` 重跑理解不再把真实转写覆盖成 `[]`。** ASR 缓存键去掉了 `mimo_asr_api_key_present`（旧 sidecar 里的这个字段忽略，升级后不会重新转写），无 key 的占位转写（`UNAVAILABLE_NO_KEY`）不再算缓存命中；没有 key 时，由 key 前缀决定的默认 endpoint 不参与 ASR、VLM 与 MiMo overview 的缓存比对，所以用 Token Plan key 做出的转写、画面分析和 overview 离线照常复用，overview 也不再被删除。缓存仍未命中（例如 `cp -R` 不带 `-p` 复制 work_dir 改了文件时间）而 `asr_result.json` 里已有转写时，运行停下并保留转写，提示用 `cp -p` / `cp -Rp` / `rsync -t` 保留时间重新复制，或设置 `MIMO_ASR_API_KEY` / `MIMO_API_KEY` 后重跑（会重新转写），并说明 `--skip-asr` 会把现有转写替换成 `[]`、不能拿来绕过，不再写 `[]` 后报成功；VLM 缓存失效而没有 key 时，在 ASR 之前就退出。consolidation 需要调模型而没有 key 时跳过、不发请求，`consolidation.status.json` 记新状态 `skipped_no_key`，brief 的可选阶段警告列出它；已缓存的索引照常复用。
- **故事索引的剧情时间与重复角色做确定性修复。** `plot_points[*].time` 里 `00:95` 这类秒数 ≥ 60 的写法按分×60+秒改写成规范 `MM:SS`，`约01:20`、`01:20左右`、`12.5秒`、全角冒号 `00：95`、`3分20秒`、`1.5分` 和区间 `01:20-01:45`（取起点）也能读，`第3分钟`、`第95秒` 这类序数单位读作该段的起点（`02:00`、94 秒，`第1分钟` 是 `00:00`），读不出或超出片长的时间删掉；同名条目视为一人，一条角色的名字等于另一条的别名时合并成一条（留在最先出现的条目位置，名字取组内最先出现的名字；第一条没有名字、只靠别名连进来时也不会留下无名角色或把关系改指成空；其余名字进 `aliases`），合并结果与条目顺序无关；只共享"男子""老板"这类别名的两人不合并，同时连到两个互不相连角色的条目（如名叫"男子"的路人，或别名里同时写着两人名字的条目）单独保留、不把两人并成一个，写成字符串的 `aliases` 当作一个别名，关系改指合并后的名字并去重。旧 work_dir 的索引在下次完整理解时就地修好、不调模型，`--brief-only` 生成的 brief 也读修复后的索引。
- **brief 说明哪些场景被 MiMo 内容审核拒绝。** 有 `analysis_status: moderation_refused` 的场景时，brief 多一行 `Moderation-refused scenes: N/M (Scene …)`，Scene timing guide 里对应的占位描述后加 `[moderation_refused]`，Agent 不会把空描述当成空镜头。
- **复制 work_dir 后 brief 的 storyboard 路径指向自己的目录。** `storyboard/*.json` 的 `page_images` 改存相对 work_dir 的 `storyboard/<文件名>`，`edited_video_path` 存 `edited_source.mp4`；旧版本写的绝对路径在缓存命中时改写，不重建拼图。
- **brief 的时长标签不再取整到分钟。** cut 的目标与剪后时长以前按整分钟显示，90 秒目标写成 `~2min`、101.5 秒的剪辑也是 `~2min`；现在不足一分钟写秒（`45s`），整分钟写 `2min`，其余写分秒（`1m30s`、`1m42s`），满一小时带上小时（`2h01m05s`，以前是 `121m05s`）。
- **cut 第二轮 brief 的 Scene timing guide 改用 OUTPUT 时间。** 第二轮要求按 `edited_source.mp4` 的时间写 `narration.json`，结尾却附着整片原片时间的场景表（含剪掉的场景、片尾演职员表，以及按原片场景算的"fully narrated"字数上限）。现在第二轮的这一节标题为 `## Scene timing guide (OUTPUT time)`，只列保留下来的片段，起止、安静窗口、帧动作、ASR 与字数上限都按输出时间计，被拆到多个片段的场景写作 `source scene N part M`；第一轮（写 `clip_plan.json`）仍是原片时间的场景表。第二轮各节也不再夹带原片时间：VLM 描述与深层分析里引用的原片时刻（`12.0s`、`00:30`）改写成输出时间，落在被剪掉部分的写 `[cut-away moment]`；只被剪进一部分的粗粒度 ASR 窗口不再列出整窗文字（其中有成片里听不到的台词），改为 `[partial ASR window: only part of it is in the cut, text withheld]`。
- **brief 相邻两节的场景号对得上。** Scene timing guide 写 `Scene 1` / `source scene 1`，`### Fusion scene` 与 `ASR chunk … | scenes …` 却印从 0 数的 `scene_id`，同一个场景在相邻两节差 1；现在 brief 里给人看的场景号都从 1 数（拆分场景写 `N part M`），`timeline_fusion.json` / `asr_writing_chunks.json` 里的 `scene_id` 不变。
- **`interrupts_source_sentence` 的建议入点不再把块挪到另一块上或越过它。** 以前建议的是入点之后的下一个锚点，可能远在 60 秒外，也可能正好是另一块的开头，照着改就重叠。现在建议入点前后 10 秒内离入点最近的锚点，要求整块按原时长平移过去后仍在前后两块之间（不越过相邻块，故事顺序不变），且不与任何块重叠、也不与它们相接（间隔须大于 0.15 秒）；前一块自己也收到建议时，后一块同时避开它原来和建议的位置，所以相邻两块的建议无论照改哪几条都不会互相撞上。cut 模式下平移后仍须在该块所属片段内，cut_output 下不超过 `--output-duration`；full 模式不知道视频结尾，这一点不检查。报告新增 `suggested_end`（平移后的结束时间）与 `max_shift_seconds`；范围内没有这样的锚点时 `suggested_start` 为 `null`，摘要写明前后 10 秒内没有可挪的锚点。lint 无 warning 通过时控制台打印 `narration lint：通过`，与有 warning 时的中文首行一致。
- **validate 的 lint 失败不再显示为 Python traceback。** `over_budget`、`interrupts_source_sentence` 这类普通 lint 失败以前抛出未处理的异常，最后一行只有 `#1: over_budget`（从 0 数的下标），改稿要的数字全在 `narration_lint.json` 里。现在 `validate.py` 以退出码 1 结束，只打印一段摘要：逐块写「段 N」（narration.json 里第 N 块，从 1 数）、错误码、关键数字（时间窗、字数、预算、硬上限、超出字数，或入点与建议入点）和改法，末行是 `narration_lint.json` 的路径；其他异常仍带 traceback。lint 通过但有 warning 时，控制台按同样格式逐条打印每个 warning，不再只有一行 `narration lint: 1 warnings`；失败摘要在 error 之后也列出全部 warning。cut_output 里时间窗短到 `slot_too_short` 的块不再吞掉同一块的 `over_budget` warning，两条都报（cut_output 的超预算仍只是 warning）。cut_output 越界错误和 `narration_review.md` 的 findings 也改用从 1 数的「段 N」；原声字幕里的去 AI 味 blocker（如破折号）写成「原声字幕第 N 条」并提示改 `original_subtitles.json`；`narration_lint.json` 的 `index` 与 `narration_review.json` 的 `segment` 仍从 0 数。

## [0.6.0] - 2026-09-27

三条主线：资源库与模板（登记与校验、项目绑定、每次运行的资源记录、字体文件、静态包装图层）；只读的本机剪辑台 dashboard；以及 skill 层分包、去内容哈希、测试审计后的架构梳理。

### Added

- **只读 dashboard（剪辑台）。** `video-recap/scripts/dashboard_server.py --root <目录>` 在本机回环地址启动标准库 HTTP 服务，按 `library.json` / `recap_project.json` / `recap_run_manifest.json` 发现资源库、项目与运行：总览给出下一步（QC 阻断、等 Agent 写的产物、授权与库错误），运行按阶段栏显示理解、剪辑节奏条、旁白、成片播放器与四轨时间线、QC 和 `resource_lock.json`，资源库分资源 / 模板 / 样片并附预览（模板参数按中文表列出来源，字幕样式与包装各有一块示意画布），项目页列出每个绑定解析到什么及运行时会下发的设置，⌘K 服务端搜索；只有阻断项与不会生效的绑定用红色，授权类提醒用警示色。严格只读：只允许 GET / HEAD，校验 Host / Origin，路径限定在 `--root` 内，媒体按白名单与 Range 提供；需要改动时只复制一句话给助手。视觉沿用 ZenStory 共用的 `tokens.css`。
- **静态包装图层。** video-assemble 读取 `work_dir/packaging_layers.json`，把包框、标题条、角标等图片按画布坐标叠到成片（遮原字幕之后、画面文字与字幕之前），并在 `timeline.json` 写出位置一致的 image 轨供剪映编辑；`--project` 绑定的 `packaging` 模板自动写出该文件，`resource_lock.json` 记录每个图层图片。
- **项目绑定 `--project recap_project.json`。** 把资源库里已采用的字幕样式模板、音色与 BGM 绑定到一次运行，解析为各阶段已有的 `SUBTITLE_*` / `BGM_PATH` / 音色参数；与显式设置冲突或模板画布与成片不符时在开始前停止。video-assemble 新增 `SUBTITLE_FONT_FILE`：烧录字幕经 `fontsdir`、画面文字经 `fontfile` 使用指定字体文件。示例项目在 `examples/demo-project/`。
- **运行资源记录 `resource_lock.json`。** full / cut 合成后汇总本次用到的原片、音色、BGM 与字幕字体，配置资源库时对上登记与授权状态，并在结束时打印需要人确认的项；`tts_meta.json` 新增 `voice`，记录实际使用的 provider、模型、音色或参考音频。
- **资源库格式与只读校验。** 素材库根目录下可登记资源（BGM、音效、音色、字体、图片）、带版本与采用记录的模板（字幕样式、包装图层）和样片；`video-recap/scripts/library.py check|list|show` 只读校验授权、声音授权、路径越界与引用完整性。格式见 `video-recap/references/resource-library.md`，合成示例在 `examples/resource-library/`。本期渲染不读取资源库。
- **video-cut `clip_plan.required_evidence`。** Agent 声明必保源片刻（节点、来源、原片秒、轨道、先后关系），工具在句界/画面吸附之后、渲染之前核对；缺段、错序或无效声明写入 `clip_plan_validated.json.qc.required_evidence` 并阻断，缓存复用同样重检。
- **宣发文案修订工作流。** `video-script/references/promotional-copy.md`：不重跑故事链，只修改已完成短片的文字层。
- **公共环境变量清单。** `tests/orchestrator/env-inventory-v1.json` 列出六个 skill 读取的全部环境变量及分类，配套测试对源码做 AST 扫描，未登记或疑似凭证的读取会失败。
- **video-cut `--review-shots`。** 扫描实际渲染文件内部的短镜与密集切点（只召回、不修复），结果写入 `shot_review.json` 并绑定计划/源/成片指纹；`--shot-roi` 可按实测画窗扫描。短镜阈值按实测帧率推导，不再固定 24 帧。
- **ASR 时序证据 sidecar。** `asr_timing_evidence.json` 记录来源指纹、可用性状态与词级对齐是否执行，词表修正与原始转写分列，粗窗不再被当作精字幕；brief 显示经验证的状态与指纹，缺失或陈旧时显示 `MISSING_OR_STALE`。
- **自托管 TTS 端点。** `--tts-provider index-tts` 通过 `INDEX_TTS_ENDPOINT` / `INDEX_TTS_VOICE` 接入 index-tts 协议的 JSON→WAV 服务；端点只以 sha256 落盘，拒绝带凭证的 URL 与重定向，`doctor` 离线校验配置而不探测连通性。每段 TTS 缓存与结果记录 provider receipt 与处理后 WAV 的 sha256。
- **最终 QC 可选阻断。** `--require-final-qc` 开启后，`final_qc.json` 与 `golden_eval.json` 摘要必须均为 `ok: true` 且 `blocker_count: 0`，否则不打印完成、非零退出；续跑命令保留该选项，不影响缓存指纹；不支持 dub 模式。
- **声音路径显式化（assemble）。** `assemble.py --audio-mode {narration,source-mix,adopted-packet-copy}` 与 `--audio-stream-index`：`source-mix` 不读 `tts_meta.json`、只对所选原声流做音量/BGM/响度处理；`adopted-packet-copy` 复用已采用的完整混音并按 AAC 包逐包比对，不重编码、不裁尾。`assembly_qc.json` / `assembly_manifest.json` 记录 `audio_mode` 与实际执行的音频操作；`pair_media.py` 可把独立画面与已采用音轨按流复制配对并证明包身份。
- **批准稿保护。** `--preserve-approved-text` 贯穿 full / 单源 cut / 多源 cut 的校验器再到 TTS：文本装不下窗口时列出具体段落与时长，不自动缩稿、不静默变速；失败不沿用旧的 `tts_meta.json`，成功元数据原子写入。
- **独立字幕轨。** `subtitle_track.json` 以整数 tick 绑定当前音画（仅 `adopted-packet-copy` 模式），标注估计 / 校准 / 强对齐精度，渲染前核对陈旧轨与不可显示短 cue；投影到 ASS 厘秒时保证在同一帧翻转，`assembly_manifest.json` 只引用当前绑定的轨。
- **前景合成。** `compose_foreground.py` 把调用方渲染好的 RGBA PNG 序列（可选片尾卡）叠加到锁定母版，音频按包复制并逐包核对不变，输出帧钟与解码元数据核对后才写入新目录。
- **声音路径显式化（recap）。** `recap.py --audio-mode {narration,source-mix,adopted-packet-copy}` 把声音策略与 `--edit-mode` 解耦：`source-mix` / `adopted-packet-copy` 不跑校验、评审和 TTS，full 直接合成，cut 剪完不再等 `narration.json`；运行清单记录音频策略，错配或含未绑定 `narration.json` 的 work-dir 被拒绝，续跑命令保留选择。
- **配音采用绑定（assemble）。** `--tts-meta` + `--narration-adoption` 把已采用的文字、处理后 WAV 指纹、请求的引擎/声线与速度策略绑定到实际混音：输入快照、派生 WAV 封存、隐藏候选渲染后经 QC 再与 `narration_input_binding.json` 一起发布或一起放弃；采用的速度策略按形状与范围校验，不再只接受全 1.0；旧入口保持兼容并标记为未核验。
- **显式完整混音（assemble）。** `source_score.py` 从原片声音流按精确帧区间重建原声轨、连续音乐轨与 `prepared_bed.wav` 并出具回执；`--audio-mix-adoption` 把已采用的底轨、逐段 48 kHz 配音落点与固定 master gain 渲染成最终音轨，跳过环境 BGM/duck/loudnorm/tempo，与 `narration_input_binding.json` 一起以 `audio_mix_binding.json` 事务发布；29.97/59.94 fps 画面按精确分数投影到采样钟。剪映时间线导出时，跳过的旁白段不再让其后段落的增益与采样落点错位。
- **已采用配音的本地复用（recap）。** `--tts-meta` + `--narration-adoption` + `--audio-mix-adoption` 三件套走严格 assembly-only 路径：只接受单视频、full、narration、音轨 0、新工作目录和未存在的交付文件；全有或全无、显式 TTS/评审/QC/导出参数一律拒绝；运行清单以 sha256 封存三件套，子进程 binding 与清单不符或交付文件非本次产出时不删除、非零退出，清理以 `assembly_manifest.json` 的实际输出为准。
- **批准稿保护与自托管 TTS 贯通编排器。** `recap.py --preserve-approved-text` 在 full / 单源 cut / 多源 cut 的 TTS 前把保护参数交给真实校验器与配音器，续跑命令保留；`--tts-provider index-tts` 显式透传，不能与 MiMo 声线参数或 dub 同用；`source-mix` 拒绝含显式 `subtitle_track.json` 的 work-dir。

### Changed

- **编排路径梳理。** video-recap SKILL.md 增加 `--edit-mode × --audio-mode` 路径表；编排器单视频与多视频流程共用同一段收尾（评审 → TTS → 合成 → QC），不改行为；新增 `docs/architecture.md`。
- **skill 脚本按功能族分包。** video-assemble 新增 `scripts/adoption/`（narration_binding、audio_mix_binding、strict_inputs、strict_publish、frozen_audio）、`scripts/jianying/`（原 `jianying_*`）、`scripts/subtitles/`（原 `subtitle_*`；引用方改为 `subtitles.track` / `subtitles.track_binding`）；video-recap 新增 `scripts/qc/`（原 `mimo_qc_*`）；video-understanding 新增 `scripts/briefing/`（原 `agent_brief` / `brief_*`）；video-voiceover 新增 `scripts/providers/`（fish_audio、index_tts）。公开入口脚本仍在各 skill 的 `scripts/` 顶层；`recap.py --help` 按功能族分组显示参数。
- **第二轮去防御：消费方不再重验生产方契约。** 沿用 0.5.0 的"在边界校验一次，之后信任契约"：`validate.py` 不再复刻 `narration_lint` 的形状检查（lint 补上有限值与时间顺序检查，`invalid_approved_shape` 改为常规 lint 错误码）；review/brief 对自建 bundle、review、clip_plan_validated 直接取字段；assemble 对 tts_meta / assembly_qc / timeline 直接取字段，剪映 builder 不再重检 contract 已保证的字段，CLI 组合检查只在 API 层做一次；recap 的 final_qc / recap_review / recap_inspect / mimo_qc 不再为不存在的产物形态兜底；understanding 的 `get_video_duration` 在 ffprobe 失败时抛错而不是返回 0.0，损坏的自产 JSON 一律抛错而不是当作"缺失"或"缓存未命中"；voiceover dub 的 ffmpeg 失败、畸形 ASR 响应、损坏缓存 sidecar 不再被吞成空行或静默重合成。`CONFIG.get(key, default)` 对已声明的键改为 `CONFIG[key]`，删除过期默认值。SKILL.md 去掉跨技能复述的免责与禁令，共享规则只在拥有它的技能里写一次。
- **skill 层瘦身。** SKILL.md 去掉跨技能重复的创作模式定义、密集切点规则和 TTS 供应商细节，各自只在拥有它的技能里写一次；recap 的参数清单改为指向 `--help`。长段落下沉到 `video-voiceover/references/index-tts.md`、`video-assemble/references/packaging.md`、`source-score.md` 与 `video-cut/references/shot-review.md`。`timeline-and-jianying.md` 移到 `docs/`，`env-inventory-v1.json` 移到 `tests/orchestrator/`。
- **同一句源字幕跨同源连续剪点时先合并再筛短片段**，不再把一句话切碎；不同源、真实删段、输出空隙不合并。`SUBTITLE_RENDER_VERSION` 提升到 9。
- **SRT 毫秒改为向下量化**，避免帧边界时间被四舍五入后延迟一帧；负值钳到零。
- 剪辑手法与审稿提示补充：保住动机与接受条件、反打是否新增信息、跨场镜头不得拼成虚假因果、只写证据已呈现的结果、REVISION 只提可定位的局部修法；brief 不再把 ASR 行尾当作安全剪点，改为听审后再定。
- **`recap.py` 关闭 argparse 前缀缩写**（`allow_abbrev=False`），显式选项由 parser 记录到 `args._explicit_options`，后续守卫不再靠扫描 `sys.argv`。
- **理解缓存不再把全空转写当作有效命中**（`EMPTY_UNKNOWN` 与 `UNAVAILABLE_NO_DURATION` 同样视为 MISS）；没有 sidecar 的旧缓存以 `LEGACY_UNVERIFIED` 复用。ASR 音频提取或 provider 失败时清理陈旧的 `audio.wav` 与 `asr_result.json`，时长改从提取后的 `audio.wav` 读取。
- **批准稿结构校验拒绝 `end <= start`**、乱序与空文本，结构错误以清晰的 `SystemExit` 报出并写入 `narration_lint.json`；cut_output 模式下 `--output-duration` 缺失或越界同样记录到 lint 文件，不再留下过期的 PASS。

### Removed

- 入口模块不再再导出内部函数：`mimo_qc.build_report` / `sample_video_frames` / `write_report`、`cut.load_clip_plan` 等四个、`brief.lint_narration` / `validate_narration_or_raise`、`assemble.assembly_settings_payload` / `final_loudnorm_filter`；请从所属模块导入。`recap_inspect --json` 不再输出恒为空的 `forward_state_files`。剪映导出删除无法到达的 `material_category_registry` 与未知轨道分支（未知类型本来就由时间线契约拒绝）。
- video-script 删除无人调用的 brief 生成链（narration.py / agent_brief.py / brief_*.py，约 1,300 行）及其专属 CONFIG 键，brief 行为测试移到 understanding 组；video-recap 删除与 video-script 字节相同的 creative-editing-playbook.md 副本与和 video-understanding 近重复的 research-guide.md，README / data-schema 改指拥有它们的技能。
- `video-understanding/references/data-schema.md` 只保留本技能产出的产物（vlm、asr、asr_timing_evidence、asr_writing_chunks、silence、timeline_fusion、deslop_qc_requirements）与输入 `background_research.json`；narration / clip_plan / style_card / deslop_qc 等段落改由 video-recap 的完整契约与创作简报说明，减少约 135 行重复。
- **video-cut 旧版单阶段旁白映射路径。** `cut.py` 不再读取 `narration.json`、不再把原片时间的旁白映射为 `narration_mapped.json`，`--narration` / `--no-narration-map` / `--allow-sparse-cut` 参数随之删除；`recap.py --allow-sparse-cut` 同步移除。唯一支持的 cut 流程是先剪后配：Agent 对着 `edited_source.mp4` 按输出时间线写 `narration.json`。

### Fixed

- **brief 永远拒收 `asr_clean.json` / 曾拒收 `understanding_index.json`。** `brief_context.py` 手抄的清洗 prompt 与 `consolidate.py` 漂移后指纹永不匹配；消费方不再重算生产方的 `prompt_md5`，只核对 `source_md5` 与 `model`。
- **`recap_inspect.py state` 单源 cut 的来源总是 `unknown`。** 它读取的 `source_video_fingerprint` 从未被 video-cut 写出；改读 sidecar 实际记录的 `source_fingerprints`。
- **显式混音路径的 `assembly_manifest.json` 被第二次写入覆盖为 `audio_mix_binding: null`。** 删除 try 块外重复的 manifest 构建，最终 MP4 也少哈希四次。
- `timeline.json` 的旁白起点改为向下取整到 1e-4 秒网格，序列化后不再截掉已放置音频的首个采样。
- 已放置的旁白 WAV 若为 IEEE float 格式（Python `wave` 不支持），改用 ffprobe 读取时长，不再在装配和一致性检查时报错。
- **ffmpeg 9 上长剪辑、长旁白渲染失败。** FFmpeg 9 删除了 `-filter_complex_script` / `-filter_script`：片段多的 cut、段落多或遮罩长的最终合成在渲染时报 `Unrecognized option`，loudnorm 首遍测量每次失败并静默降级为单遍 loudnorm 目标 + limiter。现在按本机 ffmpeg 实际支持的写法传参（7.0 起用 `-/filter_complex 文件`，更早版本用旧选项）。
- **dub 模式在没有 libass 的 ffmpeg 上无法启动。** dub 不烧录字幕，却被字幕烧录预检拦下（Homebrew 的 ffmpeg 自 2026-01 起不含 libass）；现在 dub 跳过该预检，显式传 `--burn-subtitles` 时直接报参数错误。

## [0.5.0] - 2026-09-05

两条主线：新增 Fish Audio TTS 通道与《锅火》60 秒案例；以及一次以「在边界校验一次，之后信任契约」为原则的全量瘦身——删除约 2,600 行防御式代码，把校验集中到真正的输入边界，并修复审查过程中发现的三处真实缺陷。行为收紧之处见 `Changed`。

### Added

- **TTS 可切换到 Fish Audio。** `--tts-provider fish-audio`（或 `TTS_PROVIDER=fish-audio`）搭配 `FISH_API_KEY` 即可使用；默认走 `s2.1-pro-free` 与内置「娱乐扒妹」解说音色（reference ID `5653cea4ac83480aaf2bf45406556185`），`FISH_TTS_REFERENCE_ID` 可覆盖。MiMo 仍是默认路径，不受影响。该免费模型无 SLA、受 Fair Use Policy 约束，官方公示的免费开放期至 2026-08-31，之后以 Fish Audio 官方政策为准。(#70)
- **《锅火》60 秒案例。** `examples/guohuo-60s/` 收录一次完整创作的产物：核心 cut、音画锁定、包装探索、看片反馈、局部 conform / 再剪与冻结项复核，并覆盖多集选段、原声与旁白分工、Fish Audio 配音与 TTS 对齐字幕，以及通过恢复源镜头连续性修复不自然接点。仓库不包含原剧音视频二进制。(#70)
- **面向密集换镜与返修流程的可复用剪辑指引。**(#70)

### Changed

- **技能脚本改为在边界校验一次，之后信任契约。** 各 skill 内部大量 `.get(key, default)`、`isinstance(...)` 兜底与 `try/except` 被移除：由本 skill 自己写出的产物（`tts_meta.json`、`timeline.json`、`clip_plan_validated.json`、QC 报告等）按字段直接读取，`CONFIG[...]` 直接取键。校验集中在真正的输入边界：`narration_lint.py` 是 agent 手写 `narration.json` 的唯一校验器，`jianying_timeline_contract.py` 是剪映时间线的唯一校验器。
- **配置与探测失败改为显式报错。** `env_int` / `env_float` / `env_bool` 遇到无法解析的环境变量不再静默回退默认值；`ffprobe` 读不出时长或视频流时抛错，不再退回 `0.0` 或 1280x720 默认画布——错误的画布会静默产出错位的字幕几何。
- **剪映资源契约收敛为规范 snake_case 对象。** `resources` 条目必须是带 `source_path` 的对象（不再接受裸字符串），`main_config` 必须是对象（不再接受 JSON 字符串或文件路径），不再接受 Jackson 的 `mainConfig` / `resourceId` / `coverImg` 别名，也不再按 `.cube` / `.ttf` 后缀推断 `resource_kind`。外部输入请在调用本 skill 前完成适配。
- **`MIMO_TOKEN_PLAN_CLUSTER` 取值非法时报错**，不再静默回退到 `cn` 集群。
- **仓库迁移到 `zenstory-ai` namespace。** 文档、marketplace 与安装指引中的地址改为 `zenstory-ai/video-recap-skills`；按旧地址安装的用户需要重新指向新仓库。

### Fixed

- **`narration.json` 的 `visual_overlays` 现在会被 lint 校验。** 此前它是唯一没有校验器覆盖的 agent 手写字段：缺 `type` / `text` 的 overlay 能通过 lint，却让 recap 编排器在 TTS 跑完之后才以 `KeyError` 崩溃。校验前移到 TTS 之前，崩溃变成可读的 lint error。
- **静音 TTS 块不再中断配音。** 零帧 WAV 会原样透传并返回中性的响度元数据，不再因除以零样本数而抛 `ZeroDivisionError`。
- **没有 ffmpeg 的机器上交付 QC 不再崩溃。** `_probe_audio_sample_rate` 属于观测性质的 delivery QC，且会在尚未渲染的计划上运行，因此 ffprobe 不存在（`OSError`）与「报告不出采样率」按同一种结果处理。渲染路径上的探测仍然照常抛错——没有 ffmpeg 本来就剪不了片。

### Security

- **凭证不再传入只需要一个判断位的 URL 构造函数。** `default_mimo_api_url` 改为接收 `is_token_plan` 布尔值，由调用方先用 `is_mimo_token_plan_key` 归类；它的返回值会被 `doctor.py` 打印，因此密钥本身不应流入。解析出的 URL 行为不变。
- **CI workflow 显式声明最小权限。** `skill-validate.yml` 补上 `permissions:` 声明，不再继承仓库默认的宽松 token 权限。(#72)

## [0.4.0] - 2026-07-27

汇总 `v0.3.3` 之后的全部工作：多源剪辑、QC、字幕与配音改进，可携带剪映草稿能力，内容驱动的创作流程，以及一轮深度审查带来的正确性、性能与配置面修复。

### 新增

- **贴合原片字幕带。** `tools/measure_subtitle.py` 用 stdlib + ffmpeg 抽帧、检测并输出红框预览；`--subtitle-y-top/--subtitle-y-bot` 把新字幕基线与字号适配到测得坐标，并显式启用该区域遮罩。
- **半透明、解说时段遮罩。** 显式启用原字幕遮罩后，默认改为 `SUBTITLE_MASK_OPACITY=0.6`、`SOURCE_SUBTITLE_MASK_TIMING=narration`，原声留白不再常驻黑条；仍可设为 `1` / `all` 恢复全黑全时段效果。
- **参考音色解说。** recap / voiceover 新增 `--voice-ref`，通过 `mimo-v2.5-tts-voiceclone` 给普通解说克隆音色；新生成时参考音频惰性转码一次、最长取 30 秒，内容与转码版本指纹参与 TTS 缓存校验。
- **正式的建议性 MiMo QC。** `--mimo-qc pre-assemble|post-render|both` 在单源/多源流水线的组装前、成片后各最多发起一次 MiMo 请求，把语义/审美观察聚合进 `mimo_qc.json`；内容缓存、`--mimo-qc-refresh`、最多 6 张/768px 临时抽帧、密钥/base64 不落盘均有回归覆盖。缺 key、401/429、超时、畸形响应和本地异常全部 fail-open，只提示 Agent/用户，永远不生成 blocker。
- **可携带剪映草稿。** video/audio/image 默认打包到 `Resources/local`；timeline v2 新增恒定变速、倒放、transform、富文本/逐字样式、转场/mask/LUT、绿幕复合和离线资源轨道。

### 改进

- **内容驱动的创作流程。** Agent 在剪辑或写稿前先比较剪辑假设，记录 POV、戏剧问题、change-based beats、具体画面/反应、`audio_owner` 与 `narration_job`；`recap_story_plan.json` / `visual_audio_board.json` 同时覆盖单视频与多视频 cut。旁白比例改为素材决定，`7:3` 只保留为粗略回退而非配额。
- **阶段技能完全自包含。** 阶段说明与本地参考不再引用兄弟技能的路径或名称；写稿阶段拥有独立的补充调研指南与 `deslop_qc.py`，不会假装新调研已被既有 VLM 消费。
- **多视频证据更可用。** 项目 brief 优先摘取逐来源的背景、索引、ASR 与场景证据，不再只截取通用写作说明。

### 变更

- **每个配置旋钮只声明在读它的技能里。** 六份 `lib.py` 中有五份此前携带所有技能配置的并集（各 155–173 个键，其中 95–132 个该技能从不读取）；`video-cut` 一直是反例（8 个键，全部使用）。现在各技能只声明自己读取的键，删除 583 条无效声明。保留集由运行时插桩实测得出（按调用栈把 parity 测试的读取与生产代码的读取区分开），而非静态匹配——这也是发现下列两处问题的方式。
  - `zone_fade_seconds` 在五份副本中均有声明，但**全仓库无任何读取点**，此前仅靠「副本之间取值一致」的断言存活。
  - `CLIP_PADDING` 在 `video-cut` 中依然无效：该技能的 `lib.py` 从未声明此键，查表始终落到内联默认值。
- **音频策略一致性断言改为按实际声明范围校验。** 此前断言五份副本对约 30 个音频键取值一致，而多数技能并不读取它们——这种一致性是复制行为自身造成的循环要求。新增不变量：**任何技能不得声明自身从不读取的配置**，从结构上杜绝上述两类问题复现。

### 修复

- **画面锚点整体偏移 1/fps 秒。** ffmpeg 的 `fps` 滤镜首帧在源时间 0，而文件编号从 1 起，所以 `frame_00001.jpg` 是 `t=0`、第 n 帧是 `(n-1)/fps`；此前按 `n/fps` 计算，把 `frame_facts`、场景归属与 storyboard 标签整体推后 1/fps 秒（>5min 视频默认 fps=1，即整整 1 秒）。这些时间戳会写进 VLM prompt 并作为写稿 Agent 的权威画面锚点。换算规则收敛到 `extract.py` 独家定义，帧/VLM 缓存带约定版本号，旧产物强制重算。
- **带封面图的素材渲染直接失败。** 组装映射 `0:v` 会匹配到 attached_pic 那一路视频流，而 `-vf` 只作用于第一路，ffmpeg 报 `Could not write header` 并留下损坏文件——发生在整条流程的最后一步。改为 `0:v:0`。
- **`--clip-padding` 非零时相邻片段被误判为重复素材。** 重叠检测此前基于加过 padding 的区间，任意两个背靠背片段都会硬失败；现按 Agent 实际写入的入出点判断。
- **`CLIP_PADDING` 环境变量此前完全无效。** 六份 CONFIG 都声明了该键并通过 `clip_padding_source` 汇报为生效，但唯一实现 padding 的 video-cut 只读 CLI 参数。
- 多视频输出时间线的 speech 证据改为与 `audio_mix` / `sentence_boundaries` 一致优先读 `asr_clean.json`；`narration_coverage_max` 等三个只读不声明的 CONFIG 键补齐；`silencedetect` 超时改为与该函数其余失败路径一致地 fail-open；多源剪辑的句子截断阻断项不再挂在无关条件下。
- 亮色画面不再把整片背景误并为字幕候选；测量结果按源隔离，失败时保留上一轮成功产物。
- 校订版原声字幕使用逐窗口全不透明遮罩，避免与原片硬字幕重影；测量坐标拒绝不兼容的非底部 ASS 对齐。
- voice reference 使用同一不可变快照完成转码和缓存标识，进程内重复调用不再继承上一次音色；CLI 与环境变量统一提前校验。
- 长视频的遮罩滤镜超过安全命令长度时改用 ffmpeg filter script，避免 Windows `CreateProcess` 上限；measured band 统一为 `[top, bot)` 并成为视觉 QC 的真实安全区。
- 测量产物提交失败会回滚整组旧产物；recap 通过显式 assemble 参数传递字幕坐标，不再污染进程环境。
- 删除测试专用/生产不可达的 assemble、review、audio automation、QC rule-loader 兼容层；MiMo/其他 non-deterministic finding 不再有 allow-list 升级 blocker 的逃生口。每个 skill 的 `lib.py` / `brief.py` / `narration.py` 复制仍刻意保留，确保单独 clone/安装即可运行。

### 性能

- **不再重复哈希/解码/探测同一批字节。** `file_fingerprint` 按进程记忆化（内容寻址不变，仅用 dev/inode/size/mtime_ns 作记忆键）：一次理解流程原本要对源视频做 8–10 次全量 sha256、对整套抽帧做 2–3 遍，40 分钟视频约 2400 张全分辨率 JPEG。VLM 的逐帧 base64 缓存由无上界字典改为 LRU；续传缓存由每个场景整份重写改为按批落盘；TTS 响度归一从三遍纯 Python 遍历改为 `array('h')` 单遍（输出字节与元数据完全一致）；命中缓存的 TTS 段不再逐段 ffprobe；黑/白帧场景过滤按场景并行。

### 构建

- `pyproject.toml` 显式声明 ruff 规则集。CI 不固定 ruff 版本，而 0.16 起默认规则集被大幅扩展，会让无关 PR 的 lint 步骤失败。
- `scripts/test.py` 将「未安装 pytest」与真实测试失败区分开。

### 测试

- 测试入口统一到跨平台 `scripts/test.py`；CI 中 frontmatter、manifest、prompt anchor 与隔离导入契约全部改为 pytest 行为/结构测试。
- 合并重复测试并增加动态 skill 发现、精确重复测试体检测、测试组注册、自包含边界、创作 JSON 结构与 multi-source brief 回归覆盖。

### 验证

- 全套 `python3 scripts/test.py` 通过（801 tests）；`ruff`、`compileall`、修改模块 `mypy` clean；其中 assemble 275 tests 覆盖剪映草稿协议、timeline 迁移和便携资源写入。
- 真实 ffmpeg 合成字幕样片验证：测量工具识别 `y=[613,637)`；遮罩像素在留白帧为 `128`、解说帧为 `51`。
- 剪映专业版 `10.8.7-beta1` 实测：视频/解说/BGM/字幕/图片轨在线，预览、保存、关闭与重开正常。

## [0.3.3] - 2026-06-28

多源视频剪辑解说 + 文件系统素材库复用为主线，并合入跨 harness 支持、成片兼容性与竖屏字幕修复、解说评审硬闸等改进。

### 新增

- **多视频剪辑解说（cut 模式）。** 一次传入多个源视频，按 `source_id` 选取片段，剪成一个成片；项目级 `multi_source_manifest.json` 作为 recap / cut / assemble 的来源契约，`clip_plan.json` 每个片段带 `source_id`，重叠检测按源隔离。多视频 MVP 仅开放 `--edit-mode cut`。
- **文件系统素材库复用。** `--material-library-dir` 搭配 `--save-materials` / `--use-materials`，把每个源视频的分析产物沉淀为 grep 友好的 `material.json` / `material.md` / 追加式 `materials_index.jsonl`，不复制原始媒体；按源指纹 + 设置指纹门控恢复，复用前清理旧 work dir 的残留产物。无 DB / embedding / 语义检索，纯文件系统 + `grep`。
- **多源 provenance 透出。** `video-assemble` / `recap_inspect` 在时间线与剪映草稿中保留 `source_id` / `source_path`；个别源缺失时按片段降级并显式标记，保留其余在场源的来源，而非丢弃整条时间线。
- **`video-understanding --brief-only`。** 从已恢复 / 缓存的分析产物重建 OUTPUT 时间轴 brief，不重跑抽帧 / ASR / VLM / 外部 API。
- **跨 harness 支持 + Claude Code marketplace。** Codex 与 OpenClaw 直接读取 `.claude-plugin` 包，无需每 harness 文件；marketplace 命名为 `video-recap`。(#50)
- **解说评审 scorecard + dub-lint 硬闸 + partial-TTS 可见性。** (#49)

### 改进

- **单视频 full / cut / dub 行为保持兼容。** 多视频仅在 cut 模式开放；单源剪辑滤镜图保持不变（字节级一致）。

### 修复

- **异源 concat 几何归一化。** 多源片段先归一到统一画布（scale / pad / setsar / fps / yuv420p）再 concat，分辨率 / SAR / 帧率不同的源视频不再让 ffmpeg 报错；不同分辨率的多视频可正常合成一个成片。
- **多源音轨按源处理。** 个别无声源不再导致整段成片静音；每个片段都有音频（原声或合成静音）。
- **密钥脱敏更精确。** 只脱敏凭证形态（`tp-` / `sk-` / `gh*_` / `AKIA` / JWT 与 `KEY=VALUE`）与凭证命名的 JSON key，不再误伤 transcript / summary 里的 `secret` / `token` 等普通词，也不再把多个 key 合并丢值。
- **出片强制 `yuv420p` + faststart。** 微信 / 手机可播、边下边播。(#51)
- **字幕样式按探测画布缩放。** 修复竖屏 (9:16) 字幕被拉伸。(#53)

### 验证

- 全套 `python3 scripts/test.py` 全部 skill groups passed（551 tests），`ruff` / `compileall` clean。
- 新增真实 ffmpeg 多分辨率 + 混合音频渲染测试（验证异源 concat 与音轨归一化）；密钥脱敏保留正常词 / 不合并 key / 凭证形态测试；assemble 按片段降级（保留在场源 provenance）测试。

## [0.3.2] - 2026-06-22

让剪映草稿导出跟上新版工程结构，方便在剪映专业版里继续精修。

### 新增

- **新版剪映 schema-driven 草稿导出。** 剪映导出从单文件 JSON 拼装拆成 schema / model / builder / track / writer 分层，草稿基线升级到 `version: 360000`、`new_version: 111.0.0`、`app_version: 5.9.5-beta1`，并补齐包含 `common_mask` 在内的新版 `materials` skeleton。
- **素材类型注册表与能力清单。** 明确区分已支持的 `video` / `audio` / `text` / `subtitle` / `speed`，以及预留但暂不写出的 image/sticker/effect/mask 等类别；未知或暂不支持类别会输出 note 并跳过，避免生成畸形草稿。

### 改进

- **剪映导出仍保持可选、懒加载、stdlib-only。** `export_jianying.py` 现在只是薄 facade，核心 ffmpeg 渲染路径不会导入任何 `jianying_*` 模块；`timeline.json` 仍是后端无关的 canonical input，ffmpeg 仍是最终成片判定标准。
- **草稿写入更安全。** 写入器继续保留非空目录避让、媒体打包、路径重写、临时目录原子替换；并新增 `draft_name` 校验，拒绝空名、绝对路径、`..`、以及路径分隔符，防止错误名称逃逸草稿父目录。
- **BGM 循环与音量自动化覆盖更完整。** 循环 BGM 会拆成多段铺满时间线，并把窗口内 `KFTypeVolume` 音量关键帧放到对应片段。

### 验证

- `ruff` / `py_compile` / `mypy --ignore-missing-imports` 覆盖剪映导出模块；相关 assemble/timeline 测试 84 passed，全项目 `scripts/test.py` 全部 skill groups passed。
- 本机剪映专业版 `10.8.7` 实测：生成并打开 schema E2E 草稿；又把历史 `longvacation_2min_work/timeline.json` 转成 `recap_tmp_convert_longvacation_2min_20260623_003900`，剪映已登记并可打开。

## [0.3.0] - 2026-06-20

长视频更稳、跨语言更干净、剪辑更顺眼，并新增解说导航与成片压缩工具。

### 新增

- **VLM 场景分析可断点续传 + 限流自愈。** 长视频（数百场景）过去偶发 HTTP 429 会让整轮画面理解失败、再跑得从头重来。现在每个场景分析完即落盘（`vlm_scene_cache.json`，原子写），失败只重试缺失/失败的场景；遇到限流(429)的场景自动降到 ¼ 并发重试一次，持久性错误（空响应／解析失败）不重试。默认 8 并发不再拖垮长视频。
- **跨语言解说降噪 `FOREIGN_SOURCE_AUDIO`。** 当原片语言与解说不同（如日剧配中文解说）时，解说下方被压低的原声本就听不懂、还会被当成「怪音」。该开关把解说下的原声压到近静音（0.05），而原声留白块仍保持满音量；显式 `SPEECH_DUCKING_VOLUME`／`ZONE_DUCKING_VOLUME` 仍可覆盖。
- **剪辑边界吸附原片切镜头 `SCENE_CUT_SNAP`。** 片段边界若落在原片硬切点附近，会先闪一下相邻镜头再切，形成可见闪烁。新增一道吸附（在自然停顿吸附之后）：用 ffmpeg 在窄窗口里探测原片硬切并把边界移上去（每片段约 2 次轻量探测，复用现有缓存）；已对齐或附近无切点的边界不动，会把片段压到 ~0.5s 以下的吸附跳过。
- **成片压缩参数 `OUTPUT_CRF` / `OUTPUT_PRESET` / `OUTPUT_MAX_HEIGHT`。** 最终混流过去硬编码 `-crf 18 -preset veryfast` 且从不缩放，成片体积偏大。现可调 CRF／preset／高度上限（缩放放在最后，遮挡与字幕先在原分辨率渲染再随帧缩小，更清晰）；默认仍是 18／veryfast／不缩放。demo：长假 2 分钟成片由 119MB 降到 16.9MB。
- **解说导航工具（咨询性，不影响成片）。** 新增只读的 `inspect`（`state` 看流程进度／源视频指纹／下一处暂停；`clip-map` 在成片↔原片时间轴间精确换算，回答「成片 30–60s = 原片哪段」）与视频故事板（源时间轴 + 剪辑成片时间轴的缩略图总览，写作时扫一张图就能定位转场／反转，复用已抽帧不重抽）。任一缺失或异常都只降级提示、绝不阻断流程。

### 变更

- **手动评审自动按成片时间轴。** `review.py` 的 `--timeline` 默认改为 `auto`：检测到已验证的剪辑成片（`clip_plan_validated.json` + `edited_source.mp4`）就按成片时间轴评审，否则按原片。消除了 cut 模式下手动评审把原片时间当成成片、误报一堆「幻觉」的问题（demo 上 4 个假阳性 → 1 个真问题）。编排器显式传入的 `cut_output` 仍优先。
- **ASR 默认分段 30→15s。** 时间戳最坏误差大致减半，并重新启用静音／ASR 交叉校验；代价是每个视频约 2× 顺序 ASR 调用（`ASR_SEGMENT_SECONDS` 可调）。
- **自带字幕在不遮挡时也显示。** 原声留白字幕过去被绑在「遮挡原字幕(mask)」开关上，干净／外语片源（无烧录字幕、mask 关）会连同自带 `user_subtitles.*` 一起被丢掉。现解耦：有自带字幕文件即视为明确意图、在留白处照常显示（干净片源无重影风险）。与 `FOREIGN_SOURCE_AUDIO` 搭配适配「外语剧 + 自带中文字幕」。

### 修复

- **cut 模式 pass2 简报崩溃。** 被拆分的场景拿到字符串 id（如 `"5.0"`）、未拆分的仍是 int，`sorted(scene_ids)` 因 int／str 混排崩溃。改为类型安全排序键（int 在前、拆分串在后），并对字节孪生的 `brief.py`／`narration.py` 同步修改（md5 保持一致）。
- **原声留白字幕滞后。** 粗粒度 ASR（按时钟分箱、按字符位置估时）会让某句晚显示约 6–8s。现「精确来源」与 ASR 兜底都改为按整句、从留白起点顺序排布，多句不再重叠或散到字符比例尾槽。
- **成片压缩两处健壮性（发布前评审发现）。** 奇数 `OUTPUT_MAX_HEIGHT`（如 721）过去会产生奇数高度、被 libx264／yuv420p 拒绝 → 空成片 + 笼统报错；现强制宽高都为偶数。`OUTPUT_CRF=0`（无损，合法值）过去被当假值改成 18；现原样保留。
- **inspect 测试接入 CI。** 新增的 inspect 测试组此前只写进 `scripts/test.sh`，而 CI 实际跑的是 `scripts/test.py`，导致这 22 个测试从未在 CI 运行；现已补进运行器。

### 其他

- demo 换成《悠长假日》第一集 2 分钟 cut 模式解说，集中展示本轮能力（无闪烁边界、跨语言降噪、自带中文字幕留白、CRF24/720p 压缩）；并更新 README 中的 demo 链接。

## [0.2.3] - 2026-06-19

一轮成片质量打磨：原声留白字幕更准（可自带字幕）、画面理解更密、解说去掉破折号、评审更稳。

### 新增

- **自带原声字幕（更准）。** 解说留白处的原声字幕，除了 Agent 校对、ASR 兜底之外，现在可以直接放一份准确的字幕文件作为**首选来源**：`work_dir/user_subtitles.json`（`[{start,end,text}]`，默认按成片时间轴；或写成 `{"timeline":"source","lines":[...]}` 用原片时间轴，按剪辑计划自动映射到成片）或 `user_subtitles.srt` / `.ass`（默认按原片时间轴映射）。优先级：自带字幕 › Agent 校对的 `original_subtitles.json` › ASR 兜底。
- **逐帧采样随场景时长伸缩。** VLM 每个场景的取帧数过去硬上限 6 帧，长场景（合并后可达上百秒）只能 1 帧／约 20 秒，`frame_facts` 严重稀疏。现按场景时长伸缩（约每 `VLM_SECONDS_PER_FRAME`=4 秒一帧，下限 3、上限 `VLM_MAX_FRAMES`=16），长场景的画面理解不再被饿死；VLM `max_tokens` 800→1500（`VLM_MAX_TOKENS`）。
- **MiMo 视频概览可作主理解来源。** 开启视频概览（`--mimo-video-overview` / `MIMO_VIDEO_OVERVIEW=1`）时，它会成为每个场景的**主要描述**（带动态、读得懂剧情），逐帧 `frame_facts` 仍保留作锚点与兜底；因为不动 `frame_facts`，substrate 评级不会因此回退。概览仍是可选项（默认关闭）。

### 变更

- **解说不再用破折号。** 破折号烧进字幕里很突兀：写作规则禁止在解说与 `original_subtitles.json` 里用破折号（——／—），渲染时再做一道归一化（替换为逗号）兜底；只改字幕显示，不动 TTS 朗读文本。
- **解说评审更确定、只对硬伤拦。** 评委固定 `temperature=0`+种子，复跑结论一致；只有 `hallucination`／`incomplete`（事实类）能在严格模式拦截，文笔类意见（钩子弱、念画面、套话等）一律降为提示；评审规则承认 `background_research` 与画面、对白并列为有效依据，不再把有据可查的设定误判成幻觉。
- **覆盖率指标按写作预算同速率计。** 解说覆盖率过去用 4.55 字／秒打分、却用 3.87 字／秒给 Agent 配额，比自己的预算还严约 18%，容易误报「讲得太少」。现统一用 3.87（含 `speech_safety_margin`）；并把几个覆盖率阈值提升为真正的 CONFIG 项。
- **ASR 人名按背景资料纠错。** 转写后用 `background_research.json` 里的人名修正单字同音错误（如 叶青眉→叶轻眉），严格限定「恰好一字之差、且窗口本身不是已知人名」，避免误改。
- **视频概览部分被审核拦截时降级。** 概览分片若部分被内容审核拦截，不再整体中止理解，而是用可用分片降级产出、未覆盖场景回退到逐帧描述；概览取帧帧率 `mimo_video_fps` 2→3。

### 修复

- **原声留白字幕与原声对不上。** 字幕时间过去依赖粗粒度 ASR（按块时间戳、中点估时），偶尔和原声对不上。现在「精确来源」（自带字幕／Agent 校对稿）按句**区间裁剪**精确落到所覆盖的留白：跨解说块的句子按时间比例切成各段、不再整句重复出现；过密的行截断显示而非直接丢成空白。

## [0.2.2] - 2026-06-18

让分块解说的成片更连贯、更好看：给原声留白补上**校对过**的字幕、解说与原声自然衔接、剪辑不再切断台词；并把会到最后才炸的失败提前暴露。

### 新增

- **原声留白也烧字幕了。** 解说块之间留给原声的留白，过去字幕是空的（解说字幕只写解说，原片自带字幕又被遮挡）。现在这些留白会烧上**原声台词字幕**，并用 `「」` 与解说区分开。优先采用 Agent 校对过的 `original_subtitles.json`（OUTPUT 时间轴 `[{start,end,text}]`：订正 ASR 错字与人名、只保留留白里真正出声的台词）；没有该文件时退回保守的 ASR 兜底——按句归到它所在的那一段留白、跳过太密读不完的行（`SUBTITLE_ORIGINAL_IN_GAPS`，默认开；cut 模式按剪辑计划把 ASR 从源时间映射到成片时间）。
- **剪辑不再切断一句台词（cut 模式）。** `video-cut` 会把每个片段的结尾向后吸附到最近的自然停顿（依据 `silence_periods.json`，上限 `CLIP_SNAP_MAX_EXTEND`，默认 2 秒；`SNAP_CLIP_LINE_END` 可开关），让原声把话说完；选片 brief 也提示 Agent 在完整句尾收口。
- **字幕烧录预检（快速失败）。** 烧字幕需要带 libass（`subtitles` 滤镜）的 ffmpeg。编排器在整条流程开跑前就检查，缺失即报错并给出处置（装一个带 libass 的 ffmpeg，或加 `--no-burn-subtitles`），不再跑完理解 / VLM / ASR / TTS、到最后渲染才失败；`video-assemble` 单独运行时同样有此预检。
- **成片时直接给出解说评审入口。** 存在 `narration_review.md` 时，编排器收尾会打印它的结论与路径，把内容风险（钩子弱 / 没主线 / 节奏）摆到眼前——仍是建议性，硬门禁只有 `validate.py`。

### 变更

- **解说块与原声自然衔接。** brief、写作规则和评审一起教会 Agent：原声留白前的那一块要把原声**引出来**，留白后的那一块要**接住**原声刚呈现的内容，让解说和它包裹的原声读成一个连贯的 beat，而不是各说各的（评审新增 `disjoint_handoff` 类别）。

### 修复

- **原声字幕过度渲染 / 与解说混在一起。** 早先的实现会把一整段（多句）ASR 文本塞进一小段留白、还在多段留白里重复出现，渲染出根本没说出口的台词。现在按句归属到单段留白、跳过过密的行、并用 `「」` 与解说分隔；最佳效果由 Agent 校对的 `original_subtitles.json` 提供。
- **文档：字幕烧录默认开启。** 两份 README 与 SKILL.md 原先把烧字幕写成需要 `--burn-subtitles` 才开，实际是默认开（用 `--no-burn-subtitles` 关闭）；已更正措辞。

## [0.2.1] - 2026-06-17

A delivery-quality release: narration now plays in blocks with the original audio breathing
between them at full volume, and the burned-in subtitle band no longer compresses the picture.

### Changed

- **Narration is delivered in BLOCKS, ~7:3.** Each beat is a few sentences written as one
  continuous thought and synthesized as a single fluent TTS utterance — fixing the choppy,
  sentence-by-sentence delivery. Between blocks the recap leaves deliberate original-audio
  blocks (~30% of the timeline) where the original scene plays at FULL volume.
- **Original-audio blocks play at full volume.** `idle_orig_volume` now defaults to `1.0` and
  `duck_bridge_seconds` to `1.5` (was `12`), so the original is ducked only under a narration
  block and swells back to full in the gaps, instead of sitting under one permanent low bed.
  This reverses the 0.2.0 "continuous bed" default. Tune with `IDLE_ORIG_VOLUME` /
  `DUCK_BRIDGE_SECONDS`.
- **Burned-in subtitles are split into short one-line chunks** timed karaoke-style across each
  block, and the source-subtitle masking band is sized for ONE line (~14% of height) instead of
  two (~23%) — the black band no longer compresses the picture.
- **The brief and lint steer block authoring.** The agent is told to write blocks and leave
  ~30% original-audio gaps; the per-sentence density lint is replaced by a block-coverage lint
  (`no_original_blocks` / `under_narrated` / `no_original_breaks` / `fragmented_beats`), and the
  block count is derived from coverage instead of beats-per-minute.

### Fixed

- **Blocks are no longer truncated by the speed-up.** `voiceover` sized a segment's text against
  the raw TTS duration, ignoring the `narration_speed` (1.3×) atempo that assemble applies before
  placement — so a correctly-budgeted block was clipped into a fragment. The truncation budget now
  accounts for `narration_speed`.

## [0.2.0] - 2026-06-16

A quality-focused release that re-architects cut mode and the narration mix so the
recap feels like a recap, not captions over a clip.

### Changed

- **Cut mode is now cut-first / narrate-second (two pauses).** The orchestrator renders
  `edited_source.mp4` from `clip_plan.json` first, then asks the agent to write
  `narration.json` against that real output timeline. Narration and picture stay in sync
  by construction — the old source→output remap that could silently drop or clamp beats is
  gone. Full mode is unchanged (single pause).
- **Continuous original-audio bed.** The original is ducked into one continuous low bed
  under the narration instead of swelling back up between sentences. Inter-beat gaps shorter
  than `duck_bridge_seconds` (default 12s, just above the max narration gap) stay ducked;
  only the lead-in and lead-out return to full volume. Tune with `DUCK_BRIDGE_SECONDS`.
- **Narration density is a guide, not a quota.** The brief frames beats/min as a target to
  aim for, explicitly telling the agent never to pad with filler or pixel-description to hit
  a number — fewer "cold", caption-like recaps.
- **`--consolidate` story index is on by default**, with a backward-compatible manifest shim
  so existing `work_dir`s still resume. Use `--no-consolidate` to opt out.
- **Research directive only fires when the substrate is thin/empty** (not on every titled
  run), and the orchestrator surfaces a research hint in the pause banner.

### Added

- **Cut-desync floor:** narration is linted against the normalized clip plan, with a blocking
  preflight that fails before TTS on heavy drop / too-sparse / long-gap output; `--allow-sparse-cut`
  ships an intentional montage anyway.
- **Phase ledger (`recap_phase.json`)** for deterministic cut-mode resume; a stale narration
  from a changed `clip_plan` can no longer resume into TTS.
- **`duck_bridge_seconds`** config knob (env `DUCK_BRIDGE_SECONDS`).

### Fixed

- **Long-video understanding rides out MiMo cluster rate limits.** A full episode fans out
  into ~90 ASR + ~185 VLM calls; the MiMo endpoints now retry up to 10× with a 60s backoff
  cap (plus a 10s floor when the server sends no `Retry-After`), and an optional
  `ASR_THROTTLE_SECONDS` spaces sequential ASR — so a transient 429 no longer aborts the run.
- **Resume cannot reuse stale artifacts.** Cached-artifact reuse now proves it matches the
  current source bytes / settings and rejects stale provenance, so a changed input or config
  can no longer silently resume on an out-of-date intermediate.

## [0.1.0]

- Initial release: turn any video into a Chinese-narration recap on `ffmpeg` + one Xiaomi
  MiMo API key. Five independent skills (understanding, script, cut, voiceover, assemble)
  plus a thin orchestrator; optional 剪映 draft export.

[Unreleased]: https://github.com/zenstory-ai/video-recap-skills/compare/v0.6.1...HEAD
[0.6.1]: https://github.com/zenstory-ai/video-recap-skills/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/zenstory-ai/video-recap-skills/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/zenstory-ai/video-recap-skills/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/zenstory-ai/video-recap-skills/compare/v0.3.3...v0.4.0
[0.3.3]: https://github.com/zenstory-ai/video-recap-skills/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/zenstory-ai/video-recap-skills/compare/v0.3.1...v0.3.2
[0.3.0]: https://github.com/zenstory-ai/video-recap-skills/compare/v0.2.3...v0.3.0
[0.2.3]: https://github.com/zenstory-ai/video-recap-skills/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/zenstory-ai/video-recap-skills/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/zenstory-ai/video-recap-skills/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/zenstory-ai/video-recap-skills/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/zenstory-ai/video-recap-skills/releases/tag/v0.1.0
