# Agent Note: 资源库第 2 期——每次运行的资源记录 resource_lock.json

Status: implemented

## Problem

一条成片用了哪首 BGM、哪个音色、哪个字体，信息散在 `recap_run_manifest.json`、`tts_meta.json`、`assembly_manifest.json` 和
逐段缓存 sidecar 里；`tts_meta.json` 甚至只记 `engine`，不记音色。第 1 期（[[2026-09-27-resource-library-format]]）有了资源登记，
但没有任何产物把"这次运行"和"库里的哪条登记、授权状态如何"连起来。

## Decision

- video-voiceover 在 `tts_meta.json` 增加 `voice`：`{provider, model, voice_id, reference}`，由 `tts_settings_payload()` 的同一份
  设置推出（MiMo 预置音色、参考音频克隆、Fish reference id、index-tts 音色）。旧文件没有该字段时按只知道 `engine` 处理。
- video-recap 新增 `scripts/resource_lock.py`：full / cut（含本地采用路径）合成完成后、写 post_render QC 之前，从已有产物汇总
  `source_video` / `voice` / `bgm` / `subtitle_font` 写成 `work_dir/resource_lock.json`（`video-recap.resource-lock.v1`）。
  它只读产物，不改变任何渲染；dub 模式不写。
- 配置了资源库（`--material-library-dir` / `VIDEO_RECAP_MATERIAL_LIBRARY_DIR`）时，用 `library.scan_library()` 按解析后的文件路径
  对上登记，音色没有文件时按 provider + voice_id 对上；对上的条目带出 `license` 与 `consent`。
- `attention` 列出 `license_unknown` / `license_restricted`、参考音频的 `consent_*`、以及配置了库却未登记的 `unregistered`，
  运行结束时逐条打印；不写进 `final_qc.json`，因为它的格式只承载阻断项。
- 预留 `project` 与 `templates` 字段，由第 3 期的项目绑定填入。

## Alternatives considered

- **把授权提示作为 final_qc 的 advisory finding** — 最强理由：所有成片检查集中在一个报告里，dashboard 只读一份。
  否：`final_qc.json` 的契约只有阻断项（`blocker_count`、`--require-final-qc` 据此判定），混入非阻断项要改 QC 契约和门禁语义；
  独立文件更容易被 dashboard 和人直接读。
- **在每个阶段技能里各自写资源记录** — 最强理由：信息在产生处记录最准确。否：阶段技能不认识资源库，记录也会散成多份；
  由编排器在最后读取各阶段已写的事实，只汇总不重算。
- **按文件大小 + 修改时间在库里模糊匹配（不要求路径一致）** — 最强理由：用户把库里的文件复制到别处再用也能认出来。
  否：同大小同修改时间的不同文件会被误认；要求"用库里的那一份"更清楚，未登记时给出 `unregistered` 提示。

## Consequences

- **收益**：每条成片旁边都有一份清单回答"用了什么、授权清楚没有"；dashboard 与后续的项目绑定有了统一的落点。
- **代价**：`tts_meta.json` 多一个字段；配置了库时每次合成后多扫描一次库（只读 JSON，开销很小）；
  字幕字体目前只有名字，没有文件——第 3 期加入字体文件后才能对上 `font` 资源。

## Verification

`tests/orchestrator/test_resource_lock.py`（5 个）覆盖登记匹配、未登记提示仅在配置库时出现、参考音频声音授权提示，以及一次完整的
full 流程确实写出 `resource_lock.json`；`tests/voiceover/test_pure_voiceover.py` 的参数化用例覆盖四种音色来源的 `voice` 记录。
