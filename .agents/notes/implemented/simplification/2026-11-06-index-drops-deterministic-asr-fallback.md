# Agent Note: 索引不再由程序补写调研人物

Status: implemented

翻转了 [[2026-10-03-reference-reads-consolidate-asr-mentions]] 备选方案里“`evidence_id` 与命中别名要留着让 ASR 贡献可追溯”的判断；那篇的消费方修复仍然成立。

## Problem

`consolidate._apply_deterministic_asr_research_fallback`（连同 `_stable_unique`、`_dialogue_segments_for_index` 约 105 行）在模型写完索引后，把 ASR 里提到的调研人物补进 `characters`，并往 `asr_mentions` 里写 `{text, evidence_id, matched_aliases}` 字典。它加出来的字段没有人真正需要：

- `research_role` 没有读取方，`evidence_ids` 只被 consolidate 和 `index_normalize` 读。
- `asr_mentions` 和 `matched_aliases` 只有 `reference_check._index_names` 读，而 `leak_corpus` 已经把调研名字、术语表和 ASR 原文都收进来了，名字不会丢。
- brief 的 `_format_consolidation` 只打印名字和描述，调研部分本来就列出同样的人物和身份。

这个字典形状还造成过一次真实崩溃（见上面那篇笔记）。

## Decision

- 删除这三个函数。`consolidate_index` 保留一段：模型没写 `research_glossary` 时用 `_research_glossary_from_context` 填上，`support` 固定 `context_only`，prompt 和 `reference_check` 都用它。
- `INDEX_PROMPT` 不变，`asr_mentions` 仍是模型可写的字符串字段，索引缓存不失效。
- `reference_check._mention_names` 的字典分支保留：已缓存的索引里还有字典，`consolidate_index` 命中缓存时不会重写。
- `tests/orchestrator/test_understanding_index_reference_contract.py` 改为真跑 `consolidate_index`（只 stub `api_call`），再用 reference 的 `leak_corpus` 读结果，断言调研别名仍进入泄漏语料。它是跨技能契约的守卫，所以重写而不是删除。

## Alternatives considered

- **保留这段补写，只把字典改成字符串。** 最强理由：模型漏掉 ASR 里的人名时，`characters` 仍然完整，直接读 JSON 的 Agent 看得到。没采用：为一个 brief 和泄漏扫描都已覆盖的场景维护一百多行字符串匹配；匹配本身还会把调研里的人物“升级”进 `characters`，而 prompt 明确要求调研不得升级为剧情事实。
- **连 `research_glossary` 一起删。** 最强理由：调研内容 `background_research.json` 本来就在。没采用：`reference_check` 从索引读别名，brief 外的直接读者也靠它拿到调研名字。

## Consequences

- 收益：少约 105 行脚本；`asr_mentions` 不再是混合类型（新写的索引）。
- 代价：模型漏掉的调研人物不再保证出现在 `understanding_index.json.characters`，只出现在 `research_glossary` 和 brief 的调研部分。
- 风险：确定性补写的别名以前能进泄漏扫描；现在靠 `research_glossary` 的别名和 ASR 原文 n-gram，契约测试钉住前者。
