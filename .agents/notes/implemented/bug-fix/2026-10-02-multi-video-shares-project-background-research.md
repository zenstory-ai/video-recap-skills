# Agent Note: 多视频运行把项目级 background_research.json 交给每个来源的理解阶段

Status: implemented

## Problem

video-recap SKILL.md §4.1 让 Agent 把调研写进 `work_dir/background_research.json`，guohuo-60s 样例也把它放在项目级。可多视频 cut 的理解阶段对每个来源单独调用 `understand.py --work-dir sources/<source_id>/`，video-understanding 只从自己的 work_dir 读这个文件（`lib.load_background_research`）。结果项目级调研从未进入 VLM 上下文、ASR 人名纠错和 consolidate；替身复现时只能手工把文件复制到每个 `sources/<id>/` 才生效。没有任何文档说明这一点。

## Decision

- `recap_runner._share_project_background_research(project_work_dir, source_work_dir)`：项目文件存在，且来源目录里没有、或来源目录里的副本比项目文件旧（`mtime_ns`）时，用 `shutil.copy2` 复制过去。比项目文件新的来源副本保留，作为单集专用调研。
- `_run_or_restore_understanding` 新增可选参数 `project_work_dir`，只有多视频调用会传；复制发生在决定真正运行 `understand.py` 之后（素材库恢复成功时不复制，恢复出的分析自带当时的调研）。
- video-recap SKILL.md §4.1 写明多视频的放置和覆盖规则。
- 测试：`tests/orchestrator/test_io_fixes.py` 新增两条：多视频第一阶段的两次理解调用都读到了项目文件；来源副本较新时不被覆盖，项目文件更新后才覆盖并保留 mtime。

## Alternatives considered

- **给 `understand.py` 加 `--background-research <path>` 参数。** 最强理由：不复制文件，来源目录里也不会有一份可能过期的副本。没采用：调研文件在 VLM 上下文、consolidate、ASR 人名表和理解缓存身份等多处按 `work_dir` 读取，改成可配置路径要动整条链和缓存契约；素材库保存也按来源目录里的文件沉淀调研。
- **每次都无条件覆盖。** 最强理由：规则最简单，项目文件永远是唯一来源。没采用：会抹掉 Agent 专门为某一集写的调研；按 mtime 判断已能覆盖“改了项目文件要生效”的常见情况。
- **只在 SKILL.md 写明“多视频要把调研放进每个 sources/<id>/”。** 最强理由：零代码。没采用：Agent 在第一次暂停前就要运行理解，按现有文档写在项目级是自然做法；让每个复现者手工复制同一份文件，是把编排器该做的事推给了用户。

## Consequences

- 收益：多视频运行按文档写一份调研即可让每集的理解用上人物名和背景。
- 代价：来源目录里多一份副本；判断依据是 mtime，如果用户先改项目文件、再改来源副本，来源副本会胜出（这是有意的）。`copy2` 保留 mtime，项目文件不变时理解缓存的身份不变；项目文件改了，各来源的理解缓存会失效并重跑（与单视频改调研时一致）。
