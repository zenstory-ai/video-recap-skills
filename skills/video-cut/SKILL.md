---
name: video-cut
user-invocable: false
description: >
 把长视频按 Agent 选择的原片区间剪成短片。作为两阶段创作流程中的剪辑环节，读取 clip_plan.json 与源视频，
 输出 edited_source.mp4；随后 Agent 按输出时间线写 narration.json。单独调用且未传 --no-narration-map 时，
 仍支持旧版单阶段路径，把原片时间的 narration.json 映射为 narration_mapped.json。
 触发词：视频剪辑、剪辑式解说、video cut、clip plan、拼剪。
---

## 1. 定位

本技能只执行 Agent 已经做出的剪辑决定：

1. 校验并补全 `clip_plan.json`，写出带 `clip_id`、原片/输出时间与时长的 `clip_plan_validated.json`。
2. 先避开原片硬切附近的闪帧风险，最后把边界吸附到可靠句末/自然停顿；声音完整性拥有最终优先级。
3. 拼接选定区间，输出 `edited_source.mp4`。
4. 编排流程默认到此停止，由 Agent 按真实输出时间线写 `narration.json`。
5. 旧版单阶段路径还会把原片时间的旁白映射为 `narration_mapped.json`。

相同输入会得到相同输出。缓存按源文件完整内容指纹、标准化计划、渲染设置及输出文件身份验证；只看 `mtime` 或只有 sidecar 而没有媒体文件都不足以复用。

## 2. 输入契约

`work_dir/clip_plan.json` 可以是数组，也可以是 `{"clips": [...]}`：

```json
{"start": 12.0, "end": 28.5, "reason": "b02 | turn | power: A→B | POV=女主 | 保留反应 | 入点=问题落下 | 出点=沉默结束"}
```

- `start` / `end` 是原片秒数；也接受 `source_start` / `source_end` 或 `in` / `out`。
- 顶层可选 `target_duration`，例如 `"10m"`。
- 多视频项目的每个片段还必须填写 `source_id`。
- `speech_boundary_anchors.json` 与 ASR 时间段由理解阶段提供；Agent 先写大致区间，工具会尝试吸附并把仍在讲话区间内的入/出点作为 blocker 返回。

`work_dir/narration.json` 只在旧版单阶段路径中可选读取；该路径要求旁白使用原片时间。若允许重复或重叠片段，旁白可带 `source_clip_id` 消歧。

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

下面的 `scripts/...` 均相对于本技能目录。若执行器从仓库根目录启动，请给脚本路径加上本技能的绝对目录。脚本不从其他技能目录读取文件；外部输入仅限命令显式传入的视频、参数与 `work_dir` 产物。

## 4. 运行命令

```bash
python3 scripts/cut.py <video> --work-dir <work_dir> \
  [--target-duration 10m] [--clip-padding 0] [--allow-overlap]
```

## 5. 输出契约

- `clip_plan_validated.json`：标准化片段，包含 `clip_id`、`source_start/end`、`output_start/end` 与 `duration`。
- `edited_source.mp4`：按计划拼接后的短视频。
- `shot_review.json`：仅 `--review-shots` 开启后生成的实际视频短镜/密集切镜候选；不会更改计划。
- `narration_mapped.json`：仅旧版单阶段路径生成；编排流程使用 `--no-narration-map`，不会生成该文件。

编排流程下游把 `edited_source.mp4` 当作视频，把 Agent 按输出时间写的 `narration.json` 当作旁白。

## 6. 边界与时间线规则

- 旧版路径中，`clip_plan.json` 与 `narration.json` 都使用原片时间；本工具负责原片 → 输出映射。
- 编排路径中，`narration.json` 直接使用剪后输出时间，不再映射。
- 默认禁止重叠或重复原片区间；`--allow-overlap` 开启后，旁白应填写 `source_clip_id`。
- 片段起点只能位于源头、可靠句末/静音窗，或与上一片段构成无损同源连续连接；片段终点同理。ASR 判定仍在讲话且无法吸附时写入 `unsafe_clip_sentence_boundary` 并阻断。
- `SCENE_CUT_SNAP` 默认开启：先按画面把 source start 向后、source end 向前吸附到附近硬切，随后句末吸附再做最终修正，避免视觉修正重新制造半句原声。默认范围为 `SCENE_CUT_SNAP_MARGIN=0.5` 秒，检测阈值为 `SCENE_CUT_DETECT_THRESHOLD=0.4`。
- scene-change score 只提供接点候选，不证明接点自然。先检查短时间窗内是否出现密集候选，再区分来源：原片自带的无关短镜头整段删除；相关但短到像闪帧的镜头通过扩展 IN/OUT 保留完整动作、反应或台词，不用定格/慢放伪造时长；由本次拼接制造的切点则优先移动边界、恢复同源连续运动、合并相邻片段或改用更自然的连接，尽量消除。成片后仍要逐个播放接点前后约 0.5–1 秒；白闪或曝光叠化再结合逐帧亮度定位，不能为了通过视觉检测切断完整台词，也不能用转场遮掩坏接点。
- 修短残镜时，同时核对原片镜头变化与输出 `crop/window` 变化。不得仅为压低 scene 分数，对接点前后少量帧施加与所属镜头不连续的极端放大或位移，造成关键表情、动作被裁或清晰度明显下降。先在原片自然切点上分别确定相邻镜头各自连续、清晰、主体完整的取景；不同镜头不要求景别或裁幅一致，但尺度变化、主体位置及视线/运动方向须在正常速度下复核。保存修复前后相同输出帧号的邻帧对照，再核声音、字幕、总帧数与播放速度。候选数量或 scene 分数下降，只说明检测结果改变，不证明接点已经修复；没有正常速度观看能力时保留未检查状态。
- 连续同源片段的无损连接不做句中双侧音频淡出；非连续片段仍在安全停顿内做防爆音淡入淡出。
- 旧版旁白映射若跨越 clip 边界，不再裁短后继续：`clamped_beats` 永久阻断，`--allow-sparse-cut` 也不能绕过旁白句子完整性。

需要检查短时间频繁切镜时，先用 ffmpeg scene filter 召回候选时间：

```bash
ffmpeg -i input.mp4 -vf "select='gt(scene,0.35)',showinfo" -an -f null -
```

`0.35` 是起始阈值，不是质量判据；大幅运动、闪白和叠化都可能误报。把候选映射回原片 shot 与本次拼接边界后，按上面的来源分类处理，并以正常速度播放决定是否保留。

需要精确到实际帧、检查长区间内部残镜并保存版本绑定证据时，使用
`scripts/shot_review.py` 或 `cut.py --review-shots`；详见 `references/shot-review.md`。
有黑边或包装文字时，可用 `shot_review.py --roi X Y WIDTH HEIGHT`（剪辑入口为
`--shot-roi`）按实测画窗扫描，避免黑边稀释分数或文字变化干扰。低反差仍可能漏检，
零候选不是“没有闪帧”的证明。

## 7. 能力边界

- 不重新转写，也不做人物、剧情或情绪等语义理解；scene filter 只承担技术边界候选检测。
- 不写旁白，也不替 Agent 选择片段；只消费 `clip_plan.json`。
- 只做生成 `edited_source.mp4` 所需的剪切、拼接与一次中间编码，不承担字幕包装或最终交付压缩。
