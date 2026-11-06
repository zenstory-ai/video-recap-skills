---
name: video-recap
description: 从输入视频生成中文解说成片或原声剧情短片。用户提供 .mp4 / .mov / .mkv / .webm，并要求剪辑、添加旁白、配音、总结或制作短剧/电视剧/电影/纪录片/科普解说时使用。负责编排 video-* 技能链：视频理解 → Agent 制定故事与声画方案 → 剪辑 → 配音 → 合成。触发词：视频解说、视频旁白、生成解说、视频 recap、video recap、voiceover、narration、auto-dub、recap。
---

## 1. 定位与流程

本技能是五个独立生产技能的轻量编排器。各技能只通过 `work_dir` 下的 JSON 与 MP4 文件交接数据，彼此不共享代码：

```text
video-understanding ─▶ Agent 按 video-script 制定方案并写稿 ─▶ [video-cut] ─▶ video-voiceover ─▶ video-assemble
```

把现有成片拆解为制作参考（`production_reference.json`）不在默认生产链上。当用户要求拆片学习时：先对样片跑 `video-understanding`（建议设 `ASR_SEGMENT_SECONDS=5`），再用 `video-reference` 依次执行测量（`measure`）、标注、校验（`check`）与导出（`export`），将导出的 `production_reference.json` 复制进下一轮运行的 `work_dir`，或登记为资源库里的 `production_reference` 模板并经 `--project` 绑定。编排器不会自动跑拆片，也不拿新成片去跟参考片做机械比对。

全流程支持断点续跑：Agent 写完 `clip_plan.json` 或 `narration.json` 后，重跑同一条命令即可往下走。续跑时脚本会核对 `recap_run_manifest.json` 中记录的源视频路径、文件大小、修改时间及运行参数，一旦发现工作目录来自其他视频或不同参数组合会直接拒绝复用；视频理解缓存也只在源片与配置完全一致时命中。每次暂停时终端打印的续跑命令保留了原始调用写法，并自动把视频与路径参数转为绝对路径、补齐 `--work-dir` 及来自环境变量的显式配置，复制粘贴到任意目录都能直接跑（同一组参数也会记入清单的 `argv`）。

画面流程 `--edit-mode full|cut|dub` 与声音策略 `--audio-mode` 相互独立：`narration` 走标准解说配音链，`source-mix` 不生成配音而直接混入原声与配乐，`adopted-packet-copy` 则原封不动拷贝输入视频已定稿的 AAC 音轨。系统仅支持下表列出的合法组合，其余组合在启动时直接报错拦截：

| 输入 | `--edit-mode` | `--audio-mode` | Agent 暂停点 | 流程 | 详见 |
|---|---|---|---|---|---|
| 单视频 | full | narration | 1 次：写 `narration.json` | 理解 → 写稿 → 校验 → 配音 → 合成 | §4 |
| 单视频 | cut | narration | 2 次：先写 `clip_plan.json`，剪完再对成片写 `narration.json` | 理解 → 剪辑 → 重出输出时间简报 → 写稿 → 配音 → 合成 | §4 |
| 多视频 | cut | narration | 2 次：同上，且每条片段必须带 `source_id` | 逐源理解 → 剪辑 → 写稿 → 配音 → 合成 | §4.3 |
| 单视频 | full | source-mix / adopted-packet-copy | 无暂停 | 直接合成当前整段视频 | §4.7 |
| 单 / 多视频 | cut | source-mix / adopted-packet-copy | 1 次：写 `clip_plan.json` | 理解 → 剪辑 → 合成（不写旁白） | §4.7 |
| 单视频 | dub | narration | 1 次：写 `dub_script.json` | 英文转写 → 写中文译稿 → 克隆原声整轨替换 | §5 |
| 已剪好的画面母版 | full | narration + 三个采用 JSON | 无暂停 | 仅执行严格合成 | 下文 |

所有 `full` 与 `cut` 路径共用同一段收尾流程：（有旁白时）语义评审 → TTS 合成 → 音画混音组装 → 成片确定性 QC。使用原声模式前请先读 `references/audio-routing.md`。

如果手头已经有剪好的画面母版和本地验收通过的“声音采用三件套”，可以直接走严格合成（assembly-only）通道：

```bash
python3 scripts/recap.py picture.mp4 --edit-mode full --work-dir NEW_WORK \
  --output-dir DELIVERY \
  --tts-meta tts_meta.json \
  --narration-adoption narration_adoption.json \
  --audio-mix-adoption audio_mix_adoption.json
```

上述三个 JSON 参数必须同时传入。该入口仅接受单视频、`full` 模式、`narration` 音频模式、第 0 号音轨、全新工作目录以及尚不存在的交付路径；全程跳过视频理解、写稿校验、解说评审、TTS 配音、视频剪辑和剪映草稿导出。具体的时间轴契约、采样对齐和媒体封装仍由 `video-assemble` 严格把关，编排器只核对子技能落盘的绑定记录是否指向同一批采用文件与画面母版，不把调用方传入的音频当成编排器自动批准的创作结果。详见 `references/audio-routing.md`。

注意：这里传入的单视频必须是**已经剪定锁死画面的母版**。如果后来又调整了剪点，原先没改过词的逐段 WAV 与 `tts_meta.json` 可以继续留用，但必须对照新母版的帧长重新换算混音采用文件里的落点样本与底轨；具体重绑步骤见 `references/audio-routing.md` 中的 “Keep adopted voice after a cut”。

## 2. 创作职责

创作拍板权完全归 Agent。动笔前先判断本轮的**创作控制模式**（`CREATE` / `DIRECTED` / `REVISION`，与 `--edit-mode` 独立），并在进入配音和渲染前依次完成五项判断：

1. **导演判断**：明确观众点进来看什么、全程跟谁的视角（POV）走、核心悬念是什么、情绪从哪儿起到哪儿落，以及哪张底牌留到最后掀开。
2. **故事编辑**：每一拍（beat）都必须交代“局势发生了什么实质转折”，绝不写流水账式的场景摘要。
3. **画面剪辑**：挑出最扎人的动作、变脸反应与最佳入出点，砍掉拖泥带水的过场。
4. **声音/旁白**：先定每一拍由画面、原声对白、动作声、沉默还是旁白当家，再按“垫半句、让半句、接半句”动笔写词。
5. **观众复核**：站在第一次点进来的观众视角，分别做关旁白看画面、闭眼纯听声音和首尾兑现自检。

三种控制模式的分工、REVISION 下的修改与锁定规则、具体写作技法以及 `recap_story_plan.json`、`visual_audio_board.json`、`style_card.json` 的格式规范，全部按 `video-script` 技能及其创作手册执行。这些 JSON 只用来沉淀可复查的创作决定，不引入任何额外服务或渲染依赖。

## 3. 环境与脚本路径

```bash
# ffmpeg: brew install ffmpeg | apt install ffmpeg | choco install ffmpeg
export MIMO_API_KEY=***
```

同一个 `MIMO_API_KEY` 即可驱动默认全链路：

- 语音转写（ASR）：`mimo-v2.5-asr`
- 画面理解与评审（VLM / Chat）：`mimo-v2.5`
- 旁白合成（TTS）：`mimo-v2.5-tts`

配音后端可通过 `--tts-provider mimo-tts|fish-audio|index-tts`（或环境变量 `TTS_PROVIDER`）透传给配音技能；Fish Audio 与自建 `index-tts` 各自的凭据配置、默认音色及限制见配音技能说明。ASR 与 VLM 始终使用 MiMo。传 `--doctor` 可执行纯离线环境自检。

以 `tp-` 开头的 Token Plan 密钥默认发往中国区集群，可通过 `MIMO_TOKEN_PLAN_CLUSTER` 切换集群。

可选增强开关：

- `--mimo-video-overview`：按场景分块调用 MiMo 视频理解模型补充全局动态描述。

全量配置项一览见 `references/config-playbook.md`，`final_qc.json` 与各中间产物的字段定义见 `references/data-schema.md`。

下文中的 `scripts/...` 均相对于本技能目录；若从仓库根目录启动，请补上本技能的绝对路径。编排器会在同级目录下查找其余五个生产技能，安装时请保持六个技能目录位于同一父目录下。

## 4. 标准解说流程（audio-mode narration）

### 4.1 背景调研

只要能从文件名或用户指令里认出片名、剧集名或科普主题，开跑前先按 `video-understanding` 的 `research-guide.md` 查清人物关系与前情，写入 `work_dir/background_research.json`。这样后续跑画面分析时就能直接叫准角色姓名、看懂潜台词，不会满屏只写出“黑衣男子”“年轻女子”。认不出作品来源时直接跳过。

多视频连剪时同样把 `background_research.json` 写在项目根 `work_dir` 下：编排器在跑每个源视频理解前，会自动把它同步到 `sources/<source_id>/`（仅当子目录尚无该文件或比项目根文件更旧时才覆盖；因此若某一集需要单独补充本集梗概，在项目级文件写完后再单独更新对应子目录即可）。

### 4.2 分析并暂停创作

```bash
python3 scripts/recap.py <video> --work-dir <work_dir> --context "背景"
```

命令跑完视频理解并生成 `agent_narration_brief.md` 后会自动暂停，把接力棒交给 Agent。此时按以下顺序执行 `video-script`：

1. 通读创作简报 `agent_narration_brief.md` 并查看原片抽帧故事板。
2. 编写 `recap_story_plan.json` 与 `visual_audio_board.json`（按需写 `style_card.json`）。
3. `full` 模式直接写 `narration.json`；`cut` 模式第一阶段只写基于原片时间的 `clip_plan.json`。
4. `cut` 模式第二阶段先看剪后故事板，在 `visual_audio_board.json` 中补上成片输出时间并复核声音分工，再写基于成片输出时间的 `narration.json`。

切记：别一上来就凑标题或憋单句解说词，先把整条片子的矛盾主线、视角和选镜定死。

时间线有两条绝不妥协的硬红线：第一，原声对白只能在可靠的句末或静音边界切入、切出和回升音量，严禁把角色正在说的话拦腰切断；第二，旁白必须完整播放每一段合成音频，任何因片段映射截断、TTS 削尾或剪映草稿引用了加速前长音频的情况都会直接报错阻断。一旦遇到 `interrupts_source_sentence`、`unsafe_clip_sentence_boundary`、`no_safe_fit` 或 `timeline_audio_mismatch`，请老老实实挪动切点、精简词句或删掉塞不下的段落，系统不提供任何强行抢话的绕过开关（`unsafe_clip_sentence_boundary` 报错里自带 `nearest_safe`，会直接标出前后 5 秒内最近的安全切点秒数）。

### 4.3 多视频与素材库

多视频输入仅支持 `cut` 模式。项目级简报会列出每个视频固定的 `source_id`，编写 `clip_plan.json` 时每条片段都必须指明所属的 `source_id`：

```bash
python3 scripts/recap.py ep1.mp4 ep2.mp4 --edit-mode cut --target-duration 10m --work-dir work_dir_multi_ep
```

第二阶段同样会从各源视频的 `sources/<source_id>/frames/` 抽帧拼出剪后故事板 `storyboard/edited_storyboard.*`，并在简报顶部标明 `S1`、`S2`… 各自对应的 `source_id`；从本地素材库直接恢复的分析结果不含抽帧图片，这部分片段不会出现在故事板拼图里，可用 `inspect clip-map` 核对。

如需把单集分析结果存进本地目录供日后跨集复用，可开启轻量文件素材库：

```bash
python3 scripts/recap.py ep1.mp4 --material-library-dir .video-materials --save-materials
python3 scripts/recap.py ep1.mp4 ep2.mp4 --edit-mode cut --material-library-dir .video-materials --use-materials
```

素材库检索直接对 JSON / MD / JSONL 文件跑文本搜索（例如 `grep -R "关键词" .video-materials`），不拷贝原始大视频文件，也不依赖外部数据库或向量索引。

同一个库根目录下还可以登记可复用的制作资源（BGM、音效、音色、字体、贴图）、带版本号与采纳记录的模板（字幕样式、包装图层、制作参考）以及参考样片。目录规范与 `scripts/library.py check|list|show` 只读查询命令详见 `references/resource-library.md`。传入 `--project recap_project.json` 即可将已采纳的字幕样式、包装层、制作参考、音色与 BGM 一键绑定到当次运行；每次 `full` 或 `cut` 合成结束后，系统会在 `work_dir/resource_lock.json` 中如实记下实际用到的资源文件及版权确认状态。

### 4.4 继续生成成片

写好当前阶段所需的 JSON 文件后，重跑同一条命令即可继续：

```bash
python3 scripts/recap.py <video> --work-dir <work_dir>  # 可追加 --edit-mode cut / --no-burn-subtitles
```

编排器会先对 `clip_plan.json` 或 `narration.json` 做确定性硬校验（而 `recap_story_plan.json` 与 `visual_audio_board.json` 供 Agent 和建议型评审参考，不作渲染门禁）。`cut` 模式在第一次续跑时剪出 `edited_source.mp4` 并再次暂停；第二次续跑时完成配音、混音与字幕渲染，最终输出 `recap_<name>.mp4`。

硬校验绝不擅自改写解说词正文（仅根据实测对白分布更新 `overlaps_speech`）；`full` 模式下若某段词太长装不下时间窗，会直接报 `over_budget` error 退回修改。若文稿已经人工定稿锁死，可加上 `--preserve-approved-text`，此时连配音阶段也严格锁死原文——一旦某段语音装不下时间窗就直接报错停机，绝不偷偷删字或带病出片。

### 4.5 字幕测量与参考音色克隆

如果想用旁白字幕带直接盖住原片自带的硬字幕，可以先测出原片字幕的纵向像素范围。在完整源码仓库下可直接运行测量小工具；若只单独安装了技能包，则由 Agent 查看抽帧图片，在同一显示画布坐标系下量出上下边界：

```bash
python3 tools/measure_subtitle.py <video>
```

随后在运行时传入测得的 `--subtitle-y-top` 与 `--subtitle-y-bot`。该坐标基于经 ffmpeg 自动旋转后的显示画布，采用半开区间 `[top, bot)` 并搭配底对齐 ASS 字幕样式；一旦显式传入上下边界，该区域默认启用不透明遮罩以彻底盖住原片底字（可通过 `SUBTITLE_MASK_OPACITY` 调整透明度）。

在标准解说模式下如需用自定义参考音频克隆旁白音色，请传 `--voice-ref <audio>`（注意与 §5 整轨替换原声的 `dub` 模式区分）。

### 4.6 最终观看与交付复核

任何脚本指标、接点打分、抽帧拼图和自动 QC 报告都替代不了肉眼看片与亲耳听音。每次准备交付前，必须直接打开**本轮最终要交付给用户的成片文件**（别看旧文件、无字幕底片或中间切片），完成五步复核：

1. **原速通看一遍**：从头到尾不倍速、不跳看，边看边记下所有扎眼或卡顿的地方。
2. **逐个盯拼接点**：把每个切点前后 0.5–1 秒拉出来看，排查闪帧、半截叠化、动作跳接和被掐断的半句原声。
3. **闭上眼纯听一遍**：不看画面只听声音，检查旁白有没有碎成一蹦一跳的电报腔、转场处声音过得顺不顺、关键原声台词清不清楚。
4. **单拎三处戏眼**：把前 5 秒开场钩子、中段情绪爆发点和片尾最后一幕单独重看一遍，确认切入时机、情绪停留和收尾余味全都立得住。
5. **返修对账与规格兜底**：REVISION 模式先逐条确认“点名要改的真改到位了、没点名的锁定项分毫未动”，最后再核对解码完整性、总时长与音画编码规格。

`scene score`、亮度曲线、抽帧故事板与自动 QC 仅用于快速揪出可疑位置，最终留不留、怎么改一律以实片播放观感为准。遇到高频快切段落时按剪辑技能的规则区分原片快切与拼接碎切；哪一层出了硬伤就回哪一层修，严禁靠堆砌转场或花字遮丑。

在 `full` 或 `cut` 交付中，如果希望让确定性成片检查直接决定命令退出码，可显式传入 `--require-final-qc`。开启后，只有当 `final_qc.json` 中 `ok: true` 且 `blocker_count: 0` 时命令才会打印完成并返回 0；一旦报告缺失、格式损坏或检出任何阻断项（blocker），脚本会保留现场报告与已渲染文件并以非零退出码终止。默认情况下该检查仅生成报告而不阻断退出码（`--edit-mode dub` 不支持该参数）。

交付成片时务必查看 `final_qc.json` 里的 `metadata.warnings`：只要非空就必须如实告知用户，不能只报一句“渲染成功”。例如出现 `subtitle_burn_degraded` 时，说明本机 ffmpeg 缺少 `libass` 滤镜，默认字幕烧录已自动降级为同目录下的同名 `.srt` 外挂字幕（原片自带的硬字幕仍在画面上）。若用户需要把新字幕烧进画面，请提示其安装带 `libass` 的 ffmpeg 后重跑；若显式传了 `--burn-subtitles` 而缺少 `libass`，流程会在开跑前直接报错拦停。同理，若旁白里写了画面文字叠加（`visual_overlays`）而本机 ffmpeg 缺少 `drawtext` 滤镜，流程会在送 TTS 配音前报错停下，按提示删掉叠加字段或更换完整版 ffmpeg 后即可续跑。

### 4.7 不需要解说的片子

```bash
# 对当前整段输入直接混音合成；不隐式跑理解、ASR 或 TTS
python3 scripts/recap.py locked_picture.mp4 --work-dir source_work --audio-mode source-mix
# 按 cut 流程生成剪辑计划，剪完直接合成原声成片，不再暂停等待 narration.json
python3 scripts/recap.py ep1.mp4 ep2.mp4 --edit-mode cut --work-dir cut_work --audio-mode source-mix
# 仅更换外层视觉包装时，原封不动拷贝当前输入的 AAC 音轨；不可同时叠加 BGM 或 TTS
python3 scripts/recap.py adopted.mp4 --work-dir packaging_work --audio-mode adopted-packet-copy
```

`source-mix` 仍会走完整的混音与音频重编码；`cut + adopted-packet-copy` 冻结的是剪辑阶段生成的 `edited_source.mp4` 音轨，而非源视频的原始 AAC 数据包。精确绑定字幕轨（`subtitle_track.json`）仅支持 `adopted-packet-copy` 模式。切换音频模式时必须使用全新的工作目录，严禁把旧的 TTS 语音、评审记录或解说花字混进原声成片。详见 `references/audio-routing.md`。

## 5. 英译中原声复刻模式

`--edit-mode dub` 是一项实验性功能：将英文视频逐句翻译为中文，并用原片说话人的克隆音色整轨替换原声（并非在压低的英文原声上叠加旁白解说）。仅在用户明确要求“英译中配音复刻”时启用。

该模式会将源视频音轨分窗发往 MiMo ASR 转写，并截取约 10 秒的原片人声作为音色参考，连同每句中文译稿一并发往 MiMo `voiceclone` 接口。因此调用时**必须显式传入 `--confirm-voice-rights`**：执行前先向用户确认其拥有该视频与音频的使用权，且原说话人已授权克隆其声音，得到明确肯定后方可加上该参数；未传该参数时脚本在启动瞬间即报错退出，不抽音频、也不发任何请求。该参数仅供 `dub` 模式使用，其他模式传入会直接报错。

```bash
python3 scripts/recap.py <video> --edit-mode dub --confirm-voice-rights --work-dir <work_dir>
```

第一阶段完成英文转写、截取参考人声，并生成 `dub_brief.md` 与 `dub_transcript.json`。随后 Agent 按照 `dub_brief.md` 中的译制规范（逐句忠实原意、严守时间窗与口型语速）编写 `dub_script.json`：

```json
[{"start": 0.0, "end": 2.0, "zh": "中文译文"}]
```

写好后重跑同一命令，脚本会先跑确定性校验（生成 `dub_lint.json`，遇到空译文、时间重叠或越界立即中止），通过后再逐句克隆合成并贴回原时间线（仅在眼看要撞上下一句时做局部微加速），最终输出 `dub_<name>.mp4`。当前版本仅支持单说话人、整条音轨替换，不分离原片背景音乐。

## 6. 自检与只读 dashboard

```bash
python3 scripts/recap.py --doctor
```

### 6.1 只读 dashboard

```bash
python3 scripts/dashboard_server.py --root <目录> [--port 0] [--open]
```

该命令在前台启动本地看板并打印访问地址（仅绑定 `127.0.0.1`，由 Agent 启动时请放入后台运行）。它会在 `--root` 目录下自动扫描 `library.json`、`recap_project.json` 和 `recap_run_manifest.json`，分门别类展示资源库、项目绑定与历次运行的剪辑节奏、旁白文稿、成片时间线、QC 报告及 `resource_lock.json`。看板为**严格只读**设计：仅响应 `GET` 与 `HEAD` 请求，绝不改写磁盘上的任何文件；页面上的「复制给助手」按钮只生成一段修改指令供粘贴回对话窗口执行。

## 7. 输出与参数

核心交付物：

- `recap_<video>.mp4`：最终解说或原声成片。
- `subtitles.srt` / `subtitles.ass`：生成的字幕文件。
- `work_dir/`：全套中间分析与校验产物，字段契约见 `references/data-schema.md`。
- `work_dir/recap_story_plan.json` 与 `visual_audio_board.json`：Agent 落盘的导演意图与分镜声画决定。

全量命令行参数以 `python3 scripts/recap.py --help` 输出为准。其中 `--style` 是原封不动透传给写稿 Agent 的自由文本要求，并非固定预设、枚举值或分类开关。

## 8. 能力边界

- 建议型语义评审默认只提建议、出错时不阻断主流程；只有显式开启严格解说评审时，事实矛盾、半截残句或评审接口不可用才会在送 TTS 前拦截。本地确定性硬校验则始终作为硬门禁运行。
- 宣发标题、封面字、花字精修或外部文案回填统一交由 `video-script` 处理，按其内置的宣发规范执行。
