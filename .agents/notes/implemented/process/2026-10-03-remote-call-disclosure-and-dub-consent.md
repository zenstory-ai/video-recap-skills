# Agent Note: 远程调用逐路径披露，dub 需显式确认声音权利

Status: implemented

关联：[ClawHub 分发](2026-10-03-clawhub-distribution.md)。

## Problem

ClawHub 的 LLM 安全扫描把两个包标为 suspicious（高置信），挡在 DISTRIBUTION_VERIFIED 之外：

- video-script@1.0.5：评审步骤会把项目稿件与证据发到远端 MiMo API，但没有面向用户的清楚说明。
- video-voiceover@1.0.7：主 TTS 流程正常，但包里还有一条范围不清的 dub 路径，会转写视频音频、克隆说话人声音并用外部服务生成配音 MP4。

扫描结论属实，不是措辞问题：

- video-script 的 SKILL.md 只在 §6 写了 `review.py` 的用法，没说它联网、发什么、发给谁、怎样关；validate/lint 是否本地也没写。代码里 `review.py` 是本技能唯一的网络调用，没有 key 时仍会把稿件和证据 POST 到 MiMo，只是被 401 拒绝。
- video-voiceover 的 SKILL.md 列了三个 TTS 供应商，但没逐路径写每个供应商收到什么；dub 只有一句“只由编排入口的 `--edit-mode dub` 调用”。`dub.py` 本身没有任何确认，谁执行 `--stage prepare` 就会把整条源音轨发给 MiMo ASR，`--stage render` 会把原说话人约 10 秒的声音当克隆参考发出去。资源库对参考音频已有 `consent.status`，dub 路径却完全绕开了它。

已通过扫描的 video-understanding 在 SKILL.md 里写明了 ASR 与 VLM 各用哪个 MiMo 模型、需要哪个 key、哪个开关跳过哪一步。

## Decision

- video-script SKILL.md：frontmatter 描述加一行外发说明；新增 `## 2. 远程调用与数据外发`（其后章节顺延一号），写明 `review.py` 是唯一联网脚本、发往 `<MIMO_API_URL>/chat/completions`（按 key 类型的默认地址、`MIMO_API_KEY`、`MIMO_MODEL`），只发文字：`narration.json` 全文、选定时间段内的 VLM 描述与 `frame_facts` 文字和对白转写、`background_research.json` 摘录、四份策划文件各前 3000 字与原声字幕文字，不发视频、帧图片或音频；单独使用时只在显式执行时运行，编排入口默认在 TTS 前跑一次，用 `--no-review-narration` / `REVIEW_NARRATION=0` 关闭（严格评审开启时关闭无效）；评审是建议型、只写报告；`validate.py` 与 lint 只在本地运行。能力边界节重复一句。
- `review.py`（`review_runner.main`）在没有 `MIMO_API_KEY` 时直接退出，不构造也不发送请求。只加在 CLI 入口，函数级 `review_narration` 不变。
- video-voiceover SKILL.md：frontmatter 描述加外发说明与 dub 范围；新增 `## 2. 远程服务与数据外发`，用一张表逐路径写服务、凭据和发送内容（MiMo TTS、`--voice-ref` 克隆、Fish Audio、自托管 IndexTTS、dub 的 MiMo ASR 与 voiceclone）；新增 `## 8. 实验性 dub 配音（英译中、克隆原声）`，写触发方式、确认门禁、外发内容、权利与同意、确定性门禁和本地产物；原运行规则里的 dub 门禁条目并入该节。
- `dub.py` 新增必填确认 `--confirm-voice-rights`：两个阶段缺少它都在抽取音频和任何请求之前退出，错误信息写明会把什么发给 MiMo ASR 与 voiceclone、什么情况下才可确认。
- recap 新增同名参数 `--confirm-voice-rights`：`--edit-mode dub` 缺少它时在建 `work_dir`、探测视频之前以 argparse 错误退出；其他模式传入同样报错；dub 模式下 `_run_dub` 把它转给 `dub.py` 的两个阶段。参数留在用户的 argv 里，暂停时打印的续跑命令与 manifest `argv` 自然带上它。它不进 `_analysis_settings`，不影响旧 `work_dir` 的复用比对。
- 只有命令行参数，没有环境变量形式：确认针对的是这一条视频和这一个说话人，不应被全局环境默认打开。
- recap SKILL.md §5、`references/config-playbook.md`、README 中英文的 dub 示例同步写明外发内容与确认参数；config-playbook 的评审行写明外发内容与关闭方式。

## Alternatives considered

- **只改 SKILL.md 文案，不加 dub 门禁**——最强理由是改动最小、不破坏任何现有命令。否：扫描指出的是 dub 能在没有任何确认的情况下克隆真人声音，这是行为问题；只改文案等于把风险换个说法，`dub.py` 依旧可被直接执行。
- **把 dub 整条路径从 video-voiceover 包里移走**——最强理由是包的范围最干净，扫描对象里就没有克隆路径。否：dub 是已发布的实验模式，编排入口靠它工作；挪包要改分发清单与依赖版本，超出本次范围，而显式确认加逐路径披露已经回答了“范围不清”。
- **把选择 `--edit-mode dub` 本身当作确认，由编排入口自动给 `dub.py` 补上确认参数**——最强理由是不改用户命令。否：选模式说明想要配音，不说明有权使用这段音频、说话人同意被克隆；Agent 代用户选模式时这一步会被跳过。
- **同时接受 `VIDEO_RECAP_DUB_CONFIRM_VOICE_RIGHTS=1` 之类的环境变量**——最强理由是与 `REQUIRE_NARRATION_REVIEW` 等开关风格一致、便于批处理。否：环境变量一次设置后对之后所有视频生效，等于一张空白授权；确认应随每次运行的命令出现。
- **评审默认关闭**——最强理由是默认不外发最保守。否：评审是写稿质量的主要防线，video-understanding 的 ASR/VLM 本就把同源素材发给同一个 MiMo；默认开启但写清外发内容和关闭开关，与已通过扫描的技能同一标准。

## Consequences

- 收益：两个被标记的包都有与 video-understanding 同级的外发说明；dub 必须带用户的显式确认才会运行，缺确认时不碰音频、不发请求；没有 MiMo key 时 `review.py` 不再把稿件发出去。
- 代价：这是 breaking 变更。所有 `--edit-mode dub` 命令必须加 `--confirm-voice-rights`，旧 `work_dir` 里不带它的续跑命令会被拒绝，需要在原命令上补参数后重跑；直接执行 `dub.py` 的脚本同样要加。非 dub 模式误带该参数会报错。
- video-script 与 video-voiceover 的 SKILL.md 章节号顺延，外部按章节号引用这两份文档的地方需要对照新编号。
- 包内容变了，重新发布需要按分发流程 bump 版本并重新锁包；本次不改 `.clawhub/publish.json`、`VERSION` 与插件清单，留给发布线处理。扫描是否放行要以重新扫描的结果为准。

## Verification

- `tests/voiceover/test_dub.py`：两个阶段缺确认时 `dub.main()` 以 `VOICE_RIGHTS_REQUIRED` 退出且阶段函数未被调用；带确认时调用对应阶段。
- `tests/orchestrator/test_strict_final_qc.py`：dub 缺确认时 argparse 退出码 2、错误里有参数名和 MiMo ASR/voiceclone、`work_dir` 未创建、未启动子进程；非 dub 模式传确认报错；带确认时 prepare/render 两次调用都转发该参数，续跑命令里带它。
- `tests/script/test_review.py`：`MIMO_API_KEY` 为空时 `review_runner.main()` 退出、没有任何请求、不写评审产物。
