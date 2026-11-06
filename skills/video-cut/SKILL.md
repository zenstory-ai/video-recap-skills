---
name: video-cut
user-invocable: false
description: 把长视频按 Agent 选择的原片区间剪成短片。作为两阶段创作流程中的剪辑环节，读取 clip_plan.json 与源视频， 输出 edited_source.mp4；随后 Agent 按输出时间线写 narration.json。支持单视频与多视频（sources manifest）拼剪， 本工具不读取、不映射旁白。 触发词：视频剪辑、剪辑式解说、video cut、clip plan、拼剪。
---

## 1. 定位

本技能只执行 Agent 已经定下的选段决定，不做剧情判断：

1. 校验并补全 `clip_plan.json`，写出带 `clip_id`、原片起止时间、输出起止时间与时长的 `clip_plan_validated.json`。
2. 先避开原片硬切点附近的残帧闪烁，再把片段边界吸附到可靠的句末或自然停顿上；原声台词不被腰斩拥有最高优先级。
3. 把每个入点对齐到源视频帧网格、每段时长对齐到整数个输出帧。对齐位移不足一帧时，优先选择句界门禁仍判安全、依然落在停顿内的一侧；两侧同样安全时选择不跨过原片硬切的一侧，避免多闪一帧。句界门禁最终检查的是帧对齐后的实际边界。
4. 拼接选定区间，输出恒定帧率的 `edited_source.mp4`，保证实际总帧数与 `clip_plan_validated.json` 完全一致。
5. 剪完即止，交由 Agent 对照真实的剪后时间线编写 `narration.json`；本工具不读取旁白，也不把原片时间自动映射成输出时间。

渲染缓存按输入内容与渲染设置复用：`edited_source.mp4.meta.json` 记录标准化后的片段列表、渲染参数以及每个源文件的 `{size, mtime_ns}`；三者全部一致且 `edited_source.mp4` 存在非空时直接复用，任一变化即重新渲染。只有元数据没有媒体文件时不复用。

## 2. 输入契约

`work_dir/clip_plan.json` 可以直接写片段数组，也可以写 `{"clips": [...]}`：

```json
{"start": 12.0, "end": 28.5, "reason": "b02 | turn | 质检员当众停线自证 → 厂长被迫签字 | POV=林工 | 保留拍表停机的瞬间 | 入点=扳手砸上铁台 | 出点=厂长低头落笔"}
```

- `start` / `end` 填写原片秒数；也兼容 `source_start` / `source_end` 或 `in` / `out`。
- 顶层可选填 `target_duration`（如 `"10m"`）。
- 多视频项目的每个片段还必须填写 `source_id`（不接受用 `id` 代替），并通过 `--sources-manifest` 传入来源清单。
- `speech_boundary_anchors.json` 与 ASR 时间窗由理解阶段提供。Agent 先写大致区间，工具会尝试把边界吸附到停顿处；若吸附后入点或出点仍切在说话中途，则作为阻断项（blocker）返回。只含语气词或 ASR 杂音的窗口（如 `"啊！"`、`"Hi."`）不算说话区间，只在紧挨真实对白的一侧保留 1 秒保护区；仅含标点的窗口（如 `"……"`）仍按说话区间保护。

多视频来源清单只接受以下固定结构，传入其他结构会直接报错并提示期望格式：

```json
{"sources": [{"source_id": "ep1", "source_path": "/media/ep1.mp4", "duration": 1520.0, "source_work_dir": "sources/ep1"}]}
```

`duration` 可省略（省略时由 `ffprobe` 实测）；`source_work_dir` 可省略，填写时相对于 `--work-dir` 解析，用于读取该来源专属的静音区间、句末锚点与 ASR 结果。其他未定义键会被忽略。

## 3. 剪辑意图契约

Agent 动笔写选段前先明确剪辑意图，让每个片段对应 `recap_story_plan.json` 中的一个叙事节点（beat）；工具负责机械执行与确定性校验。

在自由文本字段 `reason` 中用 7 段竖线格式写清选段依据：

```text
beat_id | function | change | POV | preferred moment | 入点 reason | 出点 reason
```

不要因为“这场戏重要”就把整场戏拖进来；只截取最能让人看懂处境翻转的具体动作、眼神反应或关键台词。在观众能看懂、情绪能接住的前提下晚进早出，同时保证台词、动作起落与镜头边界完整。

对于全片决不能剪丢的核心问答、当场反应或动作结果，先核准原片时间码，再在同一份 `clip_plan.json` 的 `required_evidence` 中登记精确区间：

```json
{
  "clips": [{"start": 12, "end": 18}],
  "required_evidence": {
    "nodes": [
      {"id": "refusal", "source": "/media/episode.mp4", "start": 12.25, "end": 14.5, "track": "audio", "content": "对方当面回绝请求"},
      {"id": "response", "source": "/media/episode.mp4", "start": 15, "end": 17.5, "track": "video", "content": "听到回绝后冷笑转身"}
    ],
    "before": [["refusal", "response"]]
  }
}
```

`source` 填写源文件绝对路径，`start` / `end` 为原片秒数；多源项目可加填 `source_id` 消歧。只登记真正不可或缺的具体时刻，不要把整个叙事节点笼统锁死。`before` 只填写本片必须成立的先后因果顺序；无顺序要求时写 `"before": []`。

工具会在完成画面硬切避让与句界吸附后，逐条检查每个必保时刻是否在成片中至少完整连续出现一次、来源是否匹配、先后顺序是否正确；若是音频节点，还会核对源视频是否确实带有音轨。结果节点每出现一次（哪怕只截取了局部），其声明的前提节点都必须已经在前面完整出现过，不能拿成片后半段的完整重放替开头的断头片段过关。校验结果写入 `clip_plan_validated.json.qc.required_evidence`；缺段、错序或声明无效都会在预检、缓存复用与正式渲染前阻断，且不受 `--allow-duration-drift` 豁免。

以下 `scripts/...` 路径均相对于本技能目录；若从仓库根目录启动，请补齐本技能的目录前缀。

## 4. 运行命令

```bash
python3 scripts/cut.py <video> --work-dir <work_dir> [--clip-plan <clip_plan.json>] \
  [--sources-manifest <sources.json>] [--target-duration 10m] [--allow-overlap] \
  [--allow-duration-drift] [--normalize-only] \
  [--review-shots [--shot-scene-threshold 0.35] [--shot-roi X Y W H]]
```

- `--clip-plan`：剪辑计划路径，默认读取 `<work_dir>/clip_plan.json`。
- `--sources-manifest`：多源剪辑清单；每个片段通过 `source_id` 指明所属源视频，并按各自源视频的锚点吸附句界与画面切点。
- `--target-duration`：目标时长。实际剪出时长与目标时长之比超出 `0.85–1.15` 记 warning，超出 `0.60–1.40` 直接阻断。
- `--allow-duration-drift`：仅将时长超限阻断降级为 `allowed: true` 的 warning，绝不放行句界切词或必保证据缺失。
- `--normalize-only`：只执行标准化、边界吸附与门禁检查，写出 `clip_plan_validated.json` 后立即退出，不渲染视频。
- `--review-shots`：对本次渲染或命中缓存的 `edited_source.mp4` 扫描短镜头与密集切镜候选，只生成报告、不自动改计划；`--shot-scene-threshold` 为召回阈值（默认 `0.35`，仅控制召回灵敏度，不是放行标准），`--shot-roi` 仅扫描指定像素矩形（适合带黑边或固定边框的画面），不裁切输出视频。

剪辑被阻断时命令以非零状态码退出，并把具体原因写入 `clip_plan_validated.json` 的 `qc.blocking`：

- `unsafe_clip_sentence_boundary`：片段入点或出点仍切在原声说话中途；逐边界详情见 `qc.boundary_status.sentence_checks`。每个被阻断的边界都会给出 `nearest_safe: {"before", "after"}`，列出前后 5 秒内最近的安全落点 `{time, reason, delta}`（`time` 为已对齐到源视频帧网格并复核通过的原片秒数，`delta` 为相对当前边界的偏移秒数，无可用落点时为 `null`；即使下次重跑时镜头切点避让试图把它拉回说话区间，该避让也会被自动撤回并在 `qc.boundary_status.shot_snaps` 记为 `reverted_unsafe`，因此照抄 `nearest_safe.time` 写回 `clip_plan.json` 不会再次触发句界阻断）。入点选 `before` 是多留画面、选 `after` 是往后裁；出点相反。修改前先确认不会切掉必保内容或与相邻片段重叠；若 `before` 和 `after` 都是 `null`，说明附近 5 秒内根本没有停顿，需要重选区间而非微调。
- `target_duration_drift`：剪出总时长偏离目标时长过多；明细见 `qc.target_duration`。
- `REQUIRED_EVIDENCE_INVALID` / `REQUIRED_EVIDENCE_MISSING` / `REQUIRED_EVIDENCE_ORDER` / `REQUIRED_EVIDENCE_AUDIO_UNAVAILABLE`：必保证据声明格式有误、片段缺失、先后颠倒或源文件无音轨；明细见 `qc.required_evidence`。

## 5. 输出契约

- `clip_plan_validated.json`：标准化后的剪辑计划，包含 `clip_id`、`source_start/end`、`output_start/end`、`duration` 与 `frame_count`（该片段实际渲染的帧数）。`qc.frame_grid` 记录输出帧率 `output_frame_rate`（单源沿用源帧率，多源统一到画布帧率，NTSC 记为 `30000/1001`）、总帧数与各源帧率；每次帧网格对齐前后的时间变化记录在 `qc.boundary_status.frame_snaps`。
- `edited_source.mp4`：按计划拼接出的恒定帧率视频，实际总帧数严格等于 `qc.frame_grid.frame_count`。
- `shot_review.json`：开启 `--review-shots` 时生成的成片短镜头与密集切点复核报告，不反向修改剪辑计划。

下游阶段直接把 `edited_source.mp4` 当作源视频，把 Agent 对照剪后时间线编写的 `narration.json` 当作解说稿。

## 6. 边界与时间线规则

- `clip_plan.json` 一律填写原片时间；`narration.json` 一律直接填写剪后成片的输出时间，中间不存在原片时间到输出时间的旁白自动换算。由于帧网格对齐会把边界微调不到一帧的距离（25fps 下不超过 40 ms），写旁白前必须以 `clip_plan_validated.json` 里的 `output_start/end` 为准，不要拿手写的原始区间自己累加推算。
- 如果片段边界不落在帧网格上，拼接时每个接点都会掉半帧（25fps 下画面会卡顿约 80 ms，还会让成片变成可变帧率），因此帧网格对齐不可关闭。源视频帧率未知（`r_frame_rate` 为 `0/0` 或大于 120）时入点保持不动，但每段时长仍对齐到整数个输出帧。
- 默认禁止片段重叠或重复使用同一段原片区间；确需重用时显式加 `--allow-overlap`。
- 片段入点只能落在源片开头、可靠句末/静音窗，或与上一段构成同源连续画面；出点同理。只要 ASR 判定边界仍切在说话中途且无法吸附到安全停顿，就会报 `unsafe_clip_sentence_boundary` 并阻断。
- `SCENE_CUT_SNAP` 默认开启：先在 `SCENE_CUT_SNAP_MARGIN=0.5` 秒范围内（检测阈值 `SCENE_CUT_DETECT_THRESHOLD=0.4`）把入点往后、出点往前吸附到附近的原片硬切点，消除接点处一闪而过的残帧，随后再由句末吸附做最终把关，防止画面避让把边界重新拉回半句话里；若附近没有停顿、句末吸附救不回来，只要原位置本身能过句界门禁，刚才那次把边界拉进说话区间的画面避让就会自动撤回。
- 画面切点分数（scene-change score）只能用来召回可疑接点，不能单靠分数高低判定接点好坏。先看短时间内是否扎堆出现切点候选，再按来源分别处理：原片自带的无关短镜头整段删除；与剧情相关但短得像闪帧的镜头，回原片外扩入点或出点，把动作、反应或台词留完整，绝不用定格或慢放硬凑时长；由本次拼接制造的切点，则优先挪动边界、恢复同源连续动作、合并相邻片段或改用更顺畅的接法。成片渲染后仍要按原速逐个回看接点前后 0.5–1 秒；遇到闪白或曝光叠化再结合逐帧亮度排查，绝不能为了压低视觉检测分数而腰斩完整台词，也不能用转场特效遮掩坏接点。
- 清理碎镜头时，严禁只为压低切点分数而对接点附近几帧做突兀的局部放大或移位；取景复核与修复验证流程见 `references/shot-review.md`。
- 同源连续片段的无缝拼接不在接点两侧做音频淡入淡出，以免句中音量凹陷；非连续拼接则仍在安全停顿内做短促的防爆音淡入淡出。

需要排查成片里是否有短时间密集切镜时，可先用 ffmpeg 的 scene 滤镜召回候选时刻：

```bash
ffmpeg -i input.mp4 -vf "select='gt(scene,0.35)',showinfo" -an -f null -
```

`0.35` 只是初筛阈值而非验收红线，大幅动作、闪白和叠化都可能触发高分。把召回的时刻对照回原片镜头与本次拼接点后，按上述分类处理，并以原速播放画面做最终定夺。

需要精确到帧号、排查长片段内部夹带的碎镜头并绑定剪辑计划留档时，使用 `scripts/shot_review.py` 或 `cut.py --review-shots`（画面带黑边或固定包装框时加 `--roi` / `--shot-roi`）；详见 `references/shot-review.md`。

## 7. 能力边界

- 不做剧情理解，不写旁白，不替 Agent 挑片段；scene 滤镜只负责召回技术边界候选。
- 只负责把选定原片区间切出并拼接成 `edited_source.mp4`，不烧录字幕、不加包装，也不做最终交付压制。
