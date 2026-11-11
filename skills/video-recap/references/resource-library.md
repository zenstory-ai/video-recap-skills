# 资源库、模板与样片

资源库归用户自己维护，与素材库共用同一个根目录（通过 `--material-library-dir` 或 `VIDEO_RECAP_MATERIAL_LIBRARY_DIR` 指定）。仓库不内置任何真实 BGM、音效、音色、字体或包装素材；可运行的合成演示见 `examples/resource-library/`。

```text
<library>/
  library.json                                     # {"schema": "video-recap.library.v1", "name": "…"}
  materials/…                                      # 视频理解分析缓存与剧集分集元数据（见 data-schema.md）
  resources/<kind>/<id>/resource.json (+ 资源文件) # 或按二级分类目录存放：resources/<kind>/<category>/<id>/resource.json
  templates/<kind>/<id>/v<version>/template.json
  samples/<id>/sample.json (+ 样片文件，或指向库外的绝对路径)
```

本技能目录下提供三个只读查询命令：

```bash
python3 scripts/library.py --library-dir <library> check          # 存在错误时退出码为 1；加 --json 输出机器可读报告
python3 scripts/library.py --library-dir <library> list [--kind bgm]
python3 scripts/library.py --library-dir <library> show <id|id@vN>
```

也可以启动只读看板浏览资源、模板与样片（支持图片、音频与样片预览）：`python3 scripts/dashboard_server.py --root <library 或其上层目录>`。

以上查询命令都不改写资源库。新增或修改条目时直接编辑 JSON，再跑 `check` 校验。**错误（error）**表示该条目无法投入使用；**警告（warning）**表示可以使用但需要人工复核（例如版权或声音授权未确认、样片文件不在本机、模板采纳后底层资源文件发生过变动）。

## 通用规则

- `id` 必须与所在目录名一致，只能使用小写字母、数字和 `. _ -`；模板版本目录命名为 `v<version>`。
- 顶层字段受严格校验：出现未定义的字段直接报错，防止拼写错误被静默忽略。
- 资源与模板内的文件路径相对于记录所在目录解析，且解析后的真实路径必须留在资源库根目录内（`..` 越界、绝对路径和指向库外的符号链接都会报错）。唯一例外是样片记录可以填写库外绝对路径，方便直接指向存放在外部磁盘上的成片。
- 文件身份只比对 `{size, mtime_ns}`，不计算内容哈希。
- 运行中生成的派生文件（如归一化 WAV、转码片段）只写入 `work_dir`，不会回写进资源库。

## 资源 `resource.json`

```json
{
  "schema": "video-recap.resource.v1",
  "id": "pulse-demo",
  "kind": "bgm",
  "title": "合成脉冲底噪（演示）",
  "files": [{"role": "main", "path": "pulse-demo.wav"}],
  "origin": {"creator": "…", "url": "…"},
  "license": {"status": "owned", "terms": "…", "evidence": "…"},
  "tags": ["低频"],
  "notes": ""
}
```

| `kind` | 必需文件 | 额外字段 |
|---|---|---|
| `bgm` | 至少一个音频文件（wav / mp3 / m4a / aac / flac / ogg） | 可选分类与声学画像：`category`（未填时自动继承二级分类目录名）、`sub_category`、`moods`、`scene_fit`、`energy`、`tempo_band`、`bpm`、`has_vocal`（布尔值；`true` 时 `check` 报警告且解说模式拒绝绑定）、`speech_friendly`、`measured`（如 `{duration_sec, lufs_i}`） |
| `sfx` | 至少一个音频文件（wav / mp3 / m4a / aac / flac / ogg） | 可选 `category`（如 `whoosh` / `impact` / `riser` / `accent`）、`tone`、`scene_fit`、`peak_offset_ms`（瞬态峰值毫秒偏移，混音时自动前移对齐切点）、`recommended_gain_db`、`measured` |
| `voice` | 可选参考音频 | `voice.provider` ∈ `mimo-tts` / `fish-audio` / `index-tts`，并填写 `voice.voice_id` 或附带一个参考音频；附带参考音频时必须填写 `consent.status` ∈ `unknown` / `granted` / `denied`；可选 `category`、`persona`、`gender`、`languages`、`recommended_speed` |
| `font` | 至少一个字体文件（ttf / otf / ttc） | 可选 `font.family`、`font.index`、`visibility` |
| `image` | 至少一张图片（png / jpg / webp），用于角标、包框或片尾卡 | 可选 `visibility` ∈ `public` / `internal`（`internal` 供模板内部引用，不在素材库主视图平铺展示） |

`license.status` ∈ `unknown` / `owned` / `licensed` / `restricted`，必须由人工填写；工具不会因为文件放进了资源库就默认已获授权。`unknown` 与 `restricted` 在 `check` 时会报出警告。

## 模板 `template.json`

```json
{
  "schema": "video-recap.template.v1",
  "id": "clean-white",
  "version": 1,
  "kind": "subtitle_style",
  "title": "白字细描边",
  "canvas": {"width": 900, "height": 1600},
  "params": {
    "font": {"family": "Arial"},
    "size_px": {"value": 52, "provenance": "specified"},
    "max_chars": {"value": 15, "provenance": "measured"},
    "band": {"value": {"y_top": 1280, "y_bot": 1440}, "provenance": "measured"}
  },
  "samples": ["demo-sample"],
  "status": "adopted",
  "adoption": {"date": "2026-09-27", "by": "user", "statement": "用户原话", "scope": "适用范围"}
}
```

- 模板只对 `canvas` 声明的画布尺寸生效；更换画幅必须新建模板，不会自动按比例缩放。
- 凡带 `provenance` 的参数都必须包含 `value`，且 `provenance` ∈ `measured`（从成片画面实测）/ `fitted`（调试校准得出）/ `specified`（人工直接指定）/ `unknown`。剪辑软件面板上的字号读数不等于实际像素高，应记为 `specified` 或 `unknown`。
- 引用库内资源时写 `{"resource": "<id>"}`：`params.font` 必须指向 `font` 资源，包装图层的 `image` 必须指向 `image` 资源。
- `subtitle_style` 必须包含 `params.font`（填 `resource` 或 `family`）、`params.size_px` 与 `params.max_chars`（按该画布能容纳的最长单行字数校准）；当 `size_px × max_chars` 超过画布宽度减去左右各 40px 默认边距时报错。可选参数为 `outline_px`、`shadow_px`、`primary_color`、`outline_color`、`max_lines`、`band`，出现其他参数名直接报错；若给出 `params.band.value`，需满足 `0 <= y_top < y_bot <= canvas.height`。
- `packaging` 支持静态图层 `params.layers` 或声明式演播室版式 `params.studio_plan`（`preset` ∈ `portrait_cinema` / `portrait_stage` / `landscape_cinema`），也可二者同时组合；静态图层包含唯一 `name`、`image` 资源引用以及落在画布内的整数 `rect {x, y, width, height}`；可选 `params.safe_rect`。
- `production_reference` 模板见下一节。
- `status` ∈ `draft` / `adopted` / `retired`。状态为 `adopted` 时必须填写 `adoption.date`（YYYY-MM-DD）、`by`、`statement`（用户确认原话）与 `scope`。可选 `adoption.resources` 记录采纳当下底层资源文件的大小与修改时间：

  ```json
  "resources": {"frame-demo": [{"path": "resources/image/frame-demo/frame-demo.png", "size": 6063, "mtime_ns": 0}]}
  ```

  此后若资源文件的大小或修改时间发生变化，`check` 会报 `changed_since_adoption` 警告，提醒重新确认采纳或发布新版本模板。由于修改时间属于本机文件系统属性，把资源库复制到另一台机器后，所有快照都会提示已变化。

### 制作参考模板 `production_reference`

把 `video-reference` 导出的 `production_reference.json`（仅含可迁移方法与实测数值，不含原片剧情与人名）登记为带版本、需确认采纳的模板：

1. 创建 `templates/production_reference/<id>/v1` 目录，把导出的 `production_reference.json` 原样复制进去，保持文件名不变。
2. 编写 `template.json`：`params` 固定写 `{"reference": {"path": "production_reference.json"}}`；`canvas` 从导出物复制，仅作信息展示，项目绑定时不校验画布尺寸。
3. 编写 `samples/<id>/sample.json` 指向原成片，在 `demonstrates` 与 `not_reusable` 中写明供人查阅的摘要；包含原片事实的完整拆解稿留在本地工作目录，不进资源库。
4. 运行 `library.py check`：导出物的 `schema` 必须为 `video-reference.production.v1`，`methods` 非空且每条包含合法 `dimension` 与 `rule`；任意层级只要出现 `source_facts`、`labels`、`evidence`、`entities`、`statement`、`from`、`path` 键即报错（与参考技能导出时的防泄漏复扫规则一致）。
5. 用户确认后，将 `status` 改为 `adopted` 并在 `adoption` 中记录用户原话。后续如需调整方法，新建 `v2` 目录，不直接修改已采纳的版本。

## 样片 `sample.json`

```json
{
  "schema": "video-recap.sample.v1",
  "id": "demo-sample",
  "title": "纯色演示片",
  "file": {"path": "demo-sample.mp4"},
  "canvas": {"width": 900, "height": 1600},
  "demonstrates": ["字幕带位置", "画布尺寸"],
  "not_reusable": "这条样片里哪些东西不能照搬",
  "templates": ["clean-white@v1"]
}
```

样片是核对模板效果的实物依据，不是模板本身：`demonstrates` 写明它示范了哪些版式或节奏特征，`not_reusable` 写明哪些具体内容（人物、台词字幕、时间点等）绝不能照搬，`templates` 用 `id@vN` 指回它所对应的模板版本。

## 项目绑定 `recap_project.json`

```json
{
  "schema": "video-recap.project.v1",
  "name": "某系列竖屏解说",
  "library": "../video-library",
  "bindings": {"subtitle_style": "clean-white@v1", "voice": "narrator-demo", "bgm": "pulse-demo"}
}
```

运行 `python3 scripts/recap.py <video> --project <recap_project.json 或所在目录> …` 时，编排器会在进入任何阶段前先解析项目绑定：`library` 路径相对于项目文件解析，所引用的模板状态必须为 `adopted`，且涉及的资源与模板都必须通过 `check`。解析结果会转换为各阶段既有的命令行参数或环境变量下发，各阶段技能本身不直接读取资源库：

| 绑定项 | 下发方式 |
|---|---|
| `subtitle_style` | `SUBTITLE_PLAY_RES_X/Y` = 模板画布宽高，`SUBTITLE_FONT_SIZE` ← `size_px`，`SUBTITLE_OUTLINE` ← `outline_px`，`SUBTITLE_SHADOW` ← `shadow_px`，`SUBTITLE_PRIMARY_COLOR` / `SUBTITLE_OUTLINE_COLOR`（转成 ASS `&HAABBGGRR`），`SUBTITLE_MAX_CHARS` / `SUBTITLE_MAX_LINES`；`band` → 底对齐 `SUBTITLE_ALIGNMENT=2` 且 `SUBTITLE_MARGIN_V` = 画布高 − `y_bot`；`font.family` → `SUBTITLE_FONT_NAME`，若引用字体资源则同时设置 `SUBTITLE_FONT_FILE` |
| `voice` | provider → `--tts-provider`；MiMo 预置音色 → `--mimo-tts-voice`，参考音频 → `--voice-ref`；Fish Audio → `FISH_TTS_REFERENCE_ID`；index-tts → `INDEX_TTS_VOICE` |
| `bgm` | `BGM_PATH` ← 该资源下的第一个音频文件 |
| `production_reference` | 第一次暂停前写出 `work_dir/production_reference.json`（保留导出物原内容并附上 `written_by` 与 `template {id, version}`），仅供写稿 Agent 阅读，无任何脚本消费，也不校验画布。若该路径已有非 `--project` 生成的文件：内容与导出物一致则原样保留，不一致则直接报错中止；解除绑定后只删除编排器自己写出的副本，不碰调用方手动放置的文件 |
| `packaging` | 合成前写出 `work_dir/packaging_layers.json`（包含每个图层的首个图片文件路径与 `rect`），由合成阶段叠加到成片画面并同步写入 `timeline.json` 的 image 轨 |

- 若命令行或环境变量已显式传入某项配置，且与项目绑定解析出的值不一致，程序会在启动前报错中止并指明冲突项，绝不静默覆盖。
- 合成前会核对字幕模板、包装模板的画布尺寸与成片实际画布是否一致；尺寸不符立即中止，换画幅必须绑定对应画幅的模板。
- 参考音频的 `consent.status` 为 `denied` 时拒绝绑定。
- 暂停打印的续跑命令只携带 `--project`，不把绑定展开成一长串散装参数；中途修改 `recap_project.json` 后续跑，会按新绑定重新解析。

## 运行记录

每次 `full` 或 `cut` 合成结束后，`work_dir/resource_lock.json` 都会记录本轮实际使用的原片、音色、BGM 与字幕字体，并关联资源库里的登记项与授权状态；字段定义见 `data-schema.md` 的 `resource_lock.json` 一节。
