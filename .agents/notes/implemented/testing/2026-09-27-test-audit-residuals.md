# Agent Note: 去哈希与分包之后的测试残余审计

Status: implemented

## Problem

test-slim 1/6–6/6 瘦身之后，又有三批生产改动落地：去掉全部内容哈希与消费方重验（#126）、按功能族分包（#127）、
编排器单视频与多视频共用收尾（`_deliver`）。这些改动让一部分测试变成了：

- **测已删除的行为**：哈希兼容的缓存 payload 形状、`processed_wav_sha256` 字段不存在、只为已删集合服务的配置键黑名单。
- **因为无关原因通过**：dub 克隆缓存的"旧格式视为未命中"由假 `_usable_clone_wav` 强制得出——把旧格式当命中、复用过期音频时
  124 个 voiceover 测试全过；`_text_char_count` 的去标点逻辑无人守护，换成 `len()` 时 script 组全过；
  `--voice-r` 缩写用例其实被 argparse 的 `allow_abbrev=False` 拦下；叠加 overview 不降级的用例在 250 字 ASR 下无论如何都是 "rich"；
  330 Hz 对照音从未进入流水线。
- **钉住只有测试在用的接口**：`REQUIRED_PUBLIC_EXPORTS` 表与四个入口的再导出、assemble 里只有测试传入的注入参数、
  剪映的 `material_category_registry` 与一条被时间线契约挡住、永远走不到的分支、`render_preflight` 的死分支、
  `recap_inspect` 为"未来格式"预留的 `forward_state_files`。
- **重复**：多视频 approved-text 转发（共用 `_validate_cut_output_narration` 后与单视频重复）、`test_local_adoption_boundaries.py` 三个用例、
  两份布局 QC、两份 brief 泄漏检查等。

## Decision

按 test-audit 流程：四条只读发现线逐项记录"能抓到什么 / 非测试调用方 / 更强的现有证明 / 历史 / 解锁的删除"，再分三批落地：

- **补漏洞**：dub 克隆缓存测试改为写入真实 wav、去掉假函数；`_text_char_count` 增加带标点输入；`--voice-ref` 相对路径转绝对路径
  有了真实断言；未采用路径的成片可听性检查并入 `test_narration_adoption.py`，去掉假对照；
  剪映契约增加"未知轨道类型报错"一行；生成的 cut 模式 brief 断言包含密集切点检查指引（替代读取 Python 源码的检查）。
- **删生产死代码与测试专用接口**：上面列出的再导出、注入参数、注册表与死分支，生产与文档合计 +39 / −144 行。
- **删重复与失效测试**：约 20 个用例，测试合计 +102 / −673 行。

删除前对关键项做了变异验证：`_cut_narration_is_stale` 的三种变异（去掉 None 守卫、恒 False、恒 True）都会让路由级测试失败，
所以私有谓词测试可删；dub 缓存与去标点两处在补测试前后分别变异，确认旧测试不失败、新测试失败。

未做：`strict_publish` 写完 binding 后立即检查文件存在的守卫，属于严格发布路径上的"重验自己的输出"，需要另立笔记再决定。
`review.py` 的测试门面同理保留。

## Alternatives considered

- **只删测试、不动生产代码** — 最强理由：风险最小，生产行为零变化。否：再导出、注入参数和死分支存在的唯一理由就是这些测试，
  只删测试会留下无人调用的代码，下一轮审计还要再来一次。
- **一次性全部删掉每个"看起来像实现细节"的测试** — 最强理由：删除量更大。否：私有谓词或静态检查有时是唯一的独立守卫
  （如 `test_mimo_tts_refreshes_prepared_snapshot_after_settings_probe`、剪映模板 golden、`test_core_assemble_does_not_import_exporter`），
  没有证据证明被更强测试覆盖的一律保留。

## Consequences

- **收益**：两处真实覆盖漏洞补上；生产代码净减约 100 行只为测试存在的接口；测试净减约 570 行；
  每个被删用例都能指出接替它的更强测试。
- **代价**：`mimo_qc` / `cut` / `assemble` / `brief` 不再从入口模块再导出内部函数，进程内 import 这些名字的外部脚本需要改为从所属模块导入
  （仓库文档从未把它们列为公开 API）；`recap_inspect --json` 不再输出恒为空的 `forward_state_files`。

## Verification

`PYTHON=<py3.12> scripts/test.sh`（带 static ffmpeg 7.0）：understanding 183、voiceover 123、script 117、orchestrator 322、inspect 23 全绿；
cut 1 个、assemble 6 个失败与基线相同（均为 ffmpeg 环境性失败）。`ruff check skills tests` 无告警。
