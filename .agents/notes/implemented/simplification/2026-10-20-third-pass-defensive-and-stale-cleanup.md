# Agent Note: 第三轮去防御——死参数、过期指令与重复规则

Status: implemented

## Problem

v0.6.0 之后对六个 skill 的脚本与指令做了一次只读审计（六条线并行：recap / 阶段 skill 指令，recap / assemble / understanding+script / voiceover+cut+tools 代码）。前两轮（[[2026-09-20-consumer-side-revalidation-removed]]、[[2026-09-20-slim-skill-layer]]）之后仍剩四类东西：

- **只有测试在用的参数与分支**：`restore_material(material_id=, overwrite=, prune_stale_allowed=)`、`final_qc` 的 `probe_runner/decode_runner`、MiMo 报告的 `judge`、`dub.lint_dub_script(work_dir=)`、voiceover 的 `tts_dynamic_params` 与 `_synthesize_segment` 的 `prepared is None` 路径、assemble 的 `work_dir=None` 默认值与 `assembly_contract` 的缺省分支、各入口模块（`cut.py`、`mimo_qc.py`、`voiceover.py`）为测试保留的再导出。
- **读不到的配置与永远不触发的分支**：assemble 的 `ducking_mode` 只有 `fixed` 一个取值（SKILL.md 却写着可配 `DUCKING_MODE`）、`SOURCE_VIDEO` 环境变量被 `main()` 无条件覆盖、`foreign_source_audio` 键无人读取、`deslop_qc_requirements.json` 永远写 `style_card_required: false`、`validate.py --mode cut` 没有运行时调用方、understanding 的 `_mimo_endpoint` 间接层与从未定义的键、`plan["warning"]` 无人读取。
- **指令与代码不符**：assemble SKILL.md 说"已采用配音只支持原速"（实际按 `tempo_policy` 适速）、recap 说测得字幕带用 60% 遮罩（实际不透明）、audio-routing 说旧 manifest 按 narration 解释（实际拒收）、config-playbook 列出不存在的环境变量、data-schema 的 lint 指标名与错误码过期、多处文档说留白原声字幕默认烧录（实际默认关闭）。
- **重复与防御性文字**：video-script SKILL.md §3–§5 逐条复述创作手册；"MiMo QC 不阻断""`--style` 只是参考"在多处重复；各 assemble reference 反复声明"未试听 / 不是发布批准"，并把默认路径称作 legacy；多处版本史与"尚未实现"字样。

## Decision

- 上述测试专用参数、死分支与死配置键删除；测试改为从所属模块 import，或 monkeypatch 已有的模块级函数（`final_qc._run_ffprobe`、`mimo_qc.mimo_qc_api_call` 等）。`mimo_qc.py` 仍在调用时查找 `mimo_qc_api_call`，测试接缝不变。
- 同一 skill 内的重复实现合并：briefing 直接调用 `vlm.mimo_video_overview_cache_fresh`；understanding 的三个"identity or None"辅助函数合为 `lib.optional_file_identity`；review 的版本常量只在 `evidence_bundle` 定义；dashboard 的边距常量与 provenance 标签以服务端为准；`doctor` 复用 `recap_cli.TTS_PROVIDERS`。
- 自产数据直接索引：`resource_lock` 读两个自产 manifest、`review_grounding` 读 `multi_source_manifest.json`、`consolidate` 读 `parse_index_response` 的结果、`CONFIG.get(k, default)` 对已声明键改为 `CONFIG[k]`；`load_prompt` 缺模板直接报错，不再走缺少【帧标签】的兜底 prompt。
- 指令只写当下事实：改正与代码不符的句子；同一规则只在拥有它的位置写一次（video-script 的方法论只在创作手册）；删掉版本史、元说明和描述代码已强制行为的禁令。被测试钉住的文本（角色列表、编号标题、JSON 示例、`blockers/advisories` 行、research-guide 锚点）保持不动。
- 保留（审计提出但不删）：语音参考的锁与快照机制、`measure_subtitle.py` 的事务式提交、`track_binding` 消费时的重验、`strict_publish` 写后存在性检查、`build_timeline` 的扩展参数、MiMo 请求体里的 `thinking` 字段、`narration_audio` 的重叠块（`tts_meta.json` 的负停顿只被 lint 拦截，直接运行仍可到达）、`narration_binding` 的整数索引检查（`load_adoption` 的 `!=` 放过 `1.0` / `True`）、`project_binding` 读字体族的 `.get`（库交叉引用检查整体失败时该字段不受保证）。
- 本轮审计发现的真实缺陷改行为，未并入本次清理：多源剪辑句界门禁误判、多视频 `speech_boundary_anchors_output.json` 缺 `clip_plan_identity`、`project_binding` 非对象 bindings 抛 AttributeError、VLM 帧时间戳键与 `background_research.json` 的崩溃路径、采用配音时 manifest 记录的 tempo 上限、MiMo QC 证据清单引用不存在的文件名。

## Alternatives considered

- **连同发现的缺陷一起修** — 最强理由：同一批文件，一次评审。否：缺陷修复需要各自的回归测试与行为评估，混进净删除的 diff 会让评审分不清"删掉不可达代码"和"改了可达行为"。
- **对每个死参数补测试证明可达后保留** — 最强理由：零行为风险。否：审计已逐个 grep 调用方，非测试调用方为零；补测试只会把测试接缝固化成接口。
- **保留 `ducking_mode` 的 sidechain/none 分支，补上 `DUCKING_MODE` 环境变量** — 最强理由：文档承诺过这个能力。否：`--ducking` 早已删除，环境变量从未实现，没有用户依赖；加回来是新功能，不是清理。
- **在 TTS 缓存设置里保留常量 `tts_dynamic_params: true`，避免缓存失效** — 最强理由：已有项目续跑不重合成。否：这是只为兼容旧缓存存在的死键，正是本轮要清的东西；代价是一次性的。

## Consequences

- **收益**：skills 下 Python 净减约 1,770 行（含删除的三份 parity 副本，见 [[2026-10-20-drop-unused-parity-copies]]），测试净减约 240 行，Markdown 净减约 170 行；指令与代码一致；同一规则不再有多份措辞。
- **代价**：
  - 外部脚本若进程内调用被删的参数或再导出会报 TypeError / ImportError（仓库文档从未列为公开 API）。
  - voiceover 的分段缓存设置少了 `tts_dynamic_params`，升级后第一次续跑会重新合成全部 TTS 分段（付费 API 调用）。
  - `assembly_manifest.json` 的 `assembly_settings` 少了 ducking 键，升级后第一次续跑会重渲染一次。
  - `dub` 渲染不再在 `dub_reference.wav` 被手动删除时重切参考音频，直接报 FileNotFoundError。
  - `deslop_qc.json` 不再输出恒定的 `style_card_required` / `style_card_requirement_source` 字段；`material.json` 不再写恒为 null 的 `duration`。

## Verification

`PYTHON=<py3.12> scripts/test.sh`（带 static ffmpeg 7.0）：understanding 184、voiceover 126、script 112、orchestrator 421、inspect 23 全绿；cut 1 个、assemble 6 个失败与基线逐条相同（均为 ffmpeg 环境性失败）。`ruff check skills tests scripts tools` 无告警。
