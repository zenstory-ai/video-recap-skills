# Agent Note: 句界阻断附带最近的安全边界

Status: implemented

## Problem

cut 门禁阻断 `unsafe_clip_sentence_boundary` 时只写出边界时间、`status` 和 `reason`。Agent 要自己翻 `speech_boundary_anchors.json`、`silence_periods.json` 和 ASR 去找能放边界的位置，再按门禁的 50 ms 容差、未验证锚点规则自己判断能不能过，常常要重跑几次才猜中。

## Decision

- `enforce_clip_sentence_boundaries` 的判定抽成 `_edge_classifier`，同一个函数既判定边界，也复核建议；门禁这一组函数（连同 `_continuous_source_join`）放在 `video-cut/scripts/sentence_gate.py`，`sentence_boundaries.py` 只负责吸附。
- 被阻断的边界在 `qc.boundary_status.sentence_checks`（以及 `qc.blocking` 的同一项）里带 `nearest_safe: {"before", "after"}`。候选是停顿窗（静音窗与句末锚点）的两端、每个讲话区间外侧刚好超出门禁容差的时刻（±0.06 s）、源头和源尾；逐个用门禁复核为 `safe` 的才算；有帧网格时（cut 的正常路径），还要用 `frame_grid.edge_frame_snapper` 把候选按帧对齐的同一套规则移一次、再过门禁，对齐后仍 `safe` 才报告，并且报告的是对齐后的落点（原片帧网格上的时间），不是原始候选（24/25fps 下讲话尾 +0.06 s 的候选会被对齐到容差内，不复核就会"按建议改了又被阻断"；讲话起点 −0.06 s 在 25fps 下正好是半帧，原样报告时重跑的帧对齐要在两帧之间重新取舍，可能落到原片硬切前一帧），取 `_SAFE_EDGE_SEARCH_SECONDS = 5.0` 秒内离当前边界最近的前后各一个，写 `{time, reason, delta}`，没有则为 `null`。
- 候选复核模拟的是帧对齐，不模拟切镜头避让：重跑时切镜头避让若把建议的边界拉回讲话内，`snap_source_clips` 会撤回那次避让（见 [[2026-10-02-cut-shot-snap-yields-to-speech]]），所以原样写回不会再因句界被阻断。
- 建议只看声音安全，不考虑片段重叠、最短时长和必保证据；这些由重跑时的既有校验负责。`video-cut/SKILL.md` 说明怎么用，`data-schema.md` 给出形状。

## Alternatives considered

- **报告原始候选时间（停顿窗端点、讲话区间外 0.06 s），只用帧对齐复核**：数字直接对应声学事实，好解释。没采用：候选常落在两帧之间，写回后帧对齐要重新二选一，复核时的落点和重跑时的落点只是碰巧一致；报告落点后写回就在网格上，重跑不再移动它。

- **门禁直接把边界移到最近的安全位置**：Agent 不用改计划。没采用：移多远、往哪边移是剪辑决定（多保留一句还是裁掉一句），工具只执行 Agent 的剪辑选择；吸附已经在 `CLIP_SNAP_MAX_EXTEND` / `CLIP_START_SNAP_MAX_PREPEND` 范围内自动做过，剩下的都超出了自动吸附的授权。
- **列出搜索范围内的全部安全区间**：信息最全。没采用：Agent 需要的是"往前挪到哪、往后挪到哪"两个数；区间列表要再算一遍容差。

## Consequences

- **收益**：被阻断的边界一次给出可用的修改目标，`before`/`after` 都为 `null` 时直接说明附近没有停顿，要换区间。
- **代价**：`sentence_checks` 的阻断项变长；候选数与停顿窗、讲话区间数量成正比，每个阻断边界一次线性扫描，有帧网格时每个门禁放行的候选再做一次单片段帧对齐。
