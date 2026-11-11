# Agent Note: 每项能力只留一个入口

Status: implemented

## Problem

几项能力有两个入口，第二个入口只是转调第一个，或者只有测试在用：

- `recap.py --doctor` 只把 `--tts-provider` 转给 `doctor.py`，recap 自己的预检也早就直接提示 `doctor.py`；它还让 `video` 位置参数变成可选，多出一条手写的“缺视频”报错。
- `cut.py --review-shots`、`--shot-scene-threshold`、`--shot-roi` 是 `shot_review.py` 的 `--threshold`、`--roi` 的另一种写法。recap 不传它们，`shot_review.json` 也没有代码读取。
- `consolidate.py` 的命令行入口没有任何命令或测试调用，只有 `docs/architecture.md` 把它列成入口；`understand.py` 进程内调用 `consolidate_index()`。
- `reference.py measure --no-scale` 没有文档和测试；video-reference 的笔记记录 `scale=320` 与 `--no-scale` 切点完全相同。
- `recap_inspect clip-map --source-start/--source-end` 只有测试在用。所有给 Agent 的提示都只教成片时间到原片时间这一个方向。

## Decision

- 删除 `recap.py --doctor`，`video` 改为 `nargs="+"`，缺视频由 argparse 报错。自检入口是 `doctor.py`。
- 删除 `cut.py` 的三个 flag 和渲染后的扫描调用。`shot_review.py` 是唯一入口，`--plan`、`--threshold`、`--roi` 不变；它写报告前的目标路径保护全部保留，因为用户给的 `--output` 现在是写入这个报告的唯一途径。
- 删除 `consolidate.py` 的 `main()` 和 `argparse`，进程内调用不变。删除 `reference.py --no-scale`，`measure(scaled=)` 参数保留。
- 删除 clip-map 的原片方向：`_map_source_window`、`_source_to_output`、结果里的 `cut_out_source_gaps` 和两个 flag。`cmd_clip_map` 只接受成片窗口。

## Alternatives considered

- **保留 `cut.py --review-shots`，因为一条命令就能剪完再扫。** 最强理由：Agent 不会忘记跑第二条命令。没采用：默认流程本来不跑它，且两套同义 flag 要一起维护；`production-boundaries.md` 依赖的是扫描能力本身，这项能力还在。
- **连同 `shot_review.py` 的输出目标保护一起删掉。** 最强理由：能再少约 45 行，这些代码也没有修复记录。没采用：一个写错的 `--output` 会用 JSON 覆盖视频或计划。没出过事说明它代价低，不说明它没用。
- **保留 clip-map 的原片方向。** 最强理由：查询某段原片落在成片哪里确实是个合理问题。没采用：没有文档教这个方向，`clip_plan_validated.json` 每个 clip 同时带两个时钟，`video-script/SKILL.md` 已经让 Agent 读它。

## Consequences

- 收益：少 8 个 flag 和 1 个命令行入口；`recap --help` 少一个“自检”分组。
- 代价：旧命令里的这些 flag 会被 argparse 拒绝，要改命令；扫描要单独跑一条命令；手工只重建索引要写一行 Python 或走 `understand.py`。
- 是否在默认路径：都不在。
