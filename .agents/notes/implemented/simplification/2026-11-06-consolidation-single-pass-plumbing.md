# Agent Note: consolidation 收成一个 pass 的写法

Status: implemented

相关事实更新：[[2026-11-06-remove-asr-clean-transcript]]（`do_index` 不再保留）、[[2026-10-02-understanding-internal-dedupe]]、[[2026-10-02-consolidate-truncated-reply]]、[[2026-10-02-understanding-offline-cache-safety]]、[[2026-10-02-understanding-index-normalization]]、[[2026-11-06-one-entry-per-capability]]。

## Problem

consolidation 曾有多个 pass 和多个可选阶段，现在只剩建索引一个 pass，代码仍按多个来写：

- `consolidate()` 把一次调用包进字典，里面有一个最多一个元素的 `skipped_no_key` 列表。`_require_api_key` 和 `_complete_json_reply` 的 `label` 只会是 `index`。
- `_present_consolidation_artifacts` 返回最多一个固定文件名的列表；`_write_consolidation_status` → `_write_optional_stage_status` → `_short_status_message` 三层只有一个调用方。runner 传的 `enabled` 和 `do_index` 永远相等。
- 同一个 `{size, mtime_ns} 或 None` 写了三遍：`consolidate._input_identity`、`understanding_cache._artifact_identity`、`asr_timing_evidence._identity`。`_index_meta_path` 重复 `_artifact_meta_path`。
- 每个回复解析两次：`_complete_json_reply` 解析后丢掉结果，`parse_index_response` 再解析一遍，它的非对象分支在生产路径上走不到。`coerce_character_aliases` 也调用两次，`normalize_index` 紧接着会再做一遍。

## Decision

- runner 直接调 `consolidate_index`，用 `try/except ConsolidateNoKey/except Exception/else` 算出状态。四种结果和文字不变：`skipped_no_key`、`failed`（模型报错，或没写出索引）、`skipped`（无 `vlm_analysis`）、`ok`。消息仍折叠空白并截到 240 字符。
- `consolidation.status.json` 去掉 `do_index` 和 `artifacts`，保留 `stage`、`enabled`、`status`、`message`。这个文件唯一的读取方是 `briefing/inputs.py`，它不读这两个键；`materials.py` 只复制文件。brief 的判断改成 `elif not consolidation_index`。旧文件多带键也能读。
- `consolidate.py` 用 `understanding_cache._artifact_identity` 和 `_artifact_meta_path`。`understanding_cache` 从 `asr_timing_evidence` 导入 `_identity` 作为 `_artifact_identity`，导入方向不变，没有新环。
- `_fresh` 换成 `out_path.exists() and _index_cache_matches(...)`，并从 `understanding_cache` 删除。必须留下存在性检查：`_index_cache_matches` 只比 sidecar，而 `restore_material` 会清掉 `understanding_index.json`、留下不在 `ALLOWED_ARTIFACTS` 里的 `.meta.json`。没有这个检查，索引会一直缺失，却打印"已最新"。
- `_complete_json_reply` 返回解析好的对象，`parse_index_response` 改为 `_index_from_reply(data)`，保留列表校验和 `research_glossary` 的 `support=context_only`。别名只在 `normalize_index` 里转换。
- `_index_meta` 的输出、`asr_clean: null`、`INDEX_PROMPT` 和 `understanding_index.json.meta.json` 的文件名都不变，已付费的索引继续命中。
- brief 这一侧同样只按一个可选阶段写：`_format_optional_stage_warnings` 只判断 consolidation，`_optional_stage_warning` 删除；`_consolidation_model` 删除，直接读 `CONFIG["vlm_model"]`。
- `understanding_brief.py` 用 `briefing.context._clip_text`，不再留一份副本。`_research_context` 的输出在付费 VLM 缓存键里，两种写法已对全部 Unicode 码点逐一比对，结果相同。
- `builder.py` 的 `brief_scenes`、`brief_asr`、`brief_silence` 是同一组证据，同时供 ASR 分块、timeline fusion 和 scene guide 使用；cut pass 2 里它们在 OUTPUT 时钟上。
- `_split_text_by_sentence_windows` 去掉唯一调用方到不了的分支：调用方只传一句已规整、超长的文本。

## Alternatives considered

- **让 `asr_timing_evidence` 从 `understanding_cache` 导入。** 最强理由：`understanding_cache` 是缓存身份的自然归属。没采用：`understanding_cache` 已经导入 `asr_timing_evidence`，会形成环，架构测试会拦。
- **把 `_fresh` 直接删掉。** 最强理由：`mtime` 比较确实多余，`_index_cache_matches` 已经比了 `vlm_analysis.json` 的身份。没采用：它的另一半（输出文件存在）不多余，见上。
- **保留 `artifacts` 和 `do_index` 以防外部读取。** 最强理由：落盘格式不变最省事。没采用：仓库里没有读取方，文档也只列文件名；这两个键只记录文件是否存在和一个恒等于 `enabled` 的值。

## Consequences

- 收益：一个 pass 就写成一个 pass，少一层字典、列表和包装。回复只解析一次。
- 代价：`consolidation.status.json` 少两个键，读它的外部脚本要改。
- 代价：只有已移除的 `--consolidate-asr` 能写出的旧状态（`enabled=true, do_index=false`）在没有索引时现在会提示 `missing_index`，这个提示本身属实。
- 代价：索引的 `mtime` 被人为改到比 `vlm_analysis.json` 更早、而身份不变时，现在算命中，以前会重建并花钱。这个判断不再读 `mtime` 顺序。
