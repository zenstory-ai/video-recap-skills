# Agent Note: 制作参考进资源库并经 `--project` 绑定到运行

Status: proposed

前置：[[2026-10-02-video-reference-skill]]（已落地）。本提案要等 recap 的 `scripts/resources/`、`scripts/dashboard/` 迁移合入 main 后
rebase 落地；落地时转入 `implemented/` 并与前置笔记互链。

## Problem

Part 1 只能手工把 `production_reference.json` 复制进下一次运行的 `work_dir`：没有版本，没有采纳记录，事后也查不到某次运行用的是哪个参考。
资源库（[[2026-09-27-resource-library-format]]）、项目绑定（[[2026-09-27-project-binding-and-font-files]]）与
`resource_lock.json`（[[2026-09-27-resource-lock]]）已经为字幕与包装模板解决了同样的问题。

## Proposal

- **库模板 kind `production_reference`**（`skills/video-recap/scripts/library.py`，约 +40 行）：
  - `TEMPLATE_KINDS += ("production_reference",)`；`params` 必须恰好是 `{"reference": {"path": "production_reference.json"}}`，
    路径用 `resolve_inside` 限定在版本目录内，文件不超过 2MB。
  - 新增独立的形状检查 `_check_production_reference`（不 import 参考技能的代码）：`schema` 必须是参考技能导出物的 production v1，
    `methods` 非空，每条 method 的 dimension 在五维枚举内、`rule` 非空；出现 `source_facts`、`labels`、`evidence`、`entities`、`statement`
    任一键即报错。`canvas` 仍必填，从导出物复制，仅作信息。
- **绑定**（`scripts/resources/project_binding.py`，约 +35 行）：
  - `BINDING_KINDS += "production_reference"`，走 `template()` 解析，只接受 adopted；以 role `production_reference` 记入 `used_templates`，
    `resource_lock.json` 因此自动记录 `id@vN`。
  - `check_canvas` 只检查 `GEOMETRY_ROLES = {"subtitle_style", "packaging"}`。
  - 把 `sync_packaging_layers` 抽成 `_sync_bound_file(work_dir, name, payload)`；新增 `sync_production_reference`：写入
    `work_dir/production_reference.json`，内容为导出物原样加 `written_by: "video-recap --project"` 与 `template: {id, version}`；
    绑定移除时删除自己写的副本；调用方自己放的文件（无 marker）不动；已有无 marker 且内容不同的文件时沿用现有冲突规则终止。
- `recap_runner.py` 在调用 `sync_packaging_layers` 的同一位置调用 sync（多源 runner 也加，+2 行）；`recap_timeline.py` 的暂停提示只在
  带 marker 的文件存在时多打印一行"本轮带制作参考 <id>@vN（可选，取舍写入 reference_methods）"（+3 行）。
- `references/resource-library.md` 加一节"参考模板 production_reference"：mkdir v1 → 复制导出物 → 写 `template.json` 与
  `samples/<id>/sample.json` → `library.py check` → 用用户原话写 adoption；绑定表加一行"production_reference → work_dir/production_reference.json，
  仅写稿 Agent 阅读"。库仍然只读，不加写库工具；完整 breakdown（含事实）不进库，人看的摘要放 sample.json 现有的
  `demonstrates` / `not_reusable`。
- 示例库加 `examples/resource-library/templates/production_reference/demo-pacing/v1/{template.json, production_reference.json}`，合成内容，
  状态 draft，能通过参考技能的 check。
- dashboard 不改：现有通用模板列表已能显示新 kind。
- 测试：
  - `test_resource_library.py`（参数化）：示例通过；指针越出版本目录、文件缺失、schema 错、methods 为空、维度非法、params 多键、
    文件带 `source_facts` 各自报错。
  - `test_project_binding.py`：adopted 可绑定且副本带 `written_by`/`template`、无事实键；draft 抛 `BindingError`；
    canvas 不一致时该角色不报错而 `subtitle_style` 照旧报错；移除绑定删除 recap 写的副本、保留调用方文件；无 marker 且内容不同触发冲突；
    `resource_lock` 列出该模板。
  - 守卫测试 `PRODUCTION_REFERENCE_WRITERS` 白名单加上 `video-recap/scripts/resources/project_binding.py`（改为按文件路径放行）。
- 真实验证：在临时库登记 Part 1 产出的庆余年参考并采纳，用 `recap.py 盘龙… --edit-mode cut --project P` 跑到第一个暂停点：
  work_dir 有带 marker 的文件且无事实键，暂停提示有那一行；不带 `--project` 时阶段行为与产物与原来一致，不多任何文件。

## Alternatives considered

- **方法直接内联进模板 `params`**：最强理由是少一个指针文件。不选：库的 provenance 枚举要扩出只有这个 kind 才用的值，
  还要和导出格式双份维护。
- **dashboard 加方法行**：最强理由是采纳前方便审阅。暂不做，等确有需求；通用列表已经够用。
- **采纳快照校验指针文件**：最强理由是采纳后被改能被发现。不选：约定改动就升 v<N+1>，保持最小。

## Consequences

- **收益**：参考可版本化、需采纳才能用、能追溯到具体运行。
- **代价**：recap 约 80 行代码，外加 1 个绑定 kind；recap 的 `library.py` 要单独维护一份导出物形状检查（不能 import 参考技能）。
- **默认路径**：不带 `--project` 或项目未绑定该 kind 时没有变化；不检查 canvas，不加门禁。
- **风险**：有人日后加"对照参考打分"。守卫测试的白名单让这种改动必须显式修改测试。
- **删除信号**：与前置笔记相同——连续两个版本没有运行写 `reference_methods`，也没有绑定。
