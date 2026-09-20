# Agent Note: 解说成块、原声成块，块间原声满音量

Status: implemented

## Problem

0.2.0 把原声做成一条连续低音床：`duck_bridge_seconds=12` 把相邻解说间的间隔全部并入压低区，原声只在开头结尾回满；解说又逐句合成、一句一停，两行字幕黑带压掉画面。用户反馈明确："解说成块，原声也要成块，7:3，原声不要被压低"——密集解说听起来像字幕配音，原声精彩处也听不见。

## Decision

- 解说以块为单位：一个 beat 写成几句连续思路，一次 TTS 合成；块内 `narration_tighten` 让句子紧贴上一句实际收尾播放，块间留白是作者的原声块。
- 混音默认（`video-assemble/scripts/lib.py`）：`idle_orig_volume=1.0`，块间原声满音量；`duck_bridge_seconds=1.5`，只把小于该值的相邻解说窗口并成一段压低，超过即视为原声块回满；`fade_ms=120` 防止紧凑句子一顿一顿；`narration_speed=1.15`。`FOREIGN_SOURCE_AUDIO` 下解说下方原声降到近静音，但原声块仍满音量。
- 字幕按标点拆成单行 chunk，卡拉 OK 式分配到块的播放窗口，遮挡带按一行高度设；TTS 文本先保证听感连续，字幕再按阅读宽度拆，never 反过来把朗读稿切碎。
- 比例不是配额：video-script 先为每个 beat 指定 `audio_owner`，只有有明确 `narration_job` 才写旁白；`7:3` 仅在素材判断不足时作为避免墙到墙旁白的粗略首稿参考，强对白、动作声或沉默可以完整拥有一个 beat。lint 用块覆盖率类别（`no_original_blocks` / `under_narrated` / `no_original_breaks` / `fragmented_beats`）提示，不按句密度。
- voiceover 判断一块是否放得下 must 折算 `narration_speed`（槽位实际容纳 `raw_dur / narration_speed`），否则预算正确的块会被无谓截断。

来源：961390f (#17)、187dd2b (#16)、282f097 (#34)、468182c (#63)

## Alternatives considered

- **连续低音床**（187dd2b，`duck_bridge_seconds=12`、`idle_orig_volume=0.85`）— 最强理由：密集解说下原声不会在句间"弹上来"，听感平滑，且 ffmpeg 与剪映 keyframe 都容易表达。否：原声永远被压，用户要的是原声块整段放出来；961390f 直接翻转默认值。
- **逐句紧凑 runs + choppy lint**（961390f 第一个提交的方向）— 最强理由：在不改写作模型的前提下缩短句间空隙，中位空隙 0.79s→0.54s 有实测。否：仍是一句一停的形态，遮不住"顿挫"；块内紧贴保留为 `narration_tighten`，写作模型换成块。
- **`7:3` 或 beats/min 作为配额与硬 lint** — 最强理由：可量化、易于自动判定。否：逼 Agent 凑数写 filler 和看图说话（187dd2b Step 1、d1a1297 的"冷解说"根因）；#63 改为 `audio_owner` 决定比例。

## Consequences

- **收益**：原声有呼吸、解说连贯；混音默认与剪映草稿 keyframe 形态一致。
- **代价**：块受语速预算约束，装不下要删减或拆叙事任务，不能靠加速堆字；解说与原声的衔接要靠写作规则（前块引出、后块承接）而不是混音自动完成；`DUCK_BRIDGE_SECONDS` 调大会退回连续铺底，调小会更碎，两个方向都没有 lint 兜底。
