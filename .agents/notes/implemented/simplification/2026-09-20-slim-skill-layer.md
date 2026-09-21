# Agent Note: skill 层瘦身——SKILL.md 去重、长段落下沉、错位 references 归位

Status: implemented

## Problem

六份 SKILL.md 每次调用都整体进入上下文，合计约 62K 字符，其中 video-recap 16.5K、video-script 14K。
三类内容在多份 SKILL.md 里重复：创作控制模式与五个角色（recap §2 与 script §1.1/§3 几乎一致）、
密集闪帧的来源判断规则（recap §4.6、script §3.5、cut §6 各一份）、TTS 供应商细节（recap §3 复述
voiceover 的 Fish Audio 音色 ID 与 IndexTTS 端点说明）。recap §7 还手抄了 30 多个透传参数，与 `--help` 漂移。
另有若干长段落（voiceover 的 IndexTTS receipt 细则、assemble 的 source_score 与包装流程、cut 的修短残镜取景复核）
属于只在特定操作时才需要的资料，却常驻 SKILL.md。

references 里有两个放错位置的文件：`video-recap/references/timeline-and-jianying.md`（13.6K）全文讲 video-assemble
的 timeline.json 与剪映导出，没有任何 SKILL.md 链接它，只有 README 与 docs 引用；`env-inventory-v1.json`
只被 `tests/orchestrator/test_env_inventory.py` 读取，是测试夹具。

脚本层有少量只被测试引用或无人引用的代码：video-script 与 video-understanding 各一份 `narration_tempo_budget`
（这两个 skill 都不声明另外三个 tempo 键，parity 测试不要求它们实现）、understanding 的 `step_cache_key` /
`video_fingerprint`、assemble 的 `_VISUAL_DELIVERY_FORBIDDEN_KEYS` / `SCRIPT_DIR`、未用的 `DEFAULT_MIMO_*_MODEL`
常量、asr.py 与 detect.py 各一份字节相同的 `_write_audio_meta`、recap_runner 两处只差“多视频”三字的过期 manifest 守卫、
mimo_qc.py 两个 12 参数的纯透传函数。

## Decision

- SKILL.md 只保留本技能的契约与入口；跨技能共享的创作方法、切点规则、供应商细节只在拥有它的技能里写一次，
  其他技能用一句话指向（recap 可以点名 video-script；阶段技能之间只说“剪辑阶段 / 配音技能”，不出现兄弟名字或路径）。
- recap §2 保留测试钉死的五条粗体角色，删除与 video-script 重复的 CREATE / DIRECTED / REVISION 定义、REVISION 规则
  和三份工作产物说明；不再要求 recap 阶段先读 `creative-editing-playbook.md`，由 video-script 阶段读一次。
  recap 那份 playbook 副本仍保留（parity 测试与自包含笔记锁定），是否删除另立一篇笔记。
- recap §7 的参数清单改为指向 `--help`；recap §3 的 Fish Audio / IndexTTS 段落压成一句透传说明，并链接
  `references/shift-left-qc-schema.md`（此前没有任何 SKILL.md 链接它）。
- 长段落下沉：voiceover 新增 `references/index-tts.md`；assemble 新增 `references/packaging.md` 并把 source_score
  的中文摘要追加到 `references/source-score.md`；cut 的取景复核段落并入 `references/shot-review.md`。
- `timeline-and-jianying.md` 移到 `docs/`，README / docs 链接同步；`env-inventory-v1.json` 移到 `tests/orchestrator/`。
- 脚本：删除上述无人调用的函数与常量；asr.py 改为从 detect.py 导入 `_audio_meta_path` / `_write_audio_meta`
  （detect 不导入 asr，无环）；recap_runner 抽 `_reject_stale(mismatches, label)`；mimo_qc.py 的 `build_report` / `run`
  改成 `**kwargs` 透传，`api_call` 在调用时读取模块级 `mimo_qc_api_call`，保持测试 monkeypatch 的接缝。

## Alternatives considered

- **mimo_qc.py 用 `functools.partial` 绑定 `api_call`** — 最强理由：两行代替五十行，签名完全继承。否：partial 在导入时
  就固定了 `mimo_qc_api_call` 对象，`tests/orchestrator/test_mimo_qc_adapter.py` 通过 `monkeypatch.setattr(mimo_qc, "mimo_qc_api_call", …)`
  注入假传输，五个测试因此失败；改用调用时查找的 `**kwargs` 包装。
- **把 recap SKILL.md 的角色列表也删掉、整段指向 video-script** — 最强理由：再省约 600 字符。否：
  `test_creative_roles_and_artifact_examples_form_a_structured_contract` 钉死 recap §2 必须列出五条角色，且编排者在
  进入 video-script 前就需要这份最小判断清单。
- **顺手删除 video-script 里约 1400 行只被测试到达的 brief 模块链（narration.py → agent_brief → brief_*）** — 最强理由：
  这是最大单笔收益。否：它是 `2026-06-14-self-contained-skills-duplicated-libs.md` 明确保留的自包含保险，动它要先翻转那篇
  笔记的决定，单独立项。
- **保留 recap SKILL.md 的参数清单并补齐** — 最强理由：Agent 不用跑命令就能看到全部开关。否：清单已与 argparse 漂移，
  `--help` 是唯一事实来源。

## Consequences

- **收益**：六份 SKILL.md 从 61.9K 降到 55.2K 字符（recap −2.7K、assemble −1.8K、voiceover −1.3K、cut −0.7K）；
  同一规则不再有三份措辞略有差异的版本；两份错位文件归位；脚本净减约 190 行。
- **代价**：阶段技能引用共享规则时只能用“剪辑阶段 / 配音技能”这类描述，读者要自己找到对应 SKILL.md；
  新增三个 references 文件需要 Agent 按需读取，SKILL.md 里的链接是唯一入口。
- 重访信号：若决定删除 recap 的 playbook 副本或 video-script 的 brief 模块链，先更新自包含笔记再动代码。

## Verification

`PYTHON=<py3.12> scripts/test.sh` 七组全绿（understanding 168、cut 167、voiceover 127、assemble 401、script 137、
orchestrator 341、inspect 24）；`ruff check skills tests` 无告警。
