# Agent Note: 吸收小说库高张力叙事写法，全面去译腔并润色技能与案例文案

Status: implemented

## Problem

[[2026-10-15-prose-polish-current-contracts]] 完成了首轮契约对齐与局部字句清理，但当时采取了“保留全部正反示例与案例产物原文”的保守边界，导致仓库仍存在三处短板：

1. **技能说明与参考文档仍残留英文直译词、自造黑话与多层嵌套长句**：`skills/video-script/`、`skills/video-recap/`、`skills/video-cut/`、`skills/video-voiceover/`、`skills/video-assemble/`、`skills/video-reference/` 中仍有“墙到墙旁白”“失败开放”“声音所有权”“承载此刻”“建立时刻”“知情变化”“动作回报”“反事实检查”“分数位置的结构”“单侧峰”“作者段”“实读文本”等生硬表述，既增加阅读负担，也会把说明书腔传染给 Agent 生成的文稿。
2. **写作技能缺少网感与解说质感的实操技法**：`video-script` 与理解简报虽然规定了“前提 → 触发点 → 变化/意味”和禁用破折号，但正反示例仍偏教科书总结腔（如“护送者本可以独自离开，却为了保护那个孩子，主动把追兵引向自己”），未把高留存网文与短剧解说的核心技法（具象处境加身份或利益反差直给、用肉眼可见的动作和当场后果代替抽象总结词、原声台词前后的“垫半句、让半句、接半句”）沉淀为可执行规范。
3. **仓库案例与长文指南中的示例文案仍有 AI 报告味与模板复读**：`examples/guohuo-60s/` 的策划与分镜声画板里存在大量机械复制的模板句（如所有拍一律写“尽量晚进到信息/动作将发生前”“入点=事件前 | 出点=反应落地”）及分类标签腔；`docs/video-recap-script-writing.md` 的《旧录像里的道歉》案例与 `docs/ai-video-analysis-without-invented-plot.md` 的《雨廊里的红工具箱》案例旁白也偏干瘪生硬，缺乏真人口播解说的呼吸感与咬合力。

## Decision

1. **按 `prose-gap-polish` 五步法系统重写七个技能与参考文档中的晦涩句式**：合并电报体短碎句，拆解多层括号嵌套句，清理直译黑话（如“墙到墙旁白”改为“从头到尾铺满旁白”、“失败开放”改为“出错时不阻断主流程”、“声音所有权”改为“当前拍由哪种声音主导”、“反事实检查”改为“关旁白看画面 / 闭眼纯听声音”、“分数位置的结构”改为“各叙事段落在时间轴上的起止占比”、“单侧峰”改为“帧间画面差自身突起的峰值”、“作者段 / 实读文本”改为“书面原稿 / 朗读正文”），同时严格保留所有 schema 键名、CLI 参数、远程调用披露、`--confirm-voice-rights` 与跨技能自包含边界。
2. **从 `novel` 库多品类高留存案例提炼解说质感技法，落地到 `video-script` 与理解简报**：在 `skills/video-script/SKILL.md`、`references/creative-editing-playbook.md`、`references/promotional-copy.md`、`scripts/review_response.py` 及 `skills/video-understanding/scripts/briefing/builder.py` 中落地产出规范与正反对比示例：
   - **具象处境 + 身份/利益反差起手**：前 3–5 秒直接摆出具体场合、手头动作/物件与最扎眼的反差，不铺垫空泛世界观或人物履历。
   - **手头动作咬合当场后果，杜绝公文报告词**：用“看都没看就把名单揉成一团扔进废纸篓”“嘴上放最狠的话，桌下手却死死扣住她手腕”这类可感知动作与即时反应，替代“完成认知闭环”“内心防线崩塌”“关系发生位移”“情绪锚点”等抽象总结词，并把这类公文黑话纳入 `review_response.py` 评审检查项。
   - **原声台词前后“垫半句、让半句、接半句”**：原声开口前只垫“这句话为什么扎心”，原声开口时彻底让位，尾音落定后再顺着对方变脸或沉默接半句点破后果。
3. **全面润色案例与长文指南文案**：翻新 `examples/guohuo-60s/` 的 `recap_story_plan.json`、`visual_audio_board.json`、`clip_plan.json`、`style_card.json`、`background_research.json` 及配套文档，将每一拍的入出点理由、局势翻转与声画交接改写为贴合具体镜头的导演手记（保持已发布成片对应的 7 段 `narration.json` 实读文本、`captions.json` 与 `overlay.json` 锁定时间轴不变）；重写 `docs/video-recap-script-writing.md`（含《旧录像里的道歉》声画交接定稿对比）、`docs/ai-video-analysis-without-invented-plot.md`（含《雨廊里的红工具箱》正反解说对比）、`examples/resource-library/` 及 `README.md` 中的示例旁白与说明文字。
4. **同步 ClawHub 包版本与摘要**：七个技能包在 `.clawhub/publish.json` 中各升一个补丁号，同步更新 `video-recap` 的五个 `skill:` 伴随依赖版本，并用固定中央 `clawhub_release.py lock` 重新生成 `packageDigest`。

## Alternatives considered

- **维持 [[2026-10-15-prose-polish-current-contracts]]“所有示例与案例原文不动”的限制，只改技能说明正文。** 最强理由：零触碰 JSON 示例与案例工件，最不容易触发契约测试回归。未采用：Agent 写稿时对 few-shot 示例的模仿权重远高于抽象规则，保留干瘪或模板复读的示例会直接拉低生成质量，也无法解决案例文案本身的 AI 味问题。
- **连同 `examples/guohuo-60s/narration.json` 与 `captions.json` 一起重写旁白词。** 最强理由：连成片字幕也能再换一版措辞。未采用：`guohuo-60s` 绑定了已发布的真实成片视频与 Fish Audio 实测时长（`tts_raw_duration`、`placed_audio_duration` 及 25 段 SRT 切分），在无原片与原音色重渲的静态仓库里改写 `narration.json` 会导致公开视频声画、字数统计与字幕快照失真；因此保留成片实读文本，重点润色其故事企划、声画板、剪辑理由、风格卡与复盘文档，并在 `skills/` 与 `docs/` 的教学案例中提供全新打磨的高质感解说词。

## Consequences

- 收益：技能说明通顺易读，Agent 写出的故事计划、旁白稿和宣发文案具备地道的解说网感与口播张力；仓库教学案例与文档示例彻底告别翻译腔、公文黑话与模板复读。
- 代价：涉及 `skills/` 下全部 7 个技能的说明或脚本更新，需同步升级 `.clawhub/publish.json` 中各技能 semver、更新 `video-recap` 伴随依赖并重新锁定 `packageDigest`。
