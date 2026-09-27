# Agent Note: 资源库、模板库与只读 dashboard

Status: implemented

## Problem

专业剪辑要反复复用同一批资源和包装，但仓库目前没有任何地方登记它们：

- **资源分散在环境变量和单次 work_dir 里。** BGM 只有 `BGM_PATH`（assemble `lib.py`），音效没有代码路径
  （`examples/guohuo-60s/sfx_mix_plan.json` 带授权字段但无人读取），音色是 `MIMO_TTS_VOICE` / `VOICE_REF` / `FISH_*` /
  `INDEX_TTS_*`，字体只有 `SUBTITLE_FONT_NAME`（按系统字体名解析，不带字体文件），logo / 包框只能由调用方渲染成
  RGBA 序列交给 `compose_foreground.py`。唯一持久的库是素材库（`materials.py`），且只存理解分析 JSON。
- **没有模板层。** 字幕样式是十几个 `SUBTITLE_*` 环境变量，`--style` 明确是自由文本；包框、字体参数、样片都无法保存、
  命名、跨项目复用，只能复制环境变量或项目级 Remotion 代码。
- **没有来源、授权与采用记录。** BGM 和字体只记路径或名字；授权、音色授权同意、"用户已采用这一版包装"都没有字段。
- **没有一份记录列全一次运行用了哪些资源。** 信息散在 `recap_run_manifest.json`、`assembly_manifest.json`、
  `tts_meta.json` 与逐段缓存 sidecar 里，无法回答"这条成片用的是哪首 BGM、哪个字体文件、哪版包框"。
- **没有地方浏览这些东西。** 项目、运行、QC 与资源只能靠 `recap_inspect.py` 和手工翻目录。

约束不变：`docs/production-boundaries.md` 规定只对一部剧成立的数值、路径和素材不进仓库；技能自包含、只经 work_dir 通信
（[[2026-06-14-self-contained-skills-duplicated-libs]]）；身份只用 `{path, size, mtime_ns}`（[[2026-09-20-no-content-hashing]]）。

## Decision

五期都已落地，各期细节见对应笔记；这里只记整体结构。

- **资源库是用户自有目录**，与素材库共用根目录（`--material-library-dir` / `VIDEO_RECAP_MATERIAL_LIBRARY_DIR`）：
  `library.json`、`resources/<kind>/<id>/resource.json`、`templates/<kind>/<id>/v<version>/template.json`、`samples/<id>/sample.json`。
  仓库不内置真实 BGM、字体、音色或包装，只带合成示例库 `examples/resource-library/` 与示例项目 `examples/demo-project/`。
  格式与只读校验 `library.py check|list|show`：[[2026-09-27-resource-library-format]]。
- **资源**分 `bgm / sfx / voice / font / image`；`license.status` 只由人填写，参考音频另有 `consent.status`；文件身份沿用 `{path, size, mtime_ns}`。
- **模板**分 `subtitle_style / packaging`，带版本、画布、参数来源（`measured / fitted / specified / unknown`）与采用记录；
  只有 `adopted` 能绑定，换画幅就是新模板；字幕样式必须用最长一行校准 `max_chars`。样片只作证据，写明示范了什么、不能照搬什么。
- **运行记录**：full / cut 合成后写 `work_dir/resource_lock.json`，对上资源库登记与授权状态，`attention` 在运行结束时打印；
  `tts_meta.json` 记录实际音色：[[2026-09-27-resource-lock]]。
- **项目绑定**：`recap.py --project recap_project.json` 只在编排器里解析，下发为各阶段已有的 `SUBTITLE_*` / `BGM_PATH` / 音色参数，
  与显式设置冲突、模板未采用、画布不符都在花钱的阶段之前停止；video-assemble 支持字体文件：[[2026-09-27-project-binding-and-font-files]]。
- **包装模板**：绑定后写出 `packaging_layers.json`，video-assemble 在合成时逐层叠加并在 `timeline.json` 写出一致的 image 轨：
  [[2026-09-27-static-packaging-layers]]。
- **只读 dashboard（剪辑台）**：video-recap 内的标准库服务，只有 GET / HEAD，回环地址、Host / Origin 校验、路径不出根目录；
  按阶段分视图，ZenStory 共用 `tokens.css`：[[2026-09-27-read-only-dashboard]]。

与最初方案不同的两处：授权与声音授权提示放在 `resource_lock.json.attention` 而不是 `final_qc.json`（后者的格式只承载阻断项）；
dashboard 不做 `--detach / --status / --stop` 会话文件（会与"只读、不写任何文件"冲突），由调用方放到后台运行。

## Alternatives considered

- **仓库内置一套默认 BGM、字体、音色和包装** — 最强理由：开箱即用，示例更好看。否：授权无法随仓库分发，且违反
  "只对一部剧成立的素材不进仓库"；仓库只放合成的示例库。
- **恢复内容哈希作为资源与采用绑定的身份** — 最强理由：资源被替换或移动时能可靠发现，采用记录有防篡改意义。
  否（暂不）：[[2026-09-20-no-content-hashing]] 刚刚移除全部哈希；`{path, size, mtime_ns}` 覆盖正常流程。
  重访信号：出现"资源被同大小同 mtime 的文件替换而误用"的真实事故，或需要跨机器同步库并校验时——
  后者已知必然发生：库拷到另一台机器后，所有采用快照的 mtime 都会变化。
- **每个阶段技能自己读取资源库** — 最强理由：单独使用 video-assemble 时也能直接套模板。否：库解析代码会被复制进多个技能，
  或者需要跨技能 import；由编排器解析并以已有参数下发，阶段技能保持只认具体路径。
- **用 SQLite 做资源库** — 最强理由：查询、去重和统计方便，Python 标准库自带。否：Agent 与人都要能直接读、grep、提交
  JSON；现有素材库已经是 JSON + grep，dashboard 读取 JSON 也足够。
- **在代码里做命名预设（`--style cinematic` 之类）** — 最强理由：一个参数就能换整体观感。否：`--style` 已明确是自由文本；
  预设写死在代码里就成了项目数据进仓库。模板是用户库里的数据。
- **dashboard 允许编辑（改绑定、标授权、采用模板）** — 最强理由：少切回对话。否：写接口需要冲突检测、原子写和令牌，
  会变成第二个事实来源；采用与授权必须留下用户原话，放在对话里做更合适。

## Consequences

- **收益**：资源和模板可命名、可版本化、可跨项目复用；每条成片都有一份 `resource_lock.json` 说明用了什么；
  授权和采用状态有明确字段；dashboard 让人不用翻目录就能看到项目、运行、资源和模板。
- **代价**：新增五个 schema 与一个项目文件，`config-playbook.md` 里"没有工具读取配置文件"的立场已改写为"只有 `--project` 读取项目文件"；
  video-recap 变得更重（库解析 + dashboard），dashboard 需要自己的 HTTP 与浏览器测试；用户要维护自己的库目录，
  授权与采用状态靠人填写，工具只能提示缺失，不能代为判断。
- 已定：库根目录与素材库合并；dashboard 不设访问令牌（只读 + 回环 + Host / Origin 校验）。
- 重访信号：需要跨机器同步资源库并保持采用快照有效时（`mtime_ns` 是本机事实），重新考虑内容哈希；
  dub 模式的克隆参考是否纳入 `voice` 资源仍未做，出现真实需求再定。
