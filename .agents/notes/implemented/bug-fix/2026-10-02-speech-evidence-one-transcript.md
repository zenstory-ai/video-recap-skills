# Agent Note: 声音归属读同一份转写，空白文本行只是时间证据

Status: implemented

## Problem

[[2026-10-02-interjection-entry-ownership]] 让 cut 门禁、script 旁白入口检查和 assemble 入口检查共用语气词规则，但三处读的转写和喂给 `_dialogue_speech_spans` 的行仍不一样：

- full 模式下 video-script `speech_ownership.load_source_sentence_evidence` 只读 `asr_result.json`；video-cut `_load_source_speech_spans` 和 video-assemble `_asr_segments` 都是有 `asr_clean.json` 就用它。语气词规则看文本，清洗把"啊！"还原成"救我！"（或反过来）时，同一个入口在 lint 里通过、到 TTS 之后被 assemble 以 `unsafe_entry` 阻断，或者反过来被 lint 白白拦下。
- 文本为空的 ASR 行：cut 在调用前就过滤掉（[[2026-10-02-coarse-asr-anchor-honesty]] 写明"没有文字的窗口照旧不算讲话"），script full 模式只滤掉 `""`、保留纯空白行，assemble 的回退路径原样传入；而 `_dialogue_speech_spans` 用 `row.get("text", "")`，空白行不是语气词，于是被当成对白。
- 单视频评审的 grounding 只读 `asr_result.json`，多视频评审却优先 `asr_clean.json`。

## Decision

- 三份 `_dialogue_speech_spans`（video-cut `sentence_boundaries.py`、video-script `speech_ownership.py`、video-assemble `audio_mix.py`）同时改：文本为空或只有空白的行是只有时间的证据，直接跳过，不算对白，也不给相邻对白留 1 秒保护；没有 `text` 字段的行仍按对白处理（词未知，不能当作没人说话）。规则放在 helper 里，调用方过滤与否结果相同；`test_interjection_rule_stays_identical_across_cut_script_assemble` 继续按 AST 校验三份一致。
- video-script 新增 `_source_asr_rows(work_dir)`：有 `asr_clean.json` 就用它的 `segments`，否则读 `asr_result.json`，并去掉空白文本行；full 模式的 `speech_spans`（整段归属）和 `dialogue_spans`（入口）都从这里来。存在即采用，不做新鲜度或来源校验，与 cut、assemble 完全一致。
- `review_grounding._preferred_asr` 让单视频和多视频评审都优先 `asr_clean.json`。
- assemble 调用方 `_handoff_speech_evidence` 回退读 ASR 时也先去掉空白文本行，整段 `speech` 行（压低与否、`quiet_source` 判定）同样不含它们；helper 改动单独已经让入口判定不再把空白行当对白。
- 文档：`video-recap/references/data-schema.md` 入口规则一段、video-script SKILL.md 读取清单同步。

## Alternatives considered

- **只在 video-script 的调用方过滤空白行，helper 不动**：改动只在一个 skill，不碰 cut 和 assemble。没采用：helper 的 docstring 写着"没有文本的行算对白"，assemble 回退路径照样会把空白行当对白，规则仍分散在各调用方。
- **空白行算对白但不参与整段归属**：最保守，ASR 没听清的讲话也拦住。没采用：cut 从一开始就不把空白行当讲话，multi-source 的 `speech_boundary_anchors_output.json` 也不写空白行；保守的一边只剩 script 和 assemble，正是这次要消除的分歧。只有标点的行（"……"）仍算对白，听不清的讲话通常是这种形状。
- **`asr_clean.json` 只在新鲜且来源匹配时采用（briefing/inputs.py 的做法）**：防止陈旧的清洗稿。没采用：cut 和 assemble 都是存在即采用，这里多一道校验又会让三处读到不同的文件；consolidate 在 `asr_result.json` 变了之后会重写清洗稿。

## Consequences

- **收益**：同一个 work_dir 里 lint、cut、assemble 对"入口是否打断对白"读同一份文本、用同一条规则，lint 放行的入口 assemble 不会在 TTS 之后再拦。
- **代价**：full 模式有 `asr_clean.json` 时 `overlaps_speech` 可能与以前不同（清洗合并或改写了文本时）；清洗模型若把真实台词删成空白，这一行不再保护入口。没有 `text` 字段与空白文本被区别对待，读者需要知道这一点。
- 只有单测证据，未在真实 work_dir 上重跑。
