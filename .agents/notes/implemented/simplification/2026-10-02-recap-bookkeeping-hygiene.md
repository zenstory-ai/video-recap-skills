# Agent Note: recap 记账瘦身，cut 终轮先判过期再重剪

Status: implemented

## Problem

video-recap 编排器里积了一批写了没人读、或者绕远路的记账：

- `recap_phase.json` 除了 `clip_plan_identity` 还写 `edited_source_rendered`、`narration_written`、`multi_source`、`audio_mode`、`audio_stream_index`。唯一的读取方是 `_cut_narration_is_stale`，只看 `clip_plan_identity`；`recap_inspect` 只看文件在不在。不带解说的 cut 运行也写一份账本，可它不守任何东西：同一个 work_dir 换声音模式已经被 run manifest 的比对拒绝。
- 单视频 full / cut 的第一次暂停前，`_run_or_restore_understanding` 已经把同样内容写进 `recap_run_manifest.json`，`_run_single` 紧接着又写一遍。
- `restore_material` 不接收调用方手里现成的 `material_id`（`material_id_for` 对解析后的路径加大小是确定的，保存时就用它），而是用 `find_material_by_source` 扫描 `materials/*/material.json`。这个扫描要求路径和身份完全一致，所以只能用更慢的方式找到同一份素材。白名单里的 `reference_profile.json` / `reference_match_report.json` 从 #54 加进来起就没有生产者。
- 小重复：`RUN_MANIFEST` 定义了两次；`recap_inspect.load_json` 与 `lib.load_json` 逐字相同；`_record_resources` 自己再读一遍 `VIDEO_RECAP_MATERIAL_LIBRARY_DIR`；`final_qc._read_json_mapping` 与 `materials._read_material_metadata` 是同一个函数；`dashboard/runs.run_brief` 没有调用方。
- 文档与代码对不上：`data-schema.md` 提到不存在的 `--step script`；`config-playbook.md` 把写死在 CONFIG 里的 `NARRATION_COVERAGE_TARGET` / `NARRATION_BLOCK_SECONDS` 写成环境变量；`assembly_settings.py` 的 docstring 说这份 payload 会被续跑逻辑比较，实际只记录在 `assembly_manifest.json` 里；`test_narration_binding_seal.py` 设置了没人读的 `NARRATION_TAIL_PAD_SECONDS`。
- 一个顺序缺陷：cut 终轮（`narration.json` 已在）先调 `_render_cut` 跑 `cut.py`，再调 `_reject_stale_cut_narration`。`clip_plan.json` 在写稿后改过时，`cut.py` 会重新归一化、做镜头切换吸附、探测几何、重写 `clip_plan_validated.json`，计划变了还会重编码 `edited_source.mp4`，然后整个运行才以"稿已过期"退出。单视频和多视频两条路径都是这样。

## Decision

- `recap_timeline._write_phase_ledger(work_dir, clip_plan_identity)` 只写 `{"clip_plan_identity": ...}`。账本只在带解说的 cut 运行里写：第二次暂停前写一次，终轮通过过期检查后再写一次（覆盖手写稿、没有账本的情况）。不带解说的 cut 不再写账本。
- cut 终轮在 `_render_cut` 之前调用 `_reject_stale_cut_narration`，`_run_single` 与 `_run_multi_cut` 都是如此；过期的稿直接退出，`cut.py` 不运行。回归测试 `test_recap_cut_rejects_stale_narration_before_recutting` 与 `test_recap_multi_cut_rejects_stale_narration_before_recutting` 让任何子进程调用都直接失败。
- `_run_single` 去掉 full 和 cut 第一次暂停前那次重复的 `_write_run_manifest`。
- `restore_material` 的 `material_id` 改为必填，`_run_or_restore_understanding` 传入 `source_record["material_id"]`。删除 `find_material_by_source` 和它的测试，白名单去掉两个 `reference_*` 条目。
- `recap_runner` 从 `recap_runtime` 导入 `RUN_MANIFEST`，`_record_resources` 调用 `_material_library_dir(args)`，后者读 `library.LIBRARY_ENV`。`lib.read_json_object` 取代 `final_qc._read_json_mapping` 与 `materials._read_material_metadata`。`recap_inspect` 改为从本 skill 的 `lib` 导入 `load_json`，`tests/inspect` 相应改成把 scripts 目录放进 `sys.path` 后直接 `import recap_inspect`。删除 `run_brief`。
- 上面四处文档与测试的不实描述就地改正或删除。

## Alternatives considered

- **终轮在账本证明剪辑是当前版本时整个跳过 `cut.py`。** 最强理由：正常终轮里归一化、镜头切换吸附、几何探测和重写 `clip_plan_validated.json` 全是重复劳动，跳过能省下这段时间；写稿后修改的 cut 环境变量也不会再悄悄换掉 `edited_source.mp4`。没采用：它不是瘦身，要新增十几行跳过判断和测试；正常终轮的重编码已经由 `should_reuse_edited_source` 跳过；跳过 `cut.py` 还会跳过 `_surface_cut_qc`，并静默忽略终轮新给的 `--target-duration` / `--allow-duration-drift`。挪动检查顺序已经消除"先重剪再拒绝"的浪费。
- **保留 `find_material_by_source` 作为按路径查找的后备。** 最强理由：用户手工改过名的素材目录、或者别的工具用其他 id 存进来的素材，仍然能按源路径找回。没采用：库里只有 `save_material` 一个生产者，它总用 `material_id_for` 的结果作目录名；0.5.0 的旧素材用的是另一套指纹身份，扫描本来也匹配不上。为一个没有出现过的手工改名场景每次运行都 glob 整个库，不值得。
- **保留账本里的额外字段，方便排查。** 最强理由：`edited_source_rendered` / `narration_written` 能让人一眼看出 cut 停在哪一步。没采用：`recap_inspect state` 已经按产物是否存在报告下一个暂停点，账本里的这些布尔值没有任何代码读，也可能和磁盘实际状态不一致。

## Consequences

- **收益**：过期的稿不再先触发一次重剪才报错；`recap_phase.json` 的形状只剩一个键；素材恢复直接按 id 定位，不再扫描整个库；skills 与测试合计净减约 90 行。
- **代价**：手工改过名的素材目录不再能按源路径找到，要改回 `material_id_for` 给出的名字或重新沉淀。旧 work_dir 里多出来的账本字段在下一次写入时被丢掉；不带解说的 cut work_dir 里残留的旧账本不再更新，但也没有代码读它。
- 来源：round-2 瘦身候选 #17（recap-hygiene）、#36（stale-legacy-doc-refs + playbook-doc-lies），以及 cut 终轮检查顺序的修复。相关：[[2026-06-16-cut-first-narrate-second]]。
