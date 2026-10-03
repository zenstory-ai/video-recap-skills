# Agent Note: 切镜头避让把边界移进讲话时撤回

Status: implemented

## Problem

cut 的吸附顺序是切镜头避让 → 句末/静音吸附 → 帧对齐 → 句界门禁。切镜头避让（`SCENE_CUT_SNAP`，默认开）会把出点向前、入点向后移到 `SCENE_CUT_SNAP_MARGIN`（默认 0.5 秒）内的原片硬切上；原设计假设随后的句末吸附会把被拉进讲话的边界再修回停顿。但句末吸附只能吸到停顿窗（静音窗与句末锚点），ASR 空档里是音乐或环境声、没有静音窗时，它什么也做不了，边界就停在讲话内被阻断。

这让 `nearest_safe` 的承诺失效：讲话 [0,2]、[3,10]，没有静音窗，25fps，1.8 秒处有硬切，出点 1.5 被阻断，建议 `after = 2.06`（`outside_detected_speech`）；按建议写回 2.06 重跑，切镜头避让又把它拉回 1.8（`inside_detected_speech`），再次建议 2.06，Agent 永远收敛不了。`outside_detected_speech` 的建议恰好出现在这类"有 ASR 空档、无静音"的位置，解说素材里很常见；原测试关掉了切镜头与句末吸附，没覆盖到。

## Decision

- `sentence_boundaries.snap_source_clips` 在切镜头避让之前记下每段的边界；句末/静音吸附之后、帧对齐之前，`_revert_unsafe_shot_snaps` 检查被切镜头避让移动过的边界（`shot_snaps` 里 `start_action: moved_forward` / `end_action: moved_back`）：当前位置被门禁判为 `blocking`，而移动前的位置判为 `safe`（含同源无损连续），就撤回到移动前的位置。不允许重叠时，撤回后会进入其他片段原片区间的不撤回。
- 撤回写在同一条 `qc.boundary_status.shot_snaps`：`end_action`/`start_action` 改为 `reverted_unsafe`，`new_end`/`new_start` 改名 `rejected_end`/`rejected_start`（被放弃的切点），并记 `end_revert_reason`/`start_revert_reason`（当时的门禁原因）；同时打一行日志。
- 判定用门禁本身的 `_edge_classifier`（与帧对齐、`nearest_safe` 复核同一个闭包，带首帧偏移），不另写规则。撤回后的边界照常经过帧对齐（按门禁排序选帧）和门禁。

## Alternatives considered

- **保留现状，只改文档**：不动吸附逻辑，在 SKILL.md、CHANGELOG、`data-schema.md` 说明切镜头避让仍可能挪动建议的边界，让 Agent 选另一侧或对这次运行关掉 `SCENE_CUT_SNAP`。最强的理由是行为零变化、画面边界照旧干净。没采用：Agent 会照着建议反复重跑而不收敛，关掉 `SCENE_CUT_SNAP` 又影响整份计划的所有边界；而"声音优先于画面"本来就是吸附顺序的既定原则，这里只是补上句末吸附够不着的那一段。
- **切镜头避让时就先问门禁，移进讲话的直接不移**：一处判断，没有撤回。没采用：切镜头避让之后句末吸附还可能把边界修回停顿（附近有静音窗时），这时保留干净的画面切点更好；只有句末吸附也修不回来时才需要撤回，必须在句末吸附之后判断。
- **在 `nearest_safe` 复核里也模拟切镜头避让**：建议本身就避开会被拉回的位置。没采用：要在门禁里跑 ffmpeg 场景检测，每个候选一次，代价高；而且普通计划的边界同样会被这样拉进讲话，问题不只在建议上。

## Consequences

- **收益**：被切镜头避让拉进讲话、又没有停顿可吸的边界不再阻断；`nearest_safe` 的建议原样写回不会再因句界被阻断。回归测试覆盖上面的复现（24/25/30fps，默认开启切镜头与句末吸附），以及原位置本身就在讲话内时不撤回。
- **代价**：撤回的边界离原片硬切不到 `SCENE_CUT_SNAP_MARGIN`，片头或片尾可能带一小段别的镜头（闪一下）；这是声音优先的取舍。`shot_snaps` 多一种 action 和两个字段。
- 相关：[[2026-10-02-cut-nearest-safe-edges]]（建议的边界与帧对齐复核）、[[2026-10-02-cut-frame-grid-cfr]]（吸附流程的最后一步）。
