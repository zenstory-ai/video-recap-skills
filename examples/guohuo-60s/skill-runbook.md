# 使用本仓库 Skills 复现《这一秒过火》案例

本操作手册供 Agent 调用仓库内的 Skills 重建本案例的选段、配音、混音与透明包装流程。动手前请先按[仓库 README](../../README.md) 配好环境变量与依赖，并将 `examples/guohuo-60s` 复制到独立的私有工作目录中操作，保持仓库内的公开基线文件原样不动。

## 一键复现提示词（Prompt）

先将 `assets.example.json` 复制为工作区外的私有 `assets.json`，填入你合法持有的分集视频与音效文件路径，然后把下面这段任务指令发给已加载本仓库 Skills 的 Agent：

```text
使用 video-recap Skill，把 assets.json 中 episode-02、episode-03、episode-06、episode-21
剪成约 59 秒的横屏剧情解说；交付规格是 1920×1080、25fps、BT.709 limited。

创作控制模式用 DIRECTED：examples/guohuo-60s 是已采用的内容基线，不重新发散故事方向。
先按标准 cut 两阶段流程生成理解产物、核心 cut、剪后旁白、配音和 video-assemble 母版；
background_research.json 放在项目 work_dir 下，合成时加 --no-burn-subtitles（字幕由 Remotion 透明层提供）。
Skill 母版沿用片源画布，1080p 与 BT.709 标记在项目级 conform 里完成。
生成的 source_id 与公开 example 不同时，根据 assets.json 的 episode 映射重写工作副本中的 source_id，
不要改原片时间码。三段原声窗口保持 1.0x，旁白先写连续思路，字幕再按实际 placed audio 拆 cue。

音画锁定后，再按 video-assemble 的“字幕与可选包装”流程使用 examples/guohuo-60s/remotion 做项目级透明包装；
字体不可用时选同类 fallback 并抽检，不把特定字体写成核心依赖。

最后进入 REVISION：读取 revision-log.json、edit-map.json 和 picture-conform.json。
只修复最终反馈点，恢复 42 秒后源镜头的连续运动；旁白、原声、音效、字幕时序、片名和花字冻结。
正常速度完整观看最终文件，逐接点播放，并只听声音复核；再做解码、规格、音频 stream-copy 和小于 10 MB 的交付检查。
```

## 分阶段执行细节

### 1. 多源视频理解（`video-understanding`）

由 `video-recap` 编排多集视频理解，依次生成每集的 ASR 转写、VLM 帧分析、切点、静音区间、融合片段、故事板拼图及固定 `source_id`。仓库仅公开 [`background_research.json`](background_research.json) 与脱敏后的 [`multi_source_manifest.json`](multi_source_manifest.json)；复现时必须对实际片源重新跑出理解数据，不能假定不同版本片源的片头时长或时间轴分毫不差。

只需把 `background_research.json` 放在项目根 `work_dir` 下，编排器就会在分析每一集前自动将其同步到 `sources/<source_id>/`。

注意：ASR 默认按 15 秒一窗（`ASR_SEGMENT_SECONDS`）输出粗粒度时间段，按窗内字数比例折算出的句末锚点可能与画面硬字幕存在几秒偏差，理解阶段会将其标记为 `unverified` 并给出误差范围。剪辑阶段把句末锚点和实测静音窗作为安全切点边界（仅含语气词或杂音的窗口不算说话区间）；凡依赖 `unverified` 锚点放行的切点都会记为 `unverified_sentence_boundary`，必须原速回看确认没有切碎台词。若某处边界仍被 `unsafe_clip_sentence_boundary` 拦下，请对照 `qc.boundary_status.sentence_checks` 给出的 `nearest_safe` 秒数挪动入出点，切勿强行绕过门禁。

### 2. 第一阶段选段（`video-script` Pass 1，`DIRECTED` 模式）

读取新生成的创作简报，同时将以下四份公开文件作为已确认的创作基线：

- [`recap_story_plan.json`](recap_story_plan.json)
- [`visual_audio_board.json`](visual_audio_board.json)
- [`style_card.json`](style_card.json)
- [`clip_plan.json`](clip_plan.json)

如果新生成的 `source_id` 命名与公开示例不同，按私有 `assets.json` 与新 `multi_source_manifest.json` 的映射关系替换工作副本中的 `source_id` 即可。除非你手头的片源版本与原版存在时间轴偏移，否则直接沿用基线里的原片时间码，无需重新发散第二套故事方案。

### 3. 执行初剪（`video-cut`）

运行剪辑阶段校验句界并生成 `clip_plan_validated.json` 与 `edited_source.mp4`。切点分数（scene-change score）仅用于提示可疑接点，Agent 仍需逐个播放核对。公开目录不附带运行时生成的 `clip_plan_validated.json`；目录内的 [`picture-conform.json`](picture-conform.json) 是终审返修阶段的局部微调记录，不要拿它直接覆盖 `video-cut` 的原生校验结果。

### 4. 二阶段写稿、配音与母版合成（`video-script` Pass 2 → `video-voiceover` → `video-assemble`）

对照剪后输出时间线放入 [`narration.json`](narration.json)，调用 `video-voiceover`（显式传 `--tts-provider fish-audio`）生成 7 段解说语音，再由 `video-assemble` 完成音频铺轨、原声闪避（ducking）与母版合成。由于本案例使用 Remotion 透明层渲染字幕，运行 `recap.py` 时需加上 `--no-burn-subtitles`，避免母版先烧进一层 ASS 字幕、外层又叠一套 Remotion 字幕（[`assembly_manifest.json`](assembly_manifest.json) 中记录的正是 `burn_subtitles: false`）。

本案例的 7 段解说词字数约为各自时间窗保守推荐字数的 `1.5–2.3` 倍。在 `cut` 模式第二阶段，`validate --mode cut_output` 只会将超字数记为 `over_budget` 警告而不会阻断；但如果复现时换用了语速较慢的音色，某段音频可能会塞不下时间窗：

- 默认模式下，配音阶段保留完整音频并记日志；若合成阶段在安全倍率内温和提速后仍装不下，会在编码前报 `no_safe_fit` 阻断，并指明超窗段号与所需的 `needed_tempo_factor`，此时只需精简该段词句或微调时间窗即可；
- 若显式传入 `--preserve-approved-text` 锁死原文，超窗段落会在配音阶段直接报错停机；
- 严禁靠硬切语音尾音或无上限倍速把长音频硬塞进窄窗口。

若复现时使用了不同音色或语速，请按新跑出的真实落盘音频重新生成字幕条，切勿直接拿公开的 [`captions.json`](captions.json) 硬套在新音频上冒充同步。

此外，公开历史产物中记录的 `ducking_narr_weight` 为 `2.4`，而当前版本 `video-assemble` 已将该权重固定为 `1.5`；如需完全复刻当时的压音深度，可在项目级后期混音中微调并重新听审。

### 5. 叠加项目级 Remotion 透明包装

拿到验收通过的音画锁定母版后，再进入 `remotion/` 制作透明包装层：

1. **同步字幕与总帧数**：在工作副本下运行 `python3 remotion/sync_overlay.py --srt <work_dir>/subtitles.srt --master <锁定母版.mp4>`。脚本会读取 `subtitles.srt`（已按实际落盘语音切好短句）重建 `remotion/src/captions.json`，并根据母版时长与 `remotion/src/overlay.json` 中的 `fps`（本例为 `25fps`）将 `durationInFrames` 更新为 `round(秒数 × fps)`。若任何字幕、片名窗口或花字超出母版总时长，脚本会列出越界项并以退出码 `1` 告警。如需同步更新外层目录的 `captions.json`，可追加 `--out captions.json`。
2. **看片校准片名与花字落点**：对照新母版画面，检查 `remotion/src/overlay.json` 中的片名显示窗口 `title.windows`（本例为 `0.18–9.75` 秒与 `42.2–50.3` 秒）以及 `flowerCues` 四条花字的起止秒数、文案与坐标。`sync_overlay.py` 只负责检查时间是否越界，花字具体卡在哪个眼神或动作上仍需人工看片拍板。
3. **抽帧检查版式**：在 `remotion/` 目录下执行 `npm install` 与 `npm run typecheck`，先用 `npx remotion still src/index.tsx RecapOverlay <png> --frame=<n>` 抽取片头、亮场、暗场、人物近景和最长字幕帧检查可读性。
4. **渲染透明序列并合入母版**：执行 `npm run render:overlay` 渲染出完整的透明 PNG 序列，再用 ffmpeg 将透明层叠加到音画锁定母版上，音频流直接 `-c:a copy` 无损复制。若源片分辨率、帧率或色彩空间尚不符合 `1920×1080`、`25fps`、`BT.709 limited` 的交付要求，可在叠加透明层的同一次 ffmpeg 命令中一并完成缩放、帧率对齐与色彩标记转换。

### 6. 终审返修（`REVISION`：恢复尾段连续运动）

对照 [`revision-log.json`](revision-log.json) 中的修改清单（`change_set`）、锁定清单（`frozen_set`）与验收要求（`verification`），在保持解说词、原声、音效、字幕时序、片名与花字全部冻结的前提下，按 [`edit-map.json`](edit-map.json) 与 [`picture-conform.json`](picture-conform.json) 修复 42 秒后的两处画面接点（去掉 `42.08` 秒处的尾帧定格、补回 `2001–2004` 秒被剪断的长廊连续跟拍）。

修完后按原速从头到尾通看最终成片，逐个回放接点，闭眼纯听一遍全片混音，最后对照 [`delivery-qc.json`](delivery-qc.json) 核验解码完整性、音画同步与小于 10 MB 的交付体积（若复现成片时长与原版不同，请按新时长重新折算目标视频码率）。
