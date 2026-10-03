# Agent Note: 解说评审列出计划内留给原声的区间

Status: implemented

## Problem

真实运行中，video-script 的建议型评审把一个有意不写旁白、交给原声对白的拍报成"skipped"。评审 prompt 里 `recap_story_plan.json` 和 `visual_audio_board.json` 都经 `_format_json_context` 以缩进 JSON 截取前 3000 字；稍长的计划里靠后的拍连同 `audio_owner` / `narration_job` 一起被截掉，评审只看到故事计划里有这个 beat、草稿在这段时间没有旁白，就当成漏写。`incomplete` 属于事实类，可以是 error，在严格模式下会被当作阻断。

## Decision

- `review_response._format_original_audio_holds(work_dir, clock)` 生成「计划内留给原声的区间（不是漏写）」一节，放在计划 JSON 之后：
  - 拍：`visual_audio_board.json` 的 `items` 中 `audio_owner` 属于 `original_dialogue|action_sound|ambience|music|silence` 或 `narration_job == "none"` 的项；`recap_story_plan.json` 的 beat 只有在 board 没列同一个 `beat_id` 时才看它自己的 `audio_owner` / `narration_job`（board 负责声音决定）。
  - 时间按评审时钟取 `output_*` 或 `source_*`；缺这一时钟时退到另一个并如实标 `SOURCE` / `OUTPUT`，都没有就写"时间未标"。附 `original_audio_anchor`（或 `must_keep_moment`）。
  - `original_subtitles.json` 的每个字幕块（成片输出时间，full 模式与原片时间一致）。
  - 最多 40 条；没有任何条目时不出这一节。
- 该节和 RUBRIC 第 6、10 条明确：这些区间没有旁白是有意的，不得报跳过、缺失、漏写，也不得据此给 `incomplete`、`no_throughline`、`low_information_gain`、`density`；只有旁白闯入时报 `original_audio_conflict`，接不上时报 `disjoint_handoff`。`incomplete` 只指旁白本身半句话。

## Alternatives considered

- **放宽 3000 字截断**：一行改动，计划全文进 prompt。没采用：长计划会把 prompt 撑大，分块评审时每块重复一遍；截断照样可能落在关键拍上，而且评审仍要自己从 JSON 里推断哪些拍不需要旁白。
- **评审返回后按关键词过滤"跳过/缺失"类 finding**：确定性，不依赖模型听话。没采用：finding 的 `segment` 是草稿段号，指不到没有旁白的拍，只能靠匹配 issue 文本，误删真实问题的风险更大。

## Consequences

- **收益**：评审拿到确定的原声区间清单，不再依赖被截断的 JSON；有意留白不再以 `incomplete` 误伤严格模式。
- **代价**：prompt 多最多 40 行；仍是 prompt 约束，模型不服从时依旧可能误报。board 与故事计划冲突时以 board 为准，故事计划里过期的 `audio_owner` 不会生效。
- 只有单测证据（prompt 内容断言），未用真实评审调用复现那次"skipped"。
