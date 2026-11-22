# Agent Note: 从单体编排流水线重构为极简原子 CLI + 单一 timeline.json 总线

Status: implemented

## Problem

v0.7.0 的 `skills/` 膨胀到 99 个 Python 文件（24,207 行）、149 个 `CONFIG` 键、104 个环境变量、82,408 字符的技能说明文档与 50 个跨阶段 JSON 文件，带来了五类结构性负担：

1. **编排器锁死迭代**：`recap_runner.py` 与 `{size, mtime_ns}` 清单锁强制五条固定路由，用户或 Agent 只想微调一个镜头、改一句字幕或重配一句旁白时频繁触发过期拦截。
2. **品味与题材硬编码阻碍泛化**：`validate.py` / `narration_lint.py` / `packaging_*.py` 把中文短剧语速、最短镜头秒数、三套抖音精装预设、手写 PCM 音效波形与 MiMo 专属配置写死进核心脚本，且 `packaging_plan.json` 在宣发文案与视觉布局规范间发生 schema 冲突。
3. **重型脚本替代前沿模型视觉判断**：`vlm.py`、`briefing/` 与 2,258 行补偿 15 秒粗窗 ASR 的句界推断逻辑，不如让宿主多模态 Agent 直接看抽帧拼图（contact sheet）与本地/外挂精细字幕。
4. **配套功能与核心强耦合**：看板（3,450 行）、竖屏包装（2,237 行）、素材库（1,676 行）与剪映导出（1,666 行）反向穿透 `assemble.py` 与 `recap_runner.py`，读十几份阶段 JSON。
5. **参考拆片过度官僚化**：`video-reference` 维护 523 行 R1–R8 封闭校验门禁，只为产出下游并不机械读取的 `production_reference.json`。

## Decision

保留 `1 + 1` 技能拓扑（`skills/video-recap/` + `skills/video-reference/`，兼容 `.claude-plugin/` 与 `.clawhub/publish.json` 既有 slug），将整套实现重构为**无状态原子 CLI + 单一 `timeline.json` v3 机器总线 + 纯 Markdown 创作工作区**：

- **6 支核心原子 CLI（`skills/video-recap/scripts/`）**：`probe.py`、`frames.py`、`transcribe.py`、`tts.py`、`render.py`、`check.py`，共用 `_common.py`、`_audio.py`、`_subtitles.py`（纯 Python 3.10+ 标准库 + `ffmpeg`/`ffprobe`）。
  - 宿主优先：优先使用本机字幕/Whisper、本机 TTS 与宿主 Agent 原生看图，仅在显式配置或指定云端引擎时联网；`tts.py` 按内容哈希缓存并实测真实时长，绝不对人声变速挤压。
  - 机械正确性内建于 `render.py`（CFR 帧精确裁切、背景画布、闪帧防护、统一闪避混音、`-14 LUFS` / `-1.5 dBTP` 响度闭环、安全区字幕换行）；`check.py` 只执行 7 项物理健康检查并原子覆写 `check.json` v1，彻底取消主观审美阻断门禁。
- **4 支单向叶工具**：`export_jianying.py`、`dashboard.py`（静态 `review.html` + 只读 `127.0.0.1` 服务）、`packaging.py`（数据驱动 JSON 主题 + 宿主机渲染 + 防撞原声音效校对）与 `library.py`（扁平 `catalog.json` + 跨机安全素材缓存 + 授权/音色同意审计）。所有叶工具只消费 `timeline.json` / 工作区文件，核心模块永不反向导入叶工具。
- **单文档单归属工作区**：`BRIEF.md`、`MEDIA_NOTES.md`、`EDIT_PLAN.md`、`PROMO.md`（消除 `packaging_plan.json` 冲突）、`timeline.json`、`check.json`，删除全部 `{size, mtime_ns}` 清单锁。
- **精简 `video-reference`**：收敛为单一本地 CLI `analyze_reference.py`（切点/节奏/响度/拼图）+ `SKILL.md`，引导 Agent 产出脱敏 Markdown `REFERENCE_NOTES.md`，移除 R1–R8 JSON 校验链。
- **CI 防膨胀硬护栏（`tests/orchestrator/`）**：在测试中锁死 `SKILL.md <= 4,000` 字符、单份 reference `<= 2,500` 字符、全部 `skills/**/*.md <= 22,000` 字符、单文件 Python `<= 550` 行、全仓运行时 Python 总量 `<= 5,000` 行、单技能 `<= 20` 个 `.py` 文件、环境变量总数 `<= 25` 个，以及核心 → 配套单向导入隔离。

## Alternatives considered

- **仿照 `novel-to-game` 彻底零脚本（纯 Markdown 技能）** — 最强理由：零代码维护面、极致通用。否决原因：大模型能直接输出游戏代码，但无法直接解码二进制视频流、计算精确 CFR 裁切/闪避表达式/响度 LUFS 与打包剪映 MD5 草稿；每次现场手写复杂 ffmpeg 滤镜图反而更脆弱、重试成本更高。
- **拆为 5–6 个微服务式独立技能（`video-edit` / `video-packaging` / `video-jianying` / `video-materials` / `video-dashboard` / `video-reference`）** — 最强理由：ClawHub 可按需单装。否决原因：ClawHub 每技能独立打包且禁止跨技能文件依赖，过度拆分会重新引入多份公共工具副本、增加发包锁维护成本，并割裂单次剪辑会话中对同一份 `timeline.json` 的协同修改。
- **在原 24K 行编排器上渐进删减** — 最强理由：旧测试短期改动小。否决原因：`recap_runner.py`、149 键 `CONFIG` 与 50 个中间 JSON 深度互相咬合，局部修补无法消除清单锁和跨模块耦合，直接切换至单一 `timeline.json` 总线更干净。

## Consequences

- **收益**：运行时 Python 代码从 24,207 行收敛到 5,000 行预算内（约降 80%+），技能散文从 82K+ 字符收敛到 ~14.5K 字符；适用面从中文短剧解说扩展为通用多语种/多题材视频剪辑与解说；任意环节可局部重跑，同时完整保留审片台、剪映草稿、竖横屏包装与素材库四大能力。
- **代价**：不兼容旧版 `recap.py` / `understand.py` / `cut.py` / `validate.py` / `voiceover.py` / `assemble.py` CLI 与旧 `clip_plan.json` / `narration.json` 中间产物（历史工作流仍可在 `v0.7.0` 标签下使用）；旧 `tests/{understanding,script,cut,voiceover,assemble}` 替换为 `tests/{core,companions,reference,orchestrator}`；ClawHub 条目需同步升版本并重锁 `packageDigest`。

## Verification

- `uv run --no-project --with ruff==0.15.11 ruff check skills tests scripts`
- `uv run --no-project --with pytest==9.0.2 --python 3.12 python scripts/test.py`（覆盖 `core`、`companions`、`reference`、`orchestrator` 四组）
