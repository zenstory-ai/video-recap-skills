# Agent Note: skill 的 scripts/ 允许子包，剪映导出先搬进 scripts/jianying/

Status: implemented

## Problem

800 行预算（[[2026-06-14-self-contained-skills-duplicated-libs]]）让大功能按行数切成一堆同前缀平铺文件，
而不是按职责分组：video-assemble 的 `scripts/` 有 30 个 .py，其中剪映导出一项就是 `export_jianying.py` 加
`jianying_builders / jianying_model / jianying_optional / jianying_schema / jianying_templates /
jianying_timeline_contract / jianying_tracks / jianying_writer` 共 9 个文件、约 1,900 行，与混音、字幕、绑定文件
平铺在同一目录里。读者要靠前缀猜边界；`assemble.py` 还以私有名 `_maybe_export_jianying` 跨模块 import。
架构测试（`tests/orchestrator/test_test_suite_architecture.py`）与隔离导入测试都只 `glob("*.py")`，
没有给子目录留位置。

## Decision

- `skills/<name>/scripts/` 下允许子包：一个功能族的实现放进 `scripts/<族名>/` 并带 `__init__.py`，
  包内用绝对导入（`from jianying.schema import us`），因为各 skill 的 `scripts/` 本身就在 `sys.path` 上；
  公开入口脚本（有 `__main__` 或被 SKILL.md 点名的）仍留在 `scripts/` 顶层，SKILL.md 与 references 只点名顶层脚本。
- 剪映导出是第一个子包：`export_jianying.py` 留在顶层作入口，其余 8 个 `jianying_*.py` 改为
  `scripts/jianying/{builders,model,optional,schema,templates,timeline_contract,tracks,writer}.py`；
  `jianying_optional._maybe_export_jianying` 改为公开名 `maybe_export_jianying`。
- 架构测试改为递归：`_script_modules()` 用 `rglob`；跨 skill import 检查、无环检查与 800 行预算都以
  "相对 scripts/ 的点号模块名"为节点，子包内模块与顶层模块同等对待；隔离导入测试同样递归导入子包模块。

## Alternatives considered

- **把 8 个 jianying 模块合并回 2–3 个 ≤800 行的文件** — 最强理由：不动测试框架。否：合并后每个文件仍要靠前缀
  区分，且 builders（729 行）+ writer（383 行）已经超过一份预算；问题是"没有分组"，不是"文件太多"。
- **提高 800 行预算** — 最强理由：一行改动。否：预算挡的是无边界膨胀，放宽只会让大文件回来。
- **把整个剪映导出拆成独立 skill** — 最强理由：它本来就是"按需适配"。否：它读 `timeline.json` 并与 assemble 的
  产物契约耦合，独立 skill 要再复制一份 lib 与契约代码；先分包，是否独立另议（见 Tier 3 讨论）。

## Consequences

- **收益**：video-assemble 顶层从 30 个文件降到 22 个；剪映导出的边界从目录结构上可见；私有名跨模块 import 少一处。
- **代价**：测试里 `from jianying_schema import …` 改为 `from jianying.schema import …`；子包模块不再出现在
  "顶层模块清单"里，依赖顶层入口把它们带进隔离导入测试。
- 后续可比照处理的候选：video-recap 的 `mimo_qc_*`（与 `mimo_qc.py` 入口同名，需先给包改名）、
  video-assemble 的 `subtitle_*`（被 SKILL.md 点名，需先确认入口）。

## Verification

`python3 scripts/test.py assemble orchestrator`：assemble 与改动前失败集合相同（仅本机 ffmpeg 缺 libass 的 13 个）；
orchestrator 除既有 26 个 libass 失败外全绿；`ruff check skills tests scripts` 无告警。
