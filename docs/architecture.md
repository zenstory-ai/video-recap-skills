# 仓库架构说明

本仓库包含两个各自独立的技能：日常做片主技能 `skills/video-recap/` 在同一目录内串联完成视频理解、定主线与选镜、切镜头、解说词检查、TTS 配音及混音加字幕；参考片拆解技能 `skills/video-reference/` 在需要分析优秀成片时按需调用，生成可复用的 `production_reference.json`。各技能的完整命令与参数见对应目录下的 `SKILL.md` 及 `--help` 输出；历次架构取舍笔记可在 `.agents/notes/` 下检索。

## 技能分工与数据交接

```text
                               skills/video-recap/（做片主技能，由 recap.py 串联全流程）
                                                        │
understand.py ─▶ Agent 制定方案并写 clip_plan.json / narration.json ─▶ [cut.py] ─▶ validate.py ─▶ voiceover.py ─▶ assemble.py
```

- **创作各环节连贯衔接**：定故事主线（`recap_story_plan.json`）、分声音归属（`visual_audio_board.json`）、挑镜头剪辑（`clip_plan.json`）和写解说词（`narration.json`）都在同一个流程里完成，不用在多个技能目录之间来回切换（见 `architecture/2026-11-10-converge-production-skills-into-video-recap.md`）。
- **参考片拆解按需独立运行**：`video-reference` 不在默认做片流程里，只在用户需要拆解优秀成片时单独运行；导出的 `production_reference.json` 只供写稿 Agent 阅读参考，做片脚本不会读取或打分（登记为资源库模板并通过 `--project` 绑定时，也只是把副本拷进 `work_dir`）。
- **每个技能各自独立**：两个技能都能单独拷贝安装；`skills/<name>/scripts/`（含子包）只 `import` 本技能内部的模块，源码与文档都不引用另一个技能的路径。
- **Agent 负责创作取舍，脚本负责执行与基础检查**：`clip_plan.json`、`narration.json`、`recap_story_plan.json` 和 `visual_audio_board.json` 都由 Agent 编写；Python 脚本负责生成写稿简报、检查客观硬伤（拦住极短闪帧、切碎半句台词、旁白抢话、时间窗装不下配音、裁丢必保画面和残留示例占位符）、调用 TTS 接口以及用 ffmpeg 渲染合成（见 `architecture/2026-05-18-agent-owned-narration-cli-mechanical.md` 与 `simplification/2026-11-10-scripts-for-hands-and-floor-agent-for-craft.md`）。
- **统一以 `timeline.json` 为时间线基准**：最终交付以 ffmpeg 渲染出的成片和通用时间线文件 `timeline.json` 为准，剪映草稿直接读取同一份 `timeline.json` 按需导出（见 `architecture/2026-06-14-timeline-json-canonical-jianying-optional.md`）。

## 技能与内部模块划分

| 技能 / 阶段 | 核心职责 | 顶层入口脚本 | 内部功能子包 |
|---|---|---|---|
| `video-recap`（流程编排与公共工具） | 全流程串联、断点续跑核对、看片缓存与素材库管理、出片前质检、环境自检、本地只读审片页（dashboard） | `recap.py`、`recap_inspect.py`、`library.py`、`dashboard_server.py`、`doctor.py`、`final_qc.py` | `dashboard/`、`resources/` |
| `video-recap`（理解阶段） | 切镜头、ASR 语音转写、VLM 抽帧看画面、静音与台词停顿点检测，并生成供 Agent 写稿的 `agent_narration_brief.md`（含 `cut` 模式剪完后的成片时间轴简报） | `understand.py`（传 `--brief-only` 仅重建简报） | `briefing/` |
| `video-recap`（写稿与检查阶段） | 故事主线、剪辑与旁白规范，解说词时长与断句检查，Agent 出片前自查清单 | `validate.py` | — |
| `video-recap`（剪辑阶段） | 按 `clip_plan.json` 对齐台词停顿切镜头，输出 `edited_source.mp4` 与 `clip_plan_validated.json`；扫描成片里的极短闪帧与密集切点 | `cut.py`、`shot_review.py` | — |
| `video-recap`（配音阶段） | 解说旁白分段语音合成（支持 MiMo、Fish Audio、自托管 IndexTTS） | `voiceover.py` | `providers/` |
| `video-recap`（合成阶段） | 多轨混音、压低原声（ducking）、生成并烧录字幕、输出 `timeline.json`、混音质检、导出剪映草稿 | `assemble.py`、`export_jianying.py` | `subtitles/`、`jianying/` |
| `video-reference`（独立按需技能） | 实测参考成片的镜头长短与音量起伏，校验原片剧情与通用手法是否分开（R1–R8），导出脱敏后的 `production_reference.json` | `reference.py` | — |

子包划分原则（见 `architecture/2026-09-21-scripts-subpackages-jianying.md`）：`scripts/` 顶层保留命令行入口与主干模块；只在特定环节用到的代码（如简报生成 `briefing/`、TTS 接口 `providers/`、字幕生成 `subtitles/`、剪映草稿导出 `jianying/`、本地只读审片页 `dashboard/`、素材库绑定与资源记录 `resources/`）各自放进独立子包。

## 编排器内部路由

`--edit-mode` 与 `--audio-mode` 的合法组合及暂停点定义在 `skills/video-recap/SKILL.md` §1 的总表中，对应 `skills/video-recap/scripts/recap_runner.py` 中的两条执行分支：

- `_run_single` 与 `_run_multi_cut`：分别处理单视频 `full / cut` 与多视频 `cut` 主流程。
- 两条路径收尾时共用 `_deliver`：（有旁白时执行 TTS 配音）→ 混音加画框字幕 → 成片质检（`final_qc.py`）；所有 `cut` 路径共用 `_render_cut`、`_reject_stale_cut_narration` 与 `_validate_cut_output_narration`。

## 架构测试检查

运行 `python3 scripts/test.py orchestrator` 会自动检查以下规则：不跨技能 `import` 或引用路径、单个 Python 模块不超过 800 行、技能内部 `import` 无循环引用、`lib.py` 只声明本技能实际读取的配置项，以及核心渲染代码不反向依赖剪映导出子包。
