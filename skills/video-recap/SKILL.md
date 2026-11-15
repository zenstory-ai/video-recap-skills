---
name: video-recap
description: 从输入视频生成中文解说成片或原声剧情短片。用户提供 .mp4 / .mov / .mkv / .webm，并要求剪辑、添加旁白、配音、总结或制作短剧/电视剧/电影/纪录片/科普解说时使用。在同一个自包含技能内完成视频理解、导演立意与选镜剪辑、解说撰稿与校验、TTS 配音及音画混音合成。触发词：视频解说、视频旁白、生成解说、视频 recap、video recap、voiceover、narration、recap。
---

## 1. 定位与流程

本技能是自包含的视频解说与剪辑生产技能，将视频理解、故事立意、画面剪辑、旁白撰稿、配音合成与音画混音放在同一工作目录 `work_dir` 下串联完成：

```text
understand.py（视频理解）─▶ Agent 制定故事/声画方案并写 clip_plan.json 或 narration.json ─▶ [cut.py（剪辑与重出简报）] ─▶ validate.py（本地底线校验）─▶ voiceover.py（TTS 配音）─▶ assemble.py（混音与字幕渲染）
```

日常使用可直接运行总入口 `scripts/recap.py` 串联全流程并在创作节点自动暂停，也可按需单独调用 `scripts/` 下的单阶段脚本（`understand.py`、`cut.py`、`validate.py`、`voiceover.py`、`assemble.py`）。

把外部成片拆解为制作参考（`production_reference.json`）不在默认生产链上。当用户要求拆片学习时：先对样片跑 `understand.py`（建议设 `ASR_SEGMENT_SECONDS=5`），再用独立的拆片技能执行测量、标注、校验与导出，将导出的 `production_reference.json` 放入下一轮运行的 `work_dir`，或登记为资源库里的 `production_reference` 模板并经 `--project` 绑定。主流程不会自动拆片，也不拿新成片与参考片做机械打分比对。

全流程支持断点续跑：写完 `clip_plan.json` 或 `narration.json` 后重跑同一条 `recap.py` 命令即可继续。续跑时脚本会核对 `recap_run_manifest.json` 记录的源视频身份与参数，若工作目录来自其他视频或不同参数组合会拒绝复用；暂停时终端打印的续跑命令已将路径转为绝对路径并补齐 `--work-dir`。

画面流程 `--edit-mode full|cut` 与声音策略 `--audio-mode narration|source-mix` 相互独立，支持以下五条路线（其余组合启动即报错）：

| 输入 | `--edit-mode` | `--audio-mode` | Agent 暂停点 | 流程 | 详见 |
|---|---|---|---|---|---|
| 单视频 | full | narration | 1 次：写 `narration.json` | 理解 → 写稿 → 校验 → 配音 → 合成 | §4 |
| 单视频 | cut | narration | 2 次：先写 `clip_plan.json`，剪完再对成片写 `narration.json` | 理解 → 剪辑 → 重出输出时间简报 → 写稿 → 校验 → 配音 → 合成 | §4 |
| 多视频 | cut | narration | 2 次：同上，且每条片段必须带 `source_id` | 逐源理解 → 剪辑 → 写稿 → 校验 → 配音 → 合成 | §4.5 |
| 单视频 | full | source-mix | 无暂停 | 直接合成当前整段视频（不跑理解或 TTS） | §4.7 |
| 单 / 多视频 | cut | source-mix | 1 次：写 `clip_plan.json` | 理解 → 剪辑 → 合成（不写旁白） | §4.7 |

## 2. 创作职责

创作判断由 Agent 负责。动笔前先区分本轮的**创作控制模式**（`CREATE` 新建创作 / `DIRECTED` 按指定方案执行 / `REVISION` 定稿返修，与 `--edit-mode` 独立），并在配音渲染前完成五项判断：

1. **导演判断**：明确观众点进来看什么、跟谁的主视角（POV）走、核心悬念是什么、情绪从哪儿起到哪儿落，以及哪张底牌留到最后揭晓。
2. **故事编辑**：每一拍（beat）交代清楚局势、关系或筹码发生了什么实质变化，不写流水账摘要。
3. **画面剪辑**：选出有动作推进与人物反应的镜头和入出点，删掉拖沓过场。
4. **声音/旁白**：先定每一拍由画面、原声对白、动作声、静音还是旁白主导，再按“垫半句、让半句、接半句”写词。
5. **观众复核**：站在首次观看的视角做关旁白看画面、闭眼纯听声音和首尾兑现检查。

控制模式分工、`REVISION` 锁定规则、剪辑与口播细则，以及 `recap_story_plan.json`、`visual_audio_board.json`、`style_card.json` 的结构详见 `references/creative-editing-playbook.md`。

## 3. 环境与远程调用说明

```bash
# ffmpeg: brew install ffmpeg | apt install ffmpeg | choco install ffmpeg
export MIMO_API_KEY=***
```

同一个 `MIMO_API_KEY` 即可驱动默认全链路。涉及远程网络请求的环节如下：

- **视频理解（`understand.py`）**：将切分后的音频窗口发往 MiMo ASR（`mimo-v2.5-asr`），将抽帧 JPEG 连同 `background_research.json` 摘要发往 MiMo VLM（默认 `mimo-v2.6-flash`，可通过 `MIMO_MODEL` 切换 `mimo-v2.6-pro` 等）。若工作目录已有完整分析产物，传 `--brief-only` 或 `--edited-storyboard-only` 则纯本地运行、不发起网络请求。
- **旁白配音（`voiceover.py`）**：按 `--tts-provider`（默认 `auto` 优先选已配置的 MiMo，其次 Fish Audio，不自动选 `index-tts`）将解说文本发往对应 TTS 服务：
  - `mimo-tts`：发往 MiMo TTS（`mimo-v2.5-tts`）；若显式传了 `--voice-ref <audio>`，还会将该参考音频切片发往 `mimo-v2.5-tts-voiceclone`（使用前请确认已获声音授权）。
  - `fish-audio`：需配置 `FISH_API_KEY`，将每段解说文本与 `FISH_TTS_REFERENCE_ID` 发往 `FISH_TTS_API_URL`（默认 `https://api.fish.audio/v1/tts`）。
  - `index-tts`：仅在显式指定 `--tts-provider index-tts` 时启用，将文本与 `INDEX_TTS_VOICE` 发往用户自建的 `INDEX_TTS_ENDPOINT`（详见 `references/index-tts.md`）。
- **解说校验（`validate.py`）、视频剪辑（`cut.py`、`shot_review.py`）与音画合成（`assemble.py`）**：纯本地确定性逻辑与 `ffmpeg` / `ffprobe` 调用，不发起网络请求。

以 `tp-` 开头的 Token Plan 密钥默认发往中国区集群，可通过 `MIMO_TOKEN_PLAN_CLUSTER` 切换集群。配置项见 `references/config-playbook.md`，产物格式见 `references/data-schema.md`。下文 `scripts/...` 均相对于本技能目录。

## 4. 创作与制作流程

### 4.1 背景调研与视频理解

只要能从文件名或用户说明识别出片名、剧集名或科普主题，开跑前先按 `references/research-guide.md` 核实人物关系与前情，写入 `work_dir/background_research.json`（多视频连剪写在项目根 `work_dir`，脚本会自动同步到 `sources/<source_id>/`）。

随后启动编排入口：

```bash
python3 scripts/recap.py <video> --work-dir <work_dir> --context "背景"
```

脚本完成音频提取、静音与句末停顿锚点检测（`speech_boundary_anchors.json`）、场景切分、VLM 帧分析与 ASR 转写后，生成故事板拼图 `storyboard/storyboard_*.jpg` 与创作简报 `agent_narration_brief.md` 并暂停。

### 4.2 导演意图与选镜计划（cut 第一阶段）

阅读 `agent_narration_brief.md` 并查看故事板抽帧后，先写 `recap_story_plan.json`（`CREATE` 主线清晰时写 1 个选定方案即可，仅在源片存在多角度取舍时比较 2–3 个候选假设）与 `visual_audio_board.json`（逐拍明确画面瞬间、进出理由、原声锚点与声音主导方）。

在 `cut` 模式第一阶段，编写基于**源片时间轴（SOURCE）**的 `work_dir/clip_plan.json`（本阶段不写 `narration.json`）：

```json
{
  "target_duration": "3m",
  "clips": [
    {
      "start": 12.0,
      "end": 38.0,
      "reason": "b01 (hook)：沈曼推门撞见未婚夫与千金谈联姻，入点卡推门看清两人动作，出点卡转身攥紧户口本"
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

选镜与切点规则：

- **简明剪辑理由（`reason`）**：用自然中文写清该段承担的节拍作用（`hook | setup | turn | escalation | payoff`）与入出点画面/声音依据，无需拼固定段数的竖线模板。
- **必保画面与原声证据（`required_evidence`）**：不可丢失的关键动作、反应或原声问答可登记在 `required_evidence`；若边界吸附裁掉必保区间或颠倒 `before` 顺序，脚本会报错拦截。
- **晚进场与冷开场取舍**：默认顺时序“晚进场”（直接从冲突或反差动作切入，省去进门寒暄铺垫）；正序冲突出现较晚时可前置一段冷开场悬念镜，时长随悬念动作自然收住，做到不靠铺垫也能看懂、不与正片重复同一机位、只留悬念不提前亮底牌。旁白压低原声时优先垫反应镜、动作细节或稳态画面，避免角色正脸连续张嘴说话却被压低消音。
- **句末安全切点与收尾气口**：切点须落在源片首尾、句末停顿锚点、静音窗口或同源无缝顺接处；句末吸附会在停顿窗内预留最多 `0.30s` 气口且不跨硬切点，单段最短 `>= 0.8s`，片尾最后一拍留出表情或动作落定的余韵。切进台词中途会报 `unsafe_clip_sentence_boundary`（若同时引发闪帧与切词冲突则报 `shot_speech_conflict`）。
- **密集快切与碎镜排查**：`cut.py` 渲染 `edited_source.mp4` 后会自动运行 `shot_review.py` 生成 `shot_review.json`，拦截 `<= 0.20s` 的极短残镜闪帧（`flash_slivers`）。遇到动作戏或密集反打时，也可运行 `ffmpeg -i <video> -vf "select='gt(scene,0.35)',showinfo" -f null -` 或 `scripts/shot_review.py`（见 `references/shot-review.md`）检查切点，区分原片切点与拼接切点——将**原片自带的无关短镜头整段删除**或把边界移到快切簇外侧；对**由本次拼接制造的切点**（如硬切边缘残留几帧邻镜），外扩入出点或合并同源相邻段，不用转场遮掩坏接点。

写好 `clip_plan.json` 后重跑 `recap.py`，脚本完成边界吸附、渲染恒定帧率 `edited_source.mp4` 并重映射输出时间轴证据（`speech_boundary_anchors_output.json`、`storyboard/edited_storyboard.*`），随后暂停等待写解说词。

### 4.3 解说词与原声留白创作（full / cut 第二阶段）

进入写稿阶段后，先查看剪后故事板、补齐 `visual_audio_board.json` 的成片输出时间并按需编写 `style_card.json`，再写 `work_dir/narration.json`：

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

**编排 `cut` 模式下，`narration.json` 直接填写剪后成片的 OUTPUT 时间戳（`0 .. 成片总时长`）**（单独调校验器传 `validate.py --mode cut_output`）。核心写作要求（细则与正反例见 `references/creative-editing-playbook.md`）：

- **背景、筹码与点名节奏**：讲清前情关系、动机筹码与动作后果，不复述画面表象；60 秒内短片点名 1–2 个核心人物即可（次要角色用身份关系称呼），3–10 分钟中长解说随节拍推进自然引入关键人物。
- **“垫半句、让半句、接半句”与原声边界**：原声重头台词或关键动作声前提前收口让位，尾音落定后再接半句点破后果；压低原声切入（`overlaps_speech: true`）须对齐句末锚点或静音窗（违者报 `interrupts_source_sentence` / `unsafe_source_handoff`），释放原声同样避免落在角色半句话中途（对白区间内后续 3s 无锚点时给出 `unsafe_source_release` 复核提示，合成端做 `bounded_release` 平滑回升）。
- **字数预算、完整收句与去套话**：每段以完整句末标点收尾（残句报 `incomplete_sentence`），靠精炼字数匹配窗口（`full` 模式超预算 1.25 倍报 `over_budget`，不静默删字）。写连贯自然的口语长短句，不写阅读理解式升华套话；结局交给画面与原声演完，兑现后不再补总结旁白。
- **配乐与原声留白字幕**：外部配乐（`BGM_PATH`）只用纯器乐，原片已有高音量人声插曲时不叠外部配乐。传入 `--subtitle-y-top`/`--subtitle-y-bot` 默认仅在旁白区间开启 `1.0` 不透明像素带遮罩（留白段继续露出原片自带硬字幕）；若原片无硬字幕，或额外设置 `SOURCE_SUBTITLE_MASK_TIMING=all`（或同时设 `MASK_SOURCE_SUBTITLES=true` + `SOURCE_SUBTITLE_MASK_TIMING=all` + `SUBTITLE_MASK_OPACITY=1.0`）开启全片不透明遮罩并保留了原声对白，可在 `work_dir/original_subtitles.json` 按成片时间写入校对后的原声字幕（若留白段原片硬字幕仍可见，合成器会自动跳过以免叠字）。
- **平台宣发备忘与画布包装分开落盘**：故事定稿后如需输出平台标题、封面帧与简介方案，按 `references/promotional-copy.md` 写入 `work_dir/promo_copy.json`；成片画布视觉包装按 `references/packaging.md` 写入 `work_dir/packaging_plan.json`。

### 4.4 配音合成与音画混音渲染

写好 `narration.json` 后重跑同一条 `recap.py` 命令：

1. **本地确定性校验（`validate.py`）**：输出 `narration_lint.json`。对时间重叠/越界、残句、抢断原话、`full` 模式超预算与模板占位符泄漏做硬阻断（`errors`）；对原声释放点无邻近锚点（`unsafe_source_release`）、开场留白偏长（`weak_cold_open_silence`）、长段无声留白（`excessive_dead_air`）、外貌泛称（`generic_character_label`）与评论套话（`editorial_commentary_tone`）给出非阻断提示（`warnings`）。
2. **TTS 配音（`voiceover.py`）**：逐段合成语音写入 `tts_segments/` 与 `tts_meta.json`，缓存按文本、音色、模型与参考音频指纹命中；若实测语速低于下限（`TTS_MIN_SPEECH_RATE`，即合成音频异常拖长）疑似异常合成，会自动重试并将异常音频另存为 `*.rejected.wav`。
3. **音画合成（`assemble.py`）**：将画面、逐段旁白、动态闪避与句末回升后的原声、可选 BGM、可选音效（`work_dir/sfx_plan.json` 或 `packaging_plan.json.sfx`）、画布包装（`work_dir/packaging_plan.json` 或 `packaging_layers.json`，见 `references/packaging.md`）与片尾音频淡出合成为最终 MP4，自动抽帧导出 `work_dir/cover.jpg`（若 `promo_copy.json` 指定了封面秒数则优先采用），并输出 `timeline.json`（传 `--export-jianying` 可将视频、旁白、BGM、字幕与包装图片导出为剪映草稿）。若旁白在微加速上限内仍装不进可用时间窗，直接报 `no_safe_fit` 阻断，不截断尾音。声音模式与路由规则见 `references/audio-modes.md`。

### 4.5 多视频连剪与资源库

多视频输入仅支持 `cut` 模式，项目级简报会列出各片源的 `source_id`，`clip_plan.json` 中每条片段均须填写 `source_id`：

```bash
python3 scripts/recap.py ep1.mp4 ep2.mp4 --edit-mode cut --target-duration 10m --work-dir work_dir_multi_ep
```

可通过 `--material-library-dir .video-materials --save-materials` 与 `--use-materials` 跨集复用单源理解缓存；同一库根目录下还可登记配乐、音效、音色、字体、贴图及可复用模板（`subtitle_style`、`packaging`、`production_reference`），用 `scripts/library.py check|list|show` 查询并通过 `--project recap_project.json` 绑定，合成后自动生成 `work_dir/resource_lock.json`（详见 `references/resource-library.md`）。测量原片硬字幕纵向遮罩区间可查看抽帧图片，或在源码仓库下运行 `tools/measure_subtitle.py <video>` 后传入 `--subtitle-y-top` 与 `--subtitle-y-bot`。

### 4.6 最终观看与交付复核

交付前必须直接打开**最终成片文件**完成视听复核：

1. **原速通看**：不倍速从头看到尾，记下画面跳脱或节奏拖沓处。
2. **检查拼接点**：抽查切点前后 0.5–1 秒，排查闪帧、动作跳接与半句截断的原声。
3. **纯听音轨**：闭眼听一遍，确认口播连贯自然、原声闪避过渡平顺、关键台词清晰。
4. **复核开场、高潮与收尾**：单独重看开场前 5 秒、中段转折戏眼与片尾收束，确认切入时机与余韵立得住。
5. **返修核对与 `final_qc.json` 检查**：`REVISION` 模式确认修改项已落实且冻结项未回退；核对 `final_qc.json` 的解码、时长与规格结果，若 `metadata.warnings` 非空（如 `partial_tts` 或缺少 `libass` 导致 `subtitle_burn_degraded` 降级为外挂 `.srt`），须如实告知用户。

### 4.7 不需要解说的原声成片

```bash
# 对当前整段输入直接混音合成；不隐式跑理解、ASR 或 TTS
python3 scripts/recap.py locked_picture.mp4 --work-dir source_work --audio-mode source-mix
# 按 cut 流程生成剪辑计划，剪完直接合成原声成片，不再暂停等待 narration.json
python3 scripts/recap.py ep1.mp4 ep2.mp4 --edit-mode cut --work-dir cut_work --audio-mode source-mix
```

`source-mix` 同样执行原声响度归一、可选 BGM 混音、片尾淡出与音频重编码；切换音频模式须换用新工作目录。详见 `references/audio-modes.md`。

## 5. 自检与只读看板

```bash
python3 scripts/doctor.py
python3 scripts/dashboard_server.py --root <目录> [--port 0] [--open]
```

`doctor.py` 检查本机 `ffmpeg` / `ffprobe`、字幕滤镜与远程服务密钥配置；`dashboard_server.py` 在本地 `127.0.0.1` 启动只读审片台（仅响应 `GET` 与 `HEAD`），浏览 `--root` 下的成片目录、解说时间轴与素材库。

## 6. 输出与参数

核心交付物：

- `recap_<video>.mp4`：最终解说或原声成片。
- `subtitles.srt` / `subtitles.ass`：生成的字幕文件。
- `work_dir/`：中间分析、创作计划与校验产物，字段契约见 `references/data-schema.md`。
- `work_dir/recap_story_plan.json` 与 `visual_audio_board.json`：Agent 编写的故事规划与分镜声画表。

命令行参数以 `python3 scripts/recap.py --help` 及各阶段脚本 `--help` 为准；`--style` 作为自由文本原样传给写稿 Agent，不映射为固定枚举。

## 7. 能力边界

- 确定性底线检查（残镜闪帧、台词截断、旁白抢断或半句释放原声、`full` 模式字数硬上限、超长削尾、必保画面丢失、占位符泄漏、解码与封装规格）作为硬门禁运行。
- 故事立意、开场节奏、留白长短、角色点名密度与口播文风不设机械打分门禁，由 Agent 结合画面证据、原声时间轴与 `references/creative-editing-playbook.md` 自主把控并经成片视听复核确认。
