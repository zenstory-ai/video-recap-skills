# Agent Note: 第三轮审计顺带发现的七个缺陷

Status: implemented

## Problem

[[2026-10-20-third-pass-defensive-and-stale-cleanup]] 的只读审计在找冗余代码时发现了七处会改变可达行为的缺陷，当时刻意不并入净删除的清理 diff：

1. **多源剪辑句界门禁误判**：`snap_multi_source_clips` 把每个源的片段单独拼成小计划吸附，小计划的输出时间从 0 重排。同一源的两段若在全片里被别的源隔开（A[0–10]、B、A[10–20]），在小计划里仍首尾相接，`_continuous_source_join` 判为安全，10s 处落在讲话中间的硬切不会阻断。
2. **多视频旁白校验必崩**：多视频的 `speech_boundary_anchors_output.json` 没写 `clip_plan_identity`，而 video-script 的 `speech_ownership` 在 `cut_output` 模式下直接索引它，每次多视频 cut 解说校验都抛 KeyError。编排器测试 mock 了子进程，所以没暴露。
3. **`recap_project.json` 的 `bindings` 不是对象时抛 AttributeError**（或绑定值不是字符串时抛 TypeError），而不是可读的 `BindingError`。dashboard 有外层兜底，`recap.py --project` 没有。
4. **VLM 帧时间戳键**：正则 `([\d.]+)` 接受 `"."`、`"1.2.3"`，之后 brief / lint 里的 `float()` 崩溃；`vlm_analysis.json` 被缓存，重跑同样崩溃，直到 `--force`。consolidate 为此单独写过一个 `fact_key` 兜底。
5. **brief 拒收 VLM 阶段已接受的 `background_research.json`**：VLM 上下文接受字符串形式的 `plot_arcs` / `cultural_notes`，brief 的格式化对它们调 `.get()` 崩溃，而且是在付费 VLM 调用之后。brief 还用了一份没有错误提示的重复加载器。
6. **采用配音时 manifest 记录的 tempo 上限错位**：只有"显式混音 + 采用"时才记录 adoption 的上限，而显式混音根本不做 tempo；仅采用配音时渲染用的是 adoption 上限，manifest 却记 CONFIG 值。只影响记录，不影响渲染。
7. **MiMo QC 证据清单引用不存在的文件**：`asr.json`、`asr_segments.json`、七个字幕文件名、根目录的 `storyboard.json` 等没有任何 skill 会写，真实 storyboard 在 `storyboard/` 子目录，所以 `visual_metadata` 在真实运行里恒为空，prompt 却在解释其中的 `labels_burned`。

## Decision

1. 每个源只做画面与句界吸附；全片输出时间线重算之后，再按源对带全局 `output_start/end` 的片段副本跑 `enforce_clip_sentence_boundaries`。同源连续拼接只在中间没有别的源播放时成立（`sentence_boundaries.py`）。
2. 多视频写出 `clip_plan_identity: file_identity(clip_plan_validated.json)`，与单视频写法一致（`recap_timeline.py`）。
3. `resolve_project` 先校验 `bindings` 是对象、每个绑定值是 id 字符串或空，再查库（`project_binding.py`）。
4. 时间戳正则改为 `\d+(?:\.\d+)?`，不是数字的整行丢弃；consolidate 的 `fact_key` 兜底删除，直接按 `float` 排序（`vlm.py`、`consolidate.py`）。
5. `briefing/context.normalize_background_research` 把字符串条目转成文档约定的对象 / 列表形状，VLM 上下文与 brief 两个格式化共用；brief 改用 `lib.load_background_research`（非法 JSON 或非对象给出明确错误），删除重复加载器。
6. 记录条件改为"有 adoption tempo 且不是显式混音"（`assembly_settings.py`）。
7. 证据清单只列真实产物：源 ASR 为 `asr_clean.json` / `asr_result.json`，生成字幕为 `subtitles.srt`，画面元数据为 `storyboard/source_storyboard.json` 与 `storyboard/edited_storyboard.json`（`qc/mimo_evidence.py`）。

## Alternatives considered

- **(1) 不分组，直接对整份多源计划跑一次 `enforce_clip_sentence_boundaries`** — 最强理由：一次调用，天然用全局时间。否：每个源的句界窗口、讲话区间与源时长不同，单次调用只能接受一组；按源调用、但传全局输出时间，两者都满足。
- **(2) 让 `speech_ownership` 对缺失 `clip_plan_identity` 宽容、按过期处理** — 最强理由：改一处消费方即可。否：那样多视频的实测证据永远被当作过期丢弃，`cut_output` 退化为没有原声证据；问题在生产方漏写字段。
- **(4) 读取时跳过坏键，保留现有正则** — 最强理由：已存在的旧缓存也能恢复。否：读取方有 brief、lint、consolidate 多处，每处都要加兜底；在解析处拦住，坏键就不会进入缓存。已缓存坏键的旧 `vlm_analysis.json` 仍需 `--force` 重跑一次。
- **(5) 在 VLM 上下文那边也拒收字符串条目，两边一致地严格** — 最强理由：输入形状只有一种。否：研究文件是 Agent 手写的，研究指南的示例和实际写法都出现过字符串条目；VLM 阶段已经接受它们，拒收会让已有项目在调研之后才失败。
- **(7) 删除整个 `visual_metadata` 证据组和 prompt 里的 `labels_burned` 说明** — 最强理由：纯删除，不改 QC 输入。否：这组证据本来就是给 MiMo 判断画面时间标签的，只是路径写错；指向真实文件才符合原设计。

## Consequences

- **收益**：多源剪辑不再漏掉被别的源隔开的句中硬切；多视频 cut 解说能通过校验；绑定、帧标签、调研文件三类输入错误都在入口给出可读错误或被规整；manifest 如实记录渲染用的 tempo 上限；MiMo QC 拿到真实的 storyboard 元数据。
- **代价**：
  - 多源计划里过去被误判为安全的边界现在会阻断，需要 Agent 移到句末锚点。
  - 仅采用配音（无显式混音）的运行，升级后 `assembly_settings` 变化，第一次续跑重渲染一次。
  - MiMo QC 的缓存身份包含 `visual_metadata`，有 storyboard 的运行第一次续跑会重新请求一次 QC（建议型阶段）。
  - 已缓存坏帧时间戳的 `vlm_analysis.json` 不会自动修复。

## Verification

每项都有回归测试：`tests/cut/test_pure_cut.py`（两源隔开阻断、相邻同源仍为连续拼接）、`tests/orchestrator/test_io_fixes.py`（`clip_plan_identity` 等于计划文件身份）、`tests/orchestrator/test_project_binding.py`（两种畸形 bindings）、`tests/understanding/test_vlm_fixes.py`（坏时间戳整行丢弃）、`tests/understanding/test_consolidation_brief.py`（字符串条目进入 brief）、`tests/assemble/test_narration_binding_seal.py`（adoption 上限入 manifest）、`tests/orchestrator/test_mimo_qc_adapter.py`（storyboard 子目录路径）。
