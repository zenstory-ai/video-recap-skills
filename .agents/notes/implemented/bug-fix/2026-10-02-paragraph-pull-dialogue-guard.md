# Agent Note: 段落收紧提前的旁白块不进入没校验过的原声对白

Status: implemented

## Problem

`narration_audio._build_timed_narration` 把段落内（与上一块作者留白不超过 `narration_run_gap_seconds = 1.6` 秒）的后续块放到上一块实际结尾 + 0.35 秒处，最多比写的 `start` 提前 `narration_max_pull_seconds = 1.2` 秒。`narration_lint` 的 `_source_sentence_entry_issue` 只检查写的 `start` 是否落在句末停顿或安静处，提前后的实际起点从没被检查过：

- 上一块 TTS 比 slot 短时，提前的那一段可能正好是一句原声台词（写稿时特意排在这句之后），旁白会从这句中间切入。若实际间隔超过 `duck_bridge_seconds`，这句先以满音量放出，再被旁白截断。
- 这样的块在 `assembly_manifest.json` 里 `source_entry_status` 是 `null`，看不出它被挪过、挪了多少。

[[2026-10-02-hardcode-tuning-knobs-drop-narration-delay]] 把收紧固定为唯一的放置方式，所以每次运行都会走到这里。

## Decision

- `audio_mix._dialogue_free_pull_start(candidate, written_start, dialogue, quiet)`：在 `[candidate, written_start)` 里取对白区间减去实测安静窗口，剩下长于 0.05 秒的片段时，返回最后一段的结束点，否则返回 `candidate`。对白区间与原声交接的入口判定同源：`_paragraph_pull_evidence` 调 `_load_sentence_handoff_anchors` + `_handoff_speech_evidence`，只有语气词的窗口不算对白，full 模式的 ASR 空文本行不算讲话。
- `_build_timed_narration` 只在算出的起点早于写的 `start` 时读一次证据（惰性，没有提前就不读文件），把起点改为 `min(start, max(原起点, 安全点))`。对白覆盖到写的 `start` 时等于不提前。
- 真的提前了的块记 `source_entry_status = "paragraph_tightened"` 和 `written_start`（写的 `start`）；`assembly_manifest.json.audio_segments[]` 新增 `written_start`，未提前为 `null`。这样的块若因间隔超过 `duck_bridge_seconds` 成为新压低段的首块，`_apply_source_sentence_handoffs` 在实际起点上做入口判定并覆盖状态（可能是 `unsafe_entry` 阻断），`written_start` 保留。
- 文档：`video-recap/references/data-schema.md`、`video-assemble/SKILL.md`、`video-script/SKILL.md` 与 CHANGELOG。顺带改正"段落首块严格从 `start` 放置"的说法：代码是 `max(start, 上一块实际结尾 + pause_after_ms)`，上一块超时会把它往后推。

## Alternatives considered

- **提前后的起点再跑一遍句界入口判定（落在锚点停顿内就允许）。** 最强理由：与 lint 的规则完全一致，提前到句末停顿里也算安全，能提前得更多。没采用：lint 只看入口，把起点挪到前一句句末意味着旁白盖住写稿时特意留出来的整句台词；"提前的那一段不含对白"更保守，也不依赖粗粒度 ASR 锚点的 `unverified` 估计。
- **有对白就完全不提前。** 最强理由：规则最简单，写的 `start` 就是校验过的点。没采用：对白在提前区间的前半部分时，结束之后到写的 `start` 之间仍是安静的，可以照常收紧，不必丢掉句间间隔稳定的好处。
- **把段落收紧挪到 lint 之前，让 Agent 直接写收紧后的时间。** 最强理由：校验看到的就是最终时间。没采用：收紧依赖 TTS 实际时长，lint 在 TTS 之前运行，拿不到这个值。
- **只记状态，不限制提前。** 最强理由：改动最小，manifest 能看出被挪过。没采用：问题本身是旁白切进原声台词，只记录不能修好成片。

## Consequences

- 收益：提前的旁白块不会再从一句没校验过的原声台词中间切入；manifest 能看出哪些块被收紧以及原来写的时间。
- 代价：原声对白密集的段落收紧得更少，句间间隔可能比 0.35 秒长，最长回到写的 `start`；粗粒度 ASR 窗口（15 秒）覆盖的地方基本不再提前。`audio_mix` 多约 55 行，`narration_audio` 依赖 `audio_mix` 的两个函数。
- 没有可用证据时（full 模式没有 ASR）照旧提前到 1.2 秒上限；cut 模式证据过期时同样不限制，但原声交接随后会以 `anchors_unavailable` 阻断。
- 只有单测证据（`tests/assemble/test_assemble_fixes.py::test_paragraph_pull_never_enters_unchecked_source_dialogue`、`test_pure_assemble.py::test_dialogue_free_pull_start`）；需要真实重跑确认对白密集素材上的句间间隔可以接受。
