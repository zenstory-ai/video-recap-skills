# Agent Note: 移除 --consolidate-asr 和 asr_clean.json

Status: implemented

取代 [[2026-10-02-speech-evidence-one-transcript]] 里“有 `asr_clean.json` 就用它”的决定；[[2026-10-02-consolidate-truncated-reply]] 与 [[2026-10-02-interjection-entry-ownership]] 的相关事实已就地改。

## Problem

`--consolidate-asr` 默认关闭，开启后让模型补标点、改同音字、标说话人，写出 `asr_clean.json`。这一个可选文件让五个技能的六个读者各带一条“清洗稿优先”分支，判定 lint、cut、assemble、评审的结果都可能因它改变。它造成过多次漂移：单视频与多视频评审读不同转写，brief 校验新鲜度和来源而其他读者存在即用，brief 曾一直拒收它，清洗模型把真实台词删空时这一行不再保护入口。索引 pass 不依赖它。owner 决定移除。

## Decision

- 删除 recap 与 understand 的 `--consolidate-asr`，`consolidate.py` 的清洗 pass（`CLEAN_PROMPT`、`build_clean_messages`、`parse_clean_response`、`consolidate_transcript`、`_dialogue_source`、`_ASR_SPAN_TOL` 等）、`--asr` 和只剩空跑含义的 `--no-index`，`briefing/inputs._load_clean_asr`，以及 `materials.ALLOWED_ARTIFACTS` 里的 `asr_clean.json`。
- `speech_ownership._source_asr_rows`、`sentence_boundaries._load_source_speech_spans`、`audio_mix._asr_segments`、`recap_timeline._source_speech_rows` 和 brief 都只读 `asr_result.json`。`review_grounding` 的形状适配器随之删除。
- 旧文件怎么处理：`work_dir` 或恢复出来的素材里留着的 `asr_clean.json` 不报错也不删除，只是没有任何读者，所以陈旧内容不可能生效。各技能都有测试放一份内容不同的旧文件，断言结果来自原始转写。
- 三处为兼容而保留的常量：
  - `_analysis_settings` 的 `consolidate_asr` 冻结为 `False`，理由与 [[2026-11-06-remove-mimo-video-overview]] 相同。旧素材条目照常恢复（`test_entry_saved_before_the_flag_removal_still_restores`），`restore_material` 按条目自己的 `artifacts` 列表恢复，所以含 `asr_clean.json` 的旧条目不受 `ALLOWED_ARTIFACTS` 变化影响。
  - `_settings_for_compare` 继续忽略 `consolidate_asr`，记录它为 `true` 的旧 manifest 还能续跑。
  - `_index_meta` 的 `inputs.asr_clean` 恒为 `null`。`_index_cache_matches` 严格相等比较：删键会让每个旧索引多付一次重建费；保留 `null` 时，没用过清洗稿的索引继续命中，用清洗稿建的索引（记录的是文件身份）不匹配，于是用原始转写重建一次，这正是想要的结果。
- `consolidation.status.json` 去掉 `do_asr`，`do_index` 当时保留（brief 读它），后来也移除，见 [[2026-11-06-consolidation-single-pass-plumbing]]。旧文件带 `do_asr` 没有影响。

## Alternatives considered

- **旧 `work_dir` 里发现 `asr_clean.json` 就报错**：最强理由是最不含糊，和移除交付路线时的做法一致。没采用：那些路线的产物代表另一条交付契约，换路线会交付错的东西；这份文件只是可选的文字增强，读者全删之后它不能影响任何结果，拒绝只会逼用户白白重跑付费理解。
- **保留文件读取，只删生成方**：最强理由是已有清洗稿的 `work_dir` 判定不变。没采用：那样分支一个都删不掉，而且陈旧的清洗稿会一直赢，正是这次要消除的。
- **把清洗能力留在 `consolidate.py --asr` 作为手工工具**：最强理由是 Agent 需要时还能得到带标点的稿。没采用：输出如果没人读就没有用，如果有人读就回到多份转写。

## Consequences

- **收益**：lint、cut、assemble、评审和 brief 读同一份文件，不再需要来源校验；少一条 MiMo 调用路径和一种产物。
- **代价**：Agent 读到的是原始 ASR，可能是连读长句，也有同音错字（术语表修正在 `asr.py`，不受影响）。开启过清洗的旧 `work_dir` 判定可能变化，用清洗稿建的索引会重建一次（收费一次）。素材库里开启过清洗的条目按设置不匹配重新分析。
