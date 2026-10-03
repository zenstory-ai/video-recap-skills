# Agent Note: 删除独立的 pair_media / compose_foreground 命令，帧钟 helper 移入 adoption/av_clock.py

Status: implemented

## Problem

[[2026-09-21-adoption-family-stays-in-skill-as-subpackage]] 把 `pair_media.py`（把独立画面与已采用 AAC 音轨按流复制配对成 `paired.mp4`）和 `compose_foreground.py`（把调用方渲染好的 RGBA PNG 序列与可选片尾卡叠到锁定母版，音频逐包核对不变）作为顶层入口留在 video-assemble。两者都来自同事交付补丁的吸收（git 5923ced、6166bb6、be527e3），只能手写计划 JSON 后在命令行单独调用：编排器、recap、assemble 默认路径都不调用它们。代码约 510 行、参考文档约 190 行、测试约 700 行，其中 `test_compose_foreground` 与 `test_pair_media` 约占 assemble 测试组 30% 的耗时。`compose_foreground` 的"末尾恰好一张片尾卡"结构是单个项目的残留。

但 `pair_media.py` 不只是命令：`assemble.py` 在 `adopted-packet-copy` 路径上调用它的 `validate_aac_packet_interval`，`adoption/audio_mix_binding.py` 用它的 `probe_picture` / `validate_pair_timing` 做显式混音的输入与输出帧钟门禁，`test_movie_clock_boundaries.py` 也拿这两个函数作判据。直接删文件会让 `import assemble` 失败。

架构审计把它列为删除候选（E4），owner 批准；round-2 核验补充了上面的隐藏依赖和需要同步改写的文档。

## Decision

- 删除 `skills/video-assemble/scripts/pair_media.py`、`compose_foreground.py`、`references/pair-media.md`、`references/foreground-compose.md`，以及 `tests/assemble/test_pair_media.py`、`test_compose_foreground.py`。
- `probe_picture`、`validate_aac_packet_interval`、`validate_pair_timing` 及其 `_time` 原样移入新模块 `adoption/av_clock.py`（约 110 行），探测直接用 `adoption.strict_inputs.probe_json`。`assemble.py` 改为 `import adoption.av_clock as av_clock`，`audio_mix_binding.py` 改为 `from adoption.av_clock import probe_picture, validate_pair_timing`。三条以 "Pairing" 开头的报错改成不提配对的说法（`Picture clock requires one selected H264/HEVC picture stream`、`Picture clock currently requires an MP4-family container`、`Adopted audio must be AAC`），判定条件不变。
- `test_movie_clock_boundaries.py` 改从 `adoption.av_clock` 取判据，`test_millisecond_quantized_header_is_rejected` 的 `validate_pair_timing` 检查保留；`pair` 这个 operation 参数（两条用例）随命令删除，原参数化测试改名为 `test_frozen_aac_fractional_interval_survives_adopted_assemble`。
- 文档：video-assemble SKILL.md §7 改成"整片不动的包装用 `packaging_layers.json`，动画/透明层由项目级渲染器自己合成到锁定母版"，§8 删掉配对段；`references/packaging.md` 两处 `foreground-compose.md` 指针改为同样说法；`docs/architecture.md` 入口列去掉两个脚本；`docs/production-boundaries.md` 的"主题前景"改为"静态包装图层"；`examples/guohuo-60s/README.md` 与 `skill-runbook.md` 写明 Remotion 透明层由本项目自己合成，`remotion/` 保留；`subtitle-track.md` 示例路径 `/project/paired.mp4` 改为 `/project/master.mp4`。
- 未删任何其它 helper：`strict_inputs` 里 `require_declared_path` / `canonical_fraction` 等仍被 `source_score.py` 等使用，`frozen_audio` 的 `probe_audio_packets` / `verify_adopted_audio` 仍被 assemble 与 adoption 家族使用。

## Alternatives considered

- **只在 SKILL.md 里降级成一行指针，命令保留。** 最强理由：零破坏，偶尔需要"换画面但保留已采用混音"或"叠一层动画包装且证明音频没动"的调用方仍有现成工具。没采用：两条命令都没有编排器调用方，维护成本（约 1,400 行代码 + 文档 + 测试、assemble 测试组近三成耗时）一行不少；动画包装本来就由项目级渲染器（如 guohuo 的 Remotion）产出，合成一步交给同一个项目更直接。
- **整条删除 pair_media，包括 helper。** 最强理由：删得最干净。没采用：helper 是 `adopted-packet-copy` 与显式混音的生产门禁，删掉会让帧钟 / AAC 包区间检查消失，属于另一项需要 owner 单独决定的取舍。
- **helper 并进 `adoption/frozen_audio.py`。** 最强理由：少一个文件，frozen_audio 本来就处理 AAC 包。没采用：frozen_audio 只讲音频包身份，`probe_picture` 读画面帧钟、`validate_pair_timing` 比两条流的区间，放进去会让模块名说错内容；独立的 `av_clock.py` 名字直接说明用途。
- **删掉 `examples/guohuo-60s/remotion/`。** 最强理由：没有合成命令后，例子里的透明层不再能用本仓库工具叠到母版。没采用：README 把 Remotion 包装源码列为旗舰案例的一部分，`test_guohuo_example.py` 也断言它；把文案改成"项目自己合成"即可。

## Consequences

- 收益：video-assemble 脚本净少约 400 行（删 509、新增 av_clock 约 110），参考文档少约 190 行，测试少约 715 行，assemble 测试组少约 80 秒；顶层入口只剩 `assemble.py`、`export_jianying.py`、`source_score.py`。
- 代价：这是 breaking 变更。直接调用 `python3 scripts/pair_media.py` / `compose_foreground.py` 的调用方会找不到文件；想"换画面、保留已采用混音"的人需要自己用 ffmpeg 按流复制配对后再走 `--audio-mode adopted-packet-copy`，动画包装需要项目自己合成。`paired.mp4` / `pair_run.json` / `foreground_compose_run.json` 不再由本仓库产生。
- 是否在默认路径：否。默认流水线、`adopted-packet-copy` 与显式混音的行为和门禁不变，只有上面三条报错文字变了。
- 后续：`subtitle_track.json` 合约在仓库里唯一的编写说明就在被删的 `pair-media.md` 里，它的去留另立一项。

## Verification

- `ruff check .` 通过。
- `PATH=$HOME/.cache/video-recap-ffmpeg8:$PATH python3 scripts/test.py orchestrator assemble` 全绿（orchestrator 411 passed，assemble 475 passed）。
