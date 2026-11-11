---
name: video-recap
description: 从输入视频一气呵成生成中文解说成片或原声剧情短片。用户提供 .mp4 / .mov / .mkv / .webm，并要求剪辑、添加旁白、配音、总结或制作短剧/电视剧/电影/纪录片/科普解说时使用。在同一个自包含技能内完成视频理解、导演立意与选镜剪辑、解说撰稿与校验、TTS 配音及音画混音合成。触发词：视频解说、视频旁白、生成解说、视频 recap、video recap、voiceover、narration、recap。
---

## 1. 定位与流程

本技能是自包含的视频解说与剪辑生产技能，将视频理解、故事立意、画面剪辑、旁白撰稿、配音合成与音画混音收敛在同一条连贯工作流中，各阶段共享同一工作目录 `work_dir` 下的 JSON 与 MP4 产物：

```text
understand.py（视频理解）─▶ Agent 制定故事/声画方案并写 clip_plan.json 或 narration.json ─▶ [cut.py（剪辑与重出简报）] ─▶ validate.py（本地底线校验）─▶ voiceover.py（TTS 配音）─▶ assemble.py（混音与字幕渲染）
```

日常使用可直接调用总入口 `scripts/recap.py` 自动串联全流程并在创作节点暂停，也可按需单独调用本技能 `scripts/` 下的单阶段入口（`understand.py`、`cut.py`、`validate.py`、`voiceover.py`、`assemble.py`）。

把现有成片拆解为制作参考（`production_reference.json`）不在默认生产链上。当用户要求拆片学习时：先对样片跑 `understand.py`（建议设 `ASR_SEGMENT_SECONDS=5`），再用独立的拆片技能依次执行测量（`measure`）、标注、校验（`check`）与导出（`export`），将导出的 `production_reference.json` 复制进下一轮运行的 `work_dir`，或登记为资源库里的 `production_reference` 模板并经 `--project` 绑定。主流程不会自动跑拆片，也不拿新成片去跟参考片做机械比对。

全流程支持断点续跑：Agent 写完 `clip_plan.json` 或 `narration.json` 后，重跑同一条 `recap.py` 命令即可往下走。续跑时脚本会核对 `recap_run_manifest.json` 中记录的源视频路径、文件大小、修改时间及运行参数，一旦发现工作目录来自其他视频或不同参数组合会直接拒绝复用；视频理解缓存也只在源片与配置完全一致时命中。每次暂停时终端打印的续跑命令保留了原始调用写法，并自动把视频与路径参数转为绝对路径、补齐 `--work-dir`，复制粘贴到任意目录都能直接跑。

画面流程 `--edit-mode full|cut` 与声音策略 `--audio-mode narration|source-mix` 相互独立：`narration` 走标准解说配音链，`source-mix` 不生成配音而直接混入原声与可选配乐。系统仅支持下表列出的五条合法路线，其余组合在启动时直接报错拦截：

| 输入 | `--edit-mode` | `--audio-mode` | Agent 暂停点 | 流程 | 详见 |
|---|---|---|---|---|---|
| 单视频 | full | narration | 1 次：写 `narration.json` | 理解 → 写稿 → 校验 → 配音 → 合成 | §4 |
| 单视频 | cut | narration | 2 次：先写 `clip_plan.json`，剪完再对成片写 `narration.json` | 理解 → 剪辑 → 重出输出时间简报 → 写稿 → 校验 → 配音 → 合成 | §4 |
| 多视频 | cut | narration | 2 次：同上，且每条片段必须带 `source_id` | 逐源理解 → 剪辑 → 写稿 → 校验 → 配音 → 合成 | §4.5 |
| 单视频 | full | source-mix | 无暂停 | 直接合成当前整段视频（不跑理解或 TTS） | §4.7 |
| 单 / 多视频 | cut | source-mix | 1 次：写 `clip_plan.json` | 理解 → 剪辑 → 合成（不写旁白） | §4.7 |

## 2. 创作职责

创作拍板权完全归 Agent。动笔前先判断本轮的**创作控制模式**（`CREATE` 开放创作 / `DIRECTED` 定向创作 / `REVISION` 定稿返修，与 `--edit-mode` 独立），并在进入配音和渲染前作为同一个创作大脑一气呵成完成五项判断：

1. **导演判断**：明确观众点进来看什么、全程跟谁的视角（POV）走、核心悬念是什么、情绪从哪儿起到哪儿落，以及哪张底牌留到最后掀开。
2. **故事编辑**：每一拍（beat）都必须交代“局势发生了什么实质转折”，绝不写流水账式的场景摘要。
3. **画面剪辑**：挑出最扎人的动作、变脸反应与最佳入出点，砍掉拖泥带水的过场。
4. **声音/旁白**：先定每一拍由画面、原声对白、动作声、沉默还是旁白当家，再按“垫半句、让半句、接半句”动笔写词。
5. **观众复核**：站在第一次点进来的观众视角，分别做关旁白看画面、闭眼纯听声音和首尾兑现自检。

三种控制模式的分工、REVISION 下的锁定不回退规则、具体剪辑与写作技法，以及 `recap_story_plan.json`、`visual_audio_board.json`、`style_card.json` 的格式规范详见 `references/creative-editing-playbook.md`。这些 JSON 用来沉淀可复查的创作决定，确保选镜、原声留白与解说词从头到尾咬住同一个故事意图。

## 3. 环境与远程调用说明

```bash
# ffmpeg: brew install ffmpeg | apt install ffmpeg | choco install ffmpeg
export MIMO_API_KEY=***
```

同一个 `MIMO_API_KEY` 即可驱动默认全链路。为保证数据流向透明，本技能涉及远程网络调用的环节说明如下：

- **视频理解（`understand.py`）**：将切分后的音频窗口发往 MiMo ASR（`mimo-v2.5-asr`），将抽帧 JPEG 连同 `background_research.json` 摘要发往 MiMo VLM（`mimo-v2.5`）。若工作目录已有完整分析产物，传 `--brief-only` 或 `--edited-storyboard-only` 则纯本地运行、不发起任何网络请求。
- **旁白配音（`voiceover.py`）**：按 `--tts-provider`（默认 `auto` 优先选已配置的 MiMo，其次 Fish Audio，绝不自动选 `index-tts`）将解说文本发往对应 TTS 服务：
  - `mimo-tts`：发往 MiMo TTS（`mimo-v2.5-tts`）；若显式传了 `--voice-ref <audio>`，还会将该参考音频切片发往 `mimo-v2.5-tts-voiceclone`（使用前请确认已获声音授权）。
  - `fish-audio`：需配置 `FISH_API_KEY`，将每段解说文本与 `FISH_TTS_REFERENCE_ID` 发往 `FISH_TTS_API_URL`（默认 `https://api.fish.audio/v1/tts`）。
  - `index-tts`：仅在显式指定 `--tts-provider index-tts` 时启用，将文本与 `INDEX_TTS_VOICE` 发往用户自建的 `INDEX_TTS_ENDPOINT`（详见 `references/index-tts.md`）。
- **解说校验（`validate.py`）、视频剪辑（`cut.py`、`shot_review.py`）与音画合成（`assemble.py`）**：纯本地确定性逻辑与 `ffmpeg` / `ffprobe` 调用，不发起任何网络请求。

以 `tp-` 开头的 Token Plan 密钥默认发往中国区集群，可通过 `MIMO_TOKEN_PLAN_CLUSTER` 切换集群。全量配置项见 `references/config-playbook.md`，所有中间产物格式见 `references/data-schema.md`。下文中的 `scripts/...` 均相对于本技能目录。

## 4. 一气呵成的创作与剪辑流程

### 4.1 背景调研与视频理解

只要能从文件名或用户指令里认出片名、剧集名或科普主题，开跑前先按 `references/research-guide.md` 查清人物关系与前情，写入 `work_dir/background_research.json`。这样后续跑画面分析时就能直接叫准角色姓名、看懂潜台词，不会满屏只写出“黑衣男子”“年轻女子”。多视频连剪时同样写在项目根 `work_dir` 下，脚本会自动同步到各源子目录 `sources/<source_id>/`。

随后启动理解与编排入口：

```bash
python3 scripts/recap.py <video> --work-dir <work_dir> --context "背景"
```

脚本完成音频提取、静音与句末停顿锚点检测（`speech_boundary_anchors.json`）、场景切分、VLM 帧分析与 ASR 转写后，生成故事板拼图 `storyboard/storyboard_*.jpg` 与创作简报 `agent_narration_brief.md` 并自动暂停。

### 4.2 导演意图与选镜计划（cut 第一阶段）

通读 `agent_narration_brief.md` 并直接查看故事板抽帧图片后，先写 `recap_story_plan.json`（至少列出 2 个候选方案并选定主视角 POV、开场钩子、情绪弧线与留白底牌）与 `visual_audio_board.json`（逐拍明确选哪个瞬间、为何进出、声音谁当家）。

在 `cut` 模式第一阶段，接着编写基于**原片时间轴（SOURCE）**的 `work_dir/clip_plan.json`（此时还不写 `narration.json`）：

```json
{
  "target_duration": "3m",
  "clips": [
    {
      "start": 12.0,
      "end": 38.0,
      "reason": "b01 | hook | knowledge: unknown→threat | POV=沈曼 | 撞见未婚夫搂着千金谈联姻 | 入点=推门看清两人动作 | 出点=转身攥紧户口本"
    }
  ],
  "required_evidence": {
    "nodes": [
      {"id": "e1", "source": "/abs/source.mp4", "start": 14.2, "end": 18.6, "track": "video", "content": "推门撞见两人拥抱"}
    ],
    "before": []
  }
}
```

选镜与切点必须严守以下底线：

- **`reason` 七段式结构**：依次写明 `beat_id | 叙事功能 | 局势状态变化（A→B） | POV=人物 | 本段实质看点 | 入点=具体动作/原声锚点 | 出点=具体反应/落点`，倒逼每段画面都服务于故事推进。
- **必保画面与原声证据（`required_evidence`）**：拍板决定必须留住的高光动作、变脸瞬间或关键原声问答，写入顶层 `required_evidence`（`nodes` 登记 `id`/`source` 绝对路径/`start`/`end`/`track`/`content`，`before` 登记不可颠倒的先后对）；边界吸附一旦裁掉任何必保区间或打乱先后顺序，脚本会直接报错阻断。
- **开场晚进场与镜头取舍**：默认按时间顺序“晚进场”（直接从冲突爆发、荒诞反差或关键动作切入，砍掉推门进屋、寒暄起势等慢热铺垫）；仅当正序冲突出现过晚时才允许摘 `<= 2.5s` 的冷开场悬念镜，且必须做到不铺垫也能看懂、不与正片重复同一机位画面、只留悬念绝不提前剧透反转结果。旁白压低原声时，优先选听者变脸反应、动作细节或稳态镜头，避免把角色正脸滔滔不绝张嘴却被消音的画面垫在旁白底下；同款点头或呆看反应镜不重复超过两次。
- **句末安全切点与呼吸尾垫**：切点必须落在原片首尾、句末停顿锚点或静音窗口内，或者属于同源相邻片段无缝顺接；吸附句末时会自动在停顿窗口内预留最多 `0.30s` 反应/呼吸气口且绝不跨越硬切点，片段最短时长不低于 `0.8s`。片尾最后一拍必须在结局动作或台词落定后留出 `1.5–2.5s` 表情余韵，并在跨入下一场前干净收尾。踩进原声台词中间会触发 `unsafe_clip_sentence_boundary` 阻断并给出 `nearest_safe`；若回退硬切吸附会同时暴露 `<= 0.20s` 闪帧与截断台词，则直接报 `shot_speech_conflict` 阻断。
- **高频快切与碎镜头排查**：`cut.py` 渲染 `edited_source.mp4` 后会自动调用 `shot_review.py` 生成 `shot_review.json`，并对时长 `<= 0.20s`（约 1–5 帧）的极短残镜闪帧（`flash_slivers`）直接硬阻断。面对动作戏或密集反打时，也可手动运行 `ffmpeg -i <video> -vf "select='gt(scene,0.35)',showinfo" -f null -` 或只读工具 `scripts/shot_review.py`（用法见 `references/shot-review.md`）定位高分切点，但必须区分**原片自带的切点**与**由本次拼接制造的切点**——若属于**原片自带的无关短镜头整段删除**或把切点挪到快切簇外侧；若属于**由本次拼接制造的切点**（如硬切边缘残留几帧隔壁镜头、碎反打），则外扩入出点或合并同源相邻片段，严禁靠塞转场遮丑。

写好 `clip_plan.json` 后重跑 `recap.py`，脚本会调用 `cut.py` 完成三步边界吸附（镜头硬切吸附 → 句末静音吸附与尾垫 → 帧网格对齐）、渲染恒定帧率 `edited_source.mp4` 并完成闪帧体检，再把 ASR、VLM 与句末锚点重映射到成片输出时间轴（`speech_boundary_anchors_output.json`、`storyboard/edited_storyboard.*`），随后再次暂停等待写解说词。

### 4.3 解说词与原声留白创作（full / cut 第二阶段）

进入写稿阶段后（`full` 模式在第一次暂停、`cut` 模式在剪完 `edited_source.mp4` 后的第二次暂停），先查看剪后故事板、补齐 `visual_audio_board.json` 的成片输出时间，并按需编写 `style_card.json`，再撰写 `work_dir/narration.json`：

```json
[
  {
    "start": 0.2,
    "end": 5.0,
    "narration": "相恋五年揣着户口本去求婚，沈曼推开门，却撞见未婚夫正搂着千金谈联姻。",
    "pause_after_ms": 250,
    "overlaps_speech": true
  }
]
```

注意：**编排 `cut` 模式下，`narration.json` 必须直接填写剪后成片的 OUTPUT 时间戳（`0 .. 成片总时长`）**（单独调用校验器时传 `validate.py --mode cut_output`）。动笔时遵循九条核心写作铁律（详规与正反例见 `references/creative-editing-playbook.md`）：

1. **前 2.5 秒内亮出钩子或原声高光**，直接抛具体反差动作或核心悬念，不写人物简历、日期播报或寒暄开场；超过 `2.5s` 既无旁白也无原声对白的冷开场会触发 `weak_cold_open_silence` 阻断。
2. **讲清背景、人物关系与因果，控制点名密度，拒绝“看图说话”**：已提供 `background_research.json` 角色名时严禁用“黑衣男子”“年轻女子”等笼统外貌词指代（违者报 `generic_character_label` 阻断）；60 秒以内短片最多点名 1–2 个核心人物（首句最多点 1 人并顺带点明身份），其余次要角色统一用关系/身份称呼（如“前女友”“她上司”“太医”），句句围绕“处境 → 动作 → 当场后果”推进。
3. **声画对位“垫半句、让半句、接半句”且强视觉必同频**：高光原声台词、耳光声、沉默变脸前果断闭嘴让位，原声落定后再接半句点破潜台词；画面出现接吻、扇耳光、下跪、晕倒、亮信物等强视觉动作时，必须由保留原声或紧邻旁白正面交代因果，禁止各说各话；全片严禁出现连续超过 `5.0s` 既无旁白也无原声对白的死气空窗（违者报 `excessive_dead_air` 阻断）。
4. **死守原声切入与回升双红线**：压低原声切入旁白（`overlaps_speech: true`）时，`start` 必须贴紧句末锚点或静音区（违者报 `interrupts_source_sentence` 阻断）；旁白结束放回原声时同样不得在角色半句话中间突然恢复音量（违者在校验与合成阶段分别报 `unsafe_source_release` / `unsafe_source_handoff` 阻断）。
5. **严控时间窗字数预算、保持自然语速与完整句收尾**：每段必须以完整句末标点收口（残句报 `incomplete_sentence` 硬阻断），靠精炼字数适配时间窗而非指望高倍速硬塞；`full` 模式超预算 1.25 倍直接报 `over_budget` 阻断，校验器绝不擅自替 Agent 删字。
6. **清零议论升华腔与电报腔，结局交给画面演完**：写自然连贯的口语长短句，不写一词一顿的碎句；严禁写“这意味着……”“这是一场关于……的较量”“让权力与骄傲退场”“命运的齿轮”等阅读理解式点评与升华套话（触发 `editorial_commentary_tone` 告警）。故事结局必须由画面与原声当场演出来，严禁在结局兑现后再画蛇添足补一句总结升华旁白，也不要在收束旁白后再拖一句重复同一结果的原声尾巴。
7. **锚定具体画面证据与纯器乐配乐纪律**：用看得到的眼神、道具、动作细节说话，不替角色编造画面外的心理活动；若选用外部配乐（`BGM_PATH`），必须使用纯器乐（零人声歌词），且当原片已带无法避开的高音量人声插曲时不再叠外部配乐，防止多轨人声互抢。
8. **核准原声留白字幕**：当开启原片字幕遮罩且留有原声高光段落时，同步在 `work_dir/original_subtitles.json` 中按成片时间写出校对后的原声台词，修正 ASR 同音错字。
9. **写完先做三遍自检**：先通读全文看故事线通不通、事实与原片对不对得上，再对照时间窗查有没有抢话或超字数，最后跑 `validate.py`（或直接重跑 `recap.py`）。如需撰写外层宣发标题、封面帧与花字方案（`packaging_plan.json`），必须在正片故事锁死后按 `references/promotional-copy.md` 执行。

### 4.4 配音合成与音画混音渲染

写好 `narration.json` 后重跑同一条 `recap.py` 命令：

1. **本地确定性校验（`validate.py`）**：生成 `narration_lint.json`，对时间重叠、越界、字数超预算、残句、开场冷场、超过 5 秒的死气空窗、抢断原话、半句放回原声、笼统角色代称与占位符泄漏做硬门禁检查，存在 error 立即停机。
2. **TTS 配音（`voiceover.py`）**：逐段合成语音并写入 `tts_segments/` 与 `tts_meta.json`。缓存按文本、音色、模型与参考音频指纹精确命中；若某段音频时长低于正常语速下限（`TTS_MIN_SPEECH_RATE`）疑似模型幻读，会自动重试并留存 `*.rejected.wav` 拒止入库。
3. **音画合成（`assemble.py`）**：将画面、逐段旁白、压低与句末回升后的原声（详见 `references/data-schema.md` 中 `source_handoff_status` 说明）、可选 BGM（`BGM_PATH`）、可选点缀转场音效与精装视觉包装（`work_dir/packaging_plan.json`：支持 `9:16` 竖版舞台/剧集双金线画幅、`16:9` 影院印章与米白纸感字幕条、`@2x` 超采样人物铭牌/数据卡/书法花字，以及双层柔光重点词点亮字幕；或 `packaging_layers.json` 静态贴图）、片尾平滑淡出（默认 `350ms`）一并混音渲染为最终 MP4，并输出后端无关的 `timeline.json`（传 `--export-jianying` 可同步导出剪映草稿）。

任何一段旁白若在允许的微加速上限内仍塞不进可用时间窗，合成器直接报 `no_safe_fit` 阻断，**绝不削断旁白尾音**。更多声音模式、音频路由与外层视觉包装规范分别见 `references/audio-modes.md`、`references/audio-routing.md` 与 `references/packaging.md`。

### 4.5 多视频连剪与资源库

多视频输入仅支持 `cut` 模式。项目级简报会列出每个视频固定的 `source_id`，编写 `clip_plan.json` 时每条片段都必须指明所属的 `source_id`：

```bash
python3 scripts/recap.py ep1.mp4 ep2.mp4 --edit-mode cut --target-duration 10m --work-dir work_dir_multi_ep
```

如需把单集理解结果存进本地目录供跨集复用，可开启轻量文件素材库（`--material-library-dir .video-materials --save-materials` 与 `--use-materials`），直接用 `grep` 检索 JSON/MD 文件。同一个库根目录下还可登记可复用的制作资源（BGM、音效、音色、字体、贴图）、带采纳记录的模板（`subtitle_style`、`packaging`、`production_reference`）及样片，通过 `scripts/library.py check|list|show` 查询，并用 `--project recap_project.json` 一键绑定到当次运行；每次合成结束后会在 `work_dir/resource_lock.json` 中记录实际使用的资源及授权状态。详见 `references/resource-library.md`。

如需测量原片硬字幕的纵向像素区间以启用精确遮罩，可在完整源码仓库下运行 `tools/measure_subtitle.py <video>`，或直接查看抽帧图片量出上下边界，再传入 `--subtitle-y-top` 与 `--subtitle-y-bot`。

### 4.6 最终观看与交付复核

任何脚本指标、接点打分、抽帧拼图和自动 QC 报告都替代不了肉眼看片与亲耳听音。每次准备交付前，必须直接打开**本轮最终要交付给用户的成片文件**，完成五步复核：

1. **原速通看一遍**：从头到尾不倍速、不跳看，边看边记下所有扎眼或卡顿的地方。
2. **逐个盯拼接点**：把每个切点前后 0.5–1 秒拉出来看，排查闪帧、半截叠化、动作跳接和被掐断的半句原声。
3. **闭上眼纯听一遍**：不看画面只听声音，检查旁白有没有碎成一蹦一跳的电报腔、转场处声音过得顺不顺、关键原声台词清不清楚。
4. **单拎三处戏眼**：把前 5 秒开场钩子、中段情绪爆发点和片尾最后一幕单独重看一遍，确认切入时机、情绪停留和收尾余味全都立得住。
5. **返修对账与规格兜底**：REVISION 模式先逐条确认“点名要改的真改到位了、没点名的锁定项分毫未动”，最后再核对 `final_qc.json` 的解码完整性、总时长、残帧扫描与音画编码规格（`--require-final-qc` 默认开启，阻断项直接决定退出码；仅调试时可传 `--no-require-final-qc`）。

交付成片时务必查看 `final_qc.json` 里的 `metadata.warnings`：只要非空（例如开启部分容错后出现 `partial_tts` 缺段，或本机 ffmpeg 缺少 `libass` 触发 `subtitle_burn_degraded` 降级为外挂 `.srt`）就必须如实告知用户，不能只报一句“渲染成功”。

### 4.7 不需要解说的原声成片

```bash
# 对当前整段输入直接混音合成；不隐式跑理解、ASR 或 TTS
python3 scripts/recap.py locked_picture.mp4 --work-dir source_work --audio-mode source-mix
# 按 cut 流程生成剪辑计划，剪完直接合成原声成片，不再暂停等待 narration.json
python3 scripts/recap.py ep1.mp4 ep2.mp4 --edit-mode cut --work-dir cut_work --audio-mode source-mix
```

`source-mix` 会走完整的混音、片尾平滑淡出与音频重编码。切换音频模式时必须使用全新的工作目录。详见 `references/audio-routing.md`。

## 5. 自检与只读看板

```bash
python3 scripts/doctor.py
python3 scripts/dashboard_server.py --root <目录> [--port 0] [--open]
```

`doctor.py` 检查本机 `ffmpeg` / `ffprobe`、字幕滤镜与各家远程服务密钥连通性；`dashboard_server.py` 在本地 `127.0.0.1` 启动严格只读的极简审片台（仅响应 `GET` 与 `HEAD`），自动扫描 `--root` 下的成片工作目录与 `library.json`，直观展示成片播放、解说词与镜头同步对照及素材库预览。

## 6. 输出与参数

核心交付物：

- `recap_<video>.mp4`：最终解说或原声成片。
- `subtitles.srt` / `subtitles.ass`：生成的字幕文件。
- `work_dir/`：全套中间分析、计划与校验产物，字段契约见 `references/data-schema.md`。
- `work_dir/recap_story_plan.json` 与 `visual_audio_board.json`：Agent 落盘的导演意图与分镜声画决定。

全量命令行参数以 `python3 scripts/recap.py --help` 及各阶段脚本 `--help` 输出为准。其中 `--style` 是原封不动透传给写稿 Agent 的自由文本要求，并非固定预设或枚举开关。

## 7. 能力边界

- 本地确定性底线校验（残镜/碎镜、台词截断、旁白压台词、时间窗字数硬上限、超长削尾、必保画面丢失、占位符泄漏、解码与封装规格）始终作为硬门禁运行；脚本只负责机械执行与工程地板兜底。
- 故事弧线、开场钩子、声画呼吸感与事实一致性等语义与叙事品质不设模型打分门禁，由 Agent 对照画面证据、原声时间轴及 `references/creative-editing-playbook.md` 自主把控并完成交付前视听复核。
