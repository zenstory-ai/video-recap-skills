# Agent Note: 移除实验性 dub 模式

Status: implemented

取代这两篇里关于 dub 的决定：[远程调用披露与 dub 确认](../process/2026-10-03-remote-call-disclosure-and-dub-consent.md)、[删除 dub 的手动侧 CLI](2026-10-02-trim-dub-side-cli.md)。前者“任何外发路径都要在 `SKILL.md` 写明”的规则继续有效，只有 dub 的确认门禁随功能消失。

## Problem

`--edit-mode dub` 把英文视频翻译成中文，并用原说话人的克隆音色整轨替换人声。owner 决定这不是产品的一部分：产品是中文解说成片，而翻译配音保留原片结构，是另一类任务。

它却占着一条独立路径：一个 569 行的 `dub.py`、编排入口里一组只为它存在的互斥检查、一个把源视频音频发给 ASR 再克隆说话人声音的外发路径和专用的 `--confirm-voice-rights` 门禁，以及两个 `SKILL.md` 里的专门章节。

## Decision

- 删除 `skills/video-voiceover/scripts/dub.py` 与 `tests/voiceover/test_dub.py`，删除 `--edit-mode dub`、`--confirm-voice-rights`、`_run_dub` 和编排入口里的 dub 分支，以及两个 `SKILL.md`、references、架构文档和双语 README 里的 dub 内容。
- `video-voiceover/scripts/lib.py` 删除只有 dub 用到的 `mimo_asr_api_call`、`mimo_asr_model`、`mimo_api_url`、`mimo_api_key`。video-understanding 自己的 ASR 副本不动。
- 旧输入明确失败，不改跑其他模式。`recap_cli._edit_mode` 作为 `type` 拦 `--edit-mode dub`；`EDIT_MODE` 现在不再读取，任何取值都按已移除报错（见 [[2026-11-06-remove-env-twins-of-run-flags]]），所以 `EDIT_MODE=dub` 同样不会静默跑 full。`recap_source.reject_removed_dub_workdir` 拒绝 manifest 记录 `edit_mode: dub`，或含 `dub_transcript.json` / `dub_script.json` / `dub_manifest.json` 的 `work_dir`，单视频和多视频路径都查。报错指向新 `--work-dir` 和 v0.6.2。
- v0.6.2 tag 是需要这条路径的人的退路。

保留，语义不变：

- `--voice-ref` / `VOICE_REF` 解说克隆及其外发说明与授权措辞，因此 `mimo-v2.5-tts-voiceclone` 模型名仍在 `voiceover.py`。
- `tts_audio.py` 的幻读检查（`implausible_tts_duration`、`rejected_take_*`）：解说 TTS 也在用。
- `final_qc._render_warnings` 与 `recap_stage_qc._print_render_warnings` 的“manifest 只对它渲染的那个文件生效”检查：不再因 dub 存在，但对任何指向其他文件的 QC 都防止引用旧 manifest，只改注释。

## Alternatives considered

- **只从文档隐藏，保留代码。** 最强的理由是改动最小，也不影响已在用的人。不采用：外发路径和克隆能力还留在 ClawHub 包里，安全扫描和维护成本都不减少，而 owner 的决定是这不属于产品。
- **把 dub 拆成独立技能。** 这样已有用户有明确去处，打包也干净。不采用：拆出去仍要维护，owner 不打算继续做；v0.6.2 已经能满足回退。
- **不写专门的拒绝逻辑，让 `choices` 报错。** 代码更少，`--edit-mode dub` 也确实会被拒。不采用：`EDIT_MODE=dub` 绕过 `choices` 后会静默跑 full，旧 `work_dir` 不带参数重跑也一样。

## Consequences

- 收益：少一条会把源音频和说话人声音发出本机的路径，少一个专用同意门禁；`SKILL.md` 和编排入口都更短。
- 代价：依赖 dub 的用户必须停在 v0.6.2；旧 dub 命令和 `work_dir` 无法续跑，要换新目录。
- 留下少量墓碑代码（`_edit_mode`、`reject_removed_dub_workdir`），要长期留着，否则旧输入会回到静默改跑其他模式。
- 拒绝只针对字面值 `dub`：当时 `EDIT_MODE` 取其他非法值时当作 full，后来 `EDIT_MODE` 整个移除后不再有这个问题（见 [[2026-11-06-remove-env-twins-of-run-flags]]）。
- `docs/open-source-ai-video-recap-tools.md` 里关于第三方翻译配音工具的问答保留：它讲的是别的工具，正好说明这是另一类任务。
