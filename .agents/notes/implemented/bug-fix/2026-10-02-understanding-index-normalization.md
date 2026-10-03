# Agent Note: 故事索引的剧情时间与重复角色做确定性修复

Status: implemented

## Problem

`consolidate.py` Pass B 由模型写出 `understanding_index.json`，真实运行里有两类稳定出现的瑕疵：

- `plot_points[*].time` 出现秒数 ≥ 60 的 `mm:ss`，如 `00:95`：模型把场景的秒数直接填进了秒位。写稿 Agent 按这个时间去找画面会找错。
- 同一个角色被拆成两条 `characters`，彼此共享名字或别名（一条叫"范闲"，另一条叫"小范大人"、别名里有"范闲"），关系也随之分裂成两条，brief 的 Characters 段重复列人。

## Decision

- 新模块 `skills/video-understanding/scripts/index_normalize.py`（纯函数，无 I/O）：
  - `parse_plot_time` 读 `MM:SS` / `H:MM:SS` / 裸数字 / `12.5s`，各字段不做范围检查，`00:95` 读作 95 秒、`1:75` 读作 135 秒；`format_plot_time` 写回规范的 `MM:SS`（超过一小时写 `H:MM:SS`）。读不出来、为负、或超过已分析场景末尾 1 秒以上的时间直接删掉 `time` 键，剧情文本和顺序保留。字符串形式的剧情节点原样通过。
  - `merge_characters` 以名字和别名（去空白、不区分大小写）为键，按输入顺序传递式合并：首次出现的条目保留名字和位置，被并入条目的名字进 `aliases`；`aliases` / `visual_descriptions` / `asr_mentions` / `evidence_ids` 稳定去重合并，`description` / `research_role` 取第一个非空值，`confidence` 取较高者。后来的条目同时连到两组时两组合成一组。
  - `repoint_relationships` 把关系两端改指存活的名字，丢掉因合并产生的自环（"范闲—同一人—小范大人"），同 `(a, b, relation)` 的重复关系合并 `evidence_ids`。
- `consolidate_index` 在确定性 ASR/调研兜底之后调 `normalize_index`，时长上限取 `vlm_analysis` 的最大 `end`；有修复时记一条日志。缓存命中时也跑一遍：旧版本写下的索引若有变化，就地改写 json/md/meta，不再调模型。修复是幂等的，已规范的索引不会被重写。
- 测试：`tests/understanding/test_consolidate.py` 覆盖各种时间写法、不可读与越界时间、共享别名合并与关系重指、桥接合并、幂等，以及缓存命中时修复旧索引且不调模型。

## Alternatives considered

- **在 `INDEX_PROMPT` 里要求秒位 < 60、同一角色只出一条。** 最强理由：从源头减少瑕疵。没采用：prompt 写在索引 meta 里，改一个字就让所有已有索引重建一次（每个 work_dir 多一次付费调用），而模型仍可能不遵守；确定性修复对新旧索引都有效。
- **秒位 ≥ 60 时判为无效直接丢弃。** 最强理由：不猜模型的意图。没采用：真实样本里这类时间就是场景秒数，进位后和场景时间对得上；猜错的风险由场景末尾上限兜住。
- **合并时让信息更多的条目当主条目。** 最强理由：留下更好的名字。没采用："信息更多"没有稳定定义，按输入顺序才可复现；被并入的名字保留在别名里，不丢信息。

## Consequences

- 收益：索引里的剧情时间都是合法的 `MM:SS` 且在片长内；同一人只剩一条，关系不再分裂，brief 的 Characters / Relationships 段不再重复。旧 work_dir 的索引在下次理解时免费修好。
- 代价：两个不同角色若被模型写了同一个别名（如都叫"男子"），会被误合并成一人；被删掉 `time` 的剧情节点只剩顺序。合并只看名字与别名，不看描述。
