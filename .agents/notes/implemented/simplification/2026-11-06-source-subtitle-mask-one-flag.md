# Agent Note: 原字幕遮罩收成一个开关

Status: implemented

## Problem

`SOURCE_SUBTITLE_MASK_POLICY` 是为了当年 `MASK_SOURCE_SUBTITLES` 默认打开时加的：一个环境里继承来的默认值不应该悄悄盖掉画面，所以遮罩要有显式策略，只设 `MASK_SOURCE_SUBTITLES` 记成 `legacy_implicit` 并让视觉 QC 失败。

现在 `MASK_SOURCE_SUBTITLES` 默认是 `False`，设了它本身就是显式选择，这层策略的理由不存在了。剩下的是四个取值里两个行为相同（`opt_in`、`forced`），加一个只会把明确要求变成失败的 `legacy_implicit`。recap 从不设置策略，唯一写它的代码是 `assemble.py` 处理 `--subtitle-y-top/bot` 时。

## Decision

- 删除环境变量、CONFIG 里的 `source_subtitle_mask_policy` 和 `source_subtitle_mask_policy_declared`、阻断码 `mask_policy_not_explicit`。
- `_source_subtitle_mask_policy` 改名 `_source_subtitle_mask`，不再需要 `work_dir`：要求遮罩并且烧录字幕时才遮。返回 `active`、`scope`、`trigger`、`reason`、`burn_subtitles`。`trigger` 取 `mask_off`、`burned_subtitles`、`burn_subtitles_degraded`、`burn_subtitles_disabled`，缺 libass 时的 `mask_dropped` 警告继续靠它判断。
- `--subtitle-y-top/bot` 照旧打开遮罩并默认不透明。
- `assembly_manifest.json` 去掉策略三键，`examples/guohuo-60s/assembly_manifest.json` 手工同步，不重新生成。

## Alternatives considered

- **只删策略值里重复的 `forced` 和 `legacy_implicit`，保留 `safe`。** 最强理由：`safe` 是唯一独有的行为（放了 `user_subtitles.*` 就自动遮罩），改动更小。没采用：recap 不设置策略、文档只有配置手册一行，没有证据说明有人用；留它就要留整套策略与 `declared` 字段。需要时显式设 `MASK_SOURCE_SUBTITLES=1` 即可。
- **`MASK_SOURCE_SUBTITLES=1` 单独设置时继续报错。** 最强理由：不改变旧环境的结果。没采用：这正是要移除的行为；默认关闭后它只会惩罚明确的要求。

## Consequences

- 收益：少约 50 行脚本，少 1 个环境变量和 1 个 QC 阻断码；测试不再每处都设两个键。
- 代价：`MASK_SOURCE_SUBTITLES=1` 单独设置从 QC 失败变成真的遮罩；同时设 `POLICY=off` 也会开始遮罩，而且静默，因为不再读取的变量不会报错。放了 `user_subtitles.*` 且用 `safe` 的运行不再自动遮罩。这些都记在 CHANGELOG 的 Changed。
- 旧 `assembly_manifest.json` 里的策略键没有读取方，不影响续跑，也不触发重渲染。
