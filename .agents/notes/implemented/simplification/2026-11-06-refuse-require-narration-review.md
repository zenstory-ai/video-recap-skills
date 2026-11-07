# Agent Note: REQUIRE_NARRATION_REVIEW=1 报错一个版本

Status: implemented

翻转了 [[2026-11-06-remove-narration-review-chain]] 里“这个变量设了也不报错”的处理，那篇把这个选择留给了负责人判断。事实已在那篇和 [[2026-11-06-remove-env-twins-of-run-flags]] 里更新。

## Problem

[[2026-11-06-remove-env-twins-of-run-flags]] 对拒绝环境变量定的标准是：不报错会不会静默改变结果。`EDIT_MODE=cut` 符合，所以报错。`REQUIRE_NARRATION_REVIEW=1` 同样符合：它原来是 TTS 前失败关闭的门禁，现在不读了，本该被拦下的旁白会花钱合成并出片，自动化环境从输出上看不出来。CHANGELOG 里的那句话到不了只设了环境变量的流水线。

现有的拒绝循环不能直接用：它的报错写“改传 {flag}”，而这个变量没有替代 flag；它还拒绝任何非空值，可 `=0` 从来没有门禁，忽略它什么也不会变。

## Decision

- `recap_cli.parse_args` 在拒绝循环之后加一段：`env_bool("REQUIRE_NARRATION_REVIEW", False)` 为真时 `parser.error`，退出码 2，报错说明门禁已不存在、旁白靠 video-script §7.1 自检、请从环境里删掉它。
- `=0`、空值和未设置都照常运行。
- 只改 recap：只有 recap 读过这个变量，`understanding_runner` 的循环不用动。`REVIEW_NARRATION` 不用拒绝，它是一个已经不存在的远程调用的退出开关。
- 和 `EDIT_MODE` 的拒绝一起保留一个版本，之后整段删除。

## Alternatives considered

- **继续不报错。** 最强理由：真正受影响的是极少数自动化环境，CHANGELOG 已写明门禁不存在，也少留一段拒绝代码。没采用：这正是拒绝标准要防的情况，没有门禁却继续付费出片，而且只有读过 CHANGELOG 的人才知道。
- **把名字加进现有循环。** 最强理由：最省事，不多一个分支。没采用：报错会让人去传一个不存在的 flag，还会把没有影响的 `=0` 也拒掉。

## Consequences

- 收益：设了 `=1` 的环境在做任何工作之前就知道门禁没了。
- 代价：这些环境要改配置才能继续跑；多留约 5 行拒绝代码，一个版本后删除。
- 是否在默认路径：未设置时行为完全不变。
