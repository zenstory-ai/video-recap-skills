# Agent Note: 按需适配的"已采用声音"家族留在 video-assemble，以 scripts/adoption/ 子包隔离；brief 生成器进 scripts/briefing/

Status: implemented

## Problem

`docs/production-boundaries.md` 把 source_score、pair_media、compose_foreground、narration/audio-mix adoption、
strict publish 归为"按需适配"，但它们与混音、字幕、时间线等核心模块平铺在 `video-assemble/scripts/` 顶层
（8 个文件、约 2,300 行），读者分不清"每次成片都跑的"和"只有交付已采用声音时才跑的"。
video-understanding 也有同样的问题：brief 生成器 `agent_brief / brief_context / brief_inputs / brief_timeline`
与 ASR、VLM、检测模块混在一起，且与 parity 共享的五个模块（`agent_text / deslop_qc / narration_lint /
speech_ownership / timeline_fusion`）名字相近却归属不同。

## Decision

- 按需适配不拆成独立 skill，留在拥有其产物契约的 skill 内，用子包标出边界
  （子包机制见 [[2026-09-21-scripts-subpackages-jianying]]）：
  - `video-assemble/scripts/adoption/{narration_binding,audio_mix_binding,strict_inputs,strict_publish,frozen_audio}.py`。
    `source_score.py`、`pair_media.py`、`compose_foreground.py` 有 `__main__` 且被 SKILL.md / references 点名，
    留在顶层作入口；`frozen_audio` 只服务 adoption 家族与 adopted 模式的字幕轨，一并进包。
  - `video-understanding/scripts/briefing/{builder,context,inputs,timeline}.py`
    （原 `agent_brief / brief_context / brief_inputs / brief_timeline`）；入口 `brief.py` 与 parity 共享的五个模块留在顶层。
- 顶层保留的模块就是"每次成片都会经过"的核心与公开入口；子包名即功能族名。

## Alternatives considered

- **拆成独立 skill（video-delivery）** — 最强理由：安装单位与"按需"语义一致，核心 skill 不再携带交付代码。
  否：这些模块读写 `assembly_manifest.json` / `assembly_qc.json` / 时间线契约，独立 skill 必须再复制一份 `lib.py`、
  `assemble_constants`、`artifacts` 与契约代码（自包含约束禁止跨 skill import），复制量超过它们本身；
  且 recap 的 adoption 三件套路径仍要调用它，宿主上多一个 skill 的发现/安装成本没有换来简化。
- **把入口脚本也搬进子包** — 最强理由：家族完整。否：`python3 scripts/adoption/source_score.py` 直接运行时
  `sys.path[0]` 是子包目录，`from lib import …` 与 `from adoption.x import …` 都会失败，需要每个入口自带
  sys.path 引导；入口留顶层是更简单的规则。
- **顺手把 recap 的 adoption 三件套路径拆成子命令** — 最强理由：编排器的策略矩阵会小一截。否：改 CLI 契约要动
  SKILL.md、README 与 `test_local_adoption_boundaries.py` 一整组，属于行为改动，另立项。

## Consequences

- **收益**：video-assemble 顶层从 24 个文件降到 19 个，video-understanding 从 23 个降到 19 个；
  目录结构直接回答"这是核心还是按需"。
- **代价**：测试 import 改为 `from adoption.narration_binding import …` / `from briefing.builder import …`；
  合同测试里 `agent_brief.py` 的锚点路径改为 `briefing/builder.py`。
- 重访信号：若所有目标宿主都支持 skill 间共享包，再评估把 adoption 家族拆成独立 skill。

## Verification

`python3 scripts/test.py` 七组：assemble / orchestrator 失败集合与 libass 基线相同，其余全绿；`ruff check skills tests scripts` 无告警。
