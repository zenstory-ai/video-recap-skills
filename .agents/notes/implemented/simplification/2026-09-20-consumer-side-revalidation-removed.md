# Agent Note: 消费方不再重验生产方契约（第二轮去防御）

Status: implemented

## Problem

0.5.0 的"在边界校验一次，之后信任契约"只清了一部分。审计（2026-09-20）发现剩余的过度防御有三类：
消费方重算/重验生产方已经保证的契约（且两边已漂移：`brief_context` 手抄的 prompt 指纹让 brief 永远拒收
`asr_clean.json`；`recap_inspect` 读一个 video-cut 从未写过的键；`assemble.py` 第二次写 manifest 时漏掉
`audio_mix_binding`）；同一文件一次运行里被哈希十几次；损坏的自产文件被吞成"缓存未命中"/"文件不存在"。
指令层面同一句免责声明写了十几个变体。

## Decision

- 上游按契约保证的字段直接取，不 `.get` 兜底、不 `isinstance` 走查：validate.py 删除复刻 lint 的
  `_validate_approved_shape`（lint 补 `math.isfinite` 与时间顺序检查）；review/brief/assemble/recap 对自建结构
  与上游产物直接索引；剪映 builder 只信任 `jianying_timeline_contract.normalize_timeline`，导出入口只归一化一次。
- 自产文件损坏一律抛错：understanding 的 `get_video_duration`、`consolidate._load`、`understanding_brief` 加载器；
  voiceover 的 dub ffmpeg 调用检查返回码、`_run_asr` 畸形响应抛错、缓存 sidecar 损坏抛错；recap 的
  `mimo_qc_report._stage_reports`、`recap_inspect._load_optional`。缺失文件仍按各自语义处理（缓存未命中 / 阶段未跑）。
- CLI 组合检查只在 API 层做一次；`CONFIG.get(key, default)` 对已声明键改为 `CONFIG[key]`；未读取的 CONFIG 键删除。
- 保留：agent 手写输入与第三方响应的解析防御；文档标明的建议型/失败开放阶段（MiMo QC、解说评审、剪映导出）；
  `--require-final-qc` 文档标明的形状检查；recap 对 adoption 三件套的独立验证（tamper boundary）。
- 指令：跨技能共识只在拥有它的技能里写一次；删除"不发布不调度"等没有对应工具的禁令与描述代码行为的禁令。

## Alternatives considered

- **保留兜底但加测试证明其可达** — 最强理由：不改行为零风险。否：审计证明多数兜底不可达，可达的那几处正是 bug 的藏身处；
  测试只会把漂移固化。
- **为损坏的自产文件保留"当作缺失"语义** — 最强理由：续跑更宽容。否：损坏被静默当成缺失后，后续阶段用空数据继续，
  错误在更远处以更难懂的形式出现。

## Consequences

- **收益**：skills 脚本约 −530 行净减；三个真实 bug 修复；错误在发生处报出。
- **代价**：测试夹具必须写完整契约字段（`tests/assemble/tts_fixtures.py` 提供 voiceover 形状）；手改产物的调试流程会更早失败。
- 关联：[[2026-09-20-slim-skill-layer]]；哈希/指纹整体移除另立笔记。

## Verification

`scripts/test.sh` 七组全绿；`ruff check skills tests` 无告警。
