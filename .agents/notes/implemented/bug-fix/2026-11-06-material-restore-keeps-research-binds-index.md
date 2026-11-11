# Agent Note: 素材恢复保留 Agent 的调研，并把故事索引绑到恢复后的分析

Status: implemented

后续事实更新：`background_research.json` 保留与恢复后刷新 brief 的行为继续生效；关于 `understanding_index.json` 与 `consolidation.status.json` 绑定的部分已随 [[2026-11-10-scripts-for-hands-and-floor-agent-for-craft]] 整体移除。

## Problem

`materials.restore_material` 把 `ALLOWED_ARTIFACTS` 里条目没有的文件全部删掉，包括 Agent 写的 `background_research.json`，而 video-recap §4.1 和偏薄 brief 都叫 Agent 写它。第二个问题：条目不存 `understanding_index.json.meta.json`，恢复后 cut 第二阶段重建 brief 时 `_load_consolidation` 找不到 provenance，brief 报 `missing_index`，索引一节消失。两处都是 main 上就有的行为。

## Decision

- `background_research.json` 不进清理集合；条目里也有这个文件时，`work_dir` 的副本比条目的新（`mtime_ns`）就保留，否则用条目的。比较规则与多视频共享调研一致（[[2026-10-02-multi-video-shares-project-background-research]]）。
- 条目里同时有 `understanding_index.json` 和 `vlm_analysis.json`，且存下的 `consolidation.status.json` 的 `status` 是 `ok` 时，恢复写 `understanding_index.json.meta.json`，只含 brief 检查的 `source`（恢复后 `vlm_analysis.json` 的身份）、`scene_count`、`model`（当前 `CONFIG["vlm_model"]`）。已有条目不用重存就生效。其他状态或没有状态文件时不写，brief 照旧报 `missing_index`：入库时不校验 provenance，`failed` 的重跑可能留下旧索引，只有 `ok` 能说明索引出自这份分析。
- 这份 sidecar 不含 `inputs`、`prompt` 和 `schema_version`，`consolidate._index_cache_matches` 不会命中，所以之后在同一目录不走恢复地跑 `understand.py` 仍会重建索引，与此前没有 sidecar 时一样。

## Alternatives considered

- **把 sidecar 加进 `ALLOWED_ARTIFACTS`，原样入库。** 最强的理由是 `inputs`、`prompt` 都能保留，索引缓存也可能命中。不采用：已有条目没有它，而且恢复重写 `vlm_analysis.json`，`source` 身份本来就要改写；`inputs` 里的身份也会全部失效。
- **放宽 `_load_consolidation`，没有 sidecar 也读索引。** 最强的理由是改动最小，且不写新文件。不采用：那道检查防的是旧索引配新分析，放宽后普通 `work_dir` 也失去保护。

## Consequences

- 收益：恢复不再吃掉 Agent 的调研，恢复后的 cut brief 带上已付费的故事索引。
- 代价：`model` 记的是恢复时的当前模型，不是当初生成索引的模型；条目不记模型，而恢复本身也不看模型，换 `MIMO_MODEL` 后恢复的旧分析一样照常使用。
- 代价：换了别的片子的调研文件留在复用的 `work_dir` 里不会被清理；身份校验不看这个文件，复用目录时要自己删。
- 测试：`tests/orchestrator/test_materials.py` 含调研保留、新旧比较、sidecar 字段，以及恢复后真跑 `understand.py --brief-only` 看索引一节。
