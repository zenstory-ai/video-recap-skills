# Agent Note: assemble 删掉 QC 镜像、死环境变量和不被检查的版本戳

Status: implemented

## Problem

第二轮精简清单（`.omc/plans/2026-10-02-slim-candidates-round2.md` 第 18、19、21、22、23 条，PR-A3）在 video-assemble 里找到五类只写不读或永远走不到的东西：

- `assembly_manifest.json` 把 `assembly_qc.json` 的 `verdict` / `blocking_codes` / `loudness_mode` / `loudnorm_measurement` / `audio_operations` / `adopted_audio` 原样抄成 `qc_*` 等字段，而 `qc_path` 已经指向旁边那份文件。`assembly_qc.json` 又把 `visual_qc.json` 的约 20 个字段抄成 `visual_qc` rollup，并带一个 `release_gate` 块：它重复顶层 verdict，`delivery_qc` 写死 `"PASS"`，`audio_qc` 由 blocking_codes 推出。仓库里没有任何代码读这些副本：dashboard 只读 `assembly_qc.json` 的 `verdict` / `blocking_codes`，final_qc 自己读 `visual_qc.json`，`strict_publish` 只读 `delivery_qc`。
- `lib.CONFIG["source_video"]` 读环境变量 `SOURCE_VIDEO`，但 `assemble.py main()` 每次都用 `--source-video`（或空串）覆盖它，再用 `source_video_explicit` 标记来中和这个环境值。环境值到不了任何 CLI 运行。
- `lib.py` 的 `_EXISTING_CONFIG_REF` 只为测试里的 `importlib.reload(lib)` 保住 `CONFIG` 的对象身份，生产代码从不 reload。
- `segment_audio_schema_version`（manifest 顶层和每段）没有任何比较方；`subtitle_track_validation.json` 的 `validation_schema` / `projector_version` 只在同一次 assemble 进程里被检查，而 `prepare_subtitle_track` 在同一进程里先删后写这份记录，版本不可能不一致，只有测试伪造过。
- `subtitles/track.py` 的 `_LEGACY_BINDING_KEYS` 容忍 binding 里的 `sha256` / `edit_sha256`。这两个键来自 #126 之前，而 `subtitle_track.json` 第一次发布就在 0.6.0，没有任何已发布版本写过它们。

## Decision

- `assembly_manifest.json` 不再写 `qc_verdict`、`qc_blocking_codes`、`qc_loudness_mode`、`qc_loudnorm_measurement`、`audio_operations`、`adopted_audio` 和 `segment_audio_schema_version`（顶层与 `audio_segments[]`）；`qc_path` 保留，`audio_segments` 保留（data-schema.md 有记录）。`_assembly_manifest_payload` 不再读 `assembly_qc.json`。
- `assembly_qc.json` 的 `visual_qc` 只剩 `{verdict, blocking_codes}`（没跑视觉 QC 时为 `NOT_RUN`），删掉 `release_gate` 和 `_AUDIO_QC_CODES`；`verdict`、`blocking`、`blocking_codes`、`delivery_qc` 不变，视觉 QC 阻断时照旧追加 `visual_qc_failed`。
- `lib.CONFIG["source_video"]` 默认空串，不读 `SOURCE_VIDEO`；`source_video_explicit` 和 `artifacts._explicit_source_video` 删除，`main()` 直接写 `args.source_video or ""`，`media` / `artifacts` 直接读 `CONFIG["source_video"]`。
- `lib.py` 删除 `_EXISTING_CONFIG_REF` 重导入保护。环境变量探测测试改用 `_load_lib_with_env`：用 `spec_from_file_location` 把 `lib.py` 加载成单独命名的模块，与 video-cut 的做法一致。
- `assemble_constants.SEGMENT_AUDIO_SCHEMA_VERSION`、`track_binding.VALIDATION_SCHEMA` / `PROJECTOR_VERSION` 删除；`_load_validation` 只要求记录存在。voiceover 写进 `tts_meta.json` 的 `segment_audio_schema_version` 不在本篇范围，由 understanding/voiceover 内部去重一组处理。
- `subtitle_track.json` 的 picture / audio binding 把 `sha256` / `edit_sha256` 当未知字段拒绝。本条翻转 [[2026-09-20-no-content-hashing]] 中“调用方 JSON 里旧的 sha256 键被忽略”在字幕轨上的适用；narration / audio-mix adoption、source_score 等其他调用方 JSON 仍然忽略这些键。
- `tests/orchestrator/env-inventory-v1.json` 里的 `SOURCE_VIDEO` 条目保留：清单测试只要求“读到的都已登记”，多出的条目不报错，而这份清单会由另一组精简（PR-A7）整体换成 AST 检查。

## Alternatives considered

- **manifest 继续镜像 QC 结论。** 最强理由：只开一个文件就能同时看到渲染输入和结论。没采用：没有任何读者，`qc_path` 已经指向结论文件；镜像多一份要和源文件保持一致的字段。
- **`assembly_qc.json` 保留完整的视觉 rollup 和 `release_gate`。** 最强理由：看视觉事实不用再开 `visual_qc.json`。没采用：`release_gate.delivery_qc` 是常量、其余两项能从 `blocking_codes` 推出，rollup 是 `visual_qc.json` 的拷贝；保留 `{verdict, blocking_codes}` 已经够 dashboard 和人看出视觉 QC 是否阻断。
- **保留 `SOURCE_VIDEO`，让它在没有 `--source-video` 时生效。** 最强理由：shell profile 里设一次就能少传一个参数。没采用：这正是 `source_video_explicit` 当初要防的事——陈旧的 shell 值会把整片模式的 timeline / 剪映导出绑到无关原片；recap 一直显式传 `--source-video`。
- **保留版本戳以防将来跨进程读取校验记录。** 最强理由：如果以后有独立命令复用 `subtitle_track_validation.json`，版本号能挡住旧格式。没采用：现在所有读者都在同一进程、在记录重写之后；真出现跨进程读者时再加，比现在维护一个永远相等的检查便宜。
- **字幕轨继续忽略旧摘要键。** 最强理由：与其他 binding 的“忽略旧 sha256 键”一致。没采用：没有已发布版本写过带摘要键的字幕轨，容忍集合只是一段没人用的兼容代码；严格 schema 拒绝未知字段反而是这份合约的本意。

## Consequences

- 收益：assemble 脚本净少约 100 行，`assembly_qc.json` 少约 25 个字段、manifest 少 7 个顶层字段和每段 1 个字段，配置表少一个环境变量和一个内部标记，`lib.py` 不再为测试便利改写模块全局。
- 代价：外部脚本若从 `assembly_manifest.json` 读 `qc_*`、从 `assembly_qc.json` 读 `release_gate` 或视觉细节，需要改读 `assembly_qc.json` / `visual_qc.json`；仓库内没有这样的读者。shell 里设 `SOURCE_VIDEO` 的人不会再看到任何效果（之前也没有）。手写的 `subtitle_track.json` 若带 `sha256` / `edit_sha256` 会报 unknown field。
- 是否在默认路径：渲染结果、QC 判定、缓存行为都不变；`assembly_settings` 未改，不触发任何缓存失效。
- `examples/guohuo-60s/assembly_qc.json` 是历史运行产物，保留旧形状，不改。

## Verification

- `ruff check .` 通过。
- `PATH=$HOME/.cache/video-recap-ffmpeg8:$PATH python3 scripts/test.py orchestrator assemble` 全绿。
