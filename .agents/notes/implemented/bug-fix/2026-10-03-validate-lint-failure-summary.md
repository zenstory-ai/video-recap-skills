# Agent Note: validate 的 lint 失败给出逐块摘要，段号从 1 数

Status: implemented

## Problem

`validate.py` 遇到写进文档的普通 lint 失败（`over_budget`、`interrupts_source_sentence` 等），`narration_lint.validate_narration_or_raise` 抛 `ValueError`，`main` 不接，Agent 看到的是一整段 Python traceback，最后一行只有 `narration.json 预检失败: #1: over_budget; 详见 narration_lint.json`。能用来改稿的信息（时间窗、预算、硬上限、实际字数、建议入点）只在 `narration_lint.json` 里，还得再开一次文件。而且 `#1` 是从 0 数的下标，指的是 narration.json 的第 2 块。

同样的从 0 数也出现在其他给人看的地方：`over_budget` 的 message 写 `Segment 1`，cut_output 越界错误写 `segment 0`，评审的 `narration_review.md` 把模型返回的 `segment`（草稿从 0 编号）原样写成 `段 0`。

## Decision

- `narration_lint.NarrationLintError(ValueError)` 携带完整 `report`，`str()` 是新模块 `lint_summary.format_lint_failure(report, lint_path)` 渲染的摘要：首行 `narration.json 预检失败：N 个 error，修改后重跑 validate。`，之后每个 error 一行 `- 段 N <code>：<关键数字>。改法：<提示>`，最多列 12 个（多出的写"另有 K 个"），末行是 `narration_lint.json` 的路径。`over_budget` 列时间窗、实际字数、预算、硬上限和超出字数；`interrupts_source_sentence` 列入点、建议入点、锚点置信度和句尾文字；`source_sentence_anchors_unavailable`、`time_overlap`、`out_of_order` 也有专门格式，其他错误码用 lint 自己的 message。
- warning 用同一行格式：lint 通过但有 warning 时，`validate_narration_or_raise` 打印 `lint_summary.format_lint_warnings`（首行 `narration lint：通过，N 个 warning（不阻塞）`，每个 warning 一行，不设上限，末行报告路径），不再只打一行 `narration lint: N warnings`；没有 warning 时打印 `narration lint：通过`（原来是英文 `narration lint: ok`）；失败摘要在 error 之后也列出全部 warning。`slot_too_short`、`incomplete_sentence`、`crosses_scene_boundary`、`visual_beat_too_broad`（lint 为此新增 `frame_fact_count`）、`no_original_blocks` 等 warning 码各有数字与改法；`over_budget` warning 列字数、预算、估计读完时长与可用时长。lint 里 `over_budget` warning 不再写在 `slot_too_short` 的 `elif` 分支：时间窗短到 `slot_too_short` 的块被写超时两条都报。cut 与 cut_output 的超预算仍是 warning。
- `validate.main` 只接 `NarrationLintError`，转成 `SystemExit(摘要)`：退出码 1，stderr 只有摘要，没有 traceback。其他异常照旧抛出带 traceback。recap 调 validate 时不截获 stderr，摘要直接出现在运行输出里。
- 给人看的块号统一为「段 N」，N 是该块在 narration.json 里的位置、从 1 数：lint 摘要、`over_budget` 的 message、cut_output 越界错误（`段 N start=…`）、`narration_review.md` 的 findings（`segment` 为非负整数、整数值浮点或数字字符串时 +1，`null` 仍写"整体"；其他形状不是块号，写成 `段 ?（模型返回 <原值>）`，不把原值当段号）。JSON 字段不变：`narration_lint.json` 的 `index` / `previous_index`、`narration_review.json` 的 `segment` 仍从 0 数；评审 prompt 里的草稿仍从 0 编号，模型返回的 `segment` 含义不变。
- 去 AI 味（deslop）blocker 也会进 lint 的 errors，它带 `source`：来自 `original_subtitles.json` 的 blocker，`index` 是那个文件里的位置，摘要写成「原声字幕第 N 条」，改法提示改 `original_subtitles.json` 而不是 narration.json，不写「段 N」。deslop 拿到未过滤的 narration 列表、只跳过非对象条目，所以 narration 来源的 `index` 就是 narration.json 里的位置（以前先滤掉非对象再编号，前面有非对象条目时段号偏小）。`em_dash`、`placeholder_leakage` 有各自的改法提示。
- 测试：`test_approved_validation.py` 断言摘要的确切一行、报告路径、退出码 1 且 stderr 无 traceback（子进程用 `PYTHONIOENCODING=utf-8` 并按 utf-8 解码，Windows 也能读中文）、超过 12 个 error 时的截断、非 lint 异常仍抛出、cut_output 短时间窗被写超时两条 warning 都报并逐行打印、14 个 warning 全部打印、失败摘要列出 warning；原来按 `ValueError` 匹配的 validate 用例改为 `SystemExit` 并带上「段 N」；`test_review.py` 断言 md 里是 `段 1` / `段 3` / `整体` 且 JSON 的 `segment` 不变，`0.0` 写 `段 1`、列表与负数写 `段 ?（模型返回 …）`；`test_deslop_qc.py` 断言原声字幕第 3 条的破折号在摘要里写「原声字幕第 3 条」而不是「段 3」，以及前面有非对象条目时 narration 的段号仍是文件位置。

## Alternatives considered

- **在 `validate.main` 里自己读 `narration_lint.json` 拼摘要。** 最强理由：`validate_narration_or_raise` 完全不用改。没采用：报告已经在内存里，再读一次文件多一个失败点；`work_dir` 为空时（进程内调用）也没有文件可读。
- **让 `validate_narration_or_raise` 直接抛 `SystemExit`。** 最强理由：少一个异常类。没采用：它也被进程内调用，`SystemExit` 会让调用方意外退出；保留 `ValueError` 子类，进程内调用方的 `except ValueError` 仍然成立，只有入口把它转成退出。
- **把 JSON 里的 `index` 也改成从 1 数。** 最强理由：人和机器看到同一个数字。没采用：`index` 是已发布的落盘字段，recap、测试和 Agent 都按 0 读；本版本的约定是只改给人看的文字。

## Consequences

- **收益**：Agent 在一次输出里就拿到逐块的改法和数字（error 与 warning 都是），不用再开 `narration_lint.json`；块号和 narration.json 里的位置一致，不再差一。
- **代价**：warning 多的草稿（比如几十块都缺句末标点）会在控制台打出同样多行；按旧文本匹配的脚本（`#1: over_budget`、`Segment 1`、`segment 0`、`段 0`、`narration lint: N warnings`）需要更新；validate 的 lint 失败从 `ValueError` traceback 变成退出码 1 的 `SystemExit`，退出码与以前未捕获异常时相同。
