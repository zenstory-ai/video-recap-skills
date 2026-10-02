# 用仓库 Skills 复现这个案例

本 runbook 供 Agent 使用仓库 Skills 复现案例的决策结构与效果。

## 可复制的任务请求

把 `assets.example.json` 复制为工作区外的私有 `assets.json`，只填写你有权使用的素材路径，然后把下面请求交给已安装本仓库 Skills 的 Agent：

```text
使用 video-recap Skill，把 assets.json 中 episode-02、episode-03、episode-06、episode-21
剪成约 59 秒、1920×1080、25fps、BT.709 limited 的横屏剧情解说。

创作控制模式用 DIRECTED：examples/guohuo-60s 是已采用的内容基线，不重新发散故事方向。
先按标准 cut 两阶段流程生成理解产物、核心 cut、剪后旁白、配音和 video-assemble 母版。
生成的 source_id 与公开 example 不同时，根据 assets.json 的 episode 映射重写工作副本中的 source_id，
不要改原片时间码。三段原声窗口保持 1.0x，旁白先写连续思路，字幕再按实际 placed audio 拆 cue。

音画锁定后，再按 video-assemble 的“字幕与可选包装”流程使用 examples/guohuo-60s/remotion 做项目级透明包装；
字体不可用时选同类 fallback 并抽检，不把特定字体写成核心依赖。

最后进入 REVISION：读取 revision-log.json、edit-map.json 和 picture-conform.json。
只修复最终反馈点，恢复 42 秒后源镜头的连续运动；旁白、原声、音效、字幕时序、片名和花字冻结。
正常速度完整观看最终文件，逐接点播放，并只听声音复核；再做解码、规格、音频 stream-copy 和小于 10 MB 的交付检查。
```

## Agent 应走的 Skill 阶段

### 1. `video-understanding`

由 `video-recap` 编排多源理解，生成每集的 ASR、VLM、scene、silence、fusion、contact sheet 与稳定 `source_id`。公开 example 只保留 `background_research.json` 和脱敏后的 `multi_source_manifest.json`；复现时必须使用新素材重新生成理解证据，不能把公开时间码当作对不同片源也成立。

### 2. `video-script` pass 1（DIRECTED）

读取新生成的 brief，同时把这些文件作为已采用基线：

- `recap_story_plan.json`
- `visual_audio_board.json`
- `style_card.json`
- `clip_plan.json`

如果新运行生成的 `source_id` 不同，Agent 根据私有 `assets.json` 与新 `multi_source_manifest.json` 做一一映射。只有素材版本或时间轴不同才重新定位 IN / OUT；不要为了满足 CREATE 模板虚构第二套故事假设。

### 3. `video-cut`

让 Skill 校验边界并生成新的 `clip_plan_validated.json` 和 `edited_source.mp4`。`scene-change score` 负责给候选，Agent 仍要真实播放每个接点。公开案例不分发项目运行时的 validated plan；`picture-conform.json` 是后续项目级修订记录，不能覆盖新运行的 Skill 校验结果。

### 4. `video-script` pass 2 → `video-voiceover` → `video-assemble`

按剪后输出时间线写 `narration.json`，再由 voiceover 使用 Fish Audio 生成旁白，由 assemble 放置、duck、生成字幕和母版。复现时显式选择 `--tts-provider fish-audio`；公开案例的 TTS 来源只标记为 Fish Audio，运行时元数据不入库。

复现的是内容和节奏契约，不是某个未分发声音的波形。若声音或语速不同：

- 保持每段的叙事任务和连续思路；
- 让 Skill 重新适配实际音频，不裁尾；
- 从新的 placed audio 重新生成字幕 cue；
- 不直接复用本例 `captions.json` 冒充同步。

`timeline.json`、`assembly_manifest.json`、`assembly_qc.json` 展示的是采用版应达到的轨道结构和门禁，不是让调用方伪造的结果。

### 5. `video-assemble` 的项目级 Remotion 包装

内容母版通过后才读取 `remotion/`：

1. 把新 placed audio 对齐得到的 cue 写入 Remotion `captions.json`。
2. 抽检开头、亮背景、暗背景、人物近景和最长字幕。
3. 渲染完整透明层，由本项目自己叠到锁定母版。
4. 包装合成时 stream-copy 已通过的音频；复核字幕边界、片名安全区和花字信息增量。

这是 `video-assemble` Skill 的可选包装路径，Remotion 是本案例的项目级实现；渲染与合成都在项目里完成，
`video-assemble` 只负责锁定母版，不提供逐帧前景合成命令。

### 6. REVISION：看片反馈后 conform / 再剪

读取 `revision-log.json` 的 `change_set`、`frozen_set` 和 `verification`。最终采用版只解冻画面连续性：

- `edit-map.json` 说明看片发现了什么。
- `picture-conform.json` 给出接受后的源时间和输出帧映射，方便 Agent 精确执行项目 revision。
- `delivery-qc.json` 给出最终交付的参考规格和质量，不替代新文件的实测 QC。

如果新素材、配音或切点改变了时间线，Agent 应保持修订意图，重新计算成片秒点；不要机械写死“42.08 秒 if”。这也是项目 example 与核心 Skill 规则的边界。

## 复现范围

在使用合法输入、保持片源时间轴一致并按目标环境重新校准声音与字体后，可以复现选段、叙事节奏、原声留白、字幕/片名/花字包装、后期 conform 和压缩结构。媒体与声音资源由复现者依法提供，仓库只负责 Skills 契约和公开创作产物。
