# 长篇连续解说（多集、几十分钟）

把多集剧情剪成 20–60 分钟「一口气看完」：按原片时间顺序讲完整条故事线，旁白段与完整原声对手戏交替。30–240 秒短片的切换次数、人名上限不适用；`recap` 段与 `plan.py` 短片也能用。

## 1. 素材笔记

- 有外部逐场描述（分镜/剧本稿：场次、画面动作、说话人）先读它作 `MEDIA_NOTES.md` 底稿，再抽拼图核对；没有就按集逐场写：时间段、地点、人物、画面事实（`frames.py --shots` 拼图看完再写）、关键台词（标说话人）。
- 只记看到听到的；推断单独标出，写稿时不当事实用。

## 2. 大纲与写稿

- 大纲、名场面清单、写稿和通读改稿见 `long-form-script.md`。改完的稿可用 `plan.py script timeline.json` 打出旁白连原声台词的全文来读。

## 3. `recap` 段与 `plan.py`

在 `timeline.json` 写 `recap`（`plan.py build` 据此覆写 `video`、`subtitles` 与旁白 `at`；可选 `crop` 套到每个镜头，如裁掉源片烧录字幕；单条也可写 `crop` 覆盖，如几个镜头带推广条）：

```json
{"transcripts": {"s1": "media/ep1.transcript.json"},
 "items": [{"type": "narr", "id": "n1", "seg": "hook", "source": "s1", "text": "他刚丢了工作，回老家却撞见当年的对头", "window": [10.0, 36.0], "shots": [[12.0, 14.6], [30.2, 33.0]]},
           {"type": "orig", "id": "o1", "source": "s1", "in": 40.1, "out": 52.6}]}
```

- `window` 写这句讲的事在原片里的时段（按笔记、转写的时间，讲哪几句台词前后就框哪段），画面只在这里挑。旁白按逗号切成小句，每个小句一个 `shots` 源区间（同一镜头内、够长）。
- 挑画面用 `frames.py --shots --window`：`--marks` 传原选各小句的时间（候选标 `*小句号`），`--notes` 传带时间的笔记或转写（每个候选附描述，以图为准），`--far` 放宽到整场（标 `far`）。原选对就不动，换镜头先在窗口里换；确实没拍到才改这一小句，改完仍讲故事、事实不变。候选短于小句时长时可放慢到 0.75 倍，够长的优先。
- 流程：`plan.py voice` → `tts.py --timeline timeline.json --update-timeline` → `frames.py <源> --shots media/probe.json --window <window>` 看候选定 `shots` → `plan.py build timeline.json --probe media/probe.json` → `render.py` / `check.py`。
- `build` 量每个小句的实测时长排镜头与字幕，原声段剪掉超过 1.2 秒的停顿、入出点贴原片切点。报 `ERROR`（镜头不到小句时长的 0.75、小句数不对）就改 `recap` 重跑；`HINT`（倒序、画面重复、原声段重叠或钩子原声在正文重放、旁白字数出 8–40、画面离窗口超过 45 秒、原声前一句的窗口伸进这段原声即先说破）逐条复核。

## 4. 验收

- `plan.py qa timeline.json --out qa` 均匀抽 40 个正文小句，每行一个小句的头、中、尾三帧（`sample.json` 对应小句）。逐条判断：小句说到的人在画面里、动作物件情绪看得到、回忆与现在一致才算对上；对上不少于 36 个，且 `check.py` 无硬失败、无闪帧与画面重复。
- 再完整看开头 3 分钟和随机 3 段原声前后各 30 秒：衔接、原声完整、无闪帧怪音。没看的写「未核」。
- 没达标先看对不上的是哪类：稿子写了画面没有的事回去改稿；候选里有对的没选上就改 `shots`；候选里根本没有就看 `window` 是否框偏。
