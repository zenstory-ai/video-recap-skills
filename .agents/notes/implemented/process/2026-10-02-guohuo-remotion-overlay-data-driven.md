# Agent Note: guohuo-60s 的 Remotion 透明层改为读 overlay.json，并附重定时脚本

Status: implemented

## Problem

[[2026-10-02-guohuo-example-snapshots-follow-current-schema]] 在端到端复现中发现：Remotion 透明层的总帧数（`index.tsx` 的 `durationInFrames={1474}`）、四条花字（`flowerCues`）、片名首段与复现窗口（0.18–9.75 秒、42.2–50.3 秒）和片名文字都写死在 TSX 里，只换 `captions.json` 会得到偏短、错位的透明层。当时因为没有能跑无头 Chrome 的环境，只在 runbook 里写明要手改哪几个常量，否决了“改成 JSON props / `calculateMetadata`”。

runbook 还要求复现者自己从 `work_dir/subtitles.srt` 或 `_placed_*.wav` 切出 cue，仓库不提供任何工具，每次复现都要手写一遍同样的转换。

## Decision

- 新增 `examples/guohuo-60s/remotion/src/overlay.json`：`fps`、`width`、`height`、`durationInFrames`、`title {text, windows[]}`、`flowerCues[]`，数值与原 TSX 常量一致。
- `RecapOverlay` 改为接收 props `{captions, overlay}`；`index.tsx` 用两份 JSON 作 `defaultProps`，并用 `calculateMetadata` 从 props 取时长和画布，所以 `remotion render --props=<文件>` 传入的新数据连同时长一起生效。TSX 里没有时间或文字常量。
- 新增 `remotion/tsconfig.json` 与 devDependencies（`typescript` 5.9.3、`@types/react` 19.2.18），`npm run typecheck` 可做类型检查。
- 新增仅用标准库的 `remotion/sync_overlay.py`：从运行的 `subtitles.srt` 重建 `src/captions.json`（可用 `--out` 多写几份），按 `--master`（ffprobe）或 `--duration` 设 `durationInFrames`；字幕、片名窗口或花字越过母版结尾时列出来并以退出码 1 结束（文件照常写出）。它不挪动片名和花字：这些按画面决定。
- `skill-runbook.md` 第 5 节与样例 README 改写为“跑脚本 → 看片重放 overlay.json 的创作项 → typecheck / 抽帧 → 渲染”。
- 测试（orchestrator 组 `test_guohuo_example.py`）：`overlay.json` 的总帧数与 fps 对上 `delivery-qc.json`；TSX 里不出现总帧数、片名和花字文字；按采用版字幕生成的 SRT 经脚本还原出逐字节相同的 `captions.json` 与 `overlay.json`；更短的母版会被标出越界的片名窗口和花字。
- 验证：在本机 `npm install` 后 `npx tsc --noEmit` 通过；`remotion render` 渲染第 74–76 帧，片名、花字“旧情难藏”与字幕“还挽着亲哥”都在 3.0 秒处正确出现；用 `--props` 传入 50 帧的新配置时，第 46 帧显示新片名、新花字和新字幕，第 60 帧被 Remotion 以超出时长拒绝。验证后删除了 `node_modules`。

## Alternatives considered

- **维持现状，只在 runbook 写明要改的常量。** 最强理由：样例代码不动，就不会和当时的成片渲染有任何出入。没采用：复现者每次都要读懂 TSX 再改，漏改一处就是错位的透明层；现在已能实际渲染验证，原先否决的理由不再成立，而且数值原样搬进了 JSON。
- **由脚本从旁白块或场景切点自动推算片名和花字的位置。** 最强理由：复现完全不需要手工步骤。没采用：花字落在哪个镜头是创作判断，按旧时间比例平移会贴错画面；脚本只做能机械确定的事（字幕、总帧数）并检查越界。
- **把脚本做成 video-assemble 的命令。** 最强理由：所有用 Remotion 包装的项目都能用。没采用：Remotion 是这个样例的项目级实现，不是技能依赖；`overlay.json` 的形状只属于这个样例。

## Consequences

- 收益：复现时只改数据；字幕和总帧数一条命令生成，越界的创作项会被指出；类型检查和真实渲染都有现成命令。
- 代价：样例多了三个文件（`overlay.json`、`tsconfig.json`、`sync_overlay.py`）和两个 devDependencies；`sync_overlay.py` 依赖 assemble 写出的 `subtitles.srt` 形状（标准 SRT），如果改用 placed audio 的实测停顿切 cue，仍需项目自己处理；`--props` 方式渲染时需要自己保证 captions 与 overlay 来自同一次运行。
