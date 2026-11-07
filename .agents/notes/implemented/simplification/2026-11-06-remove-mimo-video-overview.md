# Agent Note: 移除 --mimo-video-overview 视频分片概览

Status: implemented

取代 [[2026-10-02-mimo-moderation-refusal-not-content]] 里关于宽判定的部分，[[2026-10-02-understanding-offline-cache-safety]] 与 [[2026-10-02-understanding-internal-dedupe]] 里 overview 相关的事实已就地改。`MIMO_MEDIA_RESOLUTION` 后来硬编码，见 [[2026-11-06-hardcode-five-unpublished-env-knobs]]。

## Problem

video-understanding 有两条画面理解路径：默认的逐帧 VLM，和默认关闭的 `--mimo-video-overview`。后者把视频按场景切片、base64 上传给 MiMo，再用结果替换逐场景描述。它带着一个 `vlm.py` 里三百多行的分片流水线、`briefing/inputs.py` 里的第二份切片与缓存键副本、三种产物（`mimo_video_overview.json` 及 `.partial.json`、`.status.json`）、`description_source` 概念和十个环境变量。CHANGELOG 宣称它更能读懂动作与剧情，但没有任何对比运行；真实运行记录里，被审核拒绝的素材上它也没有产出。默认路径从不用 `MIMO_VIDEO_API_KEY`，`recap --doctor` 却据此警告 “MiMo VLM not configured”。`recap_run_manifest.json` 只记 CLI 参数，`MIMO_VIDEO_OVERVIEW=1` 打开功能时 manifest 仍写 `false`。owner 决定不再保留。

## Decision

- 删除 recap 与 understand 的 `--mimo-video-overview`，`vlm.py` 从 `_video_data_url` 到文件末尾的全部分片代码，`understanding_cache` 的 `_write_mimo_overview_status`、`_merge_overview_into_scenes`，`briefing/inputs.py` 的分片副本与 overview 警告分支，`lib.py` 的 `OVERVIEW_REJECTION_MARKERS`、`is_usable_overview_chunk`、`settings_match`、`mimo_video_*` 配置和 `_mimo_endpoint` 路由，`doctor` 的 `mimo_video_*` 报告项与警告。`mimo_asr_api_call` 直接调 `api_call`，ASR 401 仍点名 `MIMO_ASR_API_KEY`。
- 不再读取的环境变量：`MIMO_VIDEO_OVERVIEW`、`MIMO_VIDEO_FPS`、`MIMO_VIDEO_CHUNK_MAX_SECONDS`、`MIMO_VIDEO_CHUNK_MIN_SECONDS`、`MIMO_VIDEO_CHUNK_TIMEOUT`、`MIMO_VIDEO_BASE64_MAX_MB`、`MIMO_VIDEO_MODEL`、`MIMO_VIDEO_PROMPT`、`MIMO_VIDEO_API_KEY`、`MIMO_VIDEO_API_URL`。`MIMO_MEDIA_RESOLUTION` 只有 overview 真正发送过，逐帧 VLM 从不发送；它在 VLM 缓存键里，删掉会让所有已付费的 VLM 缓存失效，所以它的固定值留在缓存键里；变量本身后来也不再读取。
- `recap_runtime._analysis_settings` 的 `mimo_video_overview` 冻结为 `False`：`materials.restore_material` 按原值比较 settings，删键会让素材库每个条目都变成“设置不匹配”并重新付费分析；续跑的 `_settings_for_compare` 也不会忽略这个键。
- `materials.SECRET_KEY_NAMES` 保留 `mimo_video_api_key`，旧素材数据仍按密钥脱敏。
- 旧 `work_dir` 里的 `mimo_video_overview*.json` 没有读者，brief 不再读 `mimo_video_overview.status.json`。`vlm_analysis.json` 落盘的一直是纯帧分析，所以描述回到帧分析，不需要迁移。
- `video-understanding/SKILL.md` 的远程调用说明改为不发视频片段；`--mimo-video-overview` 的解析错误由 argparse 给出。

## Alternatives considered

- **保留功能，只把分片副本合并**：最强理由是成片可能确实更好，只是没人量过。没采用：量过之前它就是一条要持续维护、并且会外发视频片段的路径，owner 也已决定移除；真要回来可以从 v0.6.2 取。
- **从 `_analysis_settings` 删掉这个键，并在恢复时兼容旧条目**：最强理由是 settings 里不留死字段。没采用：要在 `restore_material` 加一层只为历史条目存在的归一化，还要同时改 `_settings_for_compare`，冻结一个常量是最小改法。
- **设置了 `MIMO_VIDEO_OVERVIEW=1` 时报错**：最强理由是不让人误以为功能还开着。没采用：评审没有要求，回退的结果（帧分析）是有效的默认路径，且这会让一个已删除的变量继续被读取。代价写在下面。

## Consequences

- **收益**：video-understanding 只有一条画面路径；不再有把视频片段发出本机的路径，远程调用说明缩短；少十个环境变量、两个 flag、三种产物。
- **代价**：失去按分片读动作的描述，质量影响未量过。`MIMO_VIDEO_OVERVIEW=1` 现在被静默忽略。开启过它的 `work_dir` 续跑报设置不匹配，要用新的 `--work-dir`；旧素材条目若开启过它，按设置不匹配重新分析。
