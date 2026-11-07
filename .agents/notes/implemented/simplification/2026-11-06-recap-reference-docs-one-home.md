# Agent Note: 参考文档的同一事实只留一处

Status: implemented

## Problem

- video-recap 的 `references/data-schema.md` 整段复制了视频理解阶段六个产物的格式（`vlm_analysis.json`、`asr_result.json`、`asr_writing_chunks.json`、`silence_periods.json`、`timeline_fusion.json`、`background_research.json`）。没有 parity 测试守这些副本，它们已经漂移：recap 这份缺理解阶段关于 ASR `start/end` 只是粗窗口的警告，Agent 读到的是更弱的版本。
- `audio-routing.md` 有两处和代码相反：说缺 `audio` 字段的旧清单按 `narration` 续跑（`test_audio_binding_rejects_mode_switch_and_manifest_without_audio` 断言的是不匹配），说续跑会重置 QC 阶段（recap 没有这段代码）。其余内容重复 `SKILL.md` §1、§4.7，或是不跑 TTS 时用不上的 TTS 事实。
- `config-playbook.md` 有一行 `--source-video`，而 recap 没有这个参数，照着写会得到 argparse 错误。`data-schema.md` 列了没有发出方的 `over_narrated`。
- video-script 的 `SKILL.md` 和创作手册互相复述反事实检查、控制模式定义、§5.1 的三条规则和内容质量基线。

## Decision

- recap 的 `data-schema.md` 删掉这六节，开头一句话指向 video-understanding 技能的 `data-schema.md`。写成点名技能的散文，不写路径，因为 `test_stage_sources_never_point_to_a_sibling_skill_path` 拒绝路径形式。`SKILL.md` §6 同步说明产物格式分两处。发布上不缺文件：`.clawhub/publish.json` 里 recap 依赖 video-understanding。
- 同一文件里，渲染器机制和重复示例收成短句：`clip_plan_validated.json` 的吸附与帧对齐、多视频 validated clip 的 JSON、`packaging_plan.json` 的示例（键全部保留）、`metadata.warnings` 的示例。`audio_segments[]` 独立成节，因为它原来放在 `resource_lock` 标题下；`source_handoff_status` 和 `source_entry_status` 的取值表保留，Agent 要据此处理 `unsafe_entry` 和 `anchors_unavailable`。
- `audio-routing.md` 改掉两处错误，只留 source-mix 运行需要的内容：显式 TTS 选项被拒绝、未绑定目录被拒绝、叠加规则，以及一句 v0.6.2 指引和最接近的设置。
- `config-playbook.md` 删掉 `--source-video` 行、与 `SKILL.md` 重复的 Style / Edit mode / Cut target 行和 `timeline.json` 段落。
- video-script：§6 只留指向手册「观众复核」的一句和第 7 项检查，REVISION 段不动；手册的控制模式节换成指向 §1.1 的一句，独有的“修法要定位到 beat、镜头边界或句子”挪进 §1.1；手册删掉内容质量基线，§5.1 三条规则换成一句指针；`SKILL.md` §3 和 §8 各删一处重复。§2 的外发说明和 frontmatter 不动。

## Alternatives considered

- **删掉 video-script `SKILL.md` §4.1–4.4。** 最强理由：§4 开头就要求先读手册，这四节逐条都在手册里，能再少 39 行。没采用：实际只省约 480 字符，而 Agent 不读手册时会失去内联清单，这对写稿的影响离线验证不了。
- **两份调研指南去掉工具清单，video-script 这份再去掉关键词表。** 最强理由：50 行内容两边重复。没采用：单独安装 video-script 会失去这些内容，收益不足以抵消。
- **video-cut 只留 `shot_review.py` 一条候选路线。** 最强理由：两条路线跑的是同一个 filter。没采用：要改一条测试断言，长片上全解码也更慢，而这里只省约 9 行。

## Consequences

- 收益：六个产物只有一份格式说明；三处错误说法消失，`--source-video` 这条错误指引不会再让 Agent 撞上 argparse 错误。
- 代价：Agent 查这六个产物要多读一个文件；调试 1.5 倍隔行帧率规则之类的边角时要读代码；只打开创作手册的读者要跳回 `SKILL.md` 看控制模式定义。
- 这些只是文档改动，没有行为变化。旁白质量受不受影响离线验证不了。
