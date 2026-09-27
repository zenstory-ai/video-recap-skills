# Agent Note: 资源库、模板库与只读 dashboard

Status: proposed

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

## Proposal

### 1. 资源库是用户自有目录，仓库只提供格式与工具

沿用现有素材库目录（`--material-library-dir` / `VIDEO_RECAP_MATERIAL_LIBRARY_DIR`），在同一根目录下增加资源、模板和样片，
仓库不内置任何真实 BGM、字体、音色或包装：

```text
<library>/
  library.json                      # {schema: "video-recap.library.v1", name}
  materials/…                       # 现有理解分析库，不变
  resources/<kind>/<id>/resource.json (+ 文件本体或外部路径)
  templates/<kind>/<id>/<version>/template.json (+ 模板自带 PNG 等)
  samples/<id>/sample.json
```

资源 `kind`：`bgm`、`sfx`、`voice`（TTS provider + 音色 id，或参考音频）、`font`、`image`（logo / 包框 / 片尾卡）。
原片不复制进库，仍由素材库按 `source_id` / `material_id` 记录分析结果。

```json
{
  "schema": "video-recap.resource.v1",
  "id": "bgm-tense-pulse",
  "kind": "bgm",
  "title": "Tense Pulse",
  "files": [{"role": "main", "path": "tense-pulse.wav", "size": 1234, "mtime_ns": 1}],
  "origin": {"creator": "…", "url": "…"},
  "license": {"status": "unknown", "terms": "", "evidence": ""},
  "tags": ["紧张", "推进"]
}
```

- `license.status` ∈ `unknown | owned | licensed | restricted`，只能由人写入，工具 never 根据所在目录推断；
  `voice` 另有 `consent`（参考音频的授权同意记录）。
- 文件身份沿用 `{path, size, mtime_ns}`；相对路径相对资源目录解析，解析后 must 仍在库根目录内（拒绝 `..` 与越界符号链接）。
- 派生文件（归一化的 WAV、转码后的片段）不回写为资源，只出现在 work_dir。

### 2. 模板是带版本、带采用记录的数据，不是代码里的预设

```json
{
  "schema": "video-recap.template.v1",
  "id": "subtitle-clean-white",
  "version": 3,
  "kind": "subtitle_style",
  "canvas": {"width": 1080, "height": 1920},
  "params": {
    "font": {"resource": "font-source-han-sans-medium"},
    "size_px": {"value": 58, "provenance": "measured"},
    "outline_px": {"value": 3, "provenance": "specified"},
    "band": {"y_top": 1500, "y_bot": 1700, "provenance": "measured"}
  },
  "samples": ["sample-2026-09-launch"],
  "status": "adopted",
  "adoption": {"date": "2026-09-27", "by": "user", "statement": "…", "scope": "9:16 竖屏解说字幕"}
}
```

- 两类模板先落地：`subtitle_style`（映射到 assemble 现有 `SUBTITLE_*` 键、字体资源与字幕带坐标）和
  `packaging`（包框 / 标题条 / logo / 片尾卡等图层，每层声明图片资源、画布与安全区，由 `compose_foreground.py` 合成）。
- 每个模板只对声明的画布校准；换画幅是新模板，never 自动缩放套用。图层分开版本化，改字幕样式不影响包框。
- 参数带 `provenance`（`measured | fitted | specified | unknown`），区分实测像素与编辑器面板读数。
- `status` ∈ `draft | adopted | retired`；只有 `adopted` 能绑定到项目。采用记录写明日期、原话与适用范围；
  模板引用的资源身份变化后，该版本在 dashboard 与运行记录中标为"采用后资源已变化"，需重新采用或出新版本。
- 样片（`sample.json`）只作为证据：指向一条成片、说明它示范了哪些方面（包装外观 / 字幕 / 节奏）以及不能复用什么。

### 3. 项目绑定与运行记录

- 新增项目文件 `recap_project.json`（放在项目目录，一个项目可有多个 work_dir），记录库路径与绑定：
  `{"schema": "video-recap.project.v1", "library": "…", "bindings": {"subtitle_style": "subtitle-clean-white@3", "packaging": "…@2", "voice": "voice-…", "bgm": ["bgm-…"]}}`。
- 只有 video-recap 读取库和项目文件：它解析绑定，把结果写成 `work_dir/resource_lock.json`（本次运行用到的每个资源与模板：
  id、版本、解析后的绝对路径、文件身份、授权状态），再通过阶段技能已有的参数 / 环境变量把解析后的路径传下去
  （`BGM_PATH`、`SUBTITLE_*`、`--voice-ref`、`--mimo-voice`、`--tts-provider`……）。阶段技能 never 读取库，自包含不变。
- 需要补的阶段能力只有两处：assemble 接受字体文件（ASS 渲染传 `fontsdir`，`drawtext` 传 `fontfile`），
  以及把 `packaging` 模板的图层交给 `compose_foreground.py` 的调用路径。
- 授权为 `unknown` / `restricted` 的资源在成片 QC 里作为建议项列出，不阻断（与"三类验收"一致：授权是人的判断）。

### 4. 只读 dashboard

- 放在 video-recap 技能内：`scripts/dashboard_server.py` + `assets/dashboard/{index.html,app.js,styles.css}`，
  纯标准库 `ThreadingHTTPServer`，无构建步骤；`--detach / --status / --stop`，会话文件在 `<root>/.video-recap/`。
- **严格只读**：只有 GET；只绑定回环地址，校验 Host / Origin，拒绝符号链接与越界路径；媒体按扩展名白名单与大小上限提供，
  支持 HTTP Range 以便拖动 MP4。
- 发现规则：按标记文件（`library.json`、`recap_project.json`、`recap_run_manifest.json`）限深、限节点扫描，坏文件显示
  "无法解析"而不是让页面崩溃；运行状态复用 `recap_inspect.py` 的函数，不另写一套解析。
- 视图：项目与运行（阶段进度、下一步缺什么、QC 摘要、成片预览、`resource_lock.json`）、资源库（按类型、授权状态、被哪些项目使用）、
  模板与样片（版本、采用记录、画布、样片预览）。任何动作都回到对话里做，dashboard 不是第二个事实来源。

### 5. 分期

1. 格式与工具：五个 schema、`library.py list / show / check`（只读校验）、合成示例库；不改渲染。
2. 运行记录：recap 从现有输入生成 `resource_lock.json`（先覆盖 BGM、音色、字体名、叠层），行为不变。
3. 项目绑定：`recap_project.json` + `subtitle_style` 模板解析 + assemble 字体文件支持。
4. 只读 dashboard。
5. `packaging` 模板与样片。

每期一个 PR，各自带实现笔记。第 1 期已落地：[[2026-09-27-resource-library-format]]。

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
- **代价**：新增五个 schema 与一个项目文件，`config-playbook.md` 里"没有工具读取配置文件"的立场需要改写；
  video-recap 变得更重（库解析 + dashboard），dashboard 需要自己的 HTTP 与浏览器测试；用户要维护自己的库目录，
  授权与采用状态靠人填写，工具只能提示缺失，不能代为判断。
- 未决问题：库根目录是否与素材库合并（本方案建议合并）；`voice` 资源是否也覆盖 dub 模式的克隆参考；
  dashboard 是否需要访问令牌（本方案建议：只读 + 回环 + Host 校验即可，不设令牌）。
