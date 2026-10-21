# Agent Note: 四个畸形输入崩溃与采用配音的 tempo 上限记录

Status: implemented

## Problem

一次只读审计在 v0.6.0 代码上发现六个缺陷；对照当前 main 复现后，多视频 `clip_plan_identity` 与多源句界连续拼接两项已被修复（后者见 #167 的 `_continuous_source_join` 相邻 clip_id 判定），仍存在四项：

1. `resolve_project` 先对 `bindings` 做 `set(bindings)`，`bindings` 是列表时随后 `.get()` 抛 AttributeError；绑定值是列表时查表抛 TypeError。dashboard 有外层兜底，`recap.py --project` 直接回溯。
2. `vlm._parse_vlm_depth_response` 用 `([\d.]+)` 抓帧时间戳，`"."`、`"1.2.3"` 会进入 `frame_facts`；brief、lint 对键做 `float()` 时崩溃，而 `vlm_analysis.json` 已缓存，重跑同样崩溃。consolidate 为此单独写过 `fact_key` 兜底。
3. VLM 上下文 `understanding_brief._research_context` 接受 `background_research.json` 里字符串形式的 `plot_arcs` / `cultural_notes`，brief 的 `_format_background_research` 对同样的条目调 `.get()` 崩溃，且发生在付费 VLM 调用之后；brief 还用一份不带错误提示的重复加载器。
4. `assembly_settings_payload` 只在"显式混音 + 采用"时记录 adoption 的 tempo 上限，而显式混音根本不做 tempo；仅采用配音时渲染按 adoption 上限适速，manifest 却记录 CONFIG 值。只影响记录，不影响渲染。

## Decision

1. `resolve_project` 在查库前要求 `bindings` 是对象、每个绑定值是 id 字符串或空，否则抛 `BindingError`（`resources/project_binding.py`）。
2. 时间戳正则改为 `\d+(?:\.\d+)?`，不是数字的整行丢弃；consolidate 直接按 `float` 排序（`vlm.py`、`consolidate.py`）。
3. `briefing/context.normalize_background_research` 把字符串条目转成研究指南约定的对象 / 列表形状，两个格式化共用；brief 改用 `lib.load_background_research`（非法 JSON 或非对象给出明确错误），删除重复加载器。
4. 记录条件改为"有 adoption tempo 且不是显式混音"（`assembly_settings.py`）。

## Alternatives considered

- **(2) 读取时跳过坏键、保留正则** — 最强理由：已缓存坏键的旧 `vlm_analysis.json` 也能恢复。否：读取方有 brief、lint、consolidate 多处，每处都要兜底；在解析处拦住，坏键就进不了缓存。旧缓存需 `--force` 重跑一次。
- **(3) VLM 上下文也拒收字符串条目，两边一致地严格** — 最强理由：输入形状只有一种。否：研究文件由 Agent 手写，VLM 阶段已经接受字符串条目，拒收会让已有项目在调研之后才失败。

## Consequences

- **收益**：三类 Agent 手写输入的错误都在入口给出可读错误或被规整；manifest 如实记录渲染用的 tempo 上限。
- **代价**：仅采用配音（无显式混音）的运行升级后 `assembly_settings` 变化，第一次续跑重渲染一次；已缓存坏帧时间戳的 `vlm_analysis.json` 不会自动修复。

## Verification

回归测试（去掉修复时均失败）：`tests/orchestrator/test_project_binding.py` 两个畸形 bindings 用例、`tests/understanding/test_vlm_fixes.py::test_parse_frame_facts_drops_lines_whose_timestamp_is_not_a_number`、`tests/understanding/test_consolidation_brief.py::test_brief_accepts_research_written_with_bare_strings`、`tests/assemble/test_narration_binding_seal.py::test_settings_record_adopted_not_ignored_ambient_speed`。
