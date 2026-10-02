# Agent Note: understanding 删 LEGACY_UNVERIFIED ASR 状态与重复的 brief 收尾

Status: implemented

## Problem

video-understanding 内部有几处只增加分支、不增加能力的代码：

- **`LEGACY_UNVERIFIED` ASR 缓存状态。** `_asr_cache_state` 在 stage meta 有效但缺 `asr_timing_evidence.json` 时返回它，runner 据此离线复用 `asr_result.json` 并补写一份 legacy sidecar（`observed_text`/`glossary_modified`/`glossary` 全为 `null`）。它原本为 sidecar 之前的旧缓存而设，但 v0.5.0 的 `asr_result.json.meta.json` 是 `source_video_fingerprint` 形状，过不了 0.6 `_stage_cache_valid` 的整 dict 比较，本来就是 MISS（[[2026-09-20-no-content-hashing]] 已规定旧 sidecar 一律未命中）；0.6 的每条 ASR 路径都先写 sidecar。素材库也不复制 `.meta.json`，恢复出的 work_dir 同样先 MISS。现实中只剩两条路能走到它：手工删掉 sidecar；或 `--force`/人名表变化触发重跑 ASR 时 Ctrl-C（`transcribe_audio` 先删 sidecar，runner 的 `except Exception` 接不住 `KeyboardInterrupt`），下次运行就把旧转写当 LEGACY 复用，并写下一份之后一直有效的 legacy sidecar。
- **brief 收尾写了两遍。** `understanding_runner.main` 和 `--brief-only` 的 `_write_brief_from_existing_artifacts` 各自生成 storyboard、打印素材偏薄横幅、用同一组 9 个参数调 `build_agent_brief`、加 storyboard 头、打印 JSON 状态行，只有状态值、日志文字和 `force` 不同。runner 还有四个几乎一样的 `_write_consolidation_status` 调用。
- **API 辅助函数的残留参数。** `_api_headers`、`_prepare_api_payload` 接收 `api_provider`、`api_url` 只为 `del` 掉；`api_call` 的 `api_provider` 同样无用。`_mimo_endpoint` 有一个本技能从不调用的 `tts` 分支，指向 CONFIG 里不存在的键；`video` 分支引用的 `mimo_video_env_var` 也不存在，于是用 `MIMO_VIDEO_API_KEY` 时遇到 401，报错让用户去检查 `MIMO_API_KEY`。
- **VLM prompt 拼了两遍。** `vlm.analyze_scenes` 和 `understanding_cache._vlm_prompt_payload`（缓存键）各自加载 `VLM_DEPTH_PROMPT`、各带一份硬编码兜底并加"已知信息："前缀。兜底只在技能自带的 `references/prompt-templates.md` 缺失时生效，而且已经和模板分叉（兜底写"分两部分输出"，模板是三部分）。

## Decision

- 删除 `LEGACY_UNVERIFIED`：`asr_timing_evidence.py` 的 `VALID_STATUSES` 去掉它，`_glossary_binding`、`_window_evidence`、`_valid_glossary` 去掉 `legacy` 参数，`validate_asr_timing_evidence` 与 `asr_evidence_summary_for_brief` 去掉 legacy 分支；`_asr_cache_state` 在 sidecar 缺失时返回 `MISS`；runner 只在 `FRESH` 时复用 ASR。旧 build 写下的 legacy sidecar 校验不过，同样是 `MISS`，下次运行重跑 ASR。`write_asr_timing_evidence` 传入该状态会抛 `ValueError`。`references/data-schema.md` 同步删掉这一状态。
- `understanding_brief._finish_brief(video, work_dir, args, video_duration, *, storyboard_scenes, brief_scenes, asr_result, silence_periods, status, done_label, force)` 承担 storyboard、横幅、brief、storyboard 头和 JSON 状态行。完整运行传检测出的 scenes 给 storyboard、VLM 分析给 brief，状态 `analyzed`；`--brief-only` 两处都传 VLM 分析，状态 `brief_only`、`force=False`。source storyboard 仅在 `scenes.json` 存在时生成（完整运行时它总存在）。输出的日志、JSON 字段与取值不变。
- runner 先算出 `(status, message, artifacts)`，再只调一次 `_write_consolidation_status`；四种结果（disabled/failed/ok/skipped）的写入内容不变。
- `lib.py`：`_api_headers(api_key=None)`、`_prepare_api_payload(payload)`；`api_call` 去掉 `api_provider`，保留 `api_url`/`api_key`/`api_env_var`（视频与 ASR 端点靠它们路由）。`_mimo_endpoint` 只剩 `video` 与 `asr`；新增 CONFIG 键 `mimo_video_env_var`，取法与 `mimo_asr_env_var` 相同，401 报错因此点名真正提供 key 的变量。
- VLM prompt 由 `vlm.vlm_prompt_payload()` 一处构建，`analyze_scenes` 发送它的 `prompt_text`，`understanding_cache._vlm_cache_payload` 用它做缓存键。硬编码兜底删除，模板缺失时抛 `RuntimeError`。builder 放在 `vlm.py` 而不是 `understanding_cache.py`，因为后者已经 import `vlm`。模板路径与内容不变，现有 VLM 缓存不失效。
- 不在本次范围：`MIMO_VIDEO/ASR/TTS_API_*` 分能力覆盖整体收拢（另一项候选，牵涉 recap 与 voiceover）；`coverage_policy_version` 只存在于 video-script，understanding 里没有副本。

## Alternatives considered

- **保留 `LEGACY_UNVERIFIED`，只修 Ctrl-C 后的粘滞问题。** 最强理由：手工删了 sidecar 的用户仍能离线复用转写、省一次 ASR 调用。不采用：没有 sidecar 就证明不了转写对应哪段音频和哪版人名表，复用的结果本来就被标成不可信；没有任何发行版写出过能走到这条分支的缓存，留着它只为手工改 work_dir 的情形维护一整套平行的校验规则。
- **把 `_vlm_prompt_payload` 留在 `understanding_cache.py`，让 `vlm.py` 去调它。** 最强理由：缓存键的构建都在 cache 模块，归属更整齐。不采用：`understanding_cache` 已经 import `vlm`，反向依赖会形成环；prompt 属于 VLM 阶段本身，放在 `vlm.py` 更符合归属。
- **直接删掉 `_mimo_endpoint` 的 `mimo_video_env_var` 引用，回落 `MIMO_API_KEY`。** 最强理由：少一个 CONFIG 键。不采用：那正是 401 报错指错变量的原因；补一个键就让报错准确。
- **保留 VLM prompt 兜底以防模板缺失。** 最强理由：模板文件意外丢失时 VLM 仍能跑。不采用：模板随技能一起发布，兜底已和模板分叉，静默换成另一份 prompt 比明确报错更难排查。

## Consequences

- **收益**：
  - understanding 脚本净少约 90 行；ASR 缓存只有 FRESH/MISS 两种结果，Ctrl-C 后不会再把旧转写粘成永久命中。
  - 完整运行与 `--brief-only` 的 brief 收尾只有一份，以后改 brief 参数不会漏改一边。
  - 发送的 VLM prompt 与缓存键按构造一致。
  - `MIMO_VIDEO_API_KEY` 被拒时报错点名它本身。
- **代价**：
  - 手工删掉 sidecar、或 ASR 重跑被中断的 work_dir，下次运行会多一次 ASR 调用。
  - 进程内调用 `_api_headers`/`_prepare_api_payload`/`api_call` 并传 `api_provider` 的外部脚本会报参数错误；仓库内没有这样的调用方。
  - `references/prompt-templates.md` 缺少 `VLM_DEPTH_PROMPT` 时 VLM 阶段直接报错。
- **默认路径**：命令行、参数、产物与默认值不变。
- **agent 表面**：SKILL.md 不变；`references/data-schema.md` 少一个状态值。
