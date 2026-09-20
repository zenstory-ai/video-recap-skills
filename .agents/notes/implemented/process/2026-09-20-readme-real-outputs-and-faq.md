# Agent Note: README 面向读者、用真实产出说话、回答读者问过的问题

Status: implemented

## Problem

#90 把两份 README 排成给人看的顺序，#91 统一了居中 masthead，与同组织 oh-story-claudecode（#432/#433）、drama-skills（#171/#172）、novel-to-game（#58/#59）的前两步一致。但正文仍然全是说明文字：「为什么用它」八条描述能力，`examples/guohuo-60s/` 里完整的故事计划、声音分工、旁白、时间线、看片修改日志和三份 QC 报告一处都没有被节选引用；读者提过的唯一问题（issue #79，无旁白视频能否加旁白）README 没有回答。三个姊妹仓库的第三步（oh-story #434、drama-skills #173、novel-to-game #60）在 2026-09-18 用真实产出替换说明文字并加了常见问题，本仓库照此补上。

## Decision

两份 README 按姊妹仓库的顺序重排，内容改动如下：

1. **masthead** 沿用 #91，导航的第二个正文锚点从「怎么用」改为「看看它的输出」；徽章在 Stars / Release / Skills 6 / MiMo / License 之外补 Python 3.10+ 与 CI `skill-validate.yml`，与 drama-skills、novel-to-game 一致（#91 的决策记录允许仓库特有的运行事实接在核心四项之后）。一句话定位改为写清六个技能、ffmpeg、一个 key 和剪映草稿。
2. **首屏视频后一段话**说明这条 59 秒成片是什么、来自哪四集、全部产物在哪。「这是什么」改为一段定位加**五条加粗要点**：一个 key 本地只要 ffmpeg；先做创作决定再分配声音；先剪后配、多视频、素材库；成片之外能继续改（剪映草稿、自带字幕）；每一步都有机器可读记录、MiMo 顾问只建议。原「为什么用它」八条并入这五条。
3. **安装**：前提一句话，最短命令（Claude Code 两行加一句话安装）在前；Codex / OpenCode / OpenClaw 和 Fish Audio 各收进一个 `<details>`；保留自检请求；引用块写 CHANGELOG / Releases 与仓库从 `worldwonderer` 迁移。删除「本 PR 已在 OpenCode 1.14.32 上实际验证」「Codex CLI 0.144.1 smoke-tested」这类过程性句子。Fish Audio 段不再写「当前免费」，只写模型、默认音色和「计费以 Fish Audio 官方为准」，供应商定价链接不进 README。
4. **新增「看看它的输出」**四个小节，每段逐字摘自 `examples/guohuo-60s/`：
   - 故事计划：`recap_story_plan.json` 的 `director_intent` 完整引用，`beats` 十拍节选 b03 一拍；
   - 声音分工：`visual_audio_board.json` 同一拍（`audio_owner: original_dialogue`、`narration_job: none`、1.0 倍速），`narration.json` 七块中的第二、三块（10.2 s 停、23.047 s 进），`original_subtitles.json` 三句全文；`timeline.json` 的音量关键帧只用文字描述；
   - 看片修改与 QC：`revision-log.json` 最后一轮（change / frozen / verification），`edit-map.json` 的 `wedding_corridor_continuous_motion` 段，`delivery-qc.json` 的 `checks`，`content-qc.md` 九行节选「男主全懂了」一行；`assembly_qc.json` 只用文字概括；
   - 接着在剪映里改：#90 放在首屏第二块的剪映截图移到这里，链接仓库内文档；首屏改为视频加一段产物来源说明。
   节选惯例：JSON 与 Markdown 两份 README 都原样引用；英文 README 在每段后加 "Translated:" 段落，不改动引用体；省略处标「…」；表格与数组节选注明原有行数。
5. **安装后的第一条请求**三条：完整解说、多集剪成一条（合并原「长视频剪短」与「多视频合成」）、只做文本交接（原 GEO 段的引用块，删去末尾「这份规划不代表已有成片」）。
6. **流程与六个技能**：mermaid 图原样保留；架构表改为技能名链接到 `skills/<name>/`，`video-recap` 提到首行并注明日常用它、`video-script` 注明可单独调用。原「输出」清单压缩为表后一句，指向 data-schema。
7. **进阶请求**保留原六条请求原文（素材库、MiMo QC + 剪映、字幕带、克隆音色、dub、自带字幕），删除「改用当前免费的 Fish Audio 配音」一条（安装段已覆盖，且「免费」已过期），每条解释压到一两句。
8. **新增常见问题**三条，只收正文其他章节没有回答过的读者问题：#79 → `--skip-asr`；429 / 中断续跑（CHANGELOG 0.3.0 断点续传、`recap_phase.json`）；VLM 认不出人 → `background_research.json`。第一稿有十条，审稿后删掉七条：Fish Audio 是否免费（供应商定价）、旧地址安装失败（安装段引用块已有）、微信 / 竖屏两个已修 bug（属于 CHANGELOG）、非 Claude Code 能否用（安装段已答）、留白字幕对不上（进阶请求已写）、剪映能改什么（上文小节已写）、GPU 与费用（要点与安装段已写）。同一轮还删了实现细节：音量关键帧的秒数与增益、assembly QC 的具体条目数、「互不共享代码」、ops120 正文出处（留在致谢）、VLM prompt 模板链接；`MIMO_TOKEN_PLAN_CLUSTER` 收进 bash 注释。
9. **延伸阅读**：三篇站点指南（原表格的三行压成一行一条）、仓库内剪映文档、案例 runbook 与决策链、各 skill 契约与 references。致谢补上字幕带检测来源 ops120。
10. README.en.md 逐段同步，顶部加 `<!-- Last synced with README.md: 2026-09-20 -->`；导航「中文」指回 README.md，锚点 `#install` / `#see-what-it-produces`。

命令、路径、技能名与数量、模型名、reference ID、示例视频链接、项目表内容不变。

## Alternatives considered

- **只加 masthead 和 FAQ，保留原有章节顺序。** 最强理由：改动小，GEO 阶段写的指南表和边界句是为答案引擎准确复述而写的。被否：姊妹仓库的决策记录已经验证，GitHub 流量七成以上来自传统搜索和直接访问，首屏应该是视频、定位和安装；引擎需要的事实（Python 版本、技能数量、一个 key、剪映导出）在正文里仍然存在，只是不再以否定句形式出现。
- **把「看看它的输出」做成完整流程的六段（理解 → 计划 → 剪辑 → 旁白 → 组装 → 修改）。** 最强理由：案例目录的 `workflow-manifest.json` 正好按阶段列了文件。被否：drama-skills 和 novel-to-game 都把同类段落压到三个短小节，理由是对初学者太重；本仓库按「计划 → 声音分工 → 修改与 QC」三段加剪映一段，理解阶段的产物（ASR / VLM / scenes）案例目录本来就没有收录，无法节选。
- **FAQ 只写 #79 一条，其余等读者再问。** 最强理由：AGENTS.md 和姊妹仓库都强调「回答读者问过的问题」，本仓库公开 issue 只有一个。被否：CHANGELOG 里 #51（微信 / 手机播放）、#53（竖屏字幕拉伸）、0.3.0 的 429 自愈都是用户报告驱动的修复，属于读者问过的问题；其余几条（GPU / 费用、宿主、剪映能改什么、VLM 认不出人、旧地址）是原 README 各处已回答但散落的内容，集中成 FAQ 之后正文更短。
- **英文 README 把 JSON 里的中文字段值直接翻译进引用体。** 最强理由：英文读者不用再看一段 "Translated:"。被否：节选要能逐字回溯到源文件，改动引用体就无法用脚本核对；novel-to-game 对中文示例采用的也是「原样引用 + 标注 translated」。
- **视频下方不放介绍段、直接进「这是什么」。** 被否：没有这段话，读者不知道视频是仓库自己的产出、更不知道下文节选来自哪里；这一段也是 examples 目录在首屏唯一的入口。

## Consequences

- 收益：首屏依次是定位、安装入口、成片视频、产物来源；读者不用装就能看到故事计划、声音分工、旁白留白、看片修改和 QC 长什么样；三条 FAQ 各有树内出处；过期的「当前免费」说法消失；中英两份内容一致。
- 代价：README 中文 205 → 435 行、英文 214 → 446 行，增量集中在代码块；节选引用了案例文件的具体内容，案例改写时要跟着改（仓库没有检查节选一致性的脚本）；站外指南从首屏下沉到文末，点击量可能下降。
- 未做：没有为案例目录之外的模式（full 模式单视频、dub 模式）补真实产出，仓库里没有这些产物；没有录制新视频；没有新增测试。

## Verification

- 脚本核对：README.md 中全部 9 个 JSON / Markdown 代码块去除空白后逐一在 `examples/guohuo-60s/` 对应源文件中找到；README.en.md 的引用体与中文版相同。
- 两份 README 的本地链接（`examples/…`、`docs/…`、`skills/…`、`CHANGELOG.md`、`LICENSE`）全部解析到存在的文件；页内锚点 `#安装` / `#看看它的输出` 与 `#install` / `#see-what-it-produces` 各对应实际二级标题。
- drama-skills #171 决策记录列出的禁用短语 grep（`不会自动同步|不是安装命令|也不要求把所有阶段跑完|输出仍需审阅|不等于|不承诺|不表示|不保证|独立产品|截至|as of|项目页：|All ZenStory AI projects`）在两份 README 中为空。
- 站点链接（项目主页与三篇指南的中英版本、brand mark SVG）curl 均 200。
- 事实核对：技能数 6 与 `skills/` 一致；Python 3.10+ 沿用原 README；`skill-validate.yml` 是唯一 CI workflow；0.3.3 含 #51 / #53、0.3.0 含 VLM 断点续传均见 CHANGELOG；`--skip-asr` 见 `skills/video-recap/SKILL.md` 参数清单与 #79 回复；四轮修改与 `revision-log.json` 的 `rounds` 数量一致；25 条字幕、`two_pass_linear`、三项 release gate 见 `assembly_qc.json`。
