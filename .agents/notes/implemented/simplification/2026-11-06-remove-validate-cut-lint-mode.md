# Agent Note: 移除 validate.py 的 --mode cut 和原片时间的片段归属检查

Status: implemented

## Problem

`validate.py --mode cut` 校验的那种旁白已经不存在了。[[2026-06-16-cut-first-narrate-second]] 之后，cut 模式的旁白写在成片 OUTPUT 时间轴上，编排器只传 `--mode full` 和 `--mode cut_output`，`video-script/SKILL.md` 也只写这两种。只有测试还在走 `mode="cut"`。

这一个模式在 lint 里留下了一整段片段匹配分支、`outside_clip_plan`、`ambiguous_source_clip`、`invalid_source_clip_id`、`crosses_clip_boundary` 四个 code、`clip_plan` 参数和 `source_clip_id` 的读取，还让 `_validate` 分出三条路。

同一个 skill 里还有几处小重复：span 过滤写了两遍；同一段旁白要查两次所在场景，各包一个只有一个调用方的函数；schema 1 的 `boundary_use` 推断写了两遍；lint 路径表达式写了两遍；可选 JSON 读取函数写了两遍；`_env_number` 的 `cast` 参数只剩 `float`。

另外发现一个真 bug：`deslop_qc._style_card_issue` 直接 `json.loads` Agent 写的 `style_card.json`。内容不是合法 JSON 时异常穿出 lint，`validate.main` 只接 `NarrationLintError`，于是 TTS 前以 traceback 退出，而这个函数的 docstring 说这个文件从不阻塞。

## Decision

- `--mode` 只接受 `full` 和 `cut_output`，传 `cut` 由 argparse 报 `invalid choice`，退出码 2。上述四个 code 和 `clip_plan`、`_load_cut_clip_plan`、`_clip_span`、`_clip_matches_for_segment` 删除；`_suggestion_limits` 只剩 `[0, output_duration]`。
- `_validate` 只留一条路：lint、测量、（仅 cut_output）边界检查、写入。边界检查失败仍然只重写 `narration_lint.json`，不动 `narration.json`。`cut_output` 仍不把 `vlm_analysis.json` 交给 lint，`full` 仍不把 `--output-duration` 交给 lint；`vlm_analysis.json` 在两种模式下都还会被读取。
- `load_source_sentence_evidence` 的非 full 分支只服务 cut_output，缺证据或证据过期时返回空证据，后面那段给 `payload is None` 兜底的代码没有路径能走到，删除。
- 重复收成一处：`_has_connected_predecessor` 用 `_other_block_spans`；`lint_narration` 每段查一次场景；`speech_ownership.anchor_boundary_use` 是 schema 1 推断在本 skill 的唯一写法；`validate.py` 用 `speech_ownership._read_json`；`env_float` 不再经过 `_env_number`。`recap_timeline.py` 里那份 schema 1 推断属于另一个 skill，不动。
- 不合法的 `style_card.json` 和读不了的文件走现有的 `malformed_style_card` advisory，文案不变，lint 照常出报告。

## Alternatives considered

- **保留 `--mode cut`，只是不写进文档。** 最强理由：它是公开 CLI 的一个取值，外部脚本可能在调。没采用：SKILL.md、README 和 runbook 都没写过这个值，它校验的旁白格式也不再产生，留着就要继续养一条没有真实运行的分支。外部调用会得到明确的 argparse 错误，记入 CHANGELOG 的 Changed。
- **把 `test_cut_interrupt_suggestion_stays_inside_the_blocks_clip` 改成 cut_output 模式保留。** 最强理由：不减测试数量。没采用：它验证的是片段边界限制，这个限制已删除；输出时长的限制由 `test_cut_output_interrupt_suggestion_stays_inside_the_output_duration` 守着。`test_cut_lint_suggests_output_clock_anchor_from_brief_evidence` 验证的行为还在，改成 cut_output 保留。

## Consequences

- 收益：`_validate` 只有一条路，`narration_lint.json` 少四个 code，lint 少一段片段匹配。
- 代价：调用 `--mode cut` 的外部脚本要改。
- 一处有意的小变化：`end <= start` 的段不再算相邻块的“紧接前块”，相邻块可能多报一条入点 error。这种 lint 本来就会因 `invalid_time_range` 失败，所以通过与否不变。
- 是否在默认路径：是，但默认路径的输出不变。对基线跑了 full 和 cut_output 共 18 组 validate 场景，退出码、`narration.json`、`narration_lint.json` 和控制台输出一致；`interrupts_source_sentence` 的 message 去掉了“片段内”的半句，这是唯一的文字变化。
- 顺带修了 `test_lint_block_coverage_metrics_and_warnings` 的空转断言：它把 lint 报告当旁白传回 lint，靠 `invalid_json_shape` 得到空 metrics。现在传真正的旁白。
