# Agent Note: 第二、三轮全仓清理——消除门禁误杀、补齐运行期与跨平台边界并同步契约文档

Status: implemented

关联笔记：[[2026-11-10-scripts-for-hands-and-floor-agent-for-craft]]、[[2026-11-14-de-jargon-relax-craft-gates-and-split-promo-copy]]、[[2026-10-02-video-reference-skill]]。

## Problem

首轮去黑话与品味门禁降级落地后，全仓库第二、三轮逐文件复查检出五类深层遗留问题：

1. **底线门禁在合法边界场景误杀或漏检**：
   - `shot_review.py` 在绑定 `clip_plan_validated.json` 后仍把单段原片镜头内部的闪光灯、雷电、爆炸火光（两端切点均远离 `plan_joins`）判为阻断性 `flash_slivers` 终止 `cut.py`；且 `_associate_plan` 用 3 位小数四舍五入的 `output_start` 做 `bisect_left` 代替精确 `frame_count`，导致约半数拼接点产生 1 帧偏移。
   - `cut_contract.py` 未读取 `clip_plan.json` 声明的 `"allow_overlap": true`，冷开场前置高潮钩子被源区间重叠硬阻断；`sentence_boundaries.py` 对首尾无缝紧贴的连续切片（如 `[10, 40]` + `[40, 60]`）在吸附时误扩越界或裁出重句跳切；且被 `min_clip_duration` 跳过的过短片段未记入 `qc.warnings`。
   - `packaging_studio.py` 对花字按字符数 `> 16` 一刀切报 `packaging_callout_too_long` 硬阻断；`slot="auto"` 自动选位时未避让活跃字幕安全框 `sub_rect`；且在不烧录硬字幕（`--no-burn-subtitles` / 外挂 `.srt`）或留白段自动跳过 `original_subtitles.json` 时仍按假想字幕区间误报 `packaging_overlay_collision`。
   - `speech_ownership.py`、`briefing/timeline.py` 与 `materials.py` 在 `cut_output` 模式下遇到源片已实测 ASR（含从 `--use-materials` 素材库恢复的场景）但保留片段无对白时，仍触发 `source_sentence_anchors_unavailable` 死锁；`recap_runner.py` 在 `speech_boundary_anchors_output.json` 缺失时早退不生成；`detect.py` 句末正则漏掉非小数点英文句号 `.` 或误命中 `...` 尾点与 `Dr.` 缩写；行内圆括号在预检字数、TTS 朗读与字幕切分三处口径不一致。
   - `video-reference` 的 `reference_check.py` 在 `R8` 导出复扫中对固定 schema 键名（含 `_TARGET_OBJECTS` 导出的键）与受控枚举值（含 `applies_to[]` 数组元素）存在短英文名误伤，`_TIMECODE` 误伤 `"9:16"` 画幅比，`_QUOTED` 误收通用创作词。
2. **运行期静默退化、路径转义与数据截断缺陷**：
   - FFmpeg 滤镜路径转义在单引号包住的 `filename='...'` 中写成 `\'`，遇到含英文单引号 `'` 的目录直接渲染失败；`packaging_plates.py` 缺少 `Pillow` 时无告警退化且顶栏/副标题存在排版溢出；`detect.py` 负静音起点错位及 `detect_speech_boundary_anchors` 硬编码 120 秒超时未捕获异常；`asr.py` `int(total_duration)` 截断尾音；`storyboard.py` 剪后故事板未全片均匀抽样；`tts_audio.py` 非 16-bit WAV 解码与 `NaN`/声道对齐边界；`sfx_mix.py` 在 `cut` 模式误回退原片时钟且长片全轨 Python 浮点混音占用百兆内存与数十秒耗时；`assemble.py` 漏传横屏 `.srt` 画布且未优先采用 `promo_copy.json` 封面秒数；`cut_cli.py` 与 `final_qc.py` 的 `shot_review.json` 媒体身份与参数缓存判定不全；`video-reference/scripts/lib.py` 的 `ffprobe` 漏传 `:stream_side_data=rotation` 导致现代竖屏 MP4/MOV 旋转未生效。
3. **测试隔离性与跨平台（Windows）隐患**：
   - `tests/assemble/test_pure_assemble.py` 调用 `assemble.main()` 未隔离 `_preflight_burn_subtitles`，在无 `libass` 宿主机上原地篡改全局 `lib.CONFIG["burn_subtitles"]` 污染后续单测；部分集成测试漏标 `burn_subtitles=False` 或 `skipif`；`tests/voiceover/test_over_budget_text.py` 残留合并前旧路径；子进程调用缺少 UTF-8 与 Windows 基础环境变量保护。
4. **文档、配置默认值与周边工具契约偏差**：
   - `config-playbook.md` 中 `SUBTITLE_ORIGINAL_IN_GAPS` 与 `SUBTITLE_MASK_OPACITY` 默认值偏差；`--subtitle-y-top/bot` 默认遮罩时机（`narration` vs `all`）、`original_subtitles.json` 裁剪分块、`sfx` 与剪映轨道范围、`cover.jpg` 触发条件、`reference-schema.md` 的 `derived.narration_chars_per_s.value` 路径及 `examples/guohuo-60s/`、`demo-pacing` 描述与真实实现不一致；`promo_copy.json` 需在 `recap_inspect.py` 与审片台归一化展示。
5. **残留营销包装词与提示语中英混排**：
   - `README.md` / `README.en.md` 与文档中残留“精装包装/录音室级混音/真峰值锁死/铁律/红线”等绝对化或宣传措辞；部分脚本错误提示与审片台卡片存在生硬直译或中英拼贴。

## Decision

1. **精准收束误伤门禁并消除切点偏移**：
   - `shot_review._associate_plan`：优先用精确 `frame_count` 累加定位拼接帧索引，仅保留首尾或 `plan_joins`（`±1` 帧）附近的剪辑残帧在阻断性 `flash_slivers` 中，单段原片内部闪白记为 `SOURCE_INTERNAL_FLASH_ADVISORY`。
   - `cut_contract.py` + `sentence_boundaries.py`：支持 `"allow_overlap": true` 放开冷开场同源复用，将跳过短镜记入 `qc.warnings`（`clip_too_short_skipped`），并在句界吸附前锁定原本无缝紧贴的同源切点、收紧 `allow_overlap=False` 的后方上限。
   - `packaging_studio.py` + `packaging_plates.py` + `assemble.py`：花字按加权字宽（含 `subtext`）分级缩放并做画布右缘钳制，`slot="auto"` 避让活跃字幕框，并由 `assemble.py` 传入真实参与烧录的字幕区间（不烧录硬字幕时为零碰撞）；无 `Pillow` 时向 `visual_qc.json` 透传 `packaging_text_renderer_unavailable`。
   - `briefing/timeline.py` + `speech_ownership.py` + `materials.py` + `recap_runner.py`：在已实测 ASR（含素材库恢复）且保留片段无对白时标记 `speech_absent: true`，素材库恢复时清理陈旧 `asr_timing_evidence.json` 并核验 `asr_result` 身份，续跑时自动补建缺失的 `speech_boundary_anchors_output.json`；`agent_text.py`、`voiceover.py` 与 `subtitles/core.py` 统一保留 `(FBI)` 等正文圆括号并同步剥离闭集语气提示词；`reference_check.py` 补齐 `R8` schema 键名与 `applies_to[]` 枚举元素白名单、画幅比与通用词过滤。
2. **修复运行期、性能与跨平台测试边界**：
   - 统一 `lib.escape_ffmpeg_filter_path` 单引号安全转义（`'\\\''`）；修复 `video-reference` `:stream_side_data=rotation` 竖屏探测、`detect.py` 负静音起点与省略号/缩写过滤及动态超时捕获、`asr.py` 尾音分段、`storyboard.py` 全片均匀抽样、`tts_audio.py` 多位深/浮点 WAV `NaN` 与声道帧对齐、`sfx_mix.py` 局部窗口混音与 `cut` 时钟保护、`assemble.py` 横屏 `.srt` 画布与 `promo_copy.json` 封面秒数、`cut_cli.py` / `final_qc.py` `shot_review.json` 校验，以及审片台 `run_state` 仅校验清单文件并安全渲染归一化后的 `promo_copy.json`。
   - 为测试组增加 `lib.CONFIG` 快照自动恢复 fixture，补齐无 `libass` 环境下的 `burn_subtitles=False` / `skipif` 标注，修正 `test_over_budget_text.py` 路径及 Windows 子进程 UTF-8 / 基础环境变量保护，并加固 `tests/orchestrator/` 架构守卫。
3. **对齐文档、配置、示例与纯中文表达，并升级默认 MiMo VLM 模型**：
   - 因 MiMo 平台已下线 `mimo-v2.5`（返回 `Unsupported model mimo-v2.5`），将 `DEFAULT_MIMO_MODEL` 更新为 `mimo-v2.6-flash`（支持通过 `MIMO_MODEL` 切换 `mimo-v2.6-pro` 等），并令 `_prepare_api_payload` 对所有含 `-tts` / `-asr` 的模型（含 `mimo-v2.5-tts-voiceclone`）跳过注入 `thinking` 字段。
   - 全面校准 `SKILL.md`、`config-playbook.md`、`data-schema.md`、`packaging.md`、`reference-schema.md`、`README.md` / `README.en.md`、`docs/`、`examples/guohuo-60s/`、`examples/resource-library/`、`tools/measure_subtitle.py` 及未绑定英文测试断言的 CLI/QC 提示语。

## Alternatives considered

- **无 `Pillow` 时直接抛错终止 `assemble.py`** — 最强理由：杜绝任何无字占位贴片进入成片。不采用：CI 矩阵（`ubuntu/macos/windows`）按零第三方 Python 依赖运行，不安装 `Pillow`；保留标准库底板供几何与组装单测运行，同时标记 `text_rendered: false` 并向 `visual_qc.json`（及下游 `final_qc.json` 的 `metadata.warnings`）透传 `packaging_text_renderer_unavailable` 告警与终端提示，既守住零 `pip install` 测试矩阵又消除无感退化。
- **让 `--subtitle-y-top/bot` 默认把 `SOURCE_SUBTITLE_MASK_TIMING` 切为 `all`** — 最强理由：无需额外环境变量即可激活 `original_subtitles.json`。不采用：多数带硬字幕的中文源片在未编写 `original_subtitles.json` 时，留白段需要直接露出原片自带硬字幕；保留默认 `narration` 时机并在文档写明需全片重排原声字幕时加设 `SOURCE_SUBTITLE_MASK_TIMING=all` 更安全。
- **在 `recap.py` 新增 `--allow-overlap` CLI 参数** — 最强理由：与 `cut.py --allow-overlap` 对称。不采用：违反 [[2026-11-06-one-entry-per-capability]] 不跨层复制低频 CLI 开关的原则；冷开场复用高潮镜由 `clip_plan.json` 声明 `"allow_overlap": true` 最直观。

## Consequences

- **收益**：消除原片内部闪光、拼接点帧取整偏移、连续紧贴切片、冷开场前置钩子、无对白或素材库恢复成片、中英混排括号口播、不烧录字幕包装及外语拆片等场景下的误杀与运行期故障；长片 SFX 混音内存与耗时大幅下降；无 `libass` 宿主机与 Windows 环境测试完全隔离可复现；全仓文档、示例与代码行为严丝合缝。
- **代价**：`examples/guohuo-60s/clip_plan.json` 的 `reason` 改为自然中文短句后同步更新了 `tests/orchestrator/test_guohuo_example.py`；涉及 `skills/video-recap/` 与 `skills/video-reference/` 变更，需重新锁定 `.clawhub/publish.json` 的 `packageDigest`。
