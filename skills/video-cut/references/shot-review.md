# 成片内部短镜头与密集切镜召回

`shot_review.py` 是只读检查器，不改剪点、不触发吸附、不重编码视频，也不把短镜头自动删掉。
EDL 的一个长区间内仍可能藏着 2、12、14 帧反打；因此检查对象是**本轮实际渲染的视频**，不是只看计划里每段有多长。

```bash
# 默认 --threshold 0.35：
python3 scripts/shot_review.py edited_source.mp4 --output review/shot_review.json
# 只有当前 video-cut 的输出与 sidecar 齐全，才额外关联当前剪辑计划：
python3 scripts/shot_review.py edited_source.mp4 --plan clip_plan_validated.json \
  --output review/shot_review_planned.json
# 怀疑漏检时另跑一轮更低阈值做对照；0.08 只是一个更高召回的取值示例，不是规定的第二遍：
python3 scripts/shot_review.py edited_source.mp4 \
  --threshold 0.08 --output review/shot_review_sensitive.json
# 有黑边/包装时，按实测主画窗填入像素坐标，保持阈值不变做对照：
python3 scripts/shot_review.py packaged.mp4 --roi "$X" "$Y" "$WIDTH" "$HEIGHT" \
  --output review/shot_review_picture.json
# 正常 cut 后显式开启；缓存命中也查实际文件；normalize-only 不扫描：
python3 scripts/cut.py source.mp4 --work-dir work --review-shots
# 同一画窗/阈值参数也可传给 cut.py --review-shots --shot-roi X Y WIDTH HEIGHT --shot-scene-threshold T
```

`--roi` 仅裁检测输入，不改视频文件或剪辑计划。坐标采用 FFmpeg 自动转正后的原生像素，
不是播放器按 SAR 拉伸后的显示尺寸；矩形需完整位于画布内。报告的 `scene_roi` 保存实际
检测范围，未指定时为 `null`（全画布）。先排除黑边及标题/花字区，再调召回阈值；
同时保留全画布或更大范围的对照，不能通过缩小范围隐藏问题。画窗随镜头变化时，
一个固定 ROI 只能覆盖该区域，不能据此宣称已检查所有画面。

## 精度和边界

- `ffprobe` 完整解码收集真实帧 PTS；scene filter 使用整数 PTS 与过滤器 timebase，不用 seek 后的浮点 `pts_time` 反推帧号。
- 帧号从 0 开始，区间半开 `[start_frame,end_frame)`；首镜、末镜也检查。VFR 同时记录真实 rational PTS 时长，不能用平均 fps 乘秒数。
- 默认候选规则：镜头不超过 **1 秒**，或 **2 秒内至少 4 个切点**。帧数上限缺省由实测帧钟换算（`round(fps × max_short_seconds)`），不写死某个帧率；需要固定帧数时用 `--max-short-frames` 显式覆盖。参数只控制召回，不是统一剪辑标准。
- `0.35` 是默认 scene 起始阈值。加黑边、包装占比大、低反差的画面会系统性少报。**某个阈值下零候选不是没有闪帧的证据。** 用一段已知有坏短镜的窗口校准阈值，保留每一轮的报告，不静默换阈值只报告“通过”。降低阈值也可能增加曝光、运动和动效误报。
- 未提供计划时来源为 `UNKNOWN`。提供计划时先复核计划、源文件、渲染设置和实际输出指纹；失配直接失败，不猜“最新版本”。
- 靠近量化后 EDL 拼接点一帧以内仅标 `EDIT_JOIN_CANDIDATE`。内部候选可以标所在已绑定 clip、估计原片秒数，但仍为 `UNKNOWN`；没有独立源片证据，不能认定是原片自带切镜。
- 未知时码、末帧时长无法覆盖到流末尾、解码失败、扫描期间媒体/计划变动均阻断扫描。报告先置 `SCANNING`，失败写 `SCAN_FAILED`，不留下旧成功报告冒充本轮结果。仅新路径或可识别为本工具 schema 的旧报告可写；报告路径不能覆盖计划与元数据双方声明的视频/源文件，即使二者已经过期失配。

## 如何处理候选

对每个候选看前后 0.5–1 秒，核对角色表演、完整动作、对白与原片切镜；未实际播放就保留 `NEEDS_DYNAMIC_REVIEW`。
先对照源画面和取景参数：同一原生镜头中途换 crop/画窗，或曝光闪光，都可能产生切点分数；两个候选间的帧数不一定是一段真实短镜头。
修复后用同样参数重跑对照：候选消失才算真的修掉；仍被保留的短镜逐个判断，不因“短”就判错。
判断无关残镜后，在作者计划里删除整个无关镜头或恢复同源连续画面；相关反应可回原片扩完整，不能用定格补长、转场掩盖或只修导出文件。

`NO_CANDIDATES` 仅表示该阈值下没有短镜/密集切镜候选；不代表故事、听感、动态观感或发布授权通过。报告始终保留 `normal_speed_review=NOT_CHECKED`，不会写入已有 `clip_plan_validated.json['qc']`。
