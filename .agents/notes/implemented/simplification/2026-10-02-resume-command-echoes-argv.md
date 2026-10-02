# Agent Note: 续跑命令回显原始 argv

Status: implemented

## Problem

recap 在每个 Agent 暂停点（full 写稿、cut 两次暂停、多视频、dub）打印一条续跑命令。这条命令由 `recap_timeline._continuation_command` 手写重组：约 80 行，逐个 flag 判断"是否非默认值、是否叙述模式、是否由项目绑定"再拼回去。

审计（`.omc/plans/2026-10-02-skill-architecture-audit.md` boundaries B9，评审结论 sound）复核后：

- 每加一个 CLI flag 就要记得在这里补一行，漏写的 flag 在续跑时被悄悄丢掉，没有任何测试能发现"漏了哪个"——旧测试只枚举了当时已知的 flag。
- 相对路径原样写进命令：`--output-dir out`、`--material-library-dir lib` 在别的目录续跑会指向别处。`--project` 与 `--voice-ref` 因为 `main()` 先把它们解析成绝对路径才幸免。
- 有意不回传的 flag（`--doctor`、本地采用三件套）所在路径都不会暂停，回显原命令不会改变行为。

## Decision

- `recap_cli.parse_args` 解析成功后，用 `parser._actions` 把 argv 逐个 token 走一遍，得到 `args._argv`：保留用户原写法（`--flag value` 或 `--flag=value`），位置参数（视频）和路径类选项（`_PATH_DESTS`：`--work-dir`、`--output-dir`、`--voice-ref`、`--material-library-dir`、`--project`、`--tts-meta`、`--narration-adoption`、`--audio-mix-adoption`）的值按解析时的 cwd 转成绝对路径；空字符串值原样保留。
- `recap_runtime._resume_argv(work_dir, args)` 在 `args._argv` 之后补两类东西：用户没写 `--work-dir` 时补上实际使用的 work_dir；`main()` 可从环境变量取值、而用户没在命令行写出的设置（`_ENV_FILLED`：`edit_mode`、`target_duration`、`tts_provider`、`voice_ref`、`subtitle_y_top/bot`），值不等于无环境时的默认值、且不是 `--project` 绑定得来的，就补成显式 flag。续跑所在的 shell 不一定带着同样的环境变量，这与旧实现把这些值写进命令的行为一致。source 音频模式下 `tts_provider` 已被归一为 `auto`、`voice_ref` 为 `None`，所以不会把环境里的 TTS 配置提升为显式 flag。
- `_continuation_command(work_dir, args)` 只剩 `sys.executable` + `recap.py` + `_resume_argv` 的 shell 转义拼接；不再接收视频参数，4 处调用点同步修改。
- `recap_run_manifest.json`（单视频与多视频）新增 `argv`，内容就是 `_resume_argv` 的结果。manifest 的比对只看 `source_video*` / `sources` / `settings` / `audio`，`argv` 不参与续跑校验；续跑时 manifest 被重写，`argv` 随之更新。
- `tests/orchestrator/test_resume_command.py` 按 `parser._actions` 为每个选项生成一个非默认值（空格与 `=` 两种写法），在一个目录解析、生成续跑命令，换到另一个目录重新解析，要求所有设置相同；新加的 flag 自动被覆盖。原 `test_continuation_command_preserves_phase_b_flags` 的枚举用例删除。
- `--project` 写目录时，续跑命令里仍是该目录的绝对路径（以前是解析后的 `recap_project.json` 文件路径），两者指向同一个项目文件。项目绑定笔记 [[2026-09-27-project-binding-and-font-files]] 里的对应事实已就地更新。

## Alternatives considered

- **保留手写序列化，只补上相对路径的绝对化。** 最强理由：改动最小，续跑命令只含非默认值，看起来最短。没采用：漏 flag 的根本问题还在，每加一个 flag 都要改三处（parser、序列化、测试枚举）；手写逻辑本身就是 80 行重复 parser 知识的代码。
- **按 `parser._actions` 从解析后的 namespace 通用地重新序列化（只写非默认值）。** 最强理由：同样不会漏 flag，且输出规范化。没采用：`main()` 会就地改写 namespace（项目绑定、`VOICE_REF`、字幕坐标、source 模式归一），区分"用户写的"与"程序填的"仍需逐项特判；回显 argv 天然只含用户写的东西，项目绑定的值也不会被重复写出。
- **不补环境变量来源的设置，纯回显 argv。** 最强理由：最简单，审计原文只要求回显 argv。没采用：旧实现会把 `EDIT_MODE` / `TTS_PROVIDER` / `VOICE_REF` 等环境值写进命令；纯回显后在不带同样环境的 shell 续跑，`edit_mode` / `target_duration` 会被 manifest 比对拦下，而 `tts_provider` / `voice_ref` / 字幕坐标不在比对范围内，会悄悄换音色或字幕带。补上这 6 项保持原行为。

## Consequences

- 收益：`recap_timeline.py` 少约 75 行（621 → 548），新增 flag 不再需要同步续跑序列化；相对 `--output-dir` / `--material-library-dir` 不再在换目录续跑时失效；manifest 留下一条可直接重放的参数，为以后"不带视频的续跑"（agent-surface S7）打底。
- 代价：续跑命令的样子变了——保留用户原写法与 flag 顺序，`--work-dir` 和环境来源的 flag 追加在末尾，`--project` 写目录时不再展开成文件路径；解析依赖 argparse 的私有属性 `parser._actions`（`recap_cli._record_explicit_options` 已在用）。
- 是否在默认路径：是，每次暂停都会打印。谁读取产物：Agent 与用户读续跑命令；`recap_run_manifest.json.argv` 目前没有程序读取方。
