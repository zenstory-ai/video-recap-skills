# Agent Note: 制作参考进资源库并经 `--project` 绑定到运行

Status: implemented

前置：[[2026-10-02-video-reference-skill]]（已落地）。资源库、项目绑定与运行记录见
[[2026-09-27-resource-library-format]]、[[2026-09-27-project-binding-and-font-files]]、[[2026-09-27-resource-lock]]。

## Problem

Part 1 只能手工把 `production_reference.json` 复制进下一次运行的 `work_dir`：没有版本，没有采纳记录，事后也查不到某次运行用的是哪个参考。
资源库、项目绑定与 `resource_lock.json` 已经为字幕与包装模板解决了同样的问题。

## Decision

- **库模板 kind `production_reference`**（`skills/video-recap/scripts/library.py`）：
  - `TEMPLATE_KINDS` 含 `production_reference`；`params` 必须恰好是 `{"reference": {"path": "production_reference.json"}}`，
    指针文件必须在版本目录内（符号链接指出版本目录按缺失处理），不超过 2MB。
  - `_check_production_reference` 是独立的形状检查，不 import 参考技能的代码：`schema` 必须是 `video-reference.production.v1`，
    `methods` 非空，每条 method 的 `dimension` 在五维枚举内、`rule` 非空；任何层级出现 `source_facts`、`labels`、`evidence`、
    `entities`、`statement`、`from`、`path` 键即报错（与参考技能导出复扫的 `BANNED_EXPORT_KEYS` 同一组，各自维护）。所有问题都记在 `template.json` 上，所以绑定的 `require_valid` 会拒绝指针文件坏掉的模板。
  - `canvas` 仍必填，从导出物复制，仅作信息。
- **绑定**（`skills/video-recap/scripts/resources/project_binding.py`）：
  - `BINDING_KINDS` 含 `production_reference`，走 `template()` 解析，只接受 adopted；以 role `production_reference` 记入
    `used_templates`，`resource_lock.json` 因此记录 `id@vN`。
  - `check_canvas` 只检查 `GEOMETRY_ROLES = {"subtitle_style", "packaging"}`；`_deliver` 只在绑定了这两种角色之一时才探测成片画布，
    只绑参考的运行不探测。
  - `sync_packaging_layers` 与新的 `sync_production_reference` 共用 `_sync_bound_file(work_dir, name, payload)`：写带
    `written_by: "video-recap --project"` 的副本（marker 写在 payload 之后，payload 里同名键盖不掉它），或在绑定移除时只删自己写的副本。参考副本是导出物原样加 `written_by` 与
    `template: {id, version}`。`work_dir` 已有不带 marker 且内容与绑定导出物不同的文件时抛 `BindingError` 终止；与导出物完全相同
    （手工复制的同一份）时原样保留、不加 marker，所以解绑后也不会被删。包装图层的写入行为不变。
- **runner**：`_run_single` 与 `_run_multi_cut` 一开始就调用 `sync_production_reference`，所以副本在第一次暂停前就位，续跑时随绑定
  更新或撤回。`recap_timeline._pause_for_agent` 在带 marker 的副本存在、或调用方的文件与当前绑定导出物相同时多打印一行
  "本轮带制作参考 <id>@vN（可选，取舍写入 reference_methods）"，文件名留在 `project_binding.bound_reference_note` 里。
- **文档与示例**：`references/resource-library.md` 加"参考模板 `production_reference`"一节（登记步骤）与绑定表一行；示例库加
  `templates/production_reference/demo-pacing/v1/`，`production_reference.json` 由参考技能对合成数据真实 export 得到，状态 draft。
  库仍然只读，登记与采纳都是手工编辑；含事实的完整 breakdown 不进库。
- **dashboard 不改**：通用模板列表已能显示新 kind，绑定解析沿用 `library.TEMPLATE_KINDS`。
- **守卫**：`test_only_the_reference_skill_and_its_binding_name_production_reference_in_scripts` 按文件路径放行
  `skills/video-reference/scripts/`、`skills/video-recap/scripts/library.py` 与 `skills/video-recap/scripts/resources/project_binding.py`。
  `library.py` 也在白名单里，因为指针文件名就是 `production_reference.json`，它只做形状检查、不打分。
- 测试：`test_resource_library.py` 参数化覆盖指针越出版本目录（`../` 与符号链接）、多余参数、文件缺失、schema 错、methods 为空、
  维度非法、顶层与嵌套的事实键；`test_project_binding.py` 覆盖 draft 拒绝、副本内容与 marker、无事实键、该角色不核对画布而字幕照旧核对、
  解绑撤回、调用方文件保留与冲突、第一次暂停前副本就位且提示行出现、`resource_lock` 列出该模板。

## Alternatives considered

- **方法直接内联进模板 `params`**：最强理由是少一个指针文件。不选：库的 provenance 枚举要扩出只有这个 kind 才用的值（导出物用
  `labeled`/`reviewed`），还要和导出格式双份维护。
- **dashboard 加方法行**：最强理由是采纳前方便审阅。暂不做，等确有需求；通用列表已经够用。
- **采纳快照校验指针文件**：最强理由是采纳后被改能被发现。不选：约定改动就升 v<N+1>，保持最小。
- **在 `_deliver` 里与包装图层同处写副本**：最强理由是只有一个写入点。不选：`_deliver` 在暂停之后才执行，写稿 Agent 在第一次暂停
  制定方案时就该看到参考。

## Consequences

- **收益**：参考可版本化、需采纳才能用、能追溯到具体运行。
- **代价**：recap 脚本净增约 95 行（含把包装图层写入抽成共用函数），外加 1 个绑定 kind；`library.py` 要单独维护一份导出物形状检查
  （不能 import 参考技能），导出格式改 schema 版本时两边都要改。
- **默认路径**：不带 `--project` 或项目未绑定该 kind 时，`sync_production_reference` 只会删除带 marker 的旧副本，否则什么都不做；
  不检查 canvas，不加门禁，没有脚本读取副本内容。
- **风险**：有人日后加"对照参考打分"。守卫测试的白名单让这种改动必须显式修改测试。
- **删除信号**：与前置笔记相同——连续两个版本没有运行写 `reference_methods`，也没有绑定。
