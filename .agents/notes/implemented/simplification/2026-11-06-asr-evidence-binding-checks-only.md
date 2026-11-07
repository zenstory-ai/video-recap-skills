# Agent Note: ASR 证据 sidecar 只校验绑定

Status: implemented

## Problem

`validate_asr_timing_evidence` 有 81 行，加 24 行辅助函数，用来核对一份只有同模块写入的 sidecar：顶层键集合、每个窗口的键集合、状态和转写内容的关系、重读 `audio.wav.meta.json`、逐窗口对比 `start`、`end`、`post_glossary_text`。真正回答“这份转写还能不能复用”的是身份绑定，转写一变 `asr_result` 的 `{size, mtime_ns}` 就对不上（[[2026-09-20-no-content-hashing]]）。窗口里的 `start`、`end`、`text_availability`、`post_glossary_text` 只是抄 `asr_result.json`，模块外没有读取方。

## Decision

- 校验只保留：`schema_version`、`status`（合法且不是 `FAILED_*`）、源视频 / 转写 / 音频身份、术语表绑定，以及 `windows` 是列表、每项是带 `glossary_modified` 的字典。最后这条必须留：`asr_evidence_summary_for_brief` 读这个键，手改坏的 sidecar 应该按未命中处理，不应该让 brief 崩溃。
- 窗口不再写 `start`、`end`、`text_availability`、`post_glossary_text`。`SCHEMA_VERSION` 不变：多出的旧字段直接被忽略，已有 sidecar 继续有效，不触发重新转写。
- `_asr_cache_state` 对 `EMPTY_UNKNOWN`、`UNAVAILABLE_NO_KEY`、`UNAVAILABLE_NO_DURATION` 的过滤不变，这是防止失败被缓存成成功的那一层。

## Alternatives considered

- **保留状态与转写内容的一致性检查。** 最强理由：它是这份校验里唯一会真的发现矛盾的部分。没采用：写入方和读取方是同一个模块，转写被改身份就变了；唯一能触发它的是手改 sidecar，而手改中间产物本来就在续跑契约之外。
- **连 `windows` 的形状检查也删掉。** 最强理由：同样的理由，形状只有手改才会坏。没采用：这里的后果是崩溃而不是多一次重算，留两行比让 brief 抛 KeyError 划算。

## Consequences

- 收益：这个文件少 99 行；校验读起来就是一张绑定清单。
- 代价：手改过内容但绑定不变的 sidecar 不再触发收费重转写，状态与转写矛盾也不再被发现。
- 是否在默认路径：是，每次命中 ASR 缓存都走这里；正常写出的 sidecar 结果不变。
