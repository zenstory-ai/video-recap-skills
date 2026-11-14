# 成片内部短镜头与密集切镜复核

`shot_review.py` 是只读检查工具：只扫描并报告可疑切点，不改动剪辑计划、不触发边界吸附、不重编码视频，也不会自动删掉短镜头。即使剪辑计划里的某段区间长达十几秒，原片自身在这段区间内部仍可能夹着 2 帧、12 帧或 14 帧的碎反打镜头；因此必须直接扫描**本轮实际渲染出来的视频文件**，不能只看计划表上的片段时长。

```bash
# 默认按 --threshold 0.35 扫描：
python3 scripts/shot_review.py edited_source.mp4 --output review/shot_review.json
# 当剪辑输出视频与同目录 .meta.json 齐全时，可绑定剪辑计划一起核对：
python3 scripts/shot_review.py edited_source.mp4 --plan clip_plan_validated.json \
  --output review/shot_review_planned.json
# 怀疑低反差画面漏检时，可另跑一轮低阈值扫描作对照（0.08 仅为高灵敏度示例）：
python3 scripts/shot_review.py edited_source.mp4 \
  --threshold 0.08 --output review/shot_review_sensitive.json
# 画面带黑边或固定包装框时，传入实测主画面区域的像素坐标（ROI）做对照：
python3 scripts/shot_review.py packaged.mp4 --roi "$X" "$Y" "$WIDTH" "$HEIGHT" \
  --output review/shot_review_picture.json
```

`--roi` 只在检测阶段裁切送入滤镜的画面区域，不修改原视频或剪辑计划。坐标按 FFmpeg 自动旋转摆正后的原生像素填写，矩形必须完整落在画布内部；报告中的 `scene_roi` 会记录本次扫描区域（未指定时为 `null`）。排查黑边或包装干扰时，先用 `--roi` 对准主画面区域扫描，并保留全画布扫描报告对照；若主画面窗口随镜头切换而移动，单个固定 ROI 只代表该固定矩形内的检测结果。

## 精度与校验规则

- 工具通过 `ffprobe` 完整解码收集每一帧的真实 PTS，scene 滤镜直接使用整数 PTS 与滤镜时基对齐，不靠跳转（seek）后的浮点 `pts_time` 反推帧号。
- 帧号从 0 起算，区间采用左闭右开 `[start_frame, end_frame)`，片头首镜与片尾末镜同样纳入检查。可变帧率（VFR）视频直接累加每一帧的分数 PTS 时长。
- 默认召回条件为：单个镜头不超过 **1 秒**，或 **2 秒内出现至少 4 个切点**。短镜头帧数上限默认按实测帧率换算（`round(fps × 1 秒)`），可用 `--max-short-frames` 覆盖。`summary.flash_slivers` 会单独挑出时长 `<= 0.20s`（约 1–5 帧）的极短残镜闪帧，供 `cut.py` 与 `final_qc.py` 作为硬阻断项拦截；其余 `0.20s–1.0s` 的短镜头保留在 `short_spans` 中供动态审片复核，不机械误杀快节奏反应镜。
- `0.35` 是默认初筛阈值。带大片黑边、包装边框占比高或暗场低反差的画面在固定阈值下可能漏报，因此零候选时若怀疑暗场碎镜，应降低阈值复核；压低阈值也会同时召回闪光、急推拉与包装动效，需要结合原速回看甄别。
- 未传入 `--plan` 时，候选来源统一记为 `UNKNOWN`。传入 `--plan` 时，工具会核对计划片段、渲染参数是否与 `edited_source.mp4.meta.json` 一致、源文件 `{size, mtime_ns}` 是否未变、输出视频是否存在且非空；任一失配立即报错退出。落在帧对齐后的 EDL 拼接点前后 1 帧以内的候选记为 `EDIT_JOIN_CANDIDATE`；落在片段内部的候选标出所属 `clip_id` 与推算原片秒数，来源仍记为 `UNKNOWN`，需对照原片切点区分。
- 遇到时间戳缺失、末帧时长无法覆盖流末尾或解码报错时，扫描立即中止；报告启动时先写 `SCANNING`，失败改写 `SCAN_FAILED`，输出路径只允许指向新文件或本工具旧报告。

## 候选复核与修复流程

对召回的候选按原速回看前后 0.5–1 秒画面，核对人物表演、动作起落、台词完整性与原片切点（未回看前状态保持 `NEEDS_DYNAMIC_REVIEW`）。同一个原片镜头中途切换裁切窗口（crop）或遇上闪光灯也会产生高分，需先对照原片与取景参数核实。

- **无意义残帧或碎反打**：回到 `clip_plan.json` 删掉整段碎镜头，或恢复同源连续画面。
- **有信息量但被切碎的反应镜**：回原片外扩入点或出点把反应留完整，不用定格凑时长或加转场遮掩。
- **刻意保留的快节奏反应镜**：原速回看确认接点顺畅即可保留，不必因短于 1 秒机械删改。

修补碎镜头时须同时核对原片切点与输出裁切窗口（crop/window）：在原片自然切点两侧分别为前后镜头设定稳定、主体完整的取景框，不要为了压低邻帧 scene 分数对切点前后几帧做突兀局部缩放或平移。重新渲染后用相同参数重跑扫描并按原速核对声画同步（未做原速回看前 `normal_speed_review` 保持 `NOT_CHECKED`）。
