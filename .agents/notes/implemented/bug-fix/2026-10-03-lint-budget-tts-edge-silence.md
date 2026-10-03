# Agent Note: 旁白 lint 的字数预算扣掉每块 TTS 首尾静音

Status: implemented

## Problem

video-script 的 lint 用 brief 同一个公式给每块算字数预算：`_recommended_char_budget(start, end) = int((end - start - 0.1) × speech_rate × speech_safety_margin × narration_speed)`。这只按字数折算朗读时长，没算每块作为一次 TTS 合成自带的首尾静音。voiceover 不裁这段静音，assemble 判断能否放进时间窗时用的是整段音频时长。

本机真实运行留下的 98 个 MiMo 段（有 `tts_meta.json` 文本对照，-40 dBFS 门限）实测：首尾静音合计 p10 / p50 / p90 = 0.40 / 0.54 / 0.62 秒（未经 1.15 倍 narration atempo），放进时间线约 0.35 / 0.47 / 0.54 秒。2–3 秒的窗口里这占了五分之一的空间，所以短窗口的稿子能过 lint，TTS 计费之后才在 assemble 以 `no_safe_fit` 阻断。[[2026-10-02-voiceover-no-default-truncation]] 已经记下估算偏乐观、标定是后续工作。

## Decision

- video-script `agent_text.py` 新增 `TTS_UTTERANCE_OVERHEAD_SECONDS = 0.45`（实测中位数，放进时间线的秒数）和 `_lint_char_budget(start, end) = _recommended_char_budget(start, end - 0.45)`。共享的 `_recommended_char_budget` 不变，仍与 video-understanding 那份逐函数一致（[[2026-10-02-function-level-parity]]）；lint 在它之上加这一项。
- `narration_lint.lint_narration` 用 `_lint_char_budget` 算 `budget_chars`，full 模式超过它的 1.25 倍仍是 `over_budget` error；`over_budget` / `slot_too_short` warning 的条件不变，但 `estimated_tts_seconds` 加上这 0.45 秒再和时间窗比。full 报 error、cut_output 只报 warning 的分工不变。`over_budget` error 多一个字段 `tts_overhead_seconds`。
- video-script SKILL.md 的控量规则和 validate 一节写明每块另加约 0.45 秒，brief 的每窗字数没扣这一项。
- brief（video-understanding）的每窗 `char_budget` 和头部 "Effective speech budget" 仍按旧公式，本次不改那个技能；它们比 lint 宽 1–2 字，短窗口按 brief 写满会被 lint 退回。
- 测试：`tests/script/test_approved_validation.py` 钉住 `_lint_char_budget` 与 brief 公式的关系、2.5 秒窗口按 brief 写满在 full 模式报 error、cut_output 的 warning 估算含首尾静音，原有超预算用例按新预算更新数字（10–13 秒窗口 9→8 字）；1.2 秒短槽用例改为 1.4 秒，3 个字在 1.2 秒里加上首尾静音已经放不下。

## Alternatives considered

- **在共享的 `_recommended_char_budget` 里扣，brief 与 lint 一起变。** 最强理由：brief 给的每窗字数和 lint 的判定一致，Agent 照 brief 写不会被退回。本次没做：那份函数的另一半在 video-understanding，这一轮只改 video-script；改共享函数要两个技能同一提交同步改，留给 understanding 的后续改动，届时 `_lint_char_budget` 可以删掉。
- **按 `emotion` 标签给慢读情绪（悲伤、温柔、舒缓等）再乘一个系数（如 0.85）。** 最强理由：情绪化朗读听感上更慢，预算应更紧。没采用：同一批 98 段里带标签的中位语速 3.53 字/秒、不带标签 3.74，只慢约 6%，已在 0.85 的 `speech_safety_margin` 之内；"悲/伤/怅/温柔/深情"一类 9 段中位 3.64，和不带标签几乎一样，"深沉"反而 3.87；真正慢的是"惊讶""平静、悬念"这类，每种只有 1–4 段。证据撑不起一份"慢读情绪"名单，硬加会让一部分稿子无故被退回。
- **按段长比例扣（例如乘 0.85）而不是扣固定秒数。** 最强理由：一个系数更简单。没采用：首尾静音是每次合成固定的一段，与字数无关，比例扣法会对长窗口过严、对短窗口仍不够。

## Consequences

- **收益**：2–3 秒窗口的超长稿在 validate 就退回 Agent，不再先付 TTS 费用、等 assemble 阻断；cut_output 的 warning 也更早出现。
- **代价**：full 模式里以前刚好过线的短窗口稿会被退回多一轮；每块预算少约 1–2 字，长窗口影响很小。brief 的每窗字数与 lint 暂时不一致，需要 Agent 读 SKILL.md 的说明，直到 understanding 那边同步。
- 0.45 秒来自一种音色（MiMo 默认）、98 段的实测；换供应商或音色后首尾静音可能不同。重访信号：真实运行里仍有窗口 ≥ 3 秒的块在 assemble 报 `no_safe_fit`，或首尾静音中位数明显偏离 0.45 秒。
