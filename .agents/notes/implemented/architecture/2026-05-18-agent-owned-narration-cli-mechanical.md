# Agent Note: 创作决定归 Agent，CLI 只做机械步骤

Status: implemented

## Problem

早期流水线由 CLI 自己调 LLM 生成最终解说（zone/fill 混合模式、bigram 去重、自动对齐重写），质量靠一堆 case-by-case 启发式撑着。谁对成片负责说不清：Agent 拿不到创作权，代码又承担不了创作判断，用户反馈"解说冷"时只能继续堆规则。

## Decision

`narration.json`、剪辑计划 `clip_plan.json`、dub 译稿 `dub_script.json`，以及 `recap_story_plan.json` / `visual_audio_board.json` 一律由 Agent 编写；仓库脚本只准备产物（理解索引、`agent_narration_brief.md`）、做确定性校验（`validate.py` / `narration_lint.py`、dub lint）、执行 TTS 与合成。编排器 `recap.py` 走到需要创作产物的位置就暂停并打印要写的文件，Agent 写好后重复同一条命令继续。

must / never：

- 新增创作环节时，先给 Agent 写 brief 和一个确定性校验器，never 让 CLI 生成最终文案或替 Agent 选片段。
- 两份创作计划 JSON 是 Agent 与建议型评审的工作记录，不是渲染门禁；渲染只校验当前阶段硬输入（`clip_plan.json` / `narration.json`）。
- 硬门禁只能是确定性检查（`validate.py`、`assembly_qc.json`、dub lint）。LLM 评审（`review.py`、MiMo QC）默认建议型、失败开放；只有调用方显式 `--require-narration-review` 时，事实矛盾、残句、解析失败或评审不可用才在 TTS 前阻断，文笔类意见永远不阻断。
- 编排器不是无人值守调度器，不向任何平台发布。

来源：3843c39、b739126、2ae220e (#47)、d104333 (#27)、727a8a5 (#49)、468182c (#63)

## Alternatives considered

- **保留 CLI 的 LLM 生成作为 fallback** — 最强理由：没有 Agent 宿主时也能一键出片。否：它模糊了所有权，正是要移除的行为；3843c39 明确"不经新的产品决定不得重新引入自动生成"。
- **dub 模式用句子正则、去重、杂音过滤等启发式自动切分与取舍** — 最强理由：全自动、不需要 Agent 介入。否：每条规则都是针对个案的补丁，脆弱且互相打架；2ae220e 全部删除，切分/计时/翻译交给 Agent 写 `dub_script.json`。
- **把创作计划 JSON 做成渲染门禁** — 最强理由：能强制 Agent 先规划再写稿。否：会让已有工作目录和迁移中的流程直接失败（468182c Rejected）。
- **给剪辑计划加 `clip_plan_quality.json` 启发式打分** — 最强理由：剪辑质量可量化、可拦截。否：让 skill 僵硬、代码沉重，评审必须保持 Agent 原生（b739126 Rejected）。
- **模型 `verdict=FAIL` 直接作为硬闸** — 最强理由：省一层判断。否：模型判定不稳定；727a8a5 复核后改为只认 parse_error 与 `findings` 里的 error，verdict 仅作建议信号。

## Consequences

- **收益**：责任边界清楚，Agent 能把背景调研、用户上下文和自己的剪辑判断带进文案；代码里没有脆弱的文案启发式可维护。
- **代价**：每次成片至少一次暂停（cut 模式两次），无法无人值守；成片质量取决于 Agent，脚本只能守住时间、语速、句子完整性和证据引用，守不住"好不好看"。
- 例外：`validate.py` 在 full 模式会按安静窗口回写规范化字段与实测的 `overlaps_speech`，但 never 改写文本含义；已批准的定稿传 `--preserve-approved-text`，此时只允许更新实测 `overlaps_speech`，装不下窗口就失败回创作，never 自动缩稿或变速（#99）。
