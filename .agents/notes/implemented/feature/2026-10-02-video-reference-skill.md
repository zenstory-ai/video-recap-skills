# Agent Note: 按需技能 video-reference——把成片拆成不含原片事实的制作参考

Status: implemented

后续集成（资源库登记、`--project` 绑定）见 [[2026-10-02-production-reference-binding]]，尚未落地。

## Problem

手上有做得好的成片时，"它为什么好"只能靠人记住。现有资源库模板只管字幕与包装的几何参数（[[2026-09-27-resource-library-format]]），
没有地方存放叙事结构、节奏、镜头与音画分工这些做法。直接把成片丢给写稿 Agent，又会把原片的人名、台词和事件带进新作品。

owner 的约束：必须是按需能力，不能重新长成每次生产都要跑的评估层（刚删掉 MiMo QC，见 [[2026-10-02-delete-mimo-qc]]），
不加 QC 门禁，并且要保持 skill 间不共享代码（[[2026-06-14-self-contained-skills-duplicated-libs]]）。

## Decision

- 新增第七个 skill `skills/video-reference/`（`user-invocable: true`），入口 `scripts/reference.py`，子命令 `measure` / `frames` / `check` / `export`；
  模块为 `lib.py`、`reference_measure.py`、`reference_frames.py`、`reference_profile.py`、`reference_check.py`，全部带 `reference_` 前缀，不与标准库或其他 skill 重名。
- **measure**：一次 ffprobe 加一次 ffmpeg（`scale=320:-2,scdet=threshold=2` 逐帧分数 + `ebur128=peak=true:framelog=info`），
  切点取孤立峰，被压下的候选进 `shots.review_windows`，由 Agent 用 `frames` 看图后写 `labels.cut_fixes`（见 [[2026-10-02-reference-cut-detection]]）；写
  `U/reference_measurements.json`（镜头切点、镜长分布、切点密度、10 秒切点曲线（每个窗口按自身长度折算成每分钟，末尾不满 10 秒的窗口不再被低估）、整体响度/LRA/真峰值、逐秒短期响度）。
  缓存键为成片 `{size, mtime_ns}` 加是否缩放；只改分数参数时用缓存的逐帧分数重算，不重新解码。镜头统计不读 `scenes.json`：理解阶段会把短于 4 秒的镜头并掉，中位镜长会被拉高。
- **Agent 只写一个文件** `U/reference_breakdown.json`：`labels`（`audio_spans` 音轨归属、`sections` 叙事功能、`subtitles`、`basis`）、
  `source_facts`、`methods`、`skipped_dimensions`。枚举照搬写稿手册：function 为 `hook|setup|turn|escalation|payoff`，
  owner 与 narration_job 与 `visual_audio_board.json` 一致。
- **derived 只在内存里**：`reference_profile.derive()` 由 labels 与测量算出各音轨占比/块数/段内切点密度/平均短期响度、
  旁白语速（只用被旁白覆盖 ≥80% 的 ASR 窗口，ASR 失败或跳过时为 null）、声音切换落在画面切点 ±0.25 秒内的比例、
  各叙事功能的切点密度与旁白占比、分数位置的 `structure`、第一次原声出现位置。check 打印，export 写入用到的部分。
- **target 只写 `{"from": "<路径>"}`**，根只能是 `shots` / `loudness` / `derived`，路径不得带列表下标；解析结果必须是数值叶子，
  或白名单里的派生对象（`derived.structure`、`derived.first_original_at`、`derived.narration_jobs`、`derived.by_owner.<owner>`、
  `derived.by_section.<function>`）。`derived.first_original_at` 与 profile 一样只导出 `{fraction}`，`.s` 不能做 target。
  数值和 `provenance`（`derived.*` 为 `labeled`，其余为 `measured`）由 export 填入，Agent 从不手写数字。
  fact 的 `measure` 与 method 的 `measure:` 证据同样只认这三个根，且不能落在字符串叶子上（`source.size`、`schema` 之类不算证据）。
- **check（R1–R7）与 export（R8）**：封闭 schema 与枚举、标注覆盖整片、事实有时间或测量锚点且显式写 `entities`、方法有证据、
  target 可解析、`rule`/`applies_when`/`avoid_when` 的泄漏扫描（实体名来自 fact entities、`background_research.characters`、
  ASR glossary 与可选 `understanding_index.json` 的 `characters[*].name/aliases/asr_mentions`，比对前去掉空白；与 ASR 全文或
  fact statement 共有连续 8 个汉字或 5 个英文词，按无标点的汉字段与去掉全部非汉字后的整串各算一遍；绝对时间码、"第 N 秒"与"N 分 M 秒"；
  绝对路径，含 `/tmp`、`/Users` 等常见根目录的单段路径）、五维覆盖。`subtitles` 的值有类型约束（`burned` 布尔、`max_lines` ≥1 整数、
  `marks_original` 字符串、`evidence_t` 数字列表），`skipped_dimensions` 的每个值不论该维度是否已有 method 都必须是非空字符串。
  有 error 时退出码 1 且不写文件；导出后对每个键和字符串再扫一遍，并拒绝 `source_facts`/`labels`/`entities`/`evidence`/`statement`/`from`/`path` 键。
  `asr_timing_evidence.json` 的 `source_video` 与测量的成片 `{size, mtime_ns}` 不一致时给警告（理解产物可能来自另一部视频）。
- **消费方只有写稿 Agent**：video-script `SKILL.md` §2 读取清单加一条、§3 加一段：只在 `work_dir` 有 `production_reference.json` 时阅读，
  优先级"用户指令 > 本片证据 > 参考"，可在 `recap_story_plan.json` 写可选的 `reference_methods`。没有脚本对 story plan 做封闭键检查，
  所以不改 playbook 的 schema 示例。video-recap `SKILL.md` §1 加一段路由说明；`recap.py`、runner、doctor、final_qc 不变。
- 新守卫 `test_only_the_reference_skill_names_production_reference_in_scripts`：除本 skill 外，任何 `skills/*/scripts/**/*.py` 都不得出现
  `production_reference.json`。plugin manifest 不变量从"只有 router 和写稿可调用"变为三个可调用 skill。

## Alternatives considered

- **库优先（facts 与 methods 存进同一个库模板，绑定时剥离事实）**：最强理由是版本、采纳、dashboard 都现成。不选：主路径要等 recap 的
  `scripts/resources/`、`scripts/dashboard/` 迁移合入，而且原片事实会进库。库登记作为 Part 2 另行提出。
- **`--production-dir` 用自家成片的 plan 精确分块**：最强理由是自家成片的音轨与段落边界是精确真值。不选：自家成片本来就有 plan，
  参考的真实用途是外部成片；这些产物只在本次验证里当真值用。
- **silencedetect 测音轨分工**：最强理由是零标注成本。不选：设计阶段实测 302 秒成片只测出 9 段静音，分不出旁白与原声。
- **在 recap 里加"对照参考评估"**：最强理由是能自动告诉用户新片离参考多远。不选：这正是刚删掉的建议型评估层，会重新进入默认路径。
- **测量里带 ASR 摘要和开场切点密度**：最强理由是一个文件看全。不选：测量缓存只按成片身份失效，ASR 摘要会过期；
  开场切点密度与 `derived.by_section.hook.cuts_per_min` 重复。

## Consequences

- **默认路径**：没有变化。recap 不调用它，不加参数，不加 QC；有没有 `production_reference.json`，所有阶段的代码行为都一样（守卫测试保证没有脚本读取它）。
- **谁读取产物**：只有写稿 Agent（prose 指引）。`reference_methods` 是 story plan 的可选字段，没有代码读取；
  建议型评审会把 story plan 整体放进上下文，因此能看到这个字段，但不据此判定。
- **Agent 表面增量**：1 个 skill（SKILL.md 约 8.7K 字节，只在调用时加载），frontmatter description 约 150 字常驻；
  video-script SKILL.md 多 3 行，video-recap SKILL.md 多 4 行；新增 1 个 CLI。脚本约 1,315 行（6 个模块，最大 526 行），测试 100 个用例。
- **代价与盲区**：
  - 旁白占比与语速是 `labeled` 精度，受标注与 ASR 窗口限制，不是逐词对齐。
  - 切点检测认不出慢叠化和遮挡转场（详见 [[2026-10-02-reference-cut-detection]]）。
  - 泄漏扫描只拦字面：改写过的剧情、ASR 缺失时的旁白原句、7 个汉字以内的短引文都拦不住（check 给出警告）。
  - R1–R8 不判断语义：真实验证中初稿方法"旁白段用短镜头推进信息"与派生值相反，只能靠 Agent 先读 derived 再写；
    SKILL.md 与 schema 示例已据此改写。
- **删除信号**：连续两个版本没有任何运行写 `reference_methods`，也没有资源库绑定它。
- **真实验证（2026-10-02，本机 ffmpeg 8.0 / 9.0.1；以下切点数是旧的固定阈值 10 测得的，换检测器后的对照见 [[2026-10-02-reference-cut-detection]]）**：
  - 庆余年 recap（302.66 秒，1280x676）：59 个切点、60 个镜头，中位镜长 3.06 秒，11.7 切/分钟，整体 -14.8 LUFS，LRA 6.4，真峰值 -0.8。
    `clip_plan_validated.json` 的 9 个衔接点全部在 ±0.15 秒内对上切点（最大偏差 0.08 秒）。`scale=320` 与 `--no-scale` 切点完全相同，
    召回无下降，保留缩放默认；两者并行时墙钟各约 71 秒（单独 CPU 时间 36 秒 / 28 秒）。
  - Long Vacation recap（126.66 秒）：22 个切点，中位 3.0 秒，-14.6 LUFS；8 个衔接点全部对上（最大偏差 0.10 秒），耗时 32 秒。
  - 盘龙 recap（302.28 秒，3840x1598）：52 个切点，中位 2.0 秒，-14.2 LUFS；4K 解码耗时 6 分 40 秒。
  - 标注后的派生值：庆余年旁白占比 0.632（真值：TTS 时长合计 195.0 秒 / 302.66 = 0.644），LV 0.769（真值 0.769）。
    这里的音轨标注取自成片制作时的 `tts_meta.json` 落点，是"真值标注"，只证明 derive/check/export 的计算与分离正确，
    不证明 Agent 凭 ASR 标注的准确度。两部片都是原声段切点密于旁白段（庆余年 15.8 vs 9.4 切/分钟，LV 18.4 vs 7.4），
    声音切换落在画面切点上的比例很低（0.091 / 0.056）。
  - 庆余年导出的 `production_reference.json` 不含任何人名、事实、evidence、`from` 或路径；往一条方法里注入"范闲"，check 报
    `R6 … 含原片实体名「范闲」`，export 退出码 1 且不写文件。
  - **未完成（外部阻塞）**：`.env` 里的 MiMo key 在 `api.xiaomimimo.com` 与三个 token-plan 集群都返回 401 invalid_key，
    理解阶段（ASR + VLM）跑不起来。因此没有完成：以 5 秒 ASR 窗口做 Agent 标注、旁白语速对照真值（设计稿给 4.03 字/秒；按 `tts_meta.json` 可见字符除以
    音频时长本地重算为 3.59，口径不同，补做时先统一口径）、把参考复制进盘龙 `--edit-mode cut` 新运行做复用验证。拿到有效 key 后按 SKILL.md §3 补做，并把数字补进本笔记。
