# Agent Note: 旁白入口判定与 cut 门禁共用语气词规则

Status: implemented

## Problem

[[2026-10-02-coarse-asr-anchor-honesty]] 让 cut 门禁不再把只有语气词的 ASR 窗口（"啊！"、"Hi."）当作对白，但旁白入口判定没有跟着改，当时把"统一对白定义"列为被否方案，等真实重跑再定。真实重跑出现了：冷开场桥段 output 16.0–21.8（source 16–22）落在一个只有"啊！"的窗口里，cut 门禁允许在这里下刀，`narration_lint` 却把入口当作打断原声句子，用 `interrupts_source_sentence` 硬阻断。只改 script 不改 assemble 的话，阻断只会挪到 TTS 之后：assemble 的 `_entry_speech_owned` 同样把这段窗口算作讲话，会以 `unsafe_entry`（`unsafe_source_handoff`）阻断，白花一次 TTS。

## Decision

- video-cut 把规则抽成纯函数 `_dialogue_speech_spans(rows)`：只有语气词的行不算对白，与对白行首尾相接（≤0.05 秒）时在相接一侧保留 `_INTERJECTION_GUARD_SECONDS = 1.0` 秒保护；没有 `text` 字段的行是只有时间的证据，算对白。`_load_source_speech_spans` 只负责读 ASR 文件再调用它，门禁行为不变。
- video-script `speech_ownership.py` 和 video-assemble `audio_mix.py` 各有一份副本（`_NON_DIALOGUE_TOKENS`、`_NON_DIALOGUE_CJK`、`_INTERJECTION_GUARD_SECONDS`、`_interjection_only`、`_dialogue_speech_spans`），`tests/orchestrator/test_brief_narration_parity.py::test_interjection_rule_stays_identical_across_cut_script_assemble` 按 AST 校验三份一致。
- 只改入口判定：script 的证据多一个 `dialogue_spans`，`entry_overlaps_source_speech` 用它判断入口是否落在讲话里；assemble 的 `_handoff_speech_evidence` 返回 `(speech, quiet, dialogue)`，`_entry_speech_owned` 用 `dialogue` 判断。"有没有实测讲话证据"仍看完整的 `speech_spans`，所以只有语气词的素材入口按安静处理，不会退回"没有证据就算讲话"。
- assemble 在 full 模式退回读 `asr_clean.json` / `asr_result.json` 时，先去掉空文本或只有空白的行（没识别出文字的窗口不是讲话），与 cut、script 的调用方一致；这样整段 `speech` 行也不含空白行（`_dialogue_speech_spans` 本身另外跳过空白行，见 [[2026-10-02-speech-evidence-one-transcript]]），没有 `text` 字段的输出时钟 `speech_spans` 仍算对白。
- 入口不算对白时，assemble 按入口时刻记 `source_entry_status`：落在实测安静或没有讲话处记 `quiet_source`，落在只有语气词的讲话窗口里记 `non_dialogue_source`；整段都不压低的块也记。
- 整段归属（`overlaps_speech`、压低电平）和 understanding 写出的 `speech_boundary_anchors_output.json` 都不变；两种时钟的 `speech_spans` 行本来就带 `text`，消费方自己过滤。
- 文档：`video-recap/references/data-schema.md` 的入口规则说明同步更新。
- 空白文本行与三处读哪份转写，后续统一见 [[2026-10-02-speech-evidence-one-transcript]]。

## Alternatives considered

- **只改 video-script**：改动最小，正好消掉这次的 lint 阻断。没采用：assemble 入口检查仍把同一窗口算作讲话，阻断从 TTS 之前挪到 TTS 之后，更贵。
- **在 understanding / recap 写 `speech_spans` 时就去掉语气词窗口**：只需改生产方，消费方一行不动。没采用：full 模式下 script 和 assemble 直接读 `asr_result.json`（assemble 还读 `asr_clean.json`），仍要在消费方过滤；而且整段归属和压低电平也会跟着变。
- **整段归属和压低电平也用对白定义**：三处完全一致。没采用：会改变尖叫或音乐上的压低电平，没有试听证据；入口门禁是这次唯一被真实重跑卡住的地方。

## Consequences

- **收益**：cut 允许下刀的语气词区间，旁白也能从那里切入，lint 和 assemble 给出同样的结论；离对白 1 秒内的入口仍然阻断。
- **代价**：同一段逻辑有三份副本，靠 parity 测试防漂移。只含"Hi."的 15 秒窗口里如果其实有 ASR 没听清的对白，旁白可以从中间切入；cut 门禁本来就接受这个风险。剪后输出时钟上的保护区按输出时钟相邻判断，被剪开的行不再共享边缘。
- 只有单测证据；需要用这次的冷开场稿子真实重跑，确认 16.0 处的入口通过 lint 且 assemble 不报 `unsafe_entry`。
