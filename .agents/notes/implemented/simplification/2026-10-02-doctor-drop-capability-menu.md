# Agent Note: doctor 去掉能力清单，dashboard 去掉 --host

Status: implemented

## Problem

- `doctor.py` 的 `_build_capability_menu`（约 200 行，#45 加入）把 `checks` 里的同一批布尔值再整理成第三种视图：`ready` / `blocked` / `warnings/degraded` / `optional_upgrades` 四组，`recap.py --doctor --json` 输出 `capability_menu` 键，人读输出再打一段 `[capability menu]`。除了 doctor 自己的打印和测试，没有 SKILL.md、参考文档、dashboard 或编排代码读它。
- 它又不完全是重复：选中的 TTS 供应商（mimo-tts / fish-audio）没配密钥、MiMo VLM 没配，只出现在菜单的 `blocked` 里，JSON 的 `failures` / `warnings` 都不提（`failures` 只覆盖 index-tts）。Agent 按 SKILL.md 读 `--doctor` 时只看 `ok` / `failures` / `warnings`，这两条信息实际上看不到。
- `dashboard_server.py --host` 经 `_loopback_host` 只接受 IPv4 回环，所以只能在 127.0.0.0/8 的别名之间选；`main()` 却总打印 `http://127.0.0.1:<端口>/`，传 `--host 127.0.0.2` 时打印的地址是错的。SKILL.md 与 `resource-library.md` 都没写这个参数，唯一的测试是拒绝 `0.0.0.0`。

## Decision

- 删除 `_capability`、`_build_capability_menu`、`DEGRADED_GROUP` 和 `[capability menu]` 打印段。`build_report` 只返回 `ok`、`repo_root`、`checks`、`failures`、`warnings`；`checks` 与 `failures` 不变，`ok` 的判定不变。
- 菜单里独有的两条信息改为 `warnings`：
  - `mimo_video_configured` 为假时：`MiMo VLM not configured: set MIMO_VIDEO_API_KEY or MIMO_API_KEY before video understanding`。
  - 实际选中的 TTS 供应商是 mimo-tts 或 fish-audio 且未配置时：`TTS provider <名> not configured: set <变量> before voiceover`，变量提示来自 `TTS_KEY_HINTS`。index-tts 仍按原来的硬失败检查。
- 降级提示并进已有 warning：缺 libass 时说明默认运行改为交付外挂 `.srt`、显式 `--burn-subtitles` 会失败（文案随 [[2026-10-02-libass-optional-sidecar-degrade]] 更新）；ASR 那条本来就写 `--skip-asr`。
- `dashboard_server` 删除 `--host` 与 `_loopback_host`，`DashboardServer` 固定绑定 `127.0.0.1`，`make_server(root, port=0)`；Host 白名单只剩 `127.0.0.1:<端口>` 与 `localhost:<端口>`。传 `--host` 即报 argparse 错误。测试改为断言服务端绑定 127.0.0.1。

## Alternatives considered

- **保留能力清单，只补两条 warning。** 最强理由：分组的"能跑什么 / 卡在哪 / 怎么降级"对人读更直观，#45 加它就是为了这一点。没采用：分组里的每一项都能从状态行和 failures / warnings 读出来，维持两份视图要让每个新检查改两处、测两处，而且已经出现过一份视图有、另一份没有的漂移（上面两条信息）。把信息放回 Agent 真正读的 `warnings`，比维护一份没人读的 JSON 键更可靠。
- **TTS / VLM 未配置算 failure。** 最强理由：没有 TTS 就配不了音，默认流程跑不完。没采用：菜单原来也只把它们放在 `blocked` 而不影响 `ok`，改成 failure 会改变 `--doctor` 的退出码，属于行为收紧而不是瘦身；最常见的根因"没有 MIMO_API_KEY"本来就是 failure。是否收紧应单独决定。
- **保留 `--host`，修正打印的地址。** 最强理由：改一行就能修 bug，不破坏任何调用。没采用：它能选的只有回环别名，没有文档，也没有已知使用者；保留它就得继续维护 `_loopback_host` 与 Host 白名单里的第三个条目。

## Consequences

- **收益**：doctor 少约 210 行，测试少约 40 行；mimo-tts / fish-audio 与 VLM 未配置第一次出现在 JSON 的 `warnings` 里；dashboard 不会再打印错误地址，绑定地址只有一处。
- **代价**：`recap.py --doctor --json` 不再有 `capability_menu` 键，人读输出不再有分组摘要，JianYing 导出这类"可选升级"提示不再出现；读这个键的外部脚本会拿到 KeyError。`dashboard_server.py --host` 不再被接受。
- 来源：round-2 瘦身候选 #7（doctor-drop-capability-menu，按复核更正补两条 warning）与 #33（dashboard-host-flag）。相关：[[2026-09-27-read-only-dashboard]]。
