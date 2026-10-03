# Agent Note: consolidate 截断的模型回复不再写成空索引并报 ok

Status: implemented

## Problem

一次真实的 5 分钟解说运行（23 个场景、61 段 ASR）里，`understanding_index.json` 的五个列表全为空，`consolidation.status.json` 却是 `ok`，而 ASR 里明明有范闲、五竹、费介、范建、叶轻眉。复现两次：索引调用 `max_tokens=3000`，模型输出写到第 6500 字左右的 `relationships` 时被截断（`finish_reason=length`）。链路上每一步都把失败吞掉了：

- `_response_text` 只取 `content`，不看 `finish_reason`；
- `parse_index_response` 对解析失败返回空列表，这本是纯函数的合理兜底；
- 驱动照常写出索引、meta 和 md，runner 只检查文件是否存在，于是报 `ok`；meta 记下了这次输入的身份，之后的运行把空索引当作最新缓存复用。

ASR 清洗调用（`max_tokens=4000`）有同样的问题：截断后 `parse_clean_response` 原样返回未清洗的转写，照样写进 `asr_clean.json` 并缓存。

另外 `asr._run_asr` 读不到音频文件时记一条日志返回空串，上游 bug 因此被当成静音。

## Decision

- `consolidate._complete_json_reply(label, messages, max_tokens)` 包住两次调用：`_response_text` 改为返回 `(content, finish_reason)`。`finish_reason == "length"` 时加倍预算重试一次，再被截断就抛 `ConsolidateIncomplete`；回复不是 JSON 对象时直接抛，不重试（加预算修不好）。抛错发生在写盘之前，索引和清洗结果都不会落盘，旧产物保持原样。
- 预算：`_INDEX_MAX_TOKENS = _CLEAN_MAX_TOKENS = 8000`，重试 16000。
- `INDEX_PROMPT` 加一条紧凑规则：description/relation/text 每条不超过 40 字，visual_descriptions 最多 3 条，每项 evidence_ids 最多 6 个，plot_points 最多 20 条。prompt 写在 meta 里，已有索引会重建一次。
- 状态沿用现成通道：runner 捕获异常写 `status: failed` 和异常文本（含 `finish_reason=length` 与预算），brief 的 `_optional_stage_warning` 照常提示。没有新增状态值，也没有新增默认路径上的门禁。
- 合法 JSON 但段数对不上的清洗回复仍按原设计原样返回转写（`parse_clean_response` 不变）。
- `asr._run_asr` 读不到文件时抛 `RuntimeError("ASR: 无法读取音频 …")`；runner 已有的 ASR 失败处理会删掉缓存并报错。空文件和超体积分片仍返回空串。
- 测试（全部 mock API）：`tests/understanding/test_consolidate.py` 覆盖截断后重试成功、两次截断抛错且不写产物、非 JSON 不重试、清洗调用两次截断；`tests/understanding/test_io_fixes.py` 经 runner 断言截断时状态为 `failed` 且消息含 `finish_reason=length`，以及缺文件时 `_run_asr` 抛错。

## Alternatives considered

- **从截断的 JSON 里抢救已完成的部分**：最强理由是这次截断的回复里前几个角色和关系是完整的，抢救下来比什么都没有强。没采用，因为要写一个容错的增量 JSON 解析器，抢救出的索引缺哪些部分也说不清，下游会把残缺的索引当完整的用；与其写半份，不如明确失败、让 brief 提示。
- **按输入规模算预算**：最强理由是长片（上百个场景）也能拿到合适的上限。没采用，因为输出规模受 prompt 的条目上限约束、和输入长度不成比例，常数加一次加倍重试足够覆盖这次的真实输入，且更简单；真超出时会显式失败。
- **写空索引但把状态记为新的 `degraded`**：最强理由是保留产物、让 brief 知道是降级。没采用，因为空索引对 brief 没有价值，还会被 meta 当作最新缓存复用；现有的 `failed` 状态和 brief 警告已经能把原因带给写稿的人。

## Consequences

- **收益**：截断或乱码回复不再静默变成空索引或未清洗的"清洗结果"，也不会被缓存；状态文件写明原因。正常输入在 8000 token 内一次完成，紧凑规则让索引更短。
- **代价**：截断时多一次调用；两次都截断时这一轮没有索引（以前也只有一份空索引）。清洗调用失败会像以前的网络错误一样中断后面的索引调用。8000/16000 是否足够没有用真实 API 重跑验证，只基于这次截断位置（3000 token 约 6500 字、写到关系列表中段）估算。
- `_run_asr` 的调用方此前在文件缺失时得到空转写，现在会让 ASR 阶段失败。
