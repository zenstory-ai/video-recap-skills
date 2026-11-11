# Agent Note: 移除 MiMo 解说评审链

Status: implemented

后续决定：`REQUIRE_NARRATION_REVIEW=1` 现在报错一个版本，翻转了下面留给负责人判断的选择，见 [[2026-11-06-refuse-require-narration-review]]。

## Problem

每次带旁白的运行都会在 TTS 之前调用一次 `video-script/scripts/review.py`：把旁白全文、选定时间段的画面描述与对白转写、背景资料摘录和策划文件摘录发到 MiMo chat 接口，再把模型意见写进 `narration_review.json` / `.md`。这条路径默认开启、默认失败开放，也就是说它多一次付费调用和一条外发路径，却不会阻断任何默认运行。

维护它要带六个模块（`review.py`、`review_runner.py`、`review_response.py`、`review_grounding.py`、`evidence_bundle.py`、`recap_review.py`）、一份 MiMo 客户端副本（重试、退避、错误脱敏），以及两份专门测试（`test_review.py`、`test_review_gating.py`），合计约 2,600 行。它的证据映射本身也修过多轮：cut 模式把原片时间当输出时间造成假阳性，把有意留给原声的拍报成漏写（[[2026-10-02-review-respects-original-audio-beats]]）。负责人决定移除这条链。

## Decision

- 删除上述六个模块、`--review-narration`、`--no-review-narration`、`--require-narration-review`，以及 `narration_review.json`、`narration_review.md`、`narration_review_override.md` 的全部处理。`_deliver` 与 `_narrate` 的 `timeline` 参数只为评审存在，一并删除。
- 旧命令和旧 `work_dir` 打印的续跑命令走 argparse 的 `unrecognized arguments` 退出码 2，不写专门的拒绝代码。`REVIEW_NARRATION` 在 [[2026-11-06-remove-env-twins-of-run-flags]] 里已不再读取，仍然不读。`REQUIRE_NARRATION_REVIEW` 也不再当开关读，只在设为真值时报错，见 [[2026-11-06-refuse-require-narration-review]]。
- video-script 不再有任何远程调用：`lib.py` 删去 MiMo 客户端、`api_call`、`env_bool` 和相关 CONFIG 键，保留的 `validate.py`、`narration_lint`、`speech_ownership`、`entry_suggestion`、`deslop_qc`、`lint_summary` 本来就不依赖被删模块，无需搬家。video-voiceover 的 `env_bool` 也已没有调用方，一并删除。
- `review_response.RUBRIC` 里价值最高的检查进入 `video-script/SKILL.md` §7.1 的 Agent 自检清单：证据支撑、不编情节、原声拍、承诺兑现、信息增量、衔接、去套话、风格与包装。
- `docs/production-boundaries.md` 明确写出这些规则现在没有机器检查，`validate.py` 通过不代表它们成立。
- `_analysis_settings` 本来就不记录评审开关，保持不变。测试用写死的旧设置证明当前格式的 `work_dir` 能续跑、素材库条目能恢复；残留的损坏 `narration_review.*` 不被读取也不被改动。

## Alternatives considered

- **保留默认开启的评审。** 最强理由：这是唯一由第二个模型独立核对旁白的环节，它抓到过真问题，例如缺少依据的说法；而且默认开启，用户什么都不用做就有这层检查。Agent 自检是作者查自己的稿子，发现不了自己的盲区。没采用：负责人决定移除；默认失败开放意味着它从不阻断默认运行，意见要有人读才有用；它抓事实错误的代价是一次付费外发、一份要持续修的证据映射和误报。这份损失是真实的，记在下面。
- **改成默认关闭、显式开启。** 最强理由：默认不外发的目标达成了，需要第二意见的人仍然有。没采用：默认关闭的功能没有真实运行来暴露问题，代码和测试的维护量一行不减，还要继续在 AGENTS.md 要求的外发说明里解释它。
- **把评审规则改写成确定性 lint。** 最强理由：能保留机器检查，符合 `production-boundaries.md` 的方向。没采用：是否有证据支撑、有没有编情节都是语义判断，确定性规则做不到；只能抓到的固定句式 `deslop_qc` 已经在拦。
- **用 `_RemovedFlag` 给三个 flag 写专门报错。** 最强理由：报错可以说明原因。没采用：`unrecognized arguments: --no-review-narration` 已经指出了要删什么，专门报错还要多留一段关于已删除功能的代码。
- **`REQUIRE_NARRATION_REVIEW` 设了就报错一个版本。** 最强理由：与 `EDIT_MODE` 同类，设了它的环境以为有门禁，实际静默没有。没采用：这个变量此前已不再读取，真正影响的是极少数自动化环境，CHANGELOG 明确写了门禁已不存在；是否值得多保留一段拒绝逻辑留给负责人判断。

## Consequences

- 收益：video-script 的脚本不联网、不需要 `MIMO_API_KEY`；每次运行少一次付费调用，旁白不再在 TTS 之前额外发给 MiMo chat 接口；`REVIEW_NARRATION=0` 环境在上一批改动后会静默外发的风险随之消失。skill 与测试合计少约 2,600 行。
- 代价：旁白语义不再有任何机器或第二模型检查，事实错误只能靠 Agent 自检和人看成片发现，这正是 `production-boundaries.md` 第 28 行描述的“只写在说明里、返修后无人复核”。`--require-narration-review` 这道 TTS 前阻断也没有替代。
- 破坏性：带这三个 flag 的命令和旧续跑命令要删掉参数；读 `narration_review.json` 的外部脚本拿不到新文件；`REQUIRE_NARRATION_REVIEW=1` 不再有门禁，并且报错退出（[[2026-11-06-refuse-require-narration-review]]）。
- 是否在默认路径：是，默认运行少了评审这一步。旁白 TTS、lint、validate 和渲染输出不变，已由对基线的等价比对验证。
