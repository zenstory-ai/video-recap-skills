# Agent Note: 按产物归属切 skill、函数级 parity、给 skill 表面定预算

Status: proposed

## Problem

0.6.0（e7eb0a8）发布后，PR #126/#127 的瘦身成果又被吃回去了：assemble +3,070 行，recap +2,491 行（另有 1,461 行 dashboard 资产），recap 的 `scripts/` 顶层文件从 15 个回到 23 个。默认 cut 流程里，agent 动笔前要读约 57K 字符。多 agent 只读审计（7 个 mapper、3 个方案、1 轮对抗复核）给出的根因如下，都是结构性的，不是零散的脏代码：

1. **parity 以文件为单位。** `test_brief_narration_parity.py` 要求 5 个模块共 1,470 行逐字节一致。`timeline_fusion.py:5` 在模块顶层 import 了 `narration_lint`，所以 understanding 每次运行都背着约 1,060 行从不执行的 lint 代码，script 也背着约 240 行从不执行的 brief 代码。已经分叉的基础设施反而没有守护：`api_call` 3 份、`env_bool` 4 种语义、MiMo endpoint 表 4 份。
2. **新功能没有归属规则，只加不删。** 新功能落在用户 flag 最先碰到的那个 skill，所以 recap 收了 library/dashboard/MiMo QC，assemble 收了剪映导出和 adoption，voiceover 收了 dub。结果留下了无人读取的产物（`cut_delivery_qc.json`、`deslop_qc_requirements.json`）、没有调用方的模式（`validate --mode cut`、ducking sidechain/none），以及只有测试在改的开关（`tts_dynamic_params`）。
3. **创作方法没有唯一 owner。** CREATE/DIRECTED/REVISION、7:3、reason 格式这些规则同时写在 `briefing/builder.py:100-370`、`recap_timeline.py:23-41`、cut/script 的 SKILL.md 和 playbook 里，并且被 `test_creative_skill_contract.py` 钉住了每一份副本。
4. **规模没有预算。** SKILL.md 篇幅、flag 数量、顶层文件数都没有上限。800 行上限按行数切模块，切出来的边界是假的：skill 内跨模块导入私有名 215 处。文档描述的架构比实际更干净，例如 assemble SKILL 宣传的 `DUCKING_MODE` 在 `lib.py:58` 写死为 fixed。

审计还顺带发现了两个实际 bug：

- 多源 cut 写出的 `speech_boundary_anchors_output.json`（`recap_timeline.py:500-508`）缺少 `clip_plan_identity`，`speech_ownership.py:28` 直接按这个键取值。
- `materials.py` 的 `ALLOWED_ARTIFACTS` 漏了 `asr_timing_evidence.json`。

## Proposal

不新增 skill，也不放宽"skill 间不共享代码"（[[2026-06-14-self-contained-skills-duplicated-libs]]）。改变的是**归属规则**和**守护粒度**：

- **每个 skill 一句话职责：**
  - understanding：只产证据和 brief，不带 lint。
  - script：创作规则、`clip_plan`/`narration` 契约和 lint/review 的唯一 owner。
  - cut：只管剪辑与映射，不管交付 QC。
  - voiceover：负责 TTS；dub 暂时寄放在这里。
  - assemble：负责成片；adoption 和剪映放在子包里。
  - recap：只负责路由和续跑。
- **新功能按"它产出哪个产物"认定 owner。** 只在特定 flag 下才会走到的代码放进 `scripts/<family>/`，SKILL.md 里只留一行指针。

分阶段执行，每一步是一个 PR。完整证据、行号和被否决方案的对照表见 `.omc/plans/2026-10-02-skill-architecture-audit.md`。

- **Phase 0 修 bug：** 补上多源 anchors 的 `clip_plan_identity`，加一条不 mock 子进程的回归测试；补齐素材库白名单。
- **Phase 1 零破坏删减**（约 4,000 行）：
  - parity 改为函数级（只覆盖 5 个真正共用的函数），删掉 understanding 里的 lint/ownership/deslop 副本和 script 里的 timeline_fusion。约 1,390 行，是最大的一刀，必须最先做。已落地，见 [[2026-10-02-function-level-parity]]。
  - 删除只写不读的产物、走不到的分支和门面层。
  - 修正 SKILL.md 中与代码不符的内容。
  - recap 的 dashboard 和资源模块归位到子包。
  - 续跑提示改为回显原始 argv。
- **Phase 2 边界调整：**
  - 创作规则只留在 video-script 的 playbook，brief 和 pause banner 只留文字指引。完成后必须做一次真实 API 端到端，对比初稿质量。
  - SKILL.md 渐进披露：recap 降到约 8K 字符，script 降到约 120 行。
- **Phase 3 CLI 表面（0.7.0）：** recap `--help` 分层，默认只显示 core flag；删除没有生产调用方的 `validate --mode cut`、`cut.py --clip-plan`、`consolidate.py main`；支持只带 `--work-dir` 的续跑。

护栏（防止再次膨胀）：

- 函数级 parity 加"副本中的函数必须在本 skill 被调用"。
- 孤儿产物测试，以及"每个产物只有一个 producer"的归属表。
- 只有测试在用的 flag 或 CONFIG 键让测试失败。
- SKILL.md 字符预算：recap ≤8,000，script ≤7,000，assemble ≤5,000，understanding 和 voiceover ≤4,500，cut ≤4,000。
- `scripts/` 顶层白名单。
- 创作规则的标志短语只允许出现在 video-script。
- feature 笔记的 Consequences 必须写四项：是否在默认路径、谁读取产物、删除信号、agent 表面增量。

## Alternatives considered

- **取消"不共享代码"，建 `_common` 包。** 最强理由：一次解决 `api_call`、`env_bool` 的漂移。不采用：宿主只保证 `skills/<name>/` 存在，单独安装的 skill 会断。臃肿的根因是 parity 粒度，不是复制本身，函数级 parity 已经够用。
- **剪映导出、dub、library/dashboard 各拆成独立 skill。** 最强理由：assemble、voiceover、recap 各自瘦身 1,800 到 3,700 行。不采用：这只是搬动，一行没删。dub 拆出去要再复制一整套 MiMo 客户端，总代码反而变多。它还推翻了 [[2026-09-21-adoption-family-stays-in-skill-as-subpackage]] 的"先分包"决定。
- **合并 understanding 与 script。** 最强理由：parity 问题自然消失。不采用：前者是昂贵、可缓存的 API 阶段，后者是 agent 创作阶段，两者的暂停点不同。做完函数级 parity 后，共享面只剩 5 个函数。
- **recap 改用 argparse 子命令或 preset 配置文件。** 最强理由：39 个 flag 可以按流水线隔离。不采用：位置参数 `video` 会和子命令名冲突，所有文档里的命令都要改。分层 help 加 `--work-dir` 续跑已经拿到主要收益。
- **把 recap_* 收进 `pipeline/` 子包。** 最强理由：215 处私有名导入说明模块边界是假的。不采用：要改约 40 处测试导入，一行不删，还会把按行数切出来的结构固化下来。

## Consequences

- **收益：**
  - Phase 1 约删 4,000 行，Phase 2 再删约 600 行重复散文。
  - agent 默认要读的内容从约 57K 降到约 35K 字符。
  - 新功能有明确的落点和删除信号；预算测试让膨胀在 PR 里就变红，而不是等到下一次大扫除。
- **代价：**
  - Phase 2 删减创作散文后，初稿质量可能变化，必须先做真实 API 端到端对比。
  - Phase 3 删除 3 个 CLI 入口，需要升到 0.7.0 并写入 CHANGELOG。
  - 预算数字会带来"提预算要写笔记"的流程摩擦。
- **待 owner 拍板：**
  - `golden_eval.json` 是否并入 final_qc（建议并入）。
  - recap 的 data-schema.md 是否改成索引（建议改）。
  - MiMo 多模态 QC 去留：一个版本周期内没人用就整体删除，约 2,170 行。
  - full 模式的旧版文本改写：建议本轮不动，另做 A/B 实验。
