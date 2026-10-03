# Agent Note: 删除剪映 timeline-v2 手写扩展面，导出只接受 schema 2

Status: implemented

## Problem

[[2026-06-14-timeline-json-canonical-jianying-optional]] 让剪映导出成为可选 sidecar，同时在 `timeline.json` 上叠了一层"剪映专有附加字段"：恒定变速、倒放（含导出时用 ffmpeg 自动生成倒放素材）、opacity / rotation / flip、转场、蒙版、LUT（含肤色校正）、绿幕复合草稿、富文本 `style` / `words` / `style_presets`，以及 `sound` / `sticker` / `text_template` / `video_effect` / `face_effect` 资源轨和 `resource_packages` / `resource_config` 离线资源包（含 zip 解压与路径改写）。导出边界还会把 v1 时间线静默迁移到 v2。

这层东西没有任何生产者：`timeline_emit.py` 是唯一写 `timeline.json` 的地方，它只写视频片段（含原声音量自动化）、旁白 / BGM / prepared bed 音轨、只有 `text` 与起止时间的字幕轨，以及带 `scale` / `position` 的包装图层 image 段。扩展字段只能来自有人手写 `timeline.json` 再喂给 `export_jianying.py`。代价却是 video-assemble 里最大的一块实验性代码：剪映 builder、writer、契约约 950 行，三份只为绿幕复合草稿存在的协议模板约 390 行，测试约 800 行。v1 时间线只可能来自 0.3.x 的运行，`timeline.py` 从 v0.4.0 起就写 `SCHEMA_VERSION = 2`。

架构审计（`.omc/plans/2026-10-02-skill-architecture-audit.md` 剪映 v2 一列，约 520 行）把它列为删除候选，owner 批准；round-2 清单的 #31（v1 迁移）按核验意见并入本次。

## Decision

- `jianying/timeline_contract.normalize_timeline` 只接受 `schema_version == 2`。v1 报 `unsupported timeline schema_version 1; only 2 is supported`，错误里写明改成 2 即可（v2 只比 v1 多了 image 轨）。
- 契约只认 `video` / `audio` / `text` / `image` 四种轨道；其它轨道类型报 `unsupported track kind`。片段上出现 `REMOVED_ITEM_FIELDS`（speed、reverse、reverse_path、opacity、rotation_degrees、flip、transition、mask、lut、chroma、compound、green_background、style、style_id、words）或根上出现 `style_presets` / `resource_packages` 时报 `JianYing authoring extension is no longer supported`，不静默忽略，免得导出一份缺了效果却看不出来的草稿。`scale` / `position` 保留：包装图层就是靠它们在剪映里对齐 ffmpeg 渲染的位置。
- `jianying/builders.py` 删掉 speed 素材、转场 / 蒙版 / LUT 附件、绿幕复合草稿、资源轨和富文本样式；字幕素材固定用 duo 模板的默认样式（白色、8 号、UTF-16 区间），`clip_from_segment` 只映射 scale / position，alpha / flip / rotation 取模板默认值。输出与删除前默认路径逐字段相同（duo golden 测试未改断言即通过）。
- `jianying/writer.py` 只打包 `videos` / `audios` 里的媒体到 `Resources/local/{video,audio,image}`；删掉资源描述符、zip 解压、`target_path`、递归嵌套草稿和 `_bundle_resources`。`model.py` 的素材表只剩 audios / texts / videos，`tracks.py` 的布局带只剩 audio / video / image / subtitle / text，`templates.py` 和 `references/jianying/` 删掉 `empty_jy_draft.json`、`empty_jy_combination_segment.json`、`empty_jy_combination_video_material.json`。
- `export_jianying.py` 删掉倒放素材生成，`export_timeline_to_jianying` 直接 `build_draft` 后写盘。`timeline.build_timeline` 删掉 `resource_packages` / `style_presets` / `extra_tracks` 参数和各段的扩展字段透传，image 段只透传 scale / position。
- 测试：删掉只测扩展的用例（duo_protocol 的资源轨、转场、蒙版、LUT、绿幕、离线包、变速、倒放、富文本样式；contract_regressions 的倒放、v1 迁移、resource_config、复合草稿去指纹；test_timeline 的扩展透传）。保留默认路径的 duo 模板 golden、v2 契约结构测试（去掉 green_background 参数，保留 scale）、scale/position 映射测试，新增 `test_removed_authoring_extensions_are_rejected_not_ignored`，schema 测试改为 1 / 3 / 999 都被拒。`test_export_jianying.py` 里两个 `schema_version: 1` 的夹具改成 2（round-2 核验说没有这两处，是错的）。
- 文档：`docs/timeline-and-jianying.md` 的扩展字段段、能力矩阵和资源包契约换成"导出拒绝什么"的说明和一张四行映射表；`docs/capcut-jianying-draft-export.md`、assemble SKILL.md 的 timeline v2 扩展句和 `references/jianying/SOURCE.md` 同步改写。

## Alternatives considered

- **保留扩展面，只在 SKILL.md 里降级成一行指针（S5 的做法）。** 最强理由：零行为变化，有人手写 timeline.json 做转场 / 绿幕时仍能导出，还省一次 breaking 变更。没采用：代码和测试的维护量一行不少，而 agent 和流水线都不会产出这些字段；剪映里加转场、蒙版、贴纸本来就是几下点击的事，用 JSON 手写反而更难。
- **把剪映导出整体拆成独立的 video-jianying skill（B5）。** 最强理由：assemble 立刻少约 1,870 行，失败隔离更清楚。没采用：只是搬家，一行没删，还多出第 7 个 skill 和跨 harness 打包；评审结论是低价值。删扩展面才是真正减量，默认导出继续留在 assemble。
- **扩展字段改成静默忽略，不报错。** 最强理由：契约更短，旧的手写时间线还能导出点东西。没采用：带 `speed: 2` 的片段被忽略后，source 区间和 target 区间对不上，草稿会悄悄错位；明确报错只多十来行。
- **继续静默迁移 v1。** 最强理由：0.3 时代的 timeline.json 不用手改。没采用：迁移只是改版本号，报错信息里直接告诉用户改成 2 就行；保留它就得永远维护两个版本的说法。

## Consequences

- 收益：assemble 脚本净少约 810 行（builders 719→287、writer 383→208、契约 223→156），协议模板少约 390 行，测试净少约 790 行；剪映导出只剩流水线真正写出的四类轨道，读代码时不用再分辨哪些分支默认路径根本走不到。
- 代价：这是 breaking 变更。手写 timeline.json 用变速、倒放、透明度 / 旋转 / 翻转、转场、蒙版、LUT、绿幕、富文本或资源轨的调用者，导出会直接报错，需要删掉这些字段后在剪映里补做；`schema_version: 1` 的旧时间线要手动改成 2。`build_timeline` 的 `resource_packages` / `style_presets` / `extra_tracks` 参数没了，直接调用它的外部代码要改。
- 是否在默认路径：否。默认流水线的 `timeline.json` 和导出的草稿不变。删除信号：本篇即删除。agent 表面增量：SKILL.md 少一句扩展能力广告，多一句"只接受 v2、扩展字段会被拒"。

## Verification

- `ruff check .` 通过。
- `python3 scripts/test.py orchestrator assemble` 全绿（orchestrator 411 passed，assemble 544 passed）。
