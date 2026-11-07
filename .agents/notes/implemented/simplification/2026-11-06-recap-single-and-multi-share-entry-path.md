# Agent Note: recap 的单视频和多视频共用入口、source record 和 manifest 比对

Status: implemented

## Problem

编排器在单视频和多视频两条路上各写了一份相同的东西，CLI 又为一个集合绕了远路：

- `recap_cli` 在每个 parser action 上改写 `action.__class__`，换成运行时生成的子类，只为记下用了哪些 option。`_replayable_argv` 解析成功后本来就按同样的语义走一遍 token。
- `_single_source_record` 和 `_build_multi_source_records` 产出同样的字段；`_coerce_videos` 处理不可能出现的非 list 输入，并把 `main` 已经 resolve 过的路径再 resolve 三遍。
- `_execute_pipeline` 在两个分支里各写一遍 work_dir 解析和三道拒绝，`_run_multi_cut` 又做一遍；多视频的 stale 检查跑两次；三处暂停都手写续跑命令和 project 查找，`_run_single` 为此多包一层闭包。
- 两个 mismatch 函数的 settings 和 audio 比较逐字相同；两个 manifest 写入函数只差 payload；`source_videos` 只写不读，却让 `videos` 参数穿过四个函数。
- `main` 里的 `elif` 清空一个在 source-mix 下不可能被设置的 `voice_ref`。
- 一批只有一个调用方的包装和没人读的字段：`extend_assemble_args`、`_rebuild_understanding_brief`、`_cut_qc_summary_line`、`_probe_display_height_or_raise`、`_resolve_ref`、`restore_material` 返回的 `material` 和 `meta["material_dir"]`、`save_material` 的 `duration`、`CONFIG["mimo_api_key"]`，以及 doctor 里判断同一个值的两条 if/elif。

## Decision

- `_replayable_argv` 返回 `(argv, frozenset(explicit))`，`_explicit_options` 由它产出，argparse action 不再被改写。读取方仍是 `recap_source.validate_audio_routing` 和 `recap_runtime._resume_argv`。裸的 `-1` 这类位置参数在 walker 里抛 `KeyError` 是原有问题，这次不动。
- 单视频用 `_build_multi_source_records([video], args)[0]`。`main` 写 `[Path(v).resolve() for v in args.video]`，之后不再 coerce。`assign_source_ids` 给单个来源不加后缀，`material_id` 和 `settings` 的计算不变。
- `_execute_pipeline` 只解析一次 work_dir（默认目录名仍是 `work_dir_multi_<stem>` 和 `work_dir_<stem>`），三道拒绝在 mkdir 之前跑。`_run_multi_cut` 不再 mkdir，也不再重复拒绝。
- 多视频的 stale 检查合成一次，条件是“manifest 存在或 clip_plan 存在”，所以有 clip_plan、没 manifest 的目录仍然报“缺少 recap_run_manifest.json”。`_reject_stale_multi_manifest` 保留，两个测试把它当接缝用。
- `_pause_for_agent(work_dir, need_text, args, inspect_hint=None)` 自己算续跑命令和 project。
- `recap_timeline._settings_and_audio_mismatches` 是两个 mismatch 函数共用的尾部，比较的字段和报错文字不变。`recap_runtime._write_manifest_with_argv` 是共用的写入。多视频 manifest 不再写 `source_videos`。
- 上面那批包装和字段删除；`_rebuild_understanding_brief` 的理由改成调用处的注释。`_analysis_settings` 和续跑比较的字段都没动。`_narrate`、`_source_speech_rows`、`_voiceover_args`、`doctor._command_path` 是测试用的接缝，保留。

## Alternatives considered

- **把 `_build_multi_source_records` 改名为 `_build_source_records`。** 最强理由：单视频路径上读到 multi 这个词有误导。没采用：这次是行为不变的收拢，改名要动七处测试调用和一段嵌入的种子脚本，又不改任何行为；后面动这块时再改也不晚。
- **保留多视频的第二次 stale 检查。** 最强理由：两次检查之间写了 `multi_source_manifest.json`，看起来可能影响结果。没采用：`_multi_manifest_mismatches` 只读 run manifest，那次写入碰不到它；第二次检查唯一多出来的效果是报告缺 manifest，放宽第一次的条件就保住了。

## Consequences

- 收益：入口只有一条路，`action.__class__` 的改写消失，少一个 record 构造函数和一个 coerce 函数。
- 代价：有 clip_plan、没 manifest 或 manifest 过期的多视频目录，现在在重写 `multi_source_manifest.json` 之前就被拒绝，这比之前更严格。单视频的拒绝发生在 mkdir 之前，但拒绝需要的文件本身就说明目录已存在，实际不变。手动读 manifest 的人少了 `source_videos`，`sources[].source_path` 还在。旧 `material.json` 里的 `duration: null` 留着无害，新条目不再写。
- 是否在默认路径：是，默认路径的输出不变。对基线用桩子驱动 `recap_runner.main` 跑了 30 组流程（单视频 full/cut、多视频 cut、source-mix、默认目录名、stale、各种拒绝），子进程 argv、stdout、stderr、退出信息和 manifest 一致；差异只有 `source_videos` 和上面那次多视频拒绝。22 种 flag 写法（`--x=1`、`--x 1`、store_true、缩写拒绝、`--` 之后等）的 `_explicit_options`、`_argv` 和单视频 record 与基线逐项相同。
- 真实 API 端到端没有跑：MiMo key 无效。
