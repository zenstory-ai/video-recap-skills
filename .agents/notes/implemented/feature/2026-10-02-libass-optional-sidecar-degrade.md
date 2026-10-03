# Agent Note: 缺 libass 时默认烧录降级为外挂 .srt，drawtext 叠加在配音前预检

Status: implemented

## Problem

- Homebrew 自带的 `ffmpeg`（README 写的安装命令 `brew install ffmpeg`）去掉了 libass，也没有 drawtext。默认运行烧录字幕（`BURN_SUBTITLES` 默认真），recap 与 assemble 的预检都在缺 `subtitles` 滤镜时 `SystemExit`，所以 macOS 上的默认路径一律停在开跑前，用户只能自己去发现 `--no-burn-subtitles`。
- 加了 `--no-burn-subtitles` 也有坑：留白里的 `「」` 原声对白字幕在 `_original_gap_subtitle_entries` 里以 `burn_subtitles` 为闸门，关掉烧录后从 `.srt`、`timeline.json` 和剪映草稿里静默消失；`subtitles.srt` 只留在 work_dir，成片旁没有；没有任何机器可读的记录说明这次成片没有字幕。
- 旁白 `visual_overlays` 用 `drawtext` 渲染（需要 libfreetype），没有任何预检：缺 drawtext 时流程跑完理解、写稿、TTS，才在最后 `-vf drawtext` 失败。
- 遮罩策略里 `if not burn and active: trigger = "burn_subtitles_disabled"` 这一支走不到：opt_in / safe 分支先算 `active = burn and ...`，烧录关闭时 `active` 已经是假，trigger 停在策略自己的值上。
- 之前的调查与选项比较在主仓库 `.omc/release/libass-decision.json`（选项 B）。

## Decision

- 烧录分两种来源。显式：命令行传了 `--burn-subtitles`，或环境里有取真值的 `BURN_SUBTITLES`（assemble 用 `CONFIG["burn_subtitles_explicit"]`，recap 用 `recap_runtime._burn_subtitles_explicit`）。默认：两者都没有、靠默认值开启。
- 显式烧录而 ffmpeg 缺 `subtitles` 滤镜：recap 在理解前、assemble 在渲染前 `SystemExit`，和以前一样，报错里说明去掉显式要求即可得到 `.srt`。
- 默认烧录而缺 libass：recap 只打印警告继续跑，不改 `args.burn_subtitles`；assemble 的 `render_preflight._preflight_burn_subtitles` 把 `CONFIG["burn_subtitles"]` 置假、`CONFIG["burn_subtitles_degraded"] = "ffmpeg_missing_libass"`。降级只在 assemble 一处决定，recap 不转发 `--no-burn-subtitles`，两层不会各说各的。dub 仍然跳过烧录预检。
- 降级后交付的字幕内容与烧录运行相同：`_original_gap_subtitle_entries` 的闸门改为"烧录或降级"，`「」` 原声对白照常进入 `.srt`、`timeline.json` 和剪映草稿。闸门的另一半不变：遮罩覆盖留白，或 work_dir 里有 `user_subtitles.*`。遮罩仍然只在烧录时生效，所以降级时只剩用户字幕文件这一个触发条件，避免和原片自带的硬字幕叠两层。
- 只要不烧录（降级或显式关闭），assemble 把 `subtitles.srt` 复制到成片旁，名为 `recap_<stem>.srt`；烧录运行删掉同名旧文件，因为这对别名每次运行原地覆盖，旧的外挂字幕会被播放器叠到烧录字幕上。`subtitles.srt` 为空（没有任何字幕条目，如 source-mix 或 adopted-packet-copy 不带用户字幕）时同样删掉旧文件、不写空 `.srt`，`_subtitle_delivery_warnings` 也不记 `subtitle_burn_degraded`：有 libass 时这种运行烧的是空 ASS，画面上本来就什么都没有，降级没有丢任何东西。
- 降级是机器可读的、不阻断的：`visual_qc.json` 顶层 `warnings` 写一条 `{"code": "subtitle_burn_degraded", "reason", "delivered": "sidecar_srt", "mask_dropped", "message", "next_action"}`，`subtitles.burn_degraded_reason` 写原因；`assembly_manifest.json` 的 `warnings` 照抄并加 `subtitle_sidecar` 路径，`assembly_settings.subtitle_burn_degraded` 记原因；`final_qc.json` 的 `metadata.warnings` 转载 `visual_qc.json` 的 `warnings`，`final_qc.run` 的摘要多一个 `warnings` code 列表；recap 完成时 `_print_render_warnings` 打印警告与 `.srt` 路径。`findings` 仍然全是阻断项，`ok` / `blocker_count` 不受影响。video-recap SKILL.md 要求 Agent 交付时转告 `metadata.warnings`。
- 遮罩策略先按"假设烧录"算出是否会生效，再套烧录闸门：降级时 `mask.trigger = "burn_subtitles_degraded"`，显式关闭时 `burn_subtitles_disabled`。
- `visual_overlays` 叠加是写稿时明确加的内容，不是默认项，所以按"显式请求"处理，缺 drawtext 时失败而不是静默跳过：recap 在 `_deliver` 里、评审与 TTS 之前用 `recap_timeline._preflight_visual_overlays` 检查（narration 模式读 `narration.json` 经同一套 canonical 过滤后的叠加，source 模式读已有的 `visual_overlays.json`）；assemble 在渲染前用 `render_preflight._preflight_visual_overlays` 再查一次。报错写明删掉叠加或换带 drawtext 的 ffmpeg。
- `--doctor` 的 `system_tools` 新增 `subtitle_delivery`（`burned` / `sidecar_srt` / `fails_explicit_burn` / `unavailable`）、`ffmpeg_drawtext_filter`、`visual_overlays_ready`，warning 文案随之改写。

## Alternatives considered

- **保持缺 libass 即失败，只改文档指向 `ffmpeg-full`。** 最强理由：改动最小，输出语义不变；apt、gyan.dev、BtbN、conda-forge 的 ffmpeg 都带 libass，预检在任何 API 花费前就停。没采用：它让 owner 自己的开发平台和大多数新装 Homebrew 的用户默认路径不可用，`ffmpeg-full` 是 keg-only，本机那份还有 x265 dylib 问题；owner 已明确要发现问题就修。
- **把默认改成不烧录。** 最强理由：一行改动就让所有平台的默认路径脱离 libass，参考样片 guohuo 本来就是不烧录。没采用：有 libass 的外部用户会无声地失去画面字幕，而 skills 里没有任何替代的字幕渲染器，等于把核心交付推给 Remotion 一类框架，`packaging.md` 禁止这样做。
- **用 Pillow 渲染字幕 PNG 再叠加，彻底去掉 libass。** 最强理由：唯一能在 Homebrew ffmpeg 上保留零配置烧录的方案，原型渲染耗时只多约 9%。没采用：Pillow 会成为第一个第三方运行时依赖，CJK 字体发现和字形回退要另做，视觉与 libass 不一致，工作量是几天而不是一个发布周期；留给 0.7。
- **降级时也关掉 `「」` 原声对白（沿用"烧录才有"的闸门）。** 最强理由：不碰去重逻辑，绝不会和原片硬字幕叠两层。没采用：降级的意义是交付一份和烧录运行等价的字幕，丢掉原声对白等于降级还附带丢内容；而闸门的另一半（遮罩覆盖或用户字幕）保持不变，去重保护仍在。
- **降级时由 recap 转发 `--no-burn-subtitles`。** 最强理由：assemble 不需要知道"显式"与"默认"的区别。没采用：那样 assemble 看到的是显式关闭，记录不了降级，`「」` 对白也会被闸门挡掉；让 assemble 自己决定降级，记录和行为只有一个来源。
- **缺 drawtext 时跳过叠加并警告（与字幕降级同样处理）。** 最强理由：默认流程在 Homebrew 上也能跑完。没采用：字幕降级只是换了交付形式，内容还在 `.srt` 里；叠加跳过就是内容丢失，而且叠加只会来自写稿时的明确选择，按 R2 的规则属于显式请求，应当失败并说明怎么改。

## Consequences

- **收益**：Homebrew ffmpeg 上的默认运行能完整跑完并交付成片加 `.srt`；降级在 `visual_qc.json`、`assembly_manifest.json`、`final_qc.json` 和完成提示里都看得到，Agent 不会把没字幕的成片报成普通成功；`--no-burn-subtitles` 的成片旁也有字幕文件；带叠加的运行在花 TTS 钱之前就知道 ffmpeg 不够用；遮罩 trigger 如实记录烧录被关的原因。
- **代价**：同一条命令在有无 libass 的机器上产出不同（烧录 vs 外挂）；依赖"缺 libass 必然非零退出"的脚本不再得到退出码，除非显式传 `--burn-subtitles` 或设 `BURN_SUBTITLES`。shell profile 里设了 `BURN_SUBTITLES=1` 的环境在缺 libass 时每次都会失败（这是显式请求，按设计如此）。成片旁会多出 `recap_<stem>.srt`，烧录运行会删掉这个名字的文件。缺 drawtext 时写了叠加的运行仍然要人处理，不会自动完成。
- 不在本次范围：`subtitle_overflow` 视觉 QC 与 `track_binding` 的 ASS 10ms 检查在不烧录时仍然生效（与烧录运行一致，降级不会让它们更糟）；MiMo QC 已删除，无需传烧录状态。
- 相关：[[2026-10-02-delivery-yuv420p-and-color-tags]]（降级让流复制路径变常见，yuv420p 保证在那篇）、[[2026-10-02-doctor-drop-capability-menu]]（doctor warning 文案已同步）。
