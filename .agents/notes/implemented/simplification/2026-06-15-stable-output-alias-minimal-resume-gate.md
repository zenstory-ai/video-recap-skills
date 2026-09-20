# Agent Note: 成片固定别名原地覆盖，续跑门禁只证明源视频字节与设置

Status: implemented

## Problem

一轮 staleness / provenance 加固曾同时引入三层机制：assemble 的 render-identity（输出 `recap_<stem>_<hash>.mp4`，解说一变就冻结旧别名、再写一个新文件，并对 mp4 与全部输入做全量哈希、附 `.manifest.json` sidecar）；编排器的 32 键 CONFIG 快照 `phase_a_environment`；以及对 10 个 Phase-A 产物逐个指纹的校验层。结果是"改解说 → 重跑"的正常迭代冒出一堆副本，快照写了却从不比较，产物指纹层重复哈希且超出了"重跑同一命令即续跑"的文档契约。

## Decision

- `recap_<stem>.mp4` 是稳定的人类别名，每次运行原地覆盖（`assembly_contract.py`）；`assembly_manifest.json` 只记录输入来源、cut 来源指纹、渲染设置与最终输出路径。never 为输出名附哈希，never 冻结旧成片。
- Phase-B 续跑门禁 `_manifest_mismatches`（`recap_timeline.py`）只比较 `recap_run_manifest.json` 里的 `source_video`、`source_video_fingerprint`（全量 sha256）、CLI/env settings，以及 `audio` 块（`--audio-mode` / `--audio-stream-index`，本地采用三件套的 sha256；旧 manifest 缺该块时按 narration/流 0 解释）；任一不符即拒绝复用 work_dir 里的 `narration.json` / `clip_plan.json`，提示换 `--work-dir` 或删产物重跑 Phase A。多视频项目按 `source_id / source_path / source_video_fingerprint` 序列比较。
- 不对 Phase-A 中间产物做指纹校验：Phase B 不重跑理解阶段，模型 / endpoint 的 env 变化不会改变已落盘产物；手工编辑中间产物在续跑契约之外。
- 例外：cut 模式另有 `recap_phase.json` 记录 `clip_plan` 与 `narration` 指纹，`clip_plan.json` 变而 `narration.json` 未变时拒绝进入 TTS——这是保护画面对齐，不是产物完整性校验。
- 保留下来的是"拒绝把失败缓存成成功"这类修复：ASR 失败不缓存空转写、VLM 单场景失败中止、空解说不出片、缓存复用按内容而非 mtime。

来源：02e402f (#13)、187dd2b (#16) Step 5

## Alternatives considered

- **render-identity：按解说指纹给每个成片起唯一名并冻结旧文件** — 最强理由：永远不会覆盖掉一版可能想留的成片，每个文件可溯源。否：迭代解说的主流程变成不断生成副本而不是刷新一个文件，用户找不到"当前那一版"；每轮还要全量哈希 mp4。
- **10 产物指纹层：续跑前逐个校验 Phase-A 产物** — 最强理由：能发现手改过的中间产物。否：这超出"重跑同一命令"的契约，与源视频 + 设置门禁重复（Phase B 不重跑理解），只增加哈希开销。
- **32 键 CONFIG 快照** — 最强理由：环境漂移可追溯。否：三次提交后退化成 isinstance 存在性检查，写了从不比较，是死代码。

## Consequences

- **收益**：改解说只刷新一个文件；续跑检查便宜且可解释（缺 manifest、源视频不同、参数不同三类信息）。
- **代价与已知上限**：手工编辑 Phase-A 中间产物不会被发现；旧成片被覆盖，要保留版本需自行改名或换 `--output-dir`。
- 重访信号：出现"多版本成片并存"的产品需求，或 Phase B 开始重跑理解阶段（届时 env 变化会影响产物，需要重新评估校验范围）。
