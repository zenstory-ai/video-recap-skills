---
name: video-recap
description: >
 从输入视频生成中文解说成片或原声剧情短片。用户提供 .mp4 / .mov / .mkv / .webm，并要求剪辑、添加旁白、
 配音、总结、短剧/电视剧/电影/纪录片/科普解说时使用。负责编排 video-* 技能链：视频理解 →
 Agent 制定故事与视听方案 → 剪辑 → 配音 → 合成。触发词：视频解说、视频旁白、生成解说、
 视频 recap、video recap、voiceover、narration、auto-dub、recap。
---

## 1. 定位与流程

本技能是五个独立技能的轻量编排器。各技能只通过 `work_dir` 中的 JSON / MP4 产物通信，不共享代码：

```text
video-understanding ─▶ Agent 按 video-script 制定方案并写稿 ─▶ [video-cut] ─▶ video-voiceover ─▶ video-assemble
```

流程支持断点续跑：写好 `narration.json` 后重复同一条命令即可继续。第二阶段会校验
`recap_run_manifest.json`，拒绝复用来自其他源视频或其他运行参数的旧工作目录；视频理解产物也只在来源一致时复用。

画面流程 `--edit-mode full|cut|dub` 与声音策略分开：`--audio-mode narration` 保留上述解说流程；
`source-mix` 不做配音；`adopted-packet-copy` 冻结当前输入的已采用 AAC 音轨。使用原声模式时读
`references/audio-routing.md`，不要为了运行工具而编造空解说。

已有预制画面和本地采用的完整声音三件套时，可走严格 assembly-only 路径：

```bash
python3 scripts/recap.py picture.mp4 --edit-mode full --work-dir NEW_WORK \
  --output-dir DELIVERY \
  --tts-meta tts_meta.json \
  --narration-adoption narration_adoption.json \
  --audio-mix-adoption audio_mix_adoption.json
```

三个 JSON 参数必须同时出现。该入口只接受单视频、full、narration、音轨 0、新工作目录和未存在的
交付文件；不运行理解、写稿、解说评审、TTS、cut、MiMo QC 或剪映导出。语义和媒体身份仍由
video-assemble 严格验证，recap 不把调用方采用的声音或混音声明成自动创作或发布批准。详见
`references/audio-routing.md`。

这里的单视频是**已经剪好的母版**。重剪后可以复用未改动的 WAV 与 `tts_meta.json`，但必须按新母版
重新绑定画面哈希与落点；衔接步骤见 `references/audio-routing.md` 的 “Keep adopted voice after a cut”。

## 2. 创作职责

这不是单纯的 JSON / 渲染流水线。Agent 是本次内容的创作负责人。先判断本轮的**创作控制模式**（CREATE / DIRECTED / REVISION，与 `--edit-mode` 无关），再在进入昂贵的下游处理前完成五次判断：

1. **导演判断**：观众承诺、POV、戏剧问题、情绪终点与揭示节奏。
2. **故事编辑**：beat 定义为“发生了什么变化”，不是场景摘要。
3. **画面剪辑**：选择真正值得保留的具体时刻、反应、入点与出点。
4. **声音/旁白**：先分配画面、原声、沉默和旁白的任务，再写解说词。
5. **观众复核**：分别检查无旁白、只听声音和第一次观看的体验。

三种控制模式的定义、REVISION 的修改/冻结规则、创作方法以及 `recap_story_plan.json` / `visual_audio_board.json` / `style_card.json` 的写法，全部按 `video-script` 执行；它会要求先读创作手册。这些文件只记录可审计的当前决定，不增加服务或渲染依赖。

## 3. 环境与脚本路径

```bash
# ffmpeg: brew install ffmpeg | apt install ffmpeg | choco install ffmpeg
export MIMO_API_KEY=***
```

同一个 MiMo key 驱动：

- ASR：`mimo-v2.5-asr`
- VLM：`mimo-v2.5`
- TTS：`mimo-v2.5-tts`

TTS 供应商由 `--tts-provider mimo-tts|fish-audio|index-tts`（或 `TTS_PROVIDER`）透传给配音技能；Fish Audio 与自托管 index-tts 各自的环境变量、默认音色和能力限制见该技能。ASR/VLM 始终使用 MiMo。`--doctor` 仅离线核配置，不证明服务可用或声线正确。

`tp-*` Token Plan 密钥默认使用中国区集群，可用 `MIMO_TOKEN_PLAN_CLUSTER` 覆盖。

可选能力：

- `--mimo-video-overview`：按场景块补充 MiMo 视频理解。
- `--mimo-qc pre-assemble|post-render|both`：在合成前、成片后或两个阶段给出建议型复核。

MiMo QC 默认关闭；每个选定阶段最多请求一次，写入 `mimo_qc.json`。任何凭证缺失、限流、超时、格式错误或采样失败都只记录状态，不阻断流程。可覆盖配置见 `references/config-playbook.md`，QC 报告的最小契约见 `references/shift-left-qc-schema.md`。

下面的 `scripts/...` 均相对于本技能目录。若执行器从仓库根目录启动，请给脚本路径加上本技能的绝对目录。脚本启动后会自行定位兄弟技能和资源。

## 4. 标准解说流程（audio-mode narration）

### 4.1 背景调研

若能识别影片、剧集或主题，先按本技能的 `references/research-guide.md` 调研并写入
`work_dir/background_research.json`。视频理解会把人物名和剧情背景折入 VLM 上下文，避免只得到“黑衣男子”一类模糊描述。无法识别来源时可跳过。

### 4.2 分析并暂停创作

```bash
python3 scripts/recap.py <video> --work-dir <work_dir> --context "背景"
```

命令完成视频理解、写出 `agent_narration_brief.md`，然后暂停。此时按以下顺序执行 `video-script`：

1. 查看创作 brief 与原片故事板。
2. 写 `recap_story_plan.json` 和 `visual_audio_board.json`。
3. full 模式写 `narration.json`；cut 模式第一阶段只写 `clip_plan.json`。
4. cut 模式第二阶段查看剪后故事板，补充输出时间与声音分工，再写 `narration.json`。

不要从标题或旁白句子开始；先锁定故事体验和素材选择。

时间线有两条不可降级的硬约束：原声只能在可靠句末/静音边界被切入、切出或恢复；旁白必须使用
完整逐段音频，任何 clip 映射裁段、TTS 裁尾或剪映引用更长的加速前素材都阻断。Agent 收到
`interrupts_source_sentence` / `unsafe_clip_sentence_boundary` / `no_safe_fit` /
`timeline_audio_mismatch` 时，应移动边界、缩短整句或删除该块，而不是增加抢断 override。

### 4.3 多视频与素材库

多视频只支持 cut 模式。项目 brief 会列出稳定的 `source_id`，`clip_plan.json` 中每个片段都必须填写来源：

```bash
python3 scripts/recap.py ep1.mp4 ep2.mp4 --edit-mode cut --target-duration 10m --work-dir work_dir_multi_ep
```

可选文件系统素材库：

```bash
python3 scripts/recap.py ep1.mp4 --material-library-dir .video-materials --save-materials
python3 scripts/recap.py ep1.mp4 ep2.mp4 --edit-mode cut --material-library-dir .video-materials --use-materials
```

素材检索只是对 JSON / MD / JSONL 做 grep，例如 `grep -R "keyword" .video-materials`。当前版本不复制原始媒体，也不提供数据库、向量或语义搜索。

### 4.4 继续生成成片

写好所需产物后，重复同一条命令：

```bash
python3 scripts/recap.py <video> --work-dir <work_dir>  # 可追加 --edit-mode cut / --no-burn-subtitles
```

流程会校验当前阶段的硬输入（`clip_plan.json` / `narration.json`）；两份创作计划仍是 Agent 与建议型评审使用的工作记录，不是渲染门禁。cut 模式随后生成 `edited_source.mp4`，再合成旁白并输出 `recap_<name>.mp4`。

若需要建议型 MiMo 复核：

```bash
python3 scripts/recap.py <video> --work-dir <work_dir> --mimo-qc both
```

合成前复核会读取脚本、计划和 TTS 元数据；成片后还会读取最多六张临时 JPEG。相同输入命中内容缓存，`--mimo-qc-refresh` 可强制刷新。帧的 base64 与凭证不会写入磁盘。

已有批准解说稿时使用 `--preserve-approved-text`：编排器会在 full、单视频 cut 和多视频 cut 的 TTS 前把保护参数交给真实校验器，保留段落顺序、数量、时间、文本、停顿和扩展元数据；形状、来源边界和时长错误仍会失败，字符预算只形成预警，不能静默缩稿或降级为部分成功。
这项策略只保护批准的时间线与文本；`overlaps_speech` 仍可依据已有声音证据更新，声音身份、后续 tempo、实际合成 WAV 是否装入时间窗及混音仍须单独核验。

### 4.5 字幕与克隆旁白

若要把旁白字幕固定在原片字幕区域，先在仓库根目录运行：

```bash
python3 tools/measure_subtitle.py <video>
```

再传入测得的 `--subtitle-y-top/--subtitle-y-bot`。坐标基于 ffmpeg 自动旋转后的显示画布，区间为半开 `[top, bot)`，并要求底对齐 ASS 样式；显式设置后，该区域默认使用 60% 透明度的旁白窗口遮罩。

解说模式如需克隆参考声音，使用 `--voice-ref <audio>`；它与 dub 模式不同。

### 4.6 最终观看与交付复核

脚本、接点检测、样帧和 QC 报告都不能替代观看。每轮准备交付前，必须检查**本轮实际要交付的最终文件**，而不是旧别名、无字幕母版或中间代理：

1. 正常速度完整播放一次短片，不边看边改；先记录真实观看问题。
2. 播放每个拼接点前后约 0.5–1 秒，检查闪帧、原片叠化被截断、动作跳变和半句原声。
3. 完整只听声音一次，检查旁白是否碎成一句一停、场景间声音是否接得上、关键原声是否完整。
4. 单独复看开头、核心情绪/表演点和结尾，确认进入时机、回报停留和收束都成立。
5. REVISION 分别验证本轮修改项已经改变、冻结项没有意外变化；然后再做解码、时长、音画规格等机械检查。

scene score、亮度统计、contact sheet 与自动 QC 只负责定位候选问题；最终判断以真实播放为准。密集切点的来源判断与处理规则按剪辑技能执行。修复失败时回到剪点、声音或文案层，不用更多包装掩盖。

full/cut 交付如需让确定性的最终检查影响命令退出状态，显式传
`--require-final-qc`。只有 `final_qc.json` 与 `golden_eval.json` 的摘要均为
`ok: true` 且整数 `blocker_count: 0` 才打印完成并返回成功；缺失、畸形或 blocker
会保留报告和已渲染诊断媒体，但命令非零退出且不打印完成。默认仍是仅报告、不阻断。
该参数不支持 `--edit-mode dub`；dub 未传该参数时的准备和渲染行为不变。

### 4.7 不需要解说的片子

```bash
# 对当前整段输入直接合成；不隐式跑理解/ASR/TTS
python3 scripts/recap.py locked_picture.mp4 --work-dir source_work --audio-mode source-mix
# 剪辑计划仍按 cut 流程产生，剪完不再暂停等待 narration.json
python3 scripts/recap.py ep1.mp4 ep2.mp4 --edit-mode cut --work-dir cut_work --audio-mode source-mix
# 只换包装时冻结当前整片 AAC；不允许同时加 BGM/TTS
python3 scripts/recap.py adopted.mp4 --work-dir packaging_work --audio-mode adopted-packet-copy
```

`source-mix` 仍会混音和重编码；`cut + adopted-packet-copy` 冻结的是剪后中间片的声音，不是原片的 AAC 包。
当前严格字幕轨只支持 adopted 模式；其他字幕来源没有因此变成精确对齐。切换声音模式须新工作目录，不得把旧 TTS、QC 或自动生成的解说花字混入本轮原声生产。细节见 `references/audio-routing.md`。

## 5. 英译中原声复刻模式

`--edit-mode dub` 把英文视频翻译为中文，并用原说话者的克隆音色替换人声；它不是在压低原声上叠加解说。

```bash
python3 scripts/recap.py <video> --edit-mode dub --work-dir <work_dir>
```

准备阶段会转写英文、提取一段参考音频，并写出 `dub_brief.md` 与 `dub_transcript.json`。Agent 随后写：

```json
[{"start": 0.0, "end": 2.0, "zh": "中文译文"}]
```

要求：

- 逐句忠实翻译，不删钩子、不合并、不擅自压缩；原文重复，译文也按时间重复。
- 每句沿用原声 `[start, end]`，相邻句不重叠。
- 译文尽量控制在约 5 字/秒，使其能在原时间窗内说完。

重复同一命令后输出 `dub_<name>.mp4`。每句单独克隆并贴回原时间线；只有即将覆盖下一句时才局部加速。当前版本只支持单说话者、整轨替换，不分离背景音乐。

## 6. 自检命令

```bash
python3 scripts/recap.py --doctor
```

## 7. 输出与参数

主要输出：

- `recap_<video>.mp4`：最终成片。
- `subtitles.srt` / `subtitles.ass`：字幕。
- `work_dir/`：全部中间产物，契约见 `references/data-schema.md`。
- `work_dir/recap_story_plan.json` / `visual_audio_board.json`：Agent 创作意图与剪辑决定。
- `work_dir/mimo_qc.json`：可选的建议型复核，不作为发布门禁。

完整参数列表以 `python3 scripts/recap.py --help` 为准。`--style` 是原样传给 Agent 的自由文本指导，不是 preset、枚举、开关或有限风格分类。

## 8. 能力边界

- 编排器不代替 Agent 写 `narration.json` / `clip_plan.json`；具体写作遵循创作 brief 与写作阶段契约。
- 语义评审默认建议型、失败开放；只有调用方显式启用严格解说评审时，事实矛盾、残句或评审不可用才会在 TTS 前阻断。确定性校验阶段始终负责硬校验。
- MiMo QC 不能阻断、自动修复或改变退出状态，只提供定位建议。
- 建立内容质量基线不依赖平台分析、留存遥测或发布接入。
- 本技能不改宣发标题、花字或外部文案回填；入口见 `video-script` 的 references/promotional-copy.md。
- 本技能不是无人值守调度器，不会向任何平台发布内容。
- 各阶段技能不共享代码，只通过 `work_dir` 产物通信。
