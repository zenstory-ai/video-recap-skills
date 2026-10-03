---
name: video-cut
user-invocable: false
description: >
 把长视频按 Agent 选择的原片区间剪成短片。作为两阶段创作流程中的剪辑环节，读取 clip_plan.json 与源视频，
 输出 edited_source.mp4；随后 Agent 按输出时间线写 narration.json。支持单视频与多视频（sources manifest）拼剪，
 本工具不读取、不映射旁白。
 触发词：视频剪辑、剪辑式解说、video cut、clip plan、拼剪。
---

## 1. 定位

本技能只执行 Agent 已经做出的剪辑决定：

1. 校验并补全 `clip_plan.json`，写出带 `clip_id`、原片/输出时间与时长的 `clip_plan_validated.json`。
2. 先避开原片硬切附近的闪帧风险，再把边界吸附到可靠句末/自然停顿；声音完整性拥有最终优先级。
3. 把每个入点对齐到源视频帧网格、每段时长对齐到整数个输出帧（不足一帧的移动，优先选句界门禁仍判为安全、仍在停顿内的一侧；两侧同样安全时选不跨过原片硬切的一侧，避免闪一帧），句界门禁检查的是对齐后的边界。
4. 拼接选定区间，输出恒定帧率的 `edited_source.mp4`，帧数与 `clip_plan_validated.json` 记录的一致。
5. 到此停止，由 Agent 按真实输出时间线写 `narration.json`；本工具不读取旁白，也不做原片→输出映射。

相同输入会得到相同输出。`edited_source.mp4.meta.json` 记录标准化 clips、渲染设置和每个源文件的 `size`/`mtime_ns`；三者与当前一致且 `edited_source.mp4` 存在非空才复用，任一不同即重渲染。只有 sidecar 而没有媒体文件不复用。

## 2. 输入契约

`work_dir/clip_plan.json` 可以是数组，也可以是 `{"clips": [...]}`：

```json
{"start": 12.0, "end": 28.5, "reason": "b02 | turn | power: A→B | POV=女主 | 保留反应 | 入点=问题落下 | 出点=沉默结束"}
```

- `start` / `end` 是原片秒数；也接受 `source_start` / `source_end` 或 `in` / `out`。
- 顶层可选 `target_duration`，例如 `"10m"`。
- 多视频项目的每个片段还必须填写 `source_id`（不接受 `id` 代替），并用 `--sources-manifest` 传入来源清单（形状见下）。
- `speech_boundary_anchors.json` 与 ASR 时间段由理解阶段提供；Agent 先写大致区间，工具会尝试吸附并把仍在讲话区间内的入/出点作为 blocker 返回。只含语气词或 ASR 杂音的窗口（"啊！"、"Hi."）不算讲话区间，只在紧挨真实对白的一侧保留 1 秒保护。只有标点的窗口（"……"）仍算讲话。

多视频来源清单只接受一种形状，其他形状直接报错并写明期望形状：

```json
{"sources": [{"source_id": "ep1", "source_path": "/media/ep1.mp4", "duration": 1520.0, "source_work_dir": "sources/ep1"}]}
```

`duration` 可省略（省略时用 ffprobe 读取）；`source_work_dir` 可省略，填写时相对 `--work-dir`，用于读取该来源的静音、句末锚点与 ASR。其他键忽略。

## 3. 剪辑意图契约

工具不会替 Agent 做创作选择。写片段前先完成本节的剪辑意图检查，并让每个区间映射到 `recap_story_plan.json` 的一个 beat。

使用现有自由文本 `reason` 保存简洁决定：

```text
beat_id | function | change | POV | preferred moment | 入点 reason | 出点 reason
```

不要因为“事件重要”就保留整段；要保留最能让 change 成立的具体表演、反应、动作或揭示。理解与情绪允许时晚进早出，同时保证台词、动作和技术边界完整。

对不能删去的问答、反应或动作兑现，先核源证据，再在同一 `clip_plan.json` 登记精确区间：

```json
{
  "clips": [{"start": 12, "end": 18}],
  "required_evidence": {
    "nodes": [
      {"id": "refusal", "source": "/media/episode.mp4", "start": 12.25, "end": 14.5, "track": "audio", "content": "对方拒绝请求"},
      {"id": "response", "source": "/media/episode.mp4", "start": 15, "end": 17.5, "track": "video", "content": "听到拒绝后的反应与决定"}
    ],
    "before": [["refusal", "response"]]
  }
}
```

`source` 使用实际源文件绝对路径，`start/end` 是原片秒；多源可另填 `source_id` 消歧。只登记确实需要保留的具体时刻，不将整个 beat 默认锁死。`before` 只登记本片必需的先后关系；无需约束顺序时写 `before: []`。

工具在全部画面/句界吸附后检查每个必保时刻至少有一处完整连续保留、来源和先后；音频节点还检查源音轨是否存在。每次结果出现（包括局部片段）都需满足其声明的前提，不能用后面的完整段替开头缺前提的片段过关。结果写入 `clip_plan_validated.json.qc.required_evidence`；缺段、错序或无效声明会在预检、缓存复用和渲染前阻断，时长放宽选项不会跳过。该结果验证选段保留，实际语义与最终混音仍按审片步骤核对。

下面的 `scripts/...` 均相对于本技能目录。若执行器从仓库根目录启动，请给脚本路径加上本技能的绝对目录。

## 4. 运行命令

```bash
python3 scripts/cut.py <video> --work-dir <work_dir> [--clip-plan <clip_plan.json>] \
  [--sources-manifest <sources.json>] [--target-duration 10m] [--allow-overlap] \
  [--allow-duration-drift] [--normalize-only] \
  [--review-shots [--shot-scene-threshold 0.35] [--shot-roi X Y W H]]
```

- `--clip-plan`：剪辑计划路径，默认 `<work_dir>/clip_plan.json`。

- `--sources-manifest`：多源剪辑的来源清单 `{"sources": [{"source_id", "source_path"[, "duration", "source_work_dir"]}]}`；片段用 `source_id` 指明来源，并按自己的来源吸附句界与画面切点。
- `--target-duration`：目标时长。实际时长与目标之比在 0.85–1.15 之外记 warning，在 0.60–1.40 之外阻断。
- `--allow-duration-drift`：只放行时长偏差阻断（记为 `allowed: true` 的 warning），不放行句界或必保证据阻断。
- `--normalize-only`：只标准化、吸附并检查计划，写出 `clip_plan_validated.json` 后退出，不渲染。
- `--review-shots`：在渲染或复用的 `edited_source.mp4` 上召回短镜与密集切点候选，只报告、不修复；`--shot-scene-threshold` 是召回阈值（默认 0.35，不是验收标准），`--shot-roi` 只扫描该像素矩形（有黑边或包装时用），不裁画面。

cut 阻断时以非零状态退出，并把原因写入 `clip_plan_validated.json` 的 `qc.blocking`，每项带 `code`：

- `unsafe_clip_sentence_boundary`：片段边界仍在原声讲话内；逐边界判定见 `qc.boundary_status.sentence_checks`。被阻断的边界带 `nearest_safe: {"before", "after"}`：前后 5 秒内最近的安全边界 `{time, reason, delta}`（原片秒；`delta` 为相对当前边界的秒数，没有则为 `null`；已是原片帧网格上的落点并复核过；重跑时切镜头避让若把它拉回讲话内，这次避让会被撤回，`qc.boundary_status.shot_snaps` 记 `reverted_unsafe`，所以原样写回不会再因句界被阻断），按它改 `clip_plan.json` 的 `start`/`end` 后重跑。入点往前（`before`）是多保留、往后（`after`）是裁掉，出点相反；先确认改动不会切掉必保内容或与相邻片段重叠。两侧都是 `null` 说明附近没有停顿，要换区间而不是微调。
- `target_duration_drift`：时长偏差超出阻断阈值；明细见 `qc.target_duration`。
- `REQUIRED_EVIDENCE_INVALID` / `REQUIRED_EVIDENCE_MISSING` / `REQUIRED_EVIDENCE_ORDER` / `REQUIRED_EVIDENCE_AUDIO_UNAVAILABLE`：必保证据声明无效、缺段、错序或源无音轨；明细见 `qc.required_evidence`。

## 5. 输出契约

- `clip_plan_validated.json`：标准化片段，包含 `clip_id`、`source_start/end`、`output_start/end`、`duration` 与 `frame_count`（该片段渲染的帧数）。`qc.frame_grid` 记录输出帧率 `output_frame_rate`（单源沿用源帧率，多源用画布帧率，NTSC 写成 `30000/1001`）、总帧数与各源帧率；每次帧对齐的前后时间写在 `qc.boundary_status.frame_snaps`。
- `edited_source.mp4`：按计划拼接后的恒定帧率短视频，帧数等于 `qc.frame_grid.frame_count`。
- `shot_review.json`：仅 `--review-shots` 开启后生成的实际视频短镜/密集切镜候选；不会更改计划。

下游把 `edited_source.mp4` 当作视频，把 Agent 按输出时间写的 `narration.json` 当作旁白。

## 6. 边界与时间线规则

- `clip_plan.json` 使用原片时间；`narration.json` 直接使用剪后输出时间，不存在原片 → 输出的旁白映射。输出时间以 `clip_plan_validated.json` 为准：帧对齐会把边界移动不到一帧（25fps 下不超过 40 ms），写旁白前读 validated 计划，不要用自己写的原始区间推算。
- 边界不在帧网格上时，concat 会在每个接点丢掉一个帧位（25fps 下画面停顿 80 ms，成片变成可变帧率），所以帧对齐无法关闭。源帧率未知（`r_frame_rate` 为 `0/0` 或大于 120）时入点不动，时长仍对齐到整数输出帧。
- 默认禁止重叠或重复原片区间；`--allow-overlap` 开启后才允许。
- 片段起点只能位于源头、可靠句末/静音窗，或与上一片段构成无损同源连续连接；片段终点同理。ASR 判定仍在讲话且无法吸附时写入 `unsafe_clip_sentence_boundary` 并阻断。
- `SCENE_CUT_SNAP` 默认开启：先按画面把 source start 向后、source end 向前吸附到附近硬切，随后句末吸附再做最终修正，避免视觉修正重新制造半句原声；附近没有停顿、句末吸附修不回来时，把边界移进讲话的那次避让会被撤回（原位置能过门禁时）。默认范围为 `SCENE_CUT_SNAP_MARGIN=0.5` 秒，检测阈值为 `SCENE_CUT_DETECT_THRESHOLD=0.4`。
- scene-change score 只提供接点候选，不证明接点自然。先检查短时间窗内是否出现密集候选，再区分来源：原片自带的无关短镜头整段删除；相关但短到像闪帧的镜头通过扩展 IN/OUT 保留完整动作、反应或台词，不用定格/慢放伪造时长；由本次拼接制造的切点则优先移动边界、恢复同源连续运动、合并相邻片段或改用更自然的连接，尽量消除。成片后仍要逐个播放接点前后约 0.5–1 秒；白闪或曝光叠化再结合逐帧亮度定位，不能为了通过视觉检测切断完整台词，也不能用转场遮掩坏接点。
- 修短残镜时不得仅为压低 scene 分数而对接点附近施加与所属镜头不连续的极端放大或位移；取景复核与修复验证流程见 `references/shot-review.md`。
- 连续同源片段的无损连接不做句中双侧音频淡出；非连续片段仍在安全停顿内做防爆音淡入淡出。

需要检查短时间频繁切镜时，先用 ffmpeg scene filter 召回候选时间：

```bash
ffmpeg -i input.mp4 -vf "select='gt(scene,0.35)',showinfo" -an -f null -
```

`0.35` 是起始阈值，不是质量判据；大幅运动、闪白和叠化都可能误报。把候选映射回原片 shot 与本次拼接边界后，按上面的来源分类处理，并以正常速度播放决定是否保留。

需要精确到实际帧、检查长区间内部残镜并记录所用计划路径时，使用
`scripts/shot_review.py` 或 `cut.py --review-shots`（有黑边或包装时加 `--roi` / `--shot-roi`）；
详见 `references/shot-review.md`。

## 7. 能力边界

- 不做语义理解，不写旁白，不替 Agent 选择片段；scene filter 只承担技术边界候选检测。
- 只做生成 `edited_source.mp4` 所需的剪切、拼接与一次中间编码，不承担字幕包装或最终交付压缩。
