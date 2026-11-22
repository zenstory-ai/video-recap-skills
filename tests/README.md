# 单测体系

本仓库按职责分为 4 个独立测试组，由 `scripts/test.py` 逐组在独立 `pytest` 进程中运行，避免跨技能模块名污染。

## 测试分组（`scripts/test.py::GROUPS`）

| 组名 | 覆盖范围 |
|---|---|
| `core` | `skills/video-recap/scripts/` 核心模块（`_common`、`_audio`、`_subtitles`、`probe`、`frames`、`transcribe`、`tts`、`render`、`check`）的行为单测与真实 `ffmpeg` 合成体检 |
| `companions` | 4 支配套工具（`export_jianying`、`dashboard`、`packaging`、`library`）的行为、安全边界与 `timeline.json` v3 契约测试 |
| `reference` | `skills/video-reference/scripts/analyze_reference.py` 的解析单测与真实视频分析测试 |
| `orchestrator` | 全仓架构与预算检查：技能字符预算、Python 行数/文件数上限、核心→配套单向隔离、无环导入、隔离 `python -I` 导入、UTF-8 编码、插件清单与环境变量审计 |

## 写法约束

- 直接测试公开函数或真实 CLI 产物，不靠正则匹配源码里的提示词文案来证明行为。
- 文本 I/O 必须显式传入 `encoding="utf-8"`（由 Ruff `PLW1514` 与 AST 架构测试双重守护）。
- 新增测试目录必须同步登记到 `scripts/test.py::GROUPS`；完全重复的测试函数体应合并或参数化。

## 运行命令

```bash
python3 scripts/test.py                 # 全量，逐组隔离
python3 scripts/test.py core            # 单组（core / companions / reference / orchestrator）
python3 -m pytest tests/core -q         # 调试单组
PYTHON=.venv/bin/python bash scripts/test.sh core
```

不要直接运行根目录 `pytest` 或 `pytest tests/`，根级 `conftest.py` 会主动拒绝跨组收集。
