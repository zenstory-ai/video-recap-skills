# 《这一秒过火》60 秒剧情解说案例

本目录展示如何从多集长视频里提炼出一条 58.96 秒的横屏剧情解说成片，完整串联起剪辑式解说（`cut` 模式）、可选包装与看片返修（`REVISION`）的制作闭环：

```text
video-recap（多源理解 understand.py）
  → 创作第一阶段（故事与声画方案 + 原片时间 clip_plan.json）
  → 初剪渲染（cut.py → clip_plan_validated.json + edited_source.mp4）
  → 创作第二阶段（对照剪后成片时间线写 narration.json 并过 validate.py）
  → 配音合成（voiceover.py）
  → 混音与母版渲染（assemble.py → 音画锁定母版 + timeline + assembly QC）
  → 项目级包装（可选 Remotion 透明前景层）
  → REVISION：看片反馈 → 画面微调 conform → 冻结项复核
  → 最终交付 QC
```

公开产物清单见 [`workflow-manifest.json`](workflow-manifest.json)。本案例重点示范以下实战做法：

- 先用 [`recap_story_plan.json`](recap_story_plan.json) 定死观众看点、主视角（POV）、核心悬念以及每一拍的局势翻转。
- 用 [`visual_audio_board.json`](visual_audio_board.json) 排好每一拍由画面动作、原片狠话还是解说旁白主导，按“垫半句、让半句、接半句”交接声音。
- 送 TTS 合成的文稿按一口气讲完的完整意思分段，屏幕上的字幕条再根据实际朗读时间拆成顺眼的短句，绝不为了迁就短字幕去把语音切得一惊一乍。
- 切点分数（scene-change score）和抽帧拼图只用来初筛可疑位置，接点顺不顺一律以原速播放看感为准。
- 遇到短时间内扎堆的切点候选，先区分是原片自带切镜还是本次拼接留下的切口：无关碎镜头整段删掉；有戏但太短的反应镜头回原片把起落扩完整；人工拼接切口则优先恢复同源连续运动或合并相邻片段。
- 画面、声音与字幕时序彻底锁定后再上外层包装；画面花字只点破人物潜台词，绝不重复底下正在滚动的字幕。
- 修补卡顿或跳接时直接回原片恢复连续动作，绝不拿叠化或闪白遮丑。

复现步骤见 [`skill-runbook.md`](skill-runbook.md)，多轮看片打磨的取舍记录见 [`iteration-notes.md`](iteration-notes.md)。

## 最终成片

成片通过 GitHub 附件 CDN 直接播放：

```html
<video src="https://github.com/user-attachments/assets/f3c2df0c-6869-4f5b-8f4c-cce70b58b667" width="720" controls></video>
```

交付成片的编码规格与质检读数见 [`delivery-qc.json`](delivery-qc.json)。

## 各阶段产物一览

| 阶段 | 公开文件 | 在制作流程中的作用 |
|---|---|---|
| 背景调研与多源理解 | [`background_research.json`](background_research.json)、[`multi_source_manifest.json`](multi_source_manifest.json) | 提前锁定人物化名与前情关系，固定多集片源的 `source_id`；单集 ASR/VLM 产物在复现时现场生成 |
| 创作第一阶段（Pass 1） | [`recap_story_plan.json`](recap_story_plan.json)、[`visual_audio_board.json`](visual_audio_board.json)、[`style_card.json`](style_card.json)、[`clip_plan.json`](clip_plan.json) | 定好二少主视角、十个叙事节点与声画让位方案，写出基于原片秒数的初剪选段 |
| 初剪渲染（`cut.py`） | 运行时生成，不入库 | 根据 `clip_plan.json` 吸附句界并渲染 `clip_plan_validated.json` 与 `edited_source.mp4`；本案例后期做过项目级画面微调，故单列 [`picture-conform.json`](picture-conform.json) 记录，不冒充原生输出 |
| 创作第二阶段（Pass 2） | [`narration.json`](narration.json)、[`original_subtitles.json`](original_subtitles.json) | 对照剪后成片时间线编写 7 段解说词，并标出三处原速保留的原声名场面 |
| 配音合成（`voiceover.py`） | 运行时生成，不入库 | 调用 Fish Audio 逐段合成解说语音 |
| 母版混音（`assemble.py`） | [`timeline.json`](timeline.json)、[`assembly_manifest.json`](assembly_manifest.json)、[`assembly_qc.json`](assembly_qc.json) | 记录多轨混音、原声闪避（ducking）、逐段语音完整性、响度实测值及质检结论（本地绝对路径已替换为逻辑占位符） |
| 音效点缀与画面包装 | [`sfx_mix_plan.json`](sfx_mix_plan.json)、[`captions.json`](captions.json)、[`remotion/`](remotion/) | 在音画锁定母版上叠加克制的转场重音、顶部片名、四处潜台词花字与底部字幕条 |
| 看片返修与交付质检 | [`revision-log.json`](revision-log.json)、[`edit-map.json`](edit-map.json)、[`picture-conform.json`](picture-conform.json)、[`delivery-qc.json`](delivery-qc.json)、[`content-qc.md`](content-qc.md) | 将看片反馈拆解为“本轮修改项”与“锁定不动项”，完成长廊镜头连续性修复与全片事实核查 |

所有公开 JSON 中的本地媒体路径均已脱敏为 `MEDIA_NOT_DISTRIBUTED/...`，`episode-02` 等为逻辑片源 ID；本地复现时的片源路径映射模板见 [`assets.example.json`](assets.example.json)。

## Remotion 透明前景包装

包装画布规格为 `1920×1080`、`25fps`、总计 `1474` 帧。成片时长、片名出现窗口与四处花字均配置在 [`remotion/src/overlay.json`](remotion/src/overlay.json) 中，字幕条数据存放在 [`remotion/src/captions.json`](remotion/src/captions.json)，TSX 组件本身只读数据、不写死任何时间或文案常量。复现时先运行 `remotion/sync_overlay.py`，从新生成的 `subtitles.srt` 与母版时长自动重建字幕条和总帧数，再对照画面校准花字落点。透明前景层包含三部分：

1. **顶部片名标识**：横排收在画面上方黑边内，不压正片人物头顶。
2. **潜台词花字**：全片仅在四个情绪拐点亮出“旧情难藏 / 克制失守 / 本能不会说谎 / 重逢已迟”，用来点破人物心口不一，绝不复述底下的字幕。
3. **底部字幕条**：采用柔和的暖灰白字色固定在底部黑边居中；同一段解说词内部拆行换条时，底板保持稳定不闪烁。

案例优先调用 `Hannotate SC`（字幕）与 `Xingkai SC`（片名/花字），并在代码中预留了系统字体回退链。由于不同操作系统的字体渲染度量存在差异，正式出片前务必在目标机器上抽帧检查片头、亮场、暗场、人物近景与全片最长字幕。

当 Agent 按 [`skill-runbook.md`](skill-runbook.md) 跑完 `assemble.py` 拿到音画锁定母版后，进入 `remotion/` 安装依赖并渲染透明 PNG 序列，再将透明层叠加到母版画面上，同时用 `-c:a copy` 原封不动复制已验收通过的母版音轨，确保包装环节不伤分毫声音。

## REVISION：成片接点精修

终审原速看片时揪出了两处影响镜头流畅度的接点，并在不改动任何声音与字幕时间线的前提下完成了局部修复：

- **42.08 秒处**：删掉原先人为定格的 `0.64` 秒尾帧，让皇冠婚服对视镜头的画面运动一气呵成走完。
- **46.24 秒附近**：初版把原片 `2001–2004` 秒同一条长廊跟拍镜头的中间一截剪掉了，导致画面明显跳接；终版把这 3 秒原片补回，恢复完整连贯的长廊奔跑镜头，并在旁白覆盖区间内统一配速。
- **51.52 秒之后**：直接沿用已验收母版的片尾画面与音轨，保住最后 7 秒原声收尾的声画同步。

[`revision-log.json`](revision-log.json) 完整记下了每一轮的修改项与锁定项，[`edit-map.json`](edit-map.json) 与 [`picture-conform.json`](picture-conform.json) 则记录了修复前后的帧区间对照。

## 事实核查结论

截至 2026-08-24（北京时间），成片中的人物身份、剧情先后顺序与原声引述均经原片画面、正片硬字幕及官方公开物料交叉核实，无任何捏造或错位。解说词中有两处口语化浓缩表达，边界说明如下：

- **“前任变大嫂”**：剧中此时的严格名分是“大哥的未婚妻（准大嫂）”，但慕容清峄在家宴上已当面改口称呼“大嫂”，解说词直接沿用了剧中极具冲击力的当面称谓。
- **“男主全懂了”**：指慕容清峄从她替自己挡击和递蜜饯安抚他的下意识举动里，彻底确认她心里仍然装着自己，并非指他到这一刻才第一次认出她的长相。

逐句证据对照表与出处链接见 [`content-qc.md`](content-qc.md)。

## 版权与复现说明

本目录仅公开结构化创作配置、质检记录与原创包装源码，不包含任何原剧视频、人声音频、配乐或商业字体文件。如需在本地复现成片，请自备合法授权的片源与声音资源，并参照 [`skill-runbook.md`](skill-runbook.md) 将逻辑素材 ID 映射到本地路径。
