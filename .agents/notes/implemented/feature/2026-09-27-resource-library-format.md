# Agent Note: 资源库第 1 期——记录格式与只读校验工具

Status: implemented

## Problem

总体方案见 [[2026-09-27-resource-template-library-and-dashboard]]。在绑定、渲染和 dashboard 读取资源库之前，
必须先有稳定的记录格式与一个能说清"这条记录能不能用、为什么"的校验器；否则后续每一期都要各自猜字段、各自处理坏文件。

## Decision

- 资源库与素材库共用根目录（`--material-library-dir` / `VIDEO_RECAP_MATERIAL_LIBRARY_DIR`），新增
  `library.json`、`resources/<kind>/<id>/resource.json`、`templates/<kind>/<id>/v<version>/template.json`、`samples/<id>/sample.json`
  四种记录，schema 名为 `video-recap.{library,resource,template,sample}.v1`，格式写在 `skills/video-recap/references/resource-library.md`。
- 资源 `kind` ∈ `bgm / sfx / voice / font / image`；模板 `kind` ∈ `subtitle_style / packaging`；
  `license.status` ∈ `unknown / owned / licensed / restricted`；参考音频的 `consent.status` ∈ `unknown / granted / denied`；
  模板 `status` ∈ `draft / adopted / retired`，`adopted` 必须带 `adoption.{date, by, statement, scope}`；
  参数 `provenance` ∈ `measured / fitted / specified / unknown`。
- `skills/video-recap/scripts/library.py` 提供 `check / list / show`，只读：`check` 有错误时退出码为 1，`--json` 输出
  `{ok, errors, warnings, counts}`。错误 = 条目不能用（字段缺失或越界、文件缺失、引用断开、路径逃出库根目录）；
  警告 = 能用但要人看（授权或声音授权未确认、样片不在本机、采用后资源已变化）。
- 顶层字段严格，未知字段即报错；路径相对记录目录，解析后必须在库根目录内，符号链接按解析后的真实位置判断；样片可用绝对路径。
- 文件身份沿用 `{size, mtime_ns}`（[[2026-09-20-no-content-hashing]]）。模板采用时可选记录 `adoption.resources` 快照，之后变化只给警告。
- 仓库只带合成示例库 `examples/resource-library/`：正弦波 BGM / 音效、透明 PNG 包框、1 秒纯色样片、900x1600 非交付画布；
  音色示例的授权状态刻意留为 `unknown`，演示警告。
- 渲染路径本期不读取资源库；绑定、`resource_lock.json` 与 dashboard 在后续各期。

## Alternatives considered

- **同时提供 `add` / `adopt` 写入命令** — 最强理由：用户不用手写 JSON，也不会漏填身份快照。否：采用必须带用户原话和范围，
  由 Agent 在对话里确认后写 JSON 更合适；先把只读校验做稳，写入命令等真实使用暴露出重复劳动后再加。
- **宽松解析，忽略未知字段** — 最强理由：将来加字段时旧工具不报错。否：手写 JSON 最常见的错误是字段拼错，
  静默忽略会让"以为设置了授权状态"变成事实上的 `unknown`；加字段时同步升级工具与 schema。
- **把样片也限制在库内** — 最强理由：路径规则只有一条，库可整体拷走。否：成片动辄几百 MB，常放在另一块盘；
  绝对路径不在本机时只给 `sample_offline` 警告。

## Consequences

- **收益**：资源、模板、样片有了可 grep、可提交、可校验的统一格式；授权、声音授权、采用记录第一次有了明确字段；
  后续绑定与 dashboard 可以直接复用 `scan_library()` 的结果和错误码。
- **代价**：新增约 390 行工具代码与 23 个测试；用户要手写 JSON；`{size, mtime_ns}` 是本机事实，库拷到另一台机器后
  所有采用快照都会显示"已变化"——这正是方案笔记里重新考虑内容哈希的信号之一。

## Verification

`PYTHON=<py3.12> scripts/test.sh orchestrator`：345 passed（新增 `tests/orchestrator/test_resource_library.py` 23 个）；
变异验证：去掉库根目录越界检查后，`path_escapes_library` 与符号链接两个用例失败。`ruff check skills tests` 无告警；
`python3 skills/video-recap/scripts/library.py --library-dir examples/resource-library check` 为 0 error / 1 warning。
