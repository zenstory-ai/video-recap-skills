# Agent Note: 去黑话与去冗余、放宽品味型硬门禁、拆分宣发文案备忘并统一中文参考文档

Status: implemented

后续关联：放宽 [[2026-10-02-video-reference-skill]] 与 [[2026-10-02-reference-cut-detection]] 中 `R6`（`source_facts` 短语重合）与 `R7`（五维强制覆盖）的阻断口径；延续 [[2026-11-10-scripts-for-hands-and-floor-agent-for-craft]] 将残余品味阻断降级为提示；更新 [[2026-11-06-recap-reference-docs-one-home]] 中 `audio-routing.md` 与 `audio-modes.md` 的分拆事实。

## Problem

1. **指令、参考文档与动态简报存在大量 AI 黑话、营销腔、防御性免责声明与跨文件重复**：
   - `SKILL.md`、`references/`、`README.md`、`docs/` 与运行时输出中残留“一气呵成”“作为同一个创作大脑”“沉淀”“打法”“死守双红线”“铁律”“清零”“工程地板兜底”“形式主义发散”“诈骗式留存”“商业级成片包装”“流光细金线”等口号词与大厂黑话（非专业影视术语）。
   - `skills/video-recap/SKILL.md` 与 `references/creative-editing-playbook.md` 大段互相复述相同规则；`references/audio-modes.md`、`references/audio-routing.md`、`references/config-playbook.md` 以及 `briefing/builder.py`、`briefing/timeline.py` 动态生成的 `agent_narration_brief.md` 仍是全英文或中英混杂，且 `builder.py` 的解说示例带有抽象套话，还把 `emotion` 无差别写进所有 TTS 供应商示例（而 `index-tts` 固定请求体不接受段级 `emotion`）。
2. **脚本与指令中仍有越界充当审美裁判的硬阻断与形式主义门禁**：
   - `narration_lint.py` 将冷开场留白长度（`weak_cold_open_silence`）、连续超过 5 秒无对白/旁白留白（`excessive_dead_air`）以及泛指角色词（`generic_character_label`）列为阻断渲染的 `error`，并用 `这说明|这意味着|这证明了` 误伤正常因果表达。
   - `voiceover._clean_narration_text` 静默删除圆括号 `（...）` / `(...)` 内的正文，违背“不静默删改定稿”的底线。
   - `SKILL.md` 与 `creative-editing-playbook.md` 强制每次 `CREATE` 至少写 2 个竞争假设、强制 `clip_plan.json` 的每条 `reason` 写成 7 段竖线拼接伪代码（`b01 | hook | … | POV=…`），消耗注意力却无脚本消费。
   - `video-reference` 的 `reference_check.py` 在 `R6` 中对 Agent 自己写的 `source_facts[].statement` 也按连续 8 个汉字重合作硬阻断，迫使 `SKILL.md` 教 Agent“换个说法绕过检查”；`R7` 要求五维全部覆盖或逐项填写 `skipped_dimensions`，不适合纯享混剪或单维度拆解。
3. **同名文件契约冲突与陈旧外置工作流**：
   - `work_dir/packaging_plan.json` 在 `assemble.py` / `packaging_studio.py` 中是画布视觉包装配置（要求 `preset` 为 `portrait_cinema` 等，否则直接抛 `RuntimeError`），而 `promotional-copy.md` 与 `builder.py` 又让 Agent 把平台标题、封面文案写进同一个 `work_dir/packaging_plan.json`，两套 schema 互斥；且 `promotional-copy.md` 仍残留旧版外部 Remotion 整数帧换算与 `git diff` 巡检流程。
   - `data-schema.md` 与 `production-boundaries.md` 的句末反应气口顺延阈值仍写 `0.22s`（代码 `CLIP_END_PAUSE_PAD = 0.30`），`video-reference/SKILL.md` §6 存在 `11 = 5 + 7` 的算术错误。

## Decision

1. **拆分 `packaging_plan.json` 与 `promo_copy.json`，清理陈旧宣发流程**：
   - `work_dir/packaging_plan.json` 专用于成片画布视觉包装（`video-recap.packaging_plan.v1`，由 `assemble.py` 读取）；`packaging_studio.py` 在校验 `schema` 不匹配或缺少合法 `preset` 时均明确提示外部宣发文案应写入 `promo_copy.json`。
   - 平台标题、封面帧与封面字、开场钩子复核等宣发备忘改名为可选产物 `work_dir/promo_copy.json`（`video-recap.promo_copy.v1`）；重写 `references/promotional-copy.md` 并删去 Remotion 整数帧 / `mtime` / `diff` 旧流程，在 `references/data-schema.md` 与 `references/resource-library.md` 中补齐一致的字段说明。
2. **收束脚本硬门禁，保留客观底线体检**：
   - `narration_lint.py` 与 `agent_text.py`：将 `weak_cold_open_silence`、`excessive_dead_air`、`generic_character_label` 从 `errors` 降级为 `warnings`（保留 `cut_output` 的 `over_budget` 为 `warning`，交由配音与合成阶段按实测音频时长与倍速上限裁决）；从 `COMMENTARY_CLICHE_RE` 移除 `这说明|这意味着|这证明了`；在统计有效朗读字数与句末标点前统一剥离方括号舞台提示 `[...]`，拦截纯舞台提示空段（`empty_narration`）；将剩余英文报错提示统一为自然中文，删去不存在的参数免责声明。
   - `voiceover.py`：`_clean_narration_text` 仅剥离方括号舞台提示 `[...]` 与首尾空白，不再删除圆括号 `（...）` / `(...)` 内容。
   - `reference_check.py`：保留对原片实体名、ASR 转写与背景调研连续 8 汉字 / 5 英文词重合、绝对时间码与本机路径的 `R6` 硬阻断；将与 `source_facts[].statement` 的连续 8 汉字 / 5 英文词重合移入 `warnings`。`R7` 仅在 `methods` 为空时阻断，未覆盖且未在 `skipped_dimensions` 说明的维度改为 `warnings`。
3. **放宽形式主义软门禁，重写中文动态简报与参考文档**：
   - 放宽 `CREATE` 必填 2 个竞争假设为按素材歧义度决定（主线明确写 1 个，多角度取舍写 2–3 个）；放宽 `clip_plan.json` 的 7 段竖线 `reason` 为简明自然中文剪辑理由（讲清戏剧作用与入出点依据即可，历史案例快照 `examples/guohuo-60s/` 保持不动）。
   - 将 `briefing/builder.py`、`briefing/context.py` 与 `briefing/timeline.py` 生成的 `agent_narration_brief.md` 全面中文化：替换抽象套话示例，通过 `understand.py --tts-provider`、`recap_run_manifest.json` 顶层 `tts_provider` 与第二阶段/素材恢复透传，按当前 `tts_provider` 条件化展示 `emotion` 字段（`index-tts` 与 `fish-audio` 示例省略 `emotion`），统一 `hook | setup | turn | escalation | payoff` 枚举，修正多视频模式过时 MVP 提示与 `recap_inspect.py` 命令路径。
   - 合并 `references/audio-modes.md` 与 `references/audio-routing.md` 为一份中文 `references/audio-modes.md` 并删除 `references/audio-routing.md`；将 `references/config-playbook.md` 译为精炼中文参数速查表。
4. **全仓去黑话、去冗余与事实对齐**：
   - 保留影视与音频专业术语（POV、正反打、硬切、叠化、闪白、冷开场、蒙太奇、戏眼、气口、反应镜、跳切、“垫半句、让半句、接半句”、母版、Conform、EDL、ducking、LUFS、dBTP、CFR/VFR、SAR、PTS、ROI），清理全仓口号黑话、营销形容词与防御性废话。
   - 精简 `skills/video-recap/SKILL.md` 与 `creative-editing-playbook.md`、`research-guide.md`、`shot-review.md` 的重复段落；对齐 `0.30s` 句末反应气口顺延阈值与 `video-reference/SKILL.md` §6 检测行为说明。

## Alternatives considered

- **只改 Markdown 字面措辞，不动 `narration_lint.py`、`voiceover.py`、`reference_check.py` 与 `builder.py`** — 最强理由：零代码改动，现有单测无需调整。不采用：Agent 每次写稿前必读动态生成的 `agent_narration_brief.md`，写完后必过 `validate.py` 与 `voiceover.py`；保留英文混杂简报、圆括号静默删字与品味型硬阻断，文档写得再干净也会在运行时被反向拉回。
- **让 `packaging_studio.py` 兼容宣发文案对象（无 `preset` 时静默忽略）而不改文件名** — 最强理由：不用引入 `promo_copy.json` 新名字。不采用：当用户既要画布包装（如 `portrait_cinema`）又要输出平台标题/封面字时，两份结构仍会互相覆盖，且无 `preset` 静默跳过会掩盖真正写错画布配置的拼写错误。
- **完全删除 `reference_check.py` 对 `source_facts` 的重合扫描与 `R7` 维度检查** — 最强理由：代码最少。不采用：保留 `methods` 非空阻断与未覆盖维度的 `warning` 能提醒拆片者是否漏看声音或字幕维度；对 `source_facts` 长重合保留 `warning` 也能在 ASR 缺失时提示潜在原片描述照搬，同时不再逼迫 Agent 为过检而硬改正常句式。

## Consequences

- **收益**：
   - 技能指令、参考手册与运行时动态简报统一为干净自然的中文加专业影视术语，大幅压缩重复说明与无效仪式。
   - 消除 `packaging_plan.json` 的读写契约碰撞、圆括号静默删字、`index-tts` 示例字段误导以及品味型规则的误判阻断。
- **代价**：
   - 依赖 `weak_cold_open_silence`、`excessive_dead_air`、`generic_character_label` 或 `R7` 缺维度返回非零退出码的外部调用方将收到 `warnings` 而非 `errors`。
   - 删除 `skills/video-recap/references/audio-routing.md`（内容并入 `audio-modes.md`），平台宣发备忘文件名变更为 `promo_copy.json`；两个技能包在 `.clawhub/publish.json` 中升补丁号并重新锁定 `packageDigest`。
