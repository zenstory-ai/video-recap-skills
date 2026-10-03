# Agent Note: MiMo 内容审核拒绝不再被当成对白或画面描述

Status: implemented

## Problem

MiMo 对被审核拦下的请求不报错，而是把一句英文 `The request was rejected because it was considered high risk` 当作回复内容返回。video-understanding 只在可选的 `--mimo-video-overview`（`vlm._is_mimo_chunk_usable`）里识别这句话；ASR 和逐场景 VLM 照单全收。guohuo-60s 的替身复现里：

- 这句话写进了 `asr_result.json` 的 3 个 15 秒窗口，再流到 `timeline_fusion.json`、`asr_writing_chunks.json` 和 `agent_narration_brief.md`。
- 有文本的 ASR 窗口整段算作原声讲话，cut 与旁白 lint 的句界安全判断因此把这些窗口当成有人说话。
- 逐场景 VLM 的描述字段也出现了同一句话。

## Decision

- video-understanding `lib.py` 新增 `is_moderation_refusal(text)`，只匹配 MiMo 自己的措辞：`request was rejected`、`considered high risk`（不区分大小写）。
- `asr._run_asr` 遇到拒绝回复时记一条警告并返回空串，该窗口与其他无文本窗口一样按“原因未知、不代表静音”处理。
- `vlm.analyze_scenes` 遇到拒绝回复时按空回复解析，描述是既有的 `(VLM 无法识别此场景画面)`，不写 `frame_facts`，并加 `analysis_status: "moderation_refused"`。
- 测试：`tests/understanding/test_vlm_fixes.py` 新增两条：VLM 拒绝回复不进入分析结果并带状态；ASR 拒绝回复返回空串，而提到“违规”“风险”的真实对白原样保留。

## Alternatives considered

- **ASR 与逐场景 VLM 直接复用 `_MIMO_REJECTION_MARKERS`。** 最强理由：一份列表、一处维护。没采用：那份列表含 `违规`、`无法处理`、`内容审核`、`high risk` 等词，对白和剧情描述里很常见（“这事违规”），用在转写上会把真实台词清空；overview 那里误判只是退回帧描述，代价小得多。
- **把拒绝当作供应商错误，抛 `ASRProviderError` / 重试。** 最强理由：不会悄悄少一段转写。没采用：审核拒绝对同一段素材是稳定结果，重试只会多计费；整个理解阶段因此失败，代价远大于缺一段对白。
- **ASR 行也加状态字段。** 最强理由：下游能区分“被拒绝”和“无文本”。没采用：`asr_result.json` 的行形状是 `{start, end, text}`，多个技能直接读；现阶段空文本已经表达“未知”，日志留有拒绝记录。

## Consequences

- 收益：拒绝文本不再进入 brief，也不再让 cut / 旁白校验把那段时间当成讲话。
- 代价：被拒绝的窗口在 brief 里就是一段空白，Agent 需要靠画面、硬字幕或背景资料补足；只认这两句英文措辞，MiMo 改了拒绝文案就会漏判。
- 旧 work_dir 里已经写下的拒绝文本不会被清理，需要重跑 ASR / VLM。
