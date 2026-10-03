# Agent Note: full 模式校验不再静默改写解说稿

Status: implemented

Superseded in part by [[2026-10-02-voiceover-no-default-truncation]]

## Problem

[[2026-05-18-agent-owned-narration-cli-mechanical]] 把"创作归 Agent、CLI 只做机械步骤"定为原则，但给 full 模式留了一个例外：`validate.py` 默认走 `_align_narration_to_quiet`，先经 `_validate_narration_budget` 改写 Agent 的稿子，再按"安静窗口覆盖率"算 `overlaps_speech`。改写包括：

- 字数超过推荐预算 1.25 倍时在句界截短（`_truncate_at_sentence`），截不出 5 个字以上就整段丢弃；推荐预算不足 5 字的段直接丢弃；
- 句末停顿号换成句号、清理"，。"类标点（`_clean_narration_punctuation`）；
- 按 `start` 排序，bigram 重叠超过 0.6 的相邻段合并（`_post_dedup_narration`）；
- `_normalise_narration_segment` 只保留白名单字段，Agent 写的其他元数据被丢掉。

这条路径是默认生产路径：recap 只有用户显式传 `--preserve-approved-text` 时才跳过它。于是同一份 narration.json 在 full 模式和 cut_output 模式下得到两套 `overlaps_speech` 算法（full 用安静窗口占比 ≥80%，cut_output 用"原声对白区间减去安静窗口"），而默认 full 运行会悄悄缩稿、合并、丢段，违背 `docs/production-boundaries.md` 的"文本装不下窗口时回到创作，不让 TTS 静默缩稿"。另外 `_validate_narration_budget` 里处理时间重叠的分支在 lint 已经把 `time_overlap` 判为 error 之后根本走不到。

架构审计（`.omc/plans/2026-10-02-skill-architecture-audit.md` S10）提出退役这条改写，critique 判为"默认路径、需 owner 签字"；owner 已批准这一行为变更。

## Decision

- `validate.py --mode full` 与 cut_output 走同一个契约：先 `validate_narration_or_raise` 做 lint，再用 `speech_ownership.measure_narration_speech_ownership(..., mode="full")` 回写 `overlaps_speech`。除 `overlaps_speech` 外，段数、顺序、时间、文本、停顿和任意扩展字段原样落盘。
- 删除 `narration_lint.py` 的 `_validate_narration_budget`、`_quiet_windows`、`_align_narration_to_quiet`，以及 video-script `agent_text.py` 的 `_truncate_at_sentence`、`_char_bigrams`、`_post_dedup_narration`、`_normalise_narration_segment`、`_clean_narration_punctuation`。script 那份 `_overlap_seconds` 随之没有调用者，按 [[2026-10-02-function-level-parity]] 的重访信号删除并移出 `SHARED_FUNCTIONS`；understanding 那份仍由 `timeline_fusion` 使用，保留。
- `quiet_overlap_min_ratio` 这个 CONFIG 键保留：`speech_ownership.segment_overlaps_source_speech`（没有 ASR 对白区间时的安静窗口兜底）和 video-assemble 的 `audio_mix.py` 都在读。删的只是改写代码路径。
- 超预算改为退回作者：full 模式下 `_text_char_count(text) > _recommended_char_budget(start, end) * OVER_BUDGET_ERROR_RATIO`（1.25，即原改写的截短阈值）时，lint 报 `over_budget` error。消息写明段号、时间窗、字数、推荐预算和硬上限，结构化字段有 `budget_chars`、`limit_chars`、`actual_chars`、`over_chars`。没过硬上限但估算朗读时长超出时间窗的，仍是 `over_budget` warning；推荐预算不足 5 字仍是 `slot_too_short` warning，不再丢段。cut / cut_output 的超预算仍只是 warning。
- `validate.py` 删除 `--preserve-approved-text`。它在改写退役后只剩一个作用（要求段落按时间排序），所以 lint 现在在所有模式下都把未排序输入报为 `out_of_order` error，`require_chronological` 参数删除。recap 删除 `_approved_validation_args`，full 与 cut_output 两处都不再往 validate 传这个 flag；`recap.py --preserve-approved-text` 仍转给 voiceover 和续跑命令，voiceover 的 `approved_text_policy.py` 和默认句界缩稿作为最后保险保留。
- video-script `lib.py` 删除只写不读的 `CONFIG["edit_mode"]`，`validate.py` 不再写它。
- 测试：删除只测改写行为的 `tests/script/test_narration_fixes.py` 和 `test_pure_script.py` 里的 `_align_narration_to_quiet` / `_post_dedup_narration` 用例；新增"full 与 cut_output 对同一份声音证据得到同一个 `overlaps_speech`"、超预算 error 的字段与边界、短槽不丢段、过短槽报 error、所有模式要求时间顺序、validate 拒绝 `--preserve-approved-text` 等用例；recap 的转发测试改为断言 validate 不收这个 flag、voiceover 仍收到。

## Alternatives considered

- **保留默认改写，只把 `overlaps_speech` 换成 speech ownership 算法**：最强理由是 `overlaps_speech` 统一了，而 Agent 偶尔写超的稿子仍能自动出片，不用多一轮返工。没采用：改写本身就是问题，静默截短和合并会让成片与 Agent 认可的稿子不一致，Agent 也不知道哪里被改了；而 voiceover 已经有带留痕（`spoken_text` / `truncated`）的句界缩稿作为最后保险，validate 再改一遍只是多一层不可见的改动。
- **超预算报 error 的阈值用现有 warning 的条件（按实际朗读速率估算超出时间窗）**：最强理由是"装不下就是装不下"，只有一个判断标准。没采用：那条线比原改写的截短阈值（推荐预算 × 1.25）更严，今天能原样通过、只带 warning 的稿子也会被拦下，扩大了这次行为变更的影响面。沿用 1.25 倍，被拦下的正好是以前会被截短或丢弃的那些段。
- **cut_output 也把超预算升级为 error**：最强理由是 production-boundaries 的原则对两种模式一样适用，lint 规则也会更整齐。没采用：cut_output 从没有改写过稿子，这次 owner 批准的是 full 模式的行为变更；cut_output 改成拦截会让今天能出片的剪辑流程开始失败，需要单独决定。
- **保留 `validate.py --preserve-approved-text` 作为无操作的兼容 flag**：最强理由是旧脚本或 Agent 习惯性传这个 flag 时不会报错。没采用：flag 已经不改变任何行为，留着会让人以为不传就会被改写；recap 是唯一的生产调用方，已经同步去掉，直接报 unrecognized arguments 更诚实。

## Consequences

- **收益**：full 和 cut_output 只有一套 `overlaps_speech` 算法；validate 不再改 Agent 的稿子，Agent 交出的就是 TTS 拿到的；video-script 脚本净减约 170 行，测试删掉一整份只测改写的文件；`--preserve-approved-text` 在 validate 层的分支消失，approved-text 只剩 voiceover 一处语义。
- **代价**：今天能通过的 full 模式稿子，可能因为超预算或未排序而失败并退回 Agent，多一轮返工；以前被静默丢掉的过短段现在会进入 TTS，由 voiceover 决定能不能放下。full 模式的 `overlaps_speech` 会变（例如一段 84% 落在安静窗口但仍压着 0.7s 对白的旁白，以前是 false，现在是 true），原声闪避跟着变。
- 这些变化只有单测证据，发布前需要一次真实 API 的端到端运行，确认 full 模式的返工率和闪避听感可以接受。
- 重访信号：如果真实运行里 Agent 反复因为 1.25 倍阈值返工、而这些段在 voiceover 里其实放得下，说明阈值偏严，应调整 `OVER_BUDGET_ERROR_RATIO` 或预算口径，而不是恢复改写。
