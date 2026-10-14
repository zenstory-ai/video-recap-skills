# AGENTS.md

## 仓库是什么

把一段或几段视频做成中文解说成片的 skill 包。`skills/` 下七个技能：六个生产技能加一个按需参考技能。

- `video-recap`：编排器，以子进程依次调用 `video-understanding` → Agent 按 `video-script` 写计划与旁白 → `video-cut`（cut 模式）→ `video-voiceover` → `video-assemble`。
- `video-reference`：不在流水线上，按需把成片拆成 `production_reference.json`。
- 各阶段拥有什么、入口脚本和功能子包见 `docs/architecture.md`；能力边界与验收原则见 `docs/production-boundaries.md`。
- 分发渠道：Claude 插件（`.claude-plugin/`，OpenClaw 的 `plugins install` 也读它）、源码 Release（`.release/policy.json`）和 ClawHub（`.clawhub/`）。

## 改动前必须知道的规矩

- **每个 skill 自包含**：`skills/<name>/scripts/`（含子包）只 import 本技能的模块，源码与 markdown 不出现兄弟 skill 的路径；阶段之间只交换 `work_dir` 里的 JSON / MP4。不要把刻意复制的函数抽成共享模块：副本由 `tests/orchestrator/test_brief_narration_parity.py`、`test_audio_policy_parity.py` 守，改一处须同批改副本（见 `architecture/2026-06-14-self-contained-skills-duplicated-libs.md`）。
- **结构上限**：每个脚本模块不超过 800 行，skill 内 import 图无环（`tests/orchestrator/test_test_suite_architecture.py`）。
- **运行时只用 Python 标准库和 ffmpeg / ffprobe**，README 承诺不需要 `pip install`；最低 Python 3.10（README），CI 用 3.12。
- **创作产物归 Agent**：`clip_plan.json`、`narration.json` 等由 Agent 写，脚本只准备 brief、做确定性校验、执行 TTS 与渲染（`architecture/2026-05-18-agent-owned-narration-cli-mechanical.md`）。
- **远程调用必须写明**：任何把数据发出本机的路径，都要在对应 `SKILL.md` 写清发什么、发给谁、怎样关闭；dub 克隆原声必须显式 `--confirm-voice-rights`，没有环境变量形式（`process/2026-10-03-remote-call-disclosure-and-dub-consent.md`）。
- **SKILL.md frontmatter 单行键值**：`description` 写成一行，不用 `>` / `|` 块，不超过 1024 字符（`test_plugin_manifest.py` 守）。

## 验证

与 CI（`.github/workflows/skill-validate.yml`）一致：

```bash
ruff check skills tests scripts      # CI 固定 ruff==0.15.11，规则见 pyproject.toml
python3 scripts/test.py              # 全量，每个 skill 组一个独立 pytest 进程
python3 scripts/test.py orchestrator # 单组；组名见 scripts/test.py 的 GROUPS
python3 -m pytest tests/script -q    # 调试单组
```

- 不要直接跑根目录 `pytest` 或 `pytest tests/`：各 skill 都有自己的 `lib.py`，根级 `conftest.py` 会拒绝跨组收集。
- 新增测试目录必须加进 `scripts/test.py` 的 `GROUPS`，架构测试会拦漏跑；测试分层与写法约束见 `tests/README.md`。
- 改 SKILL.md、frontmatter、插件清单或打包相关文件后至少跑 `orchestrator` 组；真实渲染用例需要带 libass / drawtext 的 ffmpeg，缺失时会失败或跳过，结果里要说明。

## 版本与发布

- `CHANGELOG.md` 遵循 Keep a Changelog，小节名用 `Added` / `Changed` / `Deprecated` / `Removed` / `Fixed` / `Security` 六个英文类别，正文中文；收紧到会拒绝旧输入的改动记入 `Changed`。按惯例，行为、契约或交付物有变化的 PR 在 `## [Unreleased]` 下记一条。
- 版本号权威是 `.release/policy.json` 的 `versionFiles`：`VERSION` 与 `.claude-plugin/plugin.json` 的 `version` 一起改，并加带日期的 `## [x.y.z] - YYYY-MM-DD` 标题；只在单独授权的发布准备中切版本。发布流程见 `docs/source-release.md`。
- ClawHub：`.clawhub/publish.json` 显式列出每个技能，`packageDigest` 锁定包字节。任何改动 `skills/<name>/` 下文件的 PR，都要给该条目升 semver，同步 `video-recap` 的 `skill:` 依赖版本，并用 `validate-clawhub.yml` 固定的中央 `clawhub_release.py lock` 重新锁定；规则见 `.clawhub/README.md`。
- 未经单独授权不推 tag、不手动触发发布或 ClawHub 工作流；`CLAWHUB_QUEUED` 只表示派发被接受，不等于已发布或扫描通过。

## 重要改动必须留笔记

决策笔记放在 `.agents/notes/{proposed,implemented,rejected}/{feature,bug-fix,simplification,architecture,process,testing}/yyyy-mm-dd-topic.md`，沿用 DeepSeek Harness 的 agent-notes 约定（方法见 https://github.com/czm15053/write-notes-like-deepseek）。正文中文，小节标题保留英文。

1. 非平凡改动（改了行为、架构、跨文件或跨 skill 契约、流程与工具链、测试策略、落盘格式）动手前，先在 `.agents/notes/` 里搜同主题旧笔记：有归属就地更新事实；没有就先写 `proposed/`，落地时随同代码改动在同一次提交里转 `implemented/`。纯机械改动（排版、改名、版本号、不改行为的补丁、常规 CRUD）直接提交，不写。
2. 笔记开头是 `# Agent Note: <标题>`、空行、`Status: <状态>`（与所在目录一致，如 `implemented`）。每篇必有 `## Problem`、`## Decision`（现在时，只写已落地事实）或 `## Proposal`、`## Alternatives considered`（每个被否方案先写它最强的理由再写为何不用，没考虑过的不要编）、`## Consequences`（收益和代价都写）。
3. 禁止把一篇笔记改写成相反的决定：事实（路径、名字、默认值）就地改；决定翻转就另开一篇并互链。
4. 不建 `INDEX.md`，目录位置就是状态；检索用 `rg --hidden .agents/notes/`。
