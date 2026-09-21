# Agent Note: 删除 video-script 的 brief 死链与两份孤儿 / 近重复 references

Status: implemented

## Problem

2026-09-20 一天合并了约 20 个 feature PR 后，skill 层出现三类"看得见但没人用"的东西：

1. `skills/video-script/scripts/` 里 `narration.py → agent_brief.py → brief_inputs.py / brief_timeline.py / brief_context.py`
   这条链约 1,300 行，在 video-script 的运行时没有任何调用者：`validate.py` 只 import `narration_lint`、
   `speech_ownership`、`timeline_fusion`；`review*.py` 不碰它。它只被 `tests/script/test_pure_script.py` 的 8 个测试、
   `tests/script/test_deslop_qc.py` 的 1 个测试和 `tests/orchestrator/test_brief_narration_parity.py` 的隔离运行测试到达，
   而这些测试验证的是 video-understanding 产 brief 的行为，却挂在 script 组里。
   [[2026-06-14-self-contained-skills-duplicated-libs]] 把它作为"自包含保险"保留，
   [[2026-09-20-slim-skill-layer]] 明确列为需单独立项的最大单笔收益。
2. `skills/video-recap/references/creative-editing-playbook.md`（229 行）与 video-script 那份字节相同，
   但没有任何 SKILL.md 链接它；只有 README 与 `data-schema.md` 一句话指过去。recap SKILL.md §2 自 #126 起已改为
   "全部按 video-script 执行；它会要求先读创作手册"。
3. `references/research-guide.md` 在 video-recap / video-understanding / video-script 各一份。recap 与 understanding
   两份只差三处措辞，且 understanding 那份写着"跳过并继续写 `narration.json`"——理解阶段并不写解说，
   措辞反而是错的；script 那份语义不同（写稿前补充调研），保留。

## Decision

- video-script 只保留 lint 侧：删除 `narration.py`、`agent_brief.py`、`brief_inputs.py`、`brief_timeline.py`、
  `brief_context.py` 五个文件；`lib.py` 同步删掉只被这条链读取的 CONFIG 键。video-understanding 的同名文件不变。
  parity 测试的清单缩到两个 skill 仍共有的 `agent_text / deslop_qc / narration_lint / speech_ownership / timeline_fusion`，
  `test_script_brief_is_standalone_without_understanding_producer` 与 `_ASR_SPAN_TOL` 的 script 路径一并删除；
  `PUBLIC_ENTRYPOINTS` 去掉 `video-script/scripts/narration.py`。
- brief 行为测试搬家：`tests/script/test_pure_script.py` 里依赖该链的 8 个测试和 `test_deslop_qc.py` 的
  `test_script_narration_brief_does_not_leak_hardcoded_example_entities` 移到 `tests/understanding/test_consolidation_brief.py`，
  改 import 路径，不改断言。
- 删除 video-recap 的 `creative-editing-playbook.md`；README / README.en / `data-schema.md` 改指 video-script 那份；
  合同测试只读 video-script 的 playbook，agent_brief 锚点只读 video-understanding。
- 删除 video-recap 的 `research-guide.md`；video-understanding 那份改为 recap 原文（"开始视频理解之前"的正确时序，
  不点名兄弟 skill）；recap SKILL.md §4.1 与 README.en 改为指向 video-understanding 技能的调研指南；
  `test_research_guides_match_their_own_stage_timing` 改读 understanding 的文件。
- `.gitignore` 补 `.mypy_cache/`。

本篇翻转 [[2026-06-14-self-contained-skills-duplicated-libs]] 中"video-script 保留完整 brief 链作为自包含保险"这一条；
"skill 之间不共享代码、每个 skill 可单独安装"的约束不变——video-script 单独安装后仍能 validate / review，
只是不再能自己产 brief（它本来也从不这样用）。

## Alternatives considered

- **保留死链，只把测试挪到 understanding 组** — 最强理由：零行为风险，parity 继续守着。否：1,300 行没有调用者的代码
  本身就是雜亂来源，读者每次都要判断"这个 skill 到底产不产 brief"；parity 测试也在为无人使用的副本付维护费。
- **反过来删 video-understanding 的 brief 链、由 video-script 产 brief** — 最强理由：brief 是给写稿用的，归写稿 skill 顺理成章。
  否：brief 的输入全是理解阶段的产物与缓存（`understanding_cache`、`asr_timing_evidence`、consolidate sidecar），
  编排器也是在理解阶段结束时写 brief；搬过去等于让 script 依赖理解阶段的内部格式。
- **保留 recap 的 playbook 副本，只补上 SKILL.md 链接** — 最强理由：README 读者从 recap 目录就能找到手册。否：#126 已决定
  recap 不再要求读手册，两份字节相同的 229 行只剩 parity 成本；README 改链一行即可。
- **三份 research-guide 全部合成一份放 video-recap** — 最强理由：单一事实来源。否：阶段 skill 的 markdown 不得引用兄弟路径
  （`test_markdown_references_are_local_and_resolve_inside_each_skill`），understanding 单独安装时需要自己那份；
  script 那份语义本就不同。

## Consequences

- **收益**：video-script scripts 从 17 个文件 / 约 5,600 行降到 12 个 / 约 4,300 行；parity 清单从 11 对降到 5 对；
  references 少两份重复文件；brief 行为测试回到生产它的 skill 组。
- **代价**：video-script 单独安装时不再能离线生成 brief（此前也没有入口这样做）；README 的手册链接换目录。
- 重访信号：若日后需要 video-script 在没有理解阶段的宿主上自己产 brief，另开笔记恢复该链，而不是回滚本篇。

## Verification

`python3.12 scripts/test.py understanding script orchestrator` 三组全绿；`ruff check skills tests scripts` 无告警；
`grep -rn "narration import\|from agent_brief\|brief_context\|brief_inputs\|brief_timeline" tests/script skills/video-script` 无结果。
