# 《这一秒过火》60 秒剧情解说 Demo

这是一个从多集长视频中提炼 58.96 秒横屏剧情解说的工程样例。目录按仓库真实的 cut 编排流程整理，不把最终包装误写成完整工作流：

```text
video-understanding
  → video-script（第一阶段：方案 + 原片时间 clip_plan）
  → video-cut（clip_plan_validated + edited_source）
  → video-script（第二阶段：剪后时间 narration）
  → video-voiceover
  → video-assemble（音画锁定母版 + timeline + assembly QC）
  → 包装方向探索（video-assemble 的可选 Remotion 路径）
  → REVISION：看片反馈 → conform / 再剪 → 冻结项复核
  → 最终交付 QC
```

公开产物清单见 [`workflow-manifest.json`](workflow-manifest.json)。这个 demo 沉淀以下可复用的创作与交付方法：

- 先用 `recap_story_plan.json` 锁定观众承诺、POV、戏剧问题和 change-based beats。
- 用 `visual_audio_board.json` 决定每拍由画面、原声、动作声还是旁白主导。
- TTS 以完整思路为块，字幕再按实际朗读时间拆成短 cue；两者不互相绑死。
- scene-change score 和抽帧只用于定位问题，最终以真实播放接点为准。
- 对短时间内密集的 scene 候选先区分原片切点和人工拼接点：无关原片镜头整段删，相关短镜头扩展到完整动作/反应，人工切点优先恢复同源连续运动或合并片段。
- 包装在画面、声音和字幕时序锁定后完成；花字只补情绪，不重复字幕信息。
- 修复不自然接点时优先恢复原片连续运动，而不是叠加溶解、闪白或其他遮掩。

复现时按 [`skill-runbook.md`](skill-runbook.md) 使用 `video-recap` Skill；有效迭代链见 [`iteration-notes.md`](iteration-notes.md)。

## 成片

成片通过 GitHub 附件 CDN 展示：

```html
<video src="https://github.com/user-attachments/assets/f3c2df0c-6869-4f5b-8f4c-cce70b58b667" width="720" controls></video>
```

压缩交付规格与校验结果见 [`delivery-qc.json`](delivery-qc.json)。

## 与 Skill 契约的对应关系

| 阶段 | 公开文件 | 在真实流程中的职责 |
|---|---|---|
| research / understanding | [`background_research.json`](background_research.json)、[`multi_source_manifest.json`](multi_source_manifest.json) | 给理解阶段人物/剧情上下文，并固定多个来源的稳定 `source_id`；逐源分析在复现时重新生成 |
| video-script pass 1 | [`recap_story_plan.json`](recap_story_plan.json)、[`visual_audio_board.json`](visual_audio_board.json)、[`style_card.json`](style_card.json)、[`clip_plan.json`](clip_plan.json) | 先定 POV、change-based beats、画面/声音分工，再按原片时间选择区间 |
| video-cut | 无运行时产物入库 | Skill 根据 `clip_plan.json` 生成新的 `clip_plan_validated.json` 与 `edited_source.mp4`；本案例后续做过项目级画面 conform，因此不把扩展过的项目文件冒充 Skill 原生输出 |
| video-script pass 2 | [`narration.json`](narration.json)、[`original_subtitles.json`](original_subtitles.json) | 对剪后成片写 7 个连续 TTS 块并保留采用的原声窗口；复现时由 Skill 根据新的 validated plan 重新生成句末锚点与校验报告 |
| video-voiceover | 无媒体产物入库 | 本例旁白来自 Fish Audio；复现时重新生成分段音频 |
| video-assemble | [`timeline.json`](timeline.json)、[`assembly_manifest.json`](assembly_manifest.json)、[`assembly_qc.json`](assembly_qc.json) | 展示多轨、ducking、逐段完整性、响度与 `assembly_qc.json` 的 `verdict` / `blocking_codes`；路径已替换为逻辑占位，并已删掉当前 video-assemble 不再写的字段（记录的数值未改） |
| 可选声音/包装 | [`sfx_mix_plan.json`](sfx_mix_plan.json)、[`captions.json`](captions.json)、[`remotion/`](remotion/) | 音画锁定后探索低频音效、片名、花字和字幕透明层 |
| REVISION / delivery | [`revision-log.json`](revision-log.json)、[`edit-map.json`](edit-map.json)、[`picture-conform.json`](picture-conform.json)、[`delivery-qc.json`](delivery-qc.json)、[`content-qc.md`](content-qc.md) | 把看片建议拆为修改项/冻结项，项目级 conform 后重新看片并做内容、压缩和交付复核 |

这些 JSON 保留复现所需的结构、时间、采用判断和发布 QC；媒体路径使用 `MEDIA_NOT_DISTRIBUTED/...`，`episode-02` 等名称是逻辑素材 ID。合法输入映射模板见 [`assets.example.json`](assets.example.json)。

## Remotion 包装

包装画布为 1920×1080、25fps、1474 帧。时长、片名文字与窗口、花字都在 `remotion/src/overlay.json`，字幕在 `remotion/src/captions.json`，TSX 只读这两份数据；复现时用 `remotion/sync_overlay.py` 从新运行的 `subtitles.srt` 和母版时长重建字幕与总帧数，再看片重放片名和花字（见 runbook）。透明背景包含三层：

1. **片名标识**：横排在顶部黑边，不侵入正片画面。
2. **花字**：低频出现，使用“旧情难藏 / 克制失守 / 本能不会说谎 / 重逢已迟”补充情绪，不复述字幕。
3. **字幕条**：暖灰白而非纯白底，固定在底部黑边；同一 TTS 块内换 cue 时底板不重复进场。

案例首选字体是 `Hannotate SC`（字幕）和 `Xingkai SC`（片名/花字），源码包含系统字体 fallback。不同平台字体指标会改变视觉结果，正式交付前应在目标机器抽检长字幕、亮背景、暗背景和人物近景。

Agent 按 [`skill-runbook.md`](skill-runbook.md) 走到 `video-assemble` 的可选包装阶段后，在该目录安装锁定依赖、渲染透明 PNG 序列，再由本项目自己把它叠到音画锁定母版（`video-assemble` 不提供逐帧前景合成命令）；音轨从母版 stream-copy，避免包装步骤意外改变音频。

## REVISION：最终接点修复

- **42.08 秒**：删除人工加入的 0.64 秒尾帧停留，恢复婚服镜头中的连续运动。
- **46.24 秒附近**：此前省略了原片 2001–2004 秒的同一运动镜头，形成明显的人工跳接；最终版恢复完整连续段并在旁白覆盖期统一提速。
- **51.52 秒后**：继承已验证的画面和音频尾段，保护原声恢复区的 AV 同步。

`revision-log.json` 记录每轮建议、修改项和冻结项，`edit-map.json` 说明看片确认的接点，`picture-conform.json` 描述最终接受的项目级再剪结果；复现时仍由 Skill 生成 `clip_plan` / `clip_plan_validated`。

这类问题不应沉淀成“某一秒特殊处理”的核心代码补丁。可复用规则是：检测工具负责指出候选边界，播放负责判定；同一源镜头被人工切断时，优先恢复源连续性；收到建议后只解冻相关层，再分别验证修改项和冻结项。

## 内容审核结论

截至 2026-08-24（北京时间），成片中的核心事实均有原片或公开资料支撑，未发现需要重做的硬性事实错误。两句口语化表达有明确解释边界：

- “前任变大嫂”严格名分是“准大嫂”，但剧中人物已经当面称“大嫂”。
- “男主全懂了”指他看懂她仍然在意自己，不表示此刻才第一次认出她的身份。

逐项证据与来源链接见 [`content-qc.md`](content-qc.md)。

## 版权与复现边界

本目录只展示结构化创作产物和原创包装代码。复现完整成片需要 Agent 使用本仓库 Skills 读取合法素材与声音资源，并把逻辑素材 ID 映射到实际文件；本仓库不授予原剧、演员声音、音乐、音效或字体的任何权利。复现步骤与输入要求见 [`skill-runbook.md`](skill-runbook.md)。
