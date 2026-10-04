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

把成片拆成制作参考不是生产路径。用户要求时：对成片跑 video-understanding（建议 `ASR_SEGMENT_SECONDS=5`），
再用 video-reference 做 measure、标注、check、export，把导出的 `production_reference.json` 复制进下一次运行的
`work_dir`，或登记成资源库的 `production_reference` 模板、采纳后经 `--project` 绑定。recap 不会自动运行它，也不拿新成片与参考做比对。

流程支持断点续跑：写好 `narration.json` 后重复同一条命令即可继续。第二阶段会比对
`recap_run_manifest.json` 记录的源视频路径、文件大小/修改时间与运行参数，拒绝复用来自其他源视频或其他参数的旧工作目录；视频理解产物也只在来源一致时复用。
暂停时打印的续跑命令就是原命令：保留原来的写法，视频与路径参数转成绝对路径，补上 `--work-dir` 和来自环境变量的设置，从任何目录都能直接运行；同一份参数写在 manifest 的 `argv`。

画面流程 `--edit-mode full|cut|dub` 与声音策略 `--audio-mode` 是两个独立开关；
`narration` 保留上述解说流程，`source-mix` 不做配音，`adopted-packet-copy` 冻结当前输入的已采用 AAC 音轨。
组合只有下面几条路径，其余组合在启动时直接报错：

| 输入 | `--edit-mode` | `--audio-mode` | Agent 暂停点 | 流程 | 详见 |
|---|---|---|---|---|---|
| 单视频 | full | narration | 1：`narration.json` | 理解 → 写稿 → 校验 → 配音 → 合成 | §4 |
| 单视频 | cut | narration | 2：`clip_plan.json`，再对着成片写 `narration.json` | 理解 → 剪辑 → 重建输出时间 brief → 写稿 → 配音 → 合成 | §4 |
| 多视频 | cut | narration | 2：同上，clip 必须带 `source_id` | 逐源理解 → 剪辑 → 写稿 → 配音 → 合成 | §4.3 |
| 单视频 | full | source-mix / adopted-packet-copy | 无 | 直接合成当前整段 | §4.7 |
| 单 / 多视频 | cut | source-mix / adopted-packet-copy | 1：`clip_plan.json` | 理解 → 剪辑 → 合成，不写稿 | §4.7 |
| 单视频 | dub | narration | 1：`dub_script.json` | 英文转写 → 译稿 → 克隆音色整轨替换 | §5 |
| 已剪好的母版 | full | narration + 三个采用 JSON | 无 | 只做严格合成 | 下文 |

所有 full/cut 路径共用同一段收尾：（有旁白时）评审 → TTS → 合成 → 成片 QC。使用原声模式时读
`references/audio-routing.md`。

已有预制画面和本地采用的完整声音三件套时，可走严格 assembly-only 路径：

```bash
python3 scripts/recap.py picture.mp4 --edit-mode full --work-dir NEW_WORK \
  --output-dir DELIVERY \
  --tts-meta tts_meta.json \
  --narration-adoption narration_adoption.json \
  --audio-mix-adoption audio_mix_adoption.json
```

三个 JSON 参数必须同时出现。该入口只接受单视频、full、narration、音轨 0、新工作目录和未存在的
交付文件；不运行理解、写稿、解说评审、TTS、cut 或剪映导出。语义与媒体形状仍由
video-assemble 严格验证，recap 只核对子技能绑定记录引用的是同一批采用文件与母版路径，不把调用方
采用的声音或混音声明成自动创作或发布批准。详见 `references/audio-routing.md`。

这里的单视频是**已经剪好的母版**。重剪后可以复用未改动的 WAV 与 `tts_meta.json`，但必须按新母版
重新写混音采用文件里的落点与准备好的音床；衔接步骤见 `references/audio-routing.md` 的 “Keep adopted voice after a cut”。

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

TTS 供应商由 `--tts-provider mimo-tts|fish-audio|index-tts`（或 `TTS_PROVIDER`）透传给配音技能；Fish Audio 与自托管 index-tts 各自的环境变量、默认音色和能力限制见该技能。ASR/VLM 始终使用 MiMo。`--doctor` 只做离线配置检查。

`tp-*` Token Plan 密钥默认使用中国区集群，可用 `MIMO_TOKEN_PLAN_CLUSTER` 覆盖。

可选能力：

- `--mimo-video-overview`：按场景块补充 MiMo 视频理解。

可覆盖配置见 `references/config-playbook.md`，`final_qc.json` 的字段见 `references/data-schema.md`。

下面的 `scripts/...` 均相对于本技能目录。若执行器从仓库根目录启动，请给脚本路径加上本技能的绝对目录。脚本启动后会自行定位兄弟技能和资源。

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
python3 scripts/recap.py ep1.mp4 --material-library-dir .video-materials --save-materials
python3 scripts/recap.py ep1.mp4 ep2.mp4 --edit-mode cut --material-library-dir .video-materials --use-materials
```

素材检索只是对 JSON / MD / JSONL 做 grep，例如 `grep -R "keyword" .video-materials`。当前版本不复制原始媒体，也不提供数据库、向量或语义搜索。

同一根目录还可以登记可复用的资源（BGM、音效、音色、字体、图片）、带版本与采用记录的模板（字幕样式、包装图层、制作参考）和样片。
格式与 `scripts/library.py check|list|show` 只读工具见 `references/resource-library.md`。用 `--project recap_project.json` 把已采用的字幕样式、包装、制作参考、音色与 BGM 绑定到这次运行；
每次 full / cut 合成后 `work_dir/resource_lock.json` 记下实际用到的资源与授权状态。

### 4.4 继续生成成片

写好所需产物后，重复同一条命令：

```bash
python3 scripts/recap.py <video> --work-dir <work_dir>  # 可追加 --edit-mode cut / --no-burn-subtitles
```

流程会校验当前阶段的硬输入（`clip_plan.json` / `narration.json`）；两份创作计划仍是 Agent 与建议型评审使用的工作记录，不是渲染门禁。cut 模式随后生成 `edited_source.mp4`，再合成旁白并输出 `recap_<name>.mp4`。

校验从不改写解说稿（只更新实测的 `overlaps_speech`）；full 模式下文本装不下时间窗会以 `over_budget` error 退回给 Agent。已有批准解说稿时加 `--preserve-approved-text`，TTS 也原样保留批准稿，装不下时间窗即失败，不缩稿、不降级为部分成功。

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
`--require-final-qc`。只有 `final_qc.json` 的摘要为 `ok: true` 且整数
`blocker_count: 0` 才打印完成并返回成功；缺失、畸形或 blocker
会保留报告和已渲染诊断媒体，但命令非零退出且不打印完成。默认仍是仅报告、不阻断。
该参数不支持 `--edit-mode dub`；dub 未传该参数时的准备和渲染行为不变。

交付时读 `final_qc.json` 的 `metadata.warnings`，有内容就原样告诉用户，不能只报“完成”。`subtitle_burn_degraded`
表示 ffmpeg 缺 libass、默认烧录被降级：成片里没有字幕，字幕在成片旁的同名 `.srt`。用户要烧录字幕，就请他装带 libass 的
ffmpeg 后重跑；显式传 `--burn-subtitles` 时缺 libass 会在开跑前报错。旁白里写了 `visual_overlays` 而 ffmpeg 缺
drawtext 时，流程在配音前停下，按报错删掉叠加或换 ffmpeg 后续跑。

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

`--edit-mode dub` 是实验功能：把英文视频翻译为中文，并用原说话者的克隆音色替换人声；它不是在压低原声上叠加解说。只在用户明确要求英译中配音时使用。

dub 会把源视频音轨分窗发给 MiMo ASR 转写，并把截取的约 10 秒原说话人声音作为参考，连同每句译文发给 MiMo voiceclone。因此必须带 `--confirm-voice-rights`：先向用户确认他有权使用这段视频与音频、且说话人同意被克隆声音，确认后才加这个参数；缺少时启动即报错，不抽音频、不发请求。该参数只用于 dub，其他模式传入会报错。

```bash
python3 scripts/recap.py <video> --edit-mode dub --confirm-voice-rights --work-dir <work_dir>
```

准备阶段会转写英文、提取一段参考音频，并写出 `dub_brief.md` 与 `dub_transcript.json`。Agent 按 `dub_brief.md` 里的翻译要求（逐句忠实、时间窗、语速）写 `dub_script.json`：

```json
[{"start": 0.0, "end": 2.0, "zh": "中文译文"}]
```

重复同一命令后先做确定性 lint（`dub_lint.json`，空行、重叠、越界即中止），再输出 `dub_<name>.mp4`。每句单独克隆并贴回原时间线；只有即将覆盖下一句时才局部加速。当前版本只支持单说话者、整轨替换，不分离背景音乐。

## 6. 自检与只读 dashboard

```bash
python3 scripts/recap.py --doctor
```

### 6.1 只读 dashboard

```bash
python3 scripts/dashboard_server.py --root <目录> [--port 0] [--open]
```

前台运行并打印本机地址（只绑定 127.0.0.1，Agent 启动时放到后台）。它在 `--root` 下按 `library.json`、`recap_project.json`、
`recap_run_manifest.json` 发现资源库、项目与运行，按阶段显示剪辑节奏、旁白、成片与时间线、QC 和 `resource_lock.json`。
严格只读：只接受 GET / HEAD，不写任何文件；页面上的「复制给助手」只复制一句请求，改动回到对话里做。

## 7. 输出与参数

主要输出：

- `recap_<video>.mp4`：最终成片。
- `subtitles.srt` / `subtitles.ass`：字幕。
- `work_dir/`：全部中间产物，契约见 `references/data-schema.md`。
- `work_dir/recap_story_plan.json` / `visual_audio_board.json`：Agent 创作意图与剪辑决定。

完整参数列表以 `python3 scripts/recap.py --help` 为准。`--style` 是原样传给 Agent 的自由文本指导，不是 preset、枚举、开关或有限风格分类。

## 8. 能力边界

- 语义评审默认建议型、失败开放；只有调用方显式启用严格解说评审时，事实矛盾、残句或评审不可用才会在 TTS 前阻断。确定性校验阶段始终负责硬校验。
- 宣发标题、花字或外部文案回填见 `video-script` 的 references/promotional-copy.md。
