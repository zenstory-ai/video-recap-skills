# Agent Note: 测试按 skill 分组，每组独立 pytest 进程

Status: implemented

## Problem

每个 skill 自带同名顶层模块（`lib.py`、`narration.py` 等，见 [自包含 skill](../architecture/2026-06-14-self-contained-skills-duplicated-libs.md)）。单进程 `pytest tests/` 会把多个 skill 的同名模块装进同一个 `sys.modules`，报出误导性的 import 冲突。此外曾有测试组只登记在 `scripts/test.sh` 而 CI 跑的是 `scripts/test.py`，22 个 inspect 测试从未在 CI 执行过。

## Decision

- `scripts/test.py` 是唯一入口：`GROUPS` 逐组以子进程运行 `python -m pytest tests/<group> -q -rs`；`scripts/test.sh` 只是转调它的兼容壳。CI 在 ubuntu / macOS / windows 上跑 `ruff check skills tests scripts` 与 `python scripts/test.py`，且对每个 PR 都跑（不加路径过滤，避免 docs-only PR 在分支保护下卡死）。
- 根 `conftest.py` 的 `pytest_cmdline_main` 拒绝无参数、`.`、`tests/` 或跨组的收集（`UsageError` 指向规范命令）；只允许单组或单文件调试。
- `test_canonical_runner_includes_every_test_group` 断言 `GROUPS` 恰等于 `tests/` 下含 `test_*.py` 的子目录集合：新增测试目录 must 登记，漏跑会红。
- 加载兄弟 skill 的 `lib.py` 时用 `importlib` 创建单独命名的模块实例，never `importlib.reload` 活的 `lib` —— video-cut 的 `lib.py` 没有 `_EXISTING_CONFIG_REF` 重导入保护，reload 会让 `lib.CONFIG` 与 `cut_cli` 持有的引用分裂。
- 分层（`tests/README.md`）：纯行为测试、产物测试、契约 / 打包测试；never 通过读源码匹配一句文案来证明行为，声明式契约（SKILL.md、prompt）解析其结构而不是散落短语；精确重复的测试体由架构测试拒绝，语义重复靠评审。ffmpeg 相关单测不依赖真实 ffmpeg，真渲染测试用 `shutil.which` 守卫并以 `-rs` 让跳过可见。
- `pyproject.toml` 显式声明 ruff 规则集 `E4/E7/E9/F`：CI 不固定 ruff 版本，隐式默认集在 0.16 扩大后会让无关 PR 变红；`tests/**` 忽略 E402，因为测试要先把 skill 的 `scripts/` 放进 `sys.path`。

来源：080b22b、02e402f (#13)、c7ab23d (#12)、9a2c1c9 (#40)、468182c (#63)、c4da353 (#65)

## Alternatives considered

- **`scripts/test.sh` 与 `scripts/test.py` 各自维护分组列表** — 最强理由：shell 用户与 Windows 用户各有原生入口。否：两份列表必然漂移，#28 的 inspect 组只进了 test.sh 导致 CI 漏跑；现在 test.sh 只转调。
- **CI 里用内联 python 检查 frontmatter、manifest、prompt 锚点** — 最强理由：不需要 pytest 也能跑。否：Windows runner 的 cp1252 默认编码让内联脚本读 CJK 文件崩溃，且检查逻辑不在测试树里无人维护；改为 pytest 行为 / 结构测试。
- **`importlib.reload(lib)` 探测环境变量对 CONFIG 的影响** — 最强理由：一行搞定。否：rebind 后其他模块持有旧 dict，后续测试 patch 的对象与代码读取的对象不同，顺序相关。

## Consequences

- **收益**：没有模块串扰；每组可单独调试；分组登记有门禁。
- **代价**：全量要起七个解释器进程，比单进程慢；跨组共享 fixture 不方便；每个 skill 的 parity 与自包含约束意味着相同行为只保留一套行为测试、其余靠字节一致与隔离导入证明。
