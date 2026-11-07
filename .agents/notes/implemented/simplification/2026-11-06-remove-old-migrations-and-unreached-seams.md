# Agent Note: 移除 0.6.1 之前的迁移、没人读的产物和没人传的参数

Status: implemented

翻转了这些笔记里的迁移决定：[[2026-10-02-storyboard-relative-paths]]、[[2026-10-02-understanding-offline-cache-safety]]、[[2026-10-03-tts-content-addressed-cache]]。

## Problem

三类代码还留着，但没有生产路径在用：

- 迁移：`understanding_storyboard._relocated` 改写旧 sidecar 的绝对路径，`_STORYBOARD_HEADING_PREFIX` 替换旧 brief 头部，`_LEGACY_ASR_SETTINGS` 忽略旧 sidecar 的 `mimo_asr_api_key_present`，`tts_cache.legacy_sidecar_path` 清理旧 sidecar。新版本写出的内容已经是相对路径，这些都只对 0.6.1 之前的 `work_dir` 起作用。
- 产物：`understanding_index.md` 没有读取方，同样的内容在 `understanding_index.json` 和 brief 的 Understanding index 一节。`visual_qc.json` 的 `summary` 没有读取方。
- 参数：`materials.restore_material` 的 `overwrite`、`prune_stale_allowed`；`deslop_qc.analyze_deslop_qc` 的 `original_subtitles`；`review_response.build_review_messages` 的 `research_context`；video-script `lib.api_call` 的 `api_url`、`api_key`、`api_env_var`（这两处后来随 [[2026-11-06-remove-narration-review-chain]] 整体删除）。唯一的生产调用都不传，有的连测试也不传。还有未使用的常量 `SUBTITLE_BURN_DEGRADED`。

## Decision

- 全部删除。`restore_material` 总是清理不在本次恢复列表里的旧产物并覆盖（`background_research.json` 后来成了例外，见 [[2026-11-06-material-restore-keeps-research-binds-index]]），返回值的形状不变。`_reuse_cached_storyboard` 只负责读取和记日志，不再改写。
- `test_storyboard.py` 里 copied-work_dir 的两个用例只删旧版半段，保留当前相对路径的断言；头部替换用例改为覆盖当前标题。
- `materials.ALLOWED_ARTIFACTS` 去掉 `understanding_index.md`；素材库恢复按保存时的列表走，旧条目照常恢复。

## Alternatives considered

- **保留 `_LEGACY_ASR_SETTINGS` 再过一个版本。** 最强理由：CHANGELOG 公布过“升级后不会重新转写”，这个键只有两行，却能避免一次收费。没采用：只影响 0.6.1 之前的 `work_dir`，代价是一次 ASR；这也是一个收回已发布承诺的决定，所以写进 CHANGELOG 的 Changed 而不是悄悄改。
- **把 `api_url` 等参数留在 video-script，让它和其他技能的 `api_call` 保持一致。** 最强理由：同名函数形状相同，读起来省事。没采用：`api_call` 不在 parity 测试的共享函数里，其他技能的这些参数是有调用方的，这里没有。
- **继续写 `understanding_index.md`。** 最强理由：人打开目录时比 JSON 好读。没采用：brief 里已有同样内容，先例见 [[2026-10-02-drop-write-only-artifacts]]。

## Consequences

- 收益：少约 125 行脚本和约 100 行测试（估算），每次运行少一个产物。
- 代价：复制过的 0.6.1 之前 `work_dir` 命中 storyboard 缓存时，路径仍指向原目录；旧 ASR sidecar 未命中，重新转写一次（收费）；旧 `narr_NNN.wav.cache.json` 留在磁盘上不再被读；旧 brief 的短标题在重建一次时多叠一个头。
- `visual_qc.json` 的 `summary` 被移除后，读它的外部脚本要改读 `subtitles`、`mask`、`overlays`。
