# Agent Note: 故事索引的剧情时间与重复角色做确定性修复

Status: implemented

## Problem

`consolidate.py` Pass B 由模型写出 `understanding_index.json`，真实运行里有两类稳定出现的瑕疵：

- `plot_points[*].time` 出现秒数 ≥ 60 的 `mm:ss`，如 `00:95`：模型把场景的秒数直接填进了秒位。写稿 Agent 按这个时间去找画面会找错。
- 同一个角色被拆成两条 `characters`，彼此共享名字或别名（一条叫"范闲"，另一条叫"小范大人"、别名里有"范闲"），关系也随之分裂成两条，brief 的 Characters 段重复列人。

## Decision

- 新模块 `skills/video-understanding/scripts/index_normalize.py`（纯函数，无 I/O）：
  - `parse_plot_time` 读 `MM:SS` / `H:MM:SS` / 裸数字 / `12.5s` / `12.5秒`，去掉开头的"约""大约"，区间（`01:20-01:45`、`01:20至01:45` 等）取起点，各字段不做范围检查，`00:95` 读作 95 秒、`1:75` 读作 135 秒；`format_plot_time` 写回规范的 `MM:SS`（超过一小时写 `H:MM:SS`）。读不出来、为负、或超过已分析场景末尾 1 秒以上的时间直接删掉 `time` 键，剧情文本和顺序保留。字符串形式的剧情节点原样通过。
  - `merge_characters` 按名字（去空白、不区分大小写）判断同一人：一条的名字等于另一条的名字或某个别名才合并，两条只共享一个别名（如都带"男子""老板"）不合并。`aliases` 写成字符串时当作一个别名（`parse_index_response` 与 `normalize_index` 都把它改成列表），不会被拆成单字。按输入顺序传递式合并：首次出现的条目保留名字和位置，被并入条目的名字进 `aliases`；`aliases` / `visual_descriptions` / `asr_mentions` / `evidence_ids` 稳定去重合并，`description` / `research_role` 取第一个非空值，`confidence` 取较高者。后来的条目同时连到两组时两组合成一组。
  - `repoint_relationships` 把关系两端改指存活的名字，丢掉因合并产生的自环（"范闲—同一人—小范大人"），同 `(a, b, relation)` 的重复关系合并 `evidence_ids`。
- `consolidate_index` 在确定性 ASR/调研兜底之后调 `normalize_index`，时长上限取 `vlm_analysis` 的最大 `end`；有修复时记一条日志。缓存命中时也跑一遍：旧版本写下的索引若有变化，就地改写 json/md/meta，不再调模型。修复是幂等的，已规范的索引不会被重写。brief 的索引读取（`briefing/context._load_consolidation`）也跑一遍 `normalize_index`，所以 `--brief-only`（cut 第二轮、素材库恢复）不用等下一次完整理解；这里不传时长上限，因为 brief 的场景可能是剪辑后的时间线，而剧情时间是原片时间。
- 测试：`tests/understanding/test_consolidate.py` 覆盖各种时间写法（含"约"、"秒"、区间）、不可读与越界时间、名字即别名的合并与关系重指、桥接合并、只共享别名不合并、字符串别名不拆字、幂等，以及缓存命中时修复旧索引且不调模型；`test_consolidation_brief.py` 覆盖 brief 读取时修复拆开的旧索引。

## Alternatives considered

- **在 `INDEX_PROMPT` 里要求秒位 < 60、同一角色只出一条。** 最强理由：从源头减少瑕疵。没采用：prompt 写在索引 meta 里，改一个字就让所有已有索引重建一次（每个 work_dir 多一次付费调用），而模型仍可能不遵守；确定性修复对新旧索引都有效。
- **秒位 ≥ 60 时判为无效直接丢弃。** 最强理由：不猜模型的意图。没采用：真实样本里这类时间就是场景秒数，进位后和场景时间对得上；猜错的风险由场景末尾上限兜住。
- **合并时让信息更多的条目当主条目。** 最强理由：留下更好的名字。没采用："信息更多"没有稳定定义，按输入顺序才可复现；被并入的名字保留在别名里，不丢信息。

- **共享别名照旧合并，另设一张通用称呼停用词表（男子、老板、警察、单字等）。** 最强理由：两条真名不同、靠一个具体别名相连的条目仍能合并。没采用：停用词表永远列不全，模型换个说法（"中年男人""那个人"）就漏过；"至少一边是名字"是结构性的条件，不依赖词表。

## Consequences

- 收益：索引里的剧情时间都是合法的 `MM:SS` 且在片长内；同一人只剩一条，关系不再分裂，brief 的 Characters / Relationships 段不再重复。旧 work_dir 的索引在下次理解或下次生成 brief 时免费修好。
- 代价：只共享别名的两条不再合并，模型若把同一人写成两个不同名字、只靠一个共同别名相连，这两条会保留为两人；模型把某人的名字写成另一人的别名时仍会误合并。被删掉 `time` 的剧情节点只剩顺序。合并只看名字与别名，不看描述。
