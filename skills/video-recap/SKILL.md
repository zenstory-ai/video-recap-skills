---
name: video-recap
description: 从输入视频生成中文解说成片或原声剧情短片。用户提供 .mp4 / .mov / .mkv / .webm，并要求剪辑、添加旁白、 配音、总结、短剧/电视剧/电影/纪录片/科普解说时使用。负责编排 video-* 技能链：视频理解 → Agent 制定故事与视听方案 → 剪辑 → 配音 → 合成。触发词：视频解说、视频旁白、生成解说、 视频 recap、video recap、voiceover、narration、recap。
---

## 1. 定位与流程

本技能是五个独立技能的轻量编排器。各技能只通过 `work_dir` 中的 JSON / MP4 产物通信，不共享代码：

```text
video-understanding ─▶ Agent 按 video-script 制定方案并写稿 ─▶ [video-cut] ─▶ video-voiceover ─▶ video-assemble
```

把成片拆成制作参考不是生产路径。用户要求时：对成片跑 video-understanding（建议 `ASR_SEGMENT_SECONDS=5`），
再用 video-reference 做 measure、标注、check、export，把导出的 `production_reference.json` 复制进下一次运行的
`work_dir`，或登记成资源库的 `production_reference` 模板、采纳后经 `--project` 绑定。recap 不会自动运行它，也不拿新成片与参考做比对。

流程支持断点续跑：写好 `narration.json` 后重复同一条命令即可继续。第二阶段会比对
`recap_run_manifest.json` 记录的源视频路径、文件大小/修改时间与运行参数，拒绝复用来自其他源视频或其他参数的旧工作目录；视频理解产物也只在来源一致时复用。
暂停时打印的续跑命令就是原命令：保留原来的写法，视频与路径参数转成绝对路径，补上 `--work-dir` 和来自环境变量的设置，从任何目录都能直接运行；同一份参数写在 manifest 的 `argv`。

画面流程 `--edit-mode full|cut` 与声音策略 `--audio-mode` 是两个独立开关；
`narration` 保留上述解说流程，`source-mix` 不做配音，只处理原声（可混 BGM）。
组合只有下面几条路径，其余组合在启动时直接报错：

| 输入 | `--edit-mode` | `--audio-mode` | Agent 暂停点 | 流程 | 详见 |
|---|---|---|---|---|---|
| 单视频 | full | narration | 1：`narration.json` | 理解 → 写稿 → 校验 → 配音 → 合成 | §4 |
| 单视频 | cut | narration | 2：`clip_plan.json`，再对着成片写 `narration.json` | 理解 → 剪辑 → 重建输出时间 brief → 写稿 → 配音 → 合成 | §4 |
| 多视频 | cut | narration | 2：同上，clip 必须带 `source_id` | 逐源理解 → 剪辑 → 写稿 → 配音 → 合成 | §4.3 |
| 单视频 | full | source-mix | 无 | 直接合成当前整段 | §4.7 |
| 单 / 多视频 | cut | source-mix | 1：`clip_plan.json` | 理解 → 剪辑 → 合成，不写稿 | §4.7 |

所有 full/cut 路径共用同一段收尾：（有旁白时）TTS → 合成 → 成片 QC。使用原声模式时读
`references/audio-routing.md`。

## 2. 创作职责

Agent 负责本次创作。先判断本轮的**创作控制模式**（CREATE / DIRECTED / REVISION，与 `--edit-mode` 分别选择），再在配音和渲染前完成五次判断：

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

TTS 供应商由 `--tts-provider mimo-tts|fish-audio|index-tts`（或 `TTS_PROVIDER`）透传给配音技能；Fish Audio 与自托管 index-tts 各自的环境变量、默认音色和能力限制见该技能。ASR/VLM 始终使用 MiMo。`scripts/doctor.py` 只做离线配置检查。

本技能自己不外发数据，外发都发生在它调用的阶段：发什么、发给谁、怎样关闭见 video-understanding 与 video-voiceover 技能的远程调用说明。`--edit-mode full --audio-mode source-mix` 不跑理解和配音，整条路径没有远程调用；其他路径都会跑视频理解。

`tp-*` Token Plan 密钥默认使用中国区集群，可用 `MIMO_TOKEN_PLAN_CLUSTER` 覆盖。

可覆盖配置见 `references/config-playbook.md`，`final_qc.json` 的字段见 `references/data-schema.md`。

下面的 `scripts/...` 均相对于本技能目录。若执行器从仓库根目录启动，请给脚本路径加上本技能的绝对目录。编排脚本从同一技能根目录定位所需阶段；安装时需让五个阶段技能与本技能位于同一层。

## 4. 标准解说流程（audio-mode narration）

### 4.1 背景调研

若能识别影片、剧集或主题，先按 video-understanding 技能的调研指南 `research-guide.md` 调研并写入
`work_dir/background_research.json`。视频理解会把人物名和剧情背景折入 VLM 上下文，避免只得到“黑衣男子”一类模糊描述。无法识别来源时可跳过。
多视频运行同样写在项目 `work_dir` 下：recap 在每个来源理解前把它复制到 `sources/<source_id>/`（来源目录里没有、或比项目文件旧时才复制，所以某一集需要单独的调研时，在项目文件之后写入该来源目录即可）。

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
`timeline_audio_mismatch` 时，应移动边界、缩短整句或删除该块，而不是增加抢断 override；
`unsafe_clip_sentence_boundary` 附带的 `nearest_safe` 给出前后最近的安全边界时间。

### 4.3 多视频与素材库

多视频只支持 cut 模式。项目 brief 会列出稳定的 `source_id`，`clip_plan.json` 中每个片段都必须填写来源：

```bash
python3 scripts/recap.py ep1.mp4 ep2.mp4 --edit-mode cut --target-duration 10m --work-dir work_dir_multi_ep
```

第二阶段与单视频一样生成剪后故事板 `storyboard/edited_storyboard.*`，取自各来源 `sources/<source_id>/frames/`，brief 顶部列出 `S1`/`S2`… 对应的 `source_id`；素材库恢复的来源没有抽帧，这些片段不出现在故事板里，用 `inspect clip-map` 核对。

可选文件系统素材库：

```bash
python3 scripts/recap.py ep1.mp4 --edit-mode cut --material-library-dir .video-materials --save-materials
python3 scripts/recap.py ep1.mp4 ep2.mp4 --edit-mode cut --material-library-dir .video-materials --use-materials
```

两条命令的分析参数必须相同才会复用（见 `references/data-schema.md`），不同就重新付费分析。素材检索只是对 JSON / MD / JSONL 做 grep，例如 `grep -R "keyword" .video-materials`。当前版本不复制原始媒体，也不提供数据库、向量或语义搜索。

同一根目录还可以登记可复用的资源（BGM、音效、音色、字体、图片）、带版本与采用记录的模板（字幕样式、包装图层、制作参考）和样片。
格式与 `scripts/library.py check|list|show` 只读工具见 `references/resource-library.md`。用 `--project recap_project.json` 把已采用的字幕样式、包装、制作参考、音色与 BGM 绑定到这次运行；
每次 full / cut 合成后 `work_dir/resource_lock.json` 记下实际用到的资源与授权状态。

### 4.4 继续生成成片

写好所需产物后，重跑暂停时打印的那条续跑命令（等同于保留全部原参数）。`--context`、`--edit-mode`、`--style`、`--target-duration` 等分析参数与第一次不同会被拒绝：

```bash
python3 scripts/recap.py <video> --work-dir <work_dir> <原有参数不变>  # 可追加 --no-burn-subtitles
```

流程会校验当前阶段的硬输入（`clip_plan.json` / `narration.json`）；两份创作计划仍是 Agent 的工作记录，不是渲染门禁。cut 模式随后生成 `edited_source.mp4`，再合成旁白并输出 `recap_<name>.mp4`。

校验从不改写解说稿（只更新实测的 `overlaps_speech`）；full 模式下文本装不下时间窗会以 `over_budget` error 退回给 Agent。TTS 也原样读稿，放不下由 assemble 有界提速，仍放不下就在渲染前以 `no_safe_fit` 阻断。

### 4.5 字幕与克隆旁白

若要把旁白字幕固定在原片字幕区域，先测量位置。完整源码安装可在仓库根目录运行以下工具；单独安装技能时，由 Agent 查看视频帧测出同一显示画布上的上下边界：

```bash
python3 tools/measure_subtitle.py <video>
```

再传入测得的 `--subtitle-y-top/--subtitle-y-bot`。坐标基于 ffmpeg 自动旋转后的显示画布，区间为半开 `[top, bot)`，并要求底对齐 ASS 样式；显式设置后，该区域默认使用不透明的旁白窗口遮罩；可用 `SUBTITLE_MASK_OPACITY` 调整。

解说模式如需克隆参考声音，使用 `--voice-ref <audio>`。

### 4.6 最终观看与交付复核

脚本、接点检测、样帧和 QC 报告都不能替代观看。每轮准备交付前，必须检查**本轮实际要交付的最终文件**，而不是旧别名、无字幕母版或中间代理：

1. 正常速度完整播放一次短片，不边看边改；先记录真实观看问题。
2. 播放每个拼接点前后约 0.5–1 秒，检查闪帧、原片叠化被截断、动作跳变和半句原声。
3. 完整只听声音一次，检查旁白是否碎成一句一停、场景间声音是否接得上、关键原声是否完整。
4. 单独复看开头、核心情绪/表演点和结尾，确认进入时机、回报停留和收束都成立。
5. REVISION 分别验证本轮修改项已经改变、冻结项没有意外变化；然后再做解码、时长、音画规格等机械检查。

scene score、亮度统计、抽帧拼图与自动 QC 只负责定位候选问题；最终判断以真实播放为准。密集切点的来源判断与处理规则按剪辑技能执行。修复失败时回到剪点、声音或文案层，不用更多包装掩盖。

full/cut 交付如需让确定性的最终检查影响命令退出状态，显式传
`--require-final-qc`。只有 `final_qc.json` 的摘要为 `ok: true` 且整数
`blocker_count: 0` 才打印完成并返回成功；缺失、畸形或 blocker
会保留报告和已渲染诊断媒体，但命令非零退出且不打印完成。默认仍是仅报告、不阻断。

交付时读 `final_qc.json` 的 `metadata.warnings`，有内容就原样告诉用户，不能只报“完成”。`partial_tts` 表示 `--allow-partial-tts` 下有旁白段配音失败，成片缺这些段，只适合预览。`subtitle_burn_degraded`
表示 ffmpeg 缺 libass、默认烧录被降级：新字幕保存在成片旁的同名 `.srt`，原片已有硬字幕仍保留。用户要烧录字幕，就请他装带 libass 的
ffmpeg 后重跑；显式传 `--burn-subtitles` 时缺 libass 会在开跑前报错。旁白里写了 `visual_overlays` 而 ffmpeg 缺
drawtext 时，流程在配音前停下，按报错删掉叠加或换 ffmpeg 后续跑。

### 4.7 不需要解说的片子

```bash
# 对当前整段输入直接合成；不隐式跑理解/ASR/TTS
python3 scripts/recap.py locked_picture.mp4 --work-dir source_work --audio-mode source-mix
# 剪辑计划仍按 cut 流程产生，剪完不再暂停等待 narration.json
python3 scripts/recap.py ep1.mp4 ep2.mp4 --edit-mode cut --work-dir cut_work --audio-mode source-mix
```

`source-mix` 仍会混音和重编码，只换包装时声音也多过一代 AAC，音频包不保持原样；需要音频逐包不变请使用 v0.6.2。切换声音模式须新工作目录，不得把旧 TTS、QC 或自动生成的解说花字混入本轮原声生产。细节见 `references/audio-routing.md`。

## 5. 自检与只读 dashboard

```bash
python3 scripts/doctor.py
```

### 5.1 只读 dashboard

```bash
python3 scripts/dashboard_server.py --root <目录> [--port 0] [--open]
```

前台运行并打印本机地址（只绑定 127.0.0.1，Agent 启动时放到后台）。它在 `--root` 下按 `library.json`、`recap_project.json`、
`recap_run_manifest.json` 发现资源库、项目与运行，按阶段显示剪辑节奏、旁白、成片与时间线、QC 和 `resource_lock.json`。
严格只读：只接受 GET / HEAD，不写任何文件；页面上的「复制给助手」只复制一句请求，改动回到对话里做。

## 6. 输出与参数

主要输出：

- `recap_<video>.mp4`：最终成片。
- `subtitles.srt` / `subtitles.ass`：字幕。
- `work_dir/`：全部中间产物。视频理解阶段产物的格式见 video-understanding 技能的 `data-schema.md`，其余见 `references/data-schema.md`。
- `work_dir/recap_story_plan.json` / `visual_audio_board.json`：Agent 创作意图与剪辑决定。

完整参数列表以 `python3 scripts/recap.py --help` 为准。`--style` 是原样传给 Agent 的自由文本指导，不是 preset、枚举、开关或有限风格分类。

## 7. 能力边界

- 确定性校验阶段负责时间、字数和声音归属的硬校验。旁白语义没有机器检查：是否忠于证据、有没有编情节，由 Agent 按 `video-script` 的自检清单核对，再看成片确认。
- 宣发标题、花字或外部文案回填交给 `video-script`，由它读取自己的宣发参考。
