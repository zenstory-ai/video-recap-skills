# Agent Note: video-reference 的名字扫描读得懂理解阶段写的 asr_mentions

Status: implemented

## Problem

0.6.1 候选的真实回归里，只要理解运行有 `background_research.json`、ASR 又提到了调研里的名字，`reference.py check` 和 `export` 就以 `TypeError: cannot use 'dict' as a set element` 崩溃。这正是 video-reference 文档要求的输入（泄漏扫描要读 `understanding_index.json` 的角色名、别名和 ASR 提及）。

- 生产方：video-understanding `consolidate._apply_deterministic_asr_research_fallback` 把 `characters[*].asr_mentions` 写成 `{text, evidence_id, matched_aliases}` 字典，和模型写的纯字符串混在同一个列表里。
- 消费方：`reference_check._index_names` 对 `aliases` / `asr_mentions` 直接 `names.update(list)`，遇到字典即抛错。
- 测试只用手写的纯字符串 fixture，生产方的真实形状从未进入 reference 的测试。该代码在 main 上但不在任何 tag 里，0.6.1 会是它第一次发布。

## Decision

- `reference_check._mention_names` 规定一个 `aliases` / `asr_mentions` 条目贡献哪些名字：字符串原样；字典取 `matched_aliases` 里的字符串（`text` 是 ASR 窗口原文，已经在台词 n-gram 语料里，不当作名字）；其他类型忽略。`name` 也只收字符串。
- 测试：`tests/reference/test_reference_check.py` 用生产方形状（字符串、带 `matched_aliases` 的字典、缺 `matched_aliases` 的字典、数字、`null` 混排）跑 `run_check`，R6 照常点名；`tests/orchestrator/test_understanding_index_reference_contract.py` 在两个子进程里分别跑 understanding 的 fallback 写出索引、再用 reference 的 `leak_corpus` 读它，钉住跨技能契约。

## Alternatives considered

- **让 consolidate 改写成纯字符串。** 最强理由：消费方不用分支。没采用：`evidence_id` 与命中别名是理解阶段让 ASR 贡献可追溯的字段；已有的 `understanding_index.json` 里也已经是字典条目（缓存命中时不会重写），消费方无论如何要能读。
- **把字典的 `text` 也当名字。** 最强理由：多扫一份文本更保险。没采用：`text` 是整句 ASR，原句已经进入连续字 n-gram 扫描；当成名字只会让报错引用一整句台词而不是名字。

## Consequences

- 收益：带调研的理解运行上，check / export 不再崩溃，确定性补写的别名（如「老滕」）也进入名字扫描。
- 代价：reference 现在显式依赖 `matched_aliases` 这个键名；理解阶段改名时，契约测试会先失败。
