# Agent Note: 不设 MIMO_API_KEY 重跑理解不再毁掉已付费的转写，consolidation 离线跳过

Status: implemented

## Problem

复用 E2E 的 Agent 在一个已完成理解的 work_dir 上不带 `MIMO_API_KEY` 重跑 `understand.py`，结果真实转写被覆盖成 `[]`：

- ASR 缓存 sidecar（`asr_result.json.meta.json`）的 `settings` 里有 `mimo_asr_api_key_present`。没有 key 时它从 `true` 变成 `false`，缓存判为未命中，`transcribe_audio` 走无 key 分支，把 `asr_result.json` 写成 `[]`、证据写成 `UNAVAILABLE_NO_KEY`。
- 即使去掉这个字段，`mimo_asr_api_url` 也会变：默认 endpoint 由 key 前缀决定（`tp-*` 走 Token Plan 集群，空 key 走 `api.xiaomimimo.com`），所以 Token Plan 用户一去掉 key，URL 就不一样。VLM 缓存的 `api_url`、overview 的 `mimo_video_api_url` 同理：VLM 未命中时报"请设置 MIMO_API_KEY"退出，overview 的无 key 分支直接删掉已付费的 `mimo_video_overview.json`。
- consolidation 不看 key，照常发请求，必然 401，状态记成 `failed`。

## Decision

- `understanding_cache._asr_cache_payload` 不再写 `mimo_asr_api_key_present`。key 存在与否不是输出设置：无 key 的运行只会写 `UNAVAILABLE_NO_KEY` 占位，`_asr_cache_state` 现在把这个证据状态与 `UNAVAILABLE_NO_DURATION`、`EMPTY_UNKNOWN` 一样判为未命中，之后设置 key 会真正转写。旧 sidecar 里的这个字段比较时忽略（`_LEGACY_ASR_SETTINGS`），升级后有 key 的旧缓存仍然命中，不会多计费一次。
- `lib.offline_ignored_settings(api_key, endpoint_key)`：没有对应 key 时返回 `(endpoint_key,)`，有 key 时返回空。`_stage_cache_valid` 新增 `ignore_settings`，两边都去掉这些键再比较；`lib.settings_match` 做同样的事。用在 ASR（`mimo_asr_api_url`）、VLM（`api_url`）、overview 的最终缓存判定（`vlm.mimo_video_overview_cache_fresh`）和 brief 的 overview 读取（`briefing/inputs._mimo_overview_matches_current_inputs`）。有 key 时每个设置照旧比较，换 endpoint 仍会重算。
- runner 的 overview 分支：无 key 且缓存仍新鲜（忽略 endpoint）时复用，状态 `cached`、消息"未设置 MIMO_API_KEY，复用缓存"；只有缓存不可用时才删文件并记 `skipped_no_key`。
- consolidation：`consolidate_transcript` / `consolidate_index` 在缓存未命中、真要调模型之前检查 `CONFIG["api_key"]`，没有就抛 `ConsolidateNoKey`；`consolidate()` 逐个 pass 捕获，记进返回值的 `skipped_no_key` 列表，其余 pass 照常进行。缓存命中的 pass 不受影响。runner 据此写 `consolidation.status.json` 的新状态 `skipped_no_key`（消息点名 `MIMO_API_KEY` 和被跳过的 pass），brief 的 Optional stage warnings 与 `failed` 一样列出它。
- 测试：`test_asr_timing_evidence.py` 覆盖离线复用 Token Plan 转写、有 key 换 endpoint 仍未命中、旧 sidecar 带 `mimo_asr_api_key_present` 仍命中、无 key 占位永不命中；`test_vlm_fixes.py` 覆盖 overview 离线 endpoint 变化仍新鲜、换 prompt 仍失效；`test_io_fixes.py` 经 runner 先带 key 跑一遍，再去掉 key 重跑，断言转写不变、VLM 和 consolidation 都不发请求、新鲜索引照常复用，删掉索引后状态为 `skipped_no_key`；`test_consolidation_brief.py` 断言 brief 列出 `skipped_no_key`。

## Alternatives considered

- **无 key 时整段跳过缓存判定，直接沿用磁盘上的任何产物。** 最强理由：最省事，离线永远不会丢东西。没采用：换了视频或改了设置后离线重跑，会把旧视频的转写当成新视频的，错误比空转写更隐蔽。只忽略 key 决定的那一个 endpoint 键，其他输入照旧比较。
- **把 endpoint 从缓存键里彻底删掉。** 最强理由：一处改动，不用分有无 key。没采用：有 key 时换 endpoint（例如显式 `MIMO_ASR_API_URL` 指向另一个部署）可能换了模型版本，应该重算。
- **consolidation 无 key 时在 runner 里整段跳过。** 最强理由：不碰 consolidate.py。没采用：缓存命中的索引不需要 key，整段跳过会让离线重跑丢掉本可复用的索引提示。

## Consequences

- 收益：离线重跑（`--brief-only` 之外的完整 `understand.py`）不再覆盖转写、不再删 overview、不再因 VLM 缓存的 endpoint 不同而退出，也不再发注定 401 的请求；brief 明确说 consolidation 因无 key 跳过。
- 代价：`consolidation.status.json` 多一个状态值 `skipped_no_key`，读状态的人要认识它（仓库里只有 brief 读）。无 key 时缓存若因其他原因未命中（视频或设置变了），ASR 仍会写 `[]` 占位，这是诚实的"未转写"，不是本次修复的范围。
