# Agent Note: 无损连续连接只认计划里的相邻片段

Status: implemented

## Problem

`sentence_boundaries._continuous_source_join` 判断两段是否"同源、原片首尾相接、输出首尾相接"。多源 cut 按来源分组吸附，每组是一个只含本来源片段的子计划，吸附后按组内顺序重算输出时间轴。计划 `a:0–2, b:0–2, a:2–4` 里，子计划把 `a:0–2` 和 `a:2–4` 排成输出上相邻，于是 `a` 在 2.0 秒（ASR 讲话 1.0–3.0 之内）的两个边界都被判成 `continuous_source_join` 放行，而成片里两者之间实际插着 `b` 的片段，原声句子被切断。

## Decision

`_continuous_source_join` 额外要求 `right.clip_id - left.clip_id == 1`。`clip_id` 由 normalize 按计划顺序连续编号，子计划保留原值，所以只有计划里真正相邻的同源片段才算无损连续。单源计划、渲染里的接缝淡入淡出判断、帧对齐的入点跟随都使用这个函数，结果不变。

## Alternatives considered

- **子计划保留全局输出时间，不按组重算**：输出时间比较自然就正确。没采用：吸附函数共用 `_plan_with_snapped_clips` 的游标重算，改它要动单源路径，而 `clip_id` 已经精确表达"计划相邻"。
- **合并后在全局计划上再跑一次门禁**：判定对象最直接。没采用：门禁需要每个来源各自的停顿窗、讲话区间和时长，等于重做一遍分组。

## Consequences

- **收益**：多源计划里被别的来源隔开的同源讲话中切点会正确阻断，并附带 `nearest_safe`。
- **代价**：缺 `clip_id` 的手写测试计划按默认值 0/1 处理；生产计划总有 `clip_id`。
