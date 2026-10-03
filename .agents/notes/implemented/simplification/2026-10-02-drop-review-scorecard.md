# Agent Note: 解说评审只产出 verdict 与 findings，删除建议型 scorecard 和只写不读的评审产物

Status: implemented

## Problem

video-script 的解说评审（`review.py`）在 #49（727a8a5，见 [[2026-05-18-agent-owned-narration-cli-mechanical]] 的来源列表）里加了一层"建议型增强"：每个评审 prompt 末尾要求模型额外给一份 16 键 1-5 分 scorecard（`promise_match` / `hook_3s` / … / `packaging_consistency`），外加 `hook_candidates_review`、`retention_risk_points`、`highest_return_edits`、`information_gain_notes`、`spoken_language_rewrites`、`grounding_assertions` 六个列表。这些内容按契约永远不改 verdict、不作门禁，`recap` 和 dashboard 也不读；它们只占 prompt 和输出 token，并在 `narration_review.md` 里排在真正要处理的 findings 前面。

围绕评审还有几处只写不读的产物和防御代码：

- `grounding_qc.json`：每次评审都写，唯一读者是 recap 成片后打印的一行"🧭 Grounding QC"。它的 verdict 由 findings 推出，warnings 等于 `narration_review.json` 的 warnings；`speech_window_qc` 和 `research_guardrail` 没人读。打印函数还在 `review_ran` 判断之前执行，评审关闭或失败时会把旧运行留下的 `grounding_qc.json` 当成本轮结果打印出来。
- `silence_periods.qc.json`：video-understanding 的 `detect.py` 写出，只被 `build_grounding_qc` 读进 `speech_window_qc`。
- `deslop_qc.json`：每次校验写一份，与 `narration_lint.json["deslop_qc"]` 字节相同；唯一读者是评审 prompt 里的整份 JSON 转储，而其中的 blocker 早已作为 lint error 在评审前拦下。
- `coverage_policy_version` 版本戳定义了三遍、写进多个产物，没有任何比较；`EVIDENCE_CONTRACT_VERSION` 定义了两遍。
- `review.py` 是一层再导出门面（14 个名字），只为测试方便；`review_grounding` / `evidence_bundle` 对流水线自己写的 `vlm_analysis.json` / ASR 数组做 isinstance 和 try/float 防御，与 understanding 侧和 0.6.0"第二轮去防御"的约定不一致。
- 评审 verdict 词表还留着兼容别名 `OK`，SKILL.md 让 Agent 迭代到 "`PASS` / `OK`"。

## Decision

- `review_response.RUBRIC` 删除 scorecard 段落和输出格式里的 scorecard 与六个列表，模型只需返回 `{"verdict","summary","findings"}`。`_normalise_review` 只输出这三项；模型若仍返回旧字段，直接丢弃。`SCORECARD_KEYS`、`_normalise_scorecard`、`_clamp_score`、`_normalise_list_of_dicts`、`_normalise_string_list`、`_downgrade_context_assertions` 删除。`format_review_md` 只渲染 verdict、summary 和 findings。finding 的类别表 `CATEGORIES` 不变（`subtitle_readability` 仍可解析，只是 rubric 不再点名它）。
- 模型返回 `OK` 时记为 `PASS`；分块合并的 rank 表、SKILL.md 和 recap `data-schema.md` 里不再出现 `OK`。
- 评审 prompt 的"Scorecard 评估提示"改为"创作计划参考"，仍嵌入 `packaging_plan.json` / `recap_story_plan.json` / `visual_audio_board.json` / `style_card.json`，不再嵌入 `deslop_qc.json`；RUBRIC 第 15 条去掉 deslop_qc 那句。
- 不再写 `grounding_qc.json`：`build_grounding_qc` / `write_grounding_qc` / `_write_grounding_qc` / `_research_guardrail_qc` 与 `evidence_bundle.build_review_coverage_metadata` 删除；recap 的 `_print_grounding_qc_pointer` 删除，`_print_narration_review_pointer` 在 `review_ran=False` 时什么都不打印。`narration_review.json` 的 `evidence_contract` 照旧记录选中区间、分块数和警告。
- `detect.py` 不再写 `silence_periods.qc.json`；`annotate_quiet_windows_with_asr` 只返回标注后的窗口，每个窗口的 `asr_granularity` / `has_speech_reason` 仍写进 `silence_periods.json`。
- `narration_lint.lint_narration` 不再写 `deslop_qc.json`，报告只在 `narration_lint.json` 的 `deslop_qc` 字段；understanding brief 和两份 `data-schema.md` 改指向这个字段。
- 删除 `coverage_policy_version` 字段和三份 `COVERAGE_POLICY_VERSION`；`review_runner` 从 `evidence_bundle` 导入 `EVIDENCE_CONTRACT_VERSION`。video-script `lib` 的 `_api_headers` / `_prepare_api_payload` / `api_call` 去掉从不使用的 `api_provider` 参数（两个 helper 的 `api_url` 也去掉；`api_call` 的 `api_url` 保留）。
- `review.py` 只剩 `from review_runner import main`，`__all__ = ["main"]`；测试直接导入 `review_response` / `review_runner` / `review_grounding` / `evidence_bundle`。这推翻了 [[2026-09-27-test-audit-residuals]] 里"`review.py` 的测试门面同理保留"的决定。
- `review_grounding` 与 `evidence_bundle.filter_evidence_by_ranges` 直接读字段：去掉对 manifest、sources、scene、ASR 段的 isinstance 跳过，以及 start/end 的 try/float 与 `_safe_time` 的 AttributeError 分支。保留：manifest 里相对路径的目录越界检查、`_asr_segments` 对列表与 `{segments: [...]}` 两种形状的适配、空文本 ASR 过滤、`_remap_frame_facts` 对非 dict `frame_facts` 的透传和对键的 `float()`。

## Alternatives considered

- **保留 scorecard，只从 prompt 里去掉不常用的几个维度。** 最强理由：分数给 Agent 一个快速的优先级信号，部分维度（hook、结尾回收）确实有用。没采用：这些维度在 RUBRIC 里都已有对应的 finding 类别（`weak_hook`、`weak_payoff`、`not_write_for_ear` 等），能定位到段落并给出改法；分数本身不能定位、不改 verdict、不进门禁，也没有任何代码读它。保留一半仍要维护解析、归一化和渲染。
- **把 recap 的 Grounding QC 打印行改读 `narration_review.json`。** 最强理由：用户仍能在成片后看到证据覆盖情况。没采用：紧接着的评审指针行已经打印 verdict 和 findings 数；区间数对用户没有行动意义。直接删除也顺带修好了评审未运行时打印旧文件的问题。
- **`OK` 按未知值归入 `REVISE`。** 最强理由：词表最干净，所有非标准值走同一条路。没采用：SKILL.md 让 Agent 迭代到 verdict 为通过；模型偶尔返回的 `OK` 本意是通过，归入 `REVISE` 会让 Agent 对已获认可的稿子白白再改一轮。
- **`review_grounding` 的防御全部删掉。** 最强理由：最少代码。没采用：目录越界检查防的是 manifest 里的相对路径逃出 work_dir；ASR 形状适配是因为 `asr_clean.json` 与 `asr_result.json` 形状确实不同；空文本过滤是语义而不是防御。

## Consequences

- 收益：video-script 脚本少约 340 行（`review_response.py` 669 → 424 行，`review.py` 40 → 9 行），评审 prompt 每块少一段 scorecard 说明、一份输出格式和最多 3,000 字符的 deslop 转储，模型输出也更短；`narration_review.md` 首屏就是 findings。work_dir 少三个文件（`grounding_qc.json`、`silence_periods.qc.json`、`deslop_qc.json`）。
- 代价：`narration_review.json` 不再有 `scorecard` 和六个建议列表，读取这些键的外部脚本会拿不到值；`deslop_qc.json` 不再生成，外部工具需改读 `narration_lint.json` 的 `deslop_qc`。损坏或被手改的 `vlm_analysis.json` / ASR 行现在会让评审报错，而不是静默丢行；recap 对评审本来就失败开放。
- 门禁不变：严格评审仍只看 `findings` 里的 error 和 `parse_error`，`recap_review.review_result_status` 没有改动。
- 未做：`background_research` 在 prompt 里出现两次（`## 背景资料` 与 context-only evidence）、`packaging_plan.json` 合约及 RUBRIC 第 14 条，都不在本次范围。
