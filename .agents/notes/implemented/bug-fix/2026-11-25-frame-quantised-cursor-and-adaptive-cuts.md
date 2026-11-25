# Agent Note: 时间游标按整帧累加、原声音频同口径裁切；切点检测改为逐帧分数 + 局部中位数

Status: implemented

## Problem

- **长片音画漂移**：`render.py` 每个镜头的画面按 `round(dur*fps)` 取整帧，但 `_common.timeline_video_spans` 的游标和原声 `atrim` 用未取整的秒数。几百段累计误差约为 √(N/12) 帧（700 段约 7–8 帧、0.3 秒），后段口型漂移，`check.py av_sync` 可能硬失败；旁白 `at` 也按漂移后的游标对位。
- **切点漏检**：`probe.py --scenes` 用 ffmpeg `scdet` 全局阈值 0.15。在长剧集上对照人工核过的切点，召回只有约 0.57；阈值调到 0.05 召回约 0.89。漏掉的切点会让「一个镜头内」的区间实际跨镜，出现闪帧，原声段贴切点也会漏贴。底部烧录字幕换行还会被误判。

## Decision

- `timeline_video_spans` 用画布帧率把每段时长折成整帧（`frame_count(dur, fps) / fps`）后累加，`timeline_start/end` 与渲染画面一致；`render.py` 的原声 `atrim`、淡出、静音段都用这一整帧时长（`apad` 补齐），画面与声音逐段等长。
- `detect_scenes` 改为：ffmpeg 裁掉底部 10%、缩到 96×54，`select=gte(scene,0),metadata=print` 输出每帧场景分数；某帧分数不低于 `--scene-threshold`（缺省 0.08）且大于前后各 15 帧中位数的 3 倍加 0.02，记为切点（相邻切点至少隔 0.12 秒）。同样对照下召回约 0.92、精度约 0.86，40 分钟素材约 20 多秒。

## Alternatives considered

- **只调低 `scdet` 阈值**——最强理由：一行改动。否：阈值 0.05 召回仍只有约 0.89，且全局阈值在快动作段误报、在低反差切镜漏报；局部中位数同时改善两头。
- **纯 Python 逐帧算像素差**——最强理由：完全掌控算法。否：标准库逐像素运算一集要分钟级；ffmpeg 自带的场景分数已足够，Python 只做判决。
- **游标保持浮点、渲染改成不取整**——最强理由：时间轴字段更「精确」。否：画面只能按整帧出，浮点游标必然与实际画面错开。

## Consequences

- **收益**：长片音画、旁白对位不随段数漂移；切点召回从约 0.57 提到约 0.92，`frames.py --shots` 和 `plan.py` 的贴切点有了可靠依据。
- **代价**：`timeline_end` 与 `in/out` 之差可能相差不到一帧（按整帧取整）；`--scene-threshold` 的含义从 scdet 阈值变为逐帧分数下限（缺省 0.15 → 0.08），精度略降，多出的假切点只会把镜头多切一段。
