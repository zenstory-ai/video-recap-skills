# 资源库、模板与样片

资源库是你自己的目录，与素材库共用同一个根目录（`--material-library-dir` / `VIDEO_RECAP_MATERIAL_LIBRARY_DIR`）。
仓库不内置任何真实 BGM、音效、音色、字体或包装；合成示例见仓库的 `examples/resource-library/`。

```text
<library>/
  library.json                                     # {"schema": "video-recap.library.v1", "name": "…"}
  materials/…                                      # 理解分析库（见 data-schema.md），不变
  resources/<kind>/<id>/resource.json (+ 文件)
  templates/<kind>/<id>/v<version>/template.json
  samples/<id>/sample.json (+ 样片文件，或指向库外的绝对路径)
```

只读工具（本技能目录下）：

```bash
python3 scripts/library.py --library-dir <library> check          # 有错误时退出码 1；--json 输出机器可读报告
python3 scripts/library.py --library-dir <library> list [--kind bgm]
python3 scripts/library.py --library-dir <library> show <id|id@vN>
```

工具从不写库。新增或修改记录时直接编辑 JSON，再跑 `check`。**错误**表示该条目不能用；**警告**表示能用但需要人看一眼
（授权未确认、声音授权未确认、样片不在本机、模板采用后资源文件已变化）。

## 通用规则

- `id` 与所在目录名一致，只用小写字母、数字和 `. _ -`；模板目录名为 `v<version>`。
- 顶层字段严格：出现未列出的字段即报错，避免拼错的字段被静默忽略。
- 文件路径相对记录所在目录，解析后必须仍在库根目录内（`..` 越界、绝对路径和指向库外的符号链接都报错）。
  唯一例外是样片可以写绝对路径，因为成片常放在别的盘上。
- 文件身份是 `{size, mtime_ns}`，不计算内容哈希。
- 派生文件（归一化 WAV、转码片段）只出现在 work_dir，不回写为资源。

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

| `kind` | 文件 | 额外字段 |
|---|---|---|
| `bgm` / `sfx` | 至少一个音频文件（wav / mp3 / m4a / aac / flac / ogg） | — |
| `voice` | 可选参考音频 | `voice.provider` ∈ `mimo-tts` / `fish-audio` / `index-tts`，以及 `voice.voice_id` 或一个参考音频；有参考音频时必须写 `consent.status` ∈ `unknown` / `granted` / `denied` |
| `font` | 至少一个字体文件（ttf / otf / ttc） | 可选 `font.family`、`font.index` |
| `image` | 至少一个图片（png / jpg / webp），用于 logo、包框、片尾卡 | — |

`license.status` ∈ `unknown` / `owned` / `licensed` / `restricted`，只能由人填写；工具不会因为文件放在库里就认为有授权。
`unknown` 与 `restricted` 在 `check` 中是警告。

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
    "band": {"value": {"y_top": 1280, "y_bot": 1440}, "provenance": "measured"}
  },
  "samples": ["demo-sample"],
  "status": "adopted",
  "adoption": {"date": "2026-09-27", "by": "user", "statement": "用户原话", "scope": "适用范围"}
}
```

- 模板只对 `canvas` 声明的画布有效；换画幅就是新模板，不自动缩放套用。
- 任何带 `provenance` 的参数都要有 `value`，`provenance` ∈ `measured`（从成片实测）/ `fitted`（反复调出来的）/
  `specified`（人直接给的数值）/ `unknown`。编辑器面板上的读数不是像素，按 `specified` 或 `unknown` 记。
- 引用资源写 `{"resource": "<id>"}`：`params.font` 必须指向 `font` 资源，图层的 `image` 必须指向 `image` 资源。
- `subtitle_style` 需要 `params.font`（`resource` 或 `family`）与 `params.size_px`；`params.band.value` 若给出，需满足
  `0 <= y_top < y_bot <= canvas.height`。
- `packaging` 需要非空 `params.layers`，每层有唯一 `name`、`image` 引用与画布内的整数 `rect {x, y, width, height}`；
  可选 `params.safe_rect`。
- `status` ∈ `draft` / `adopted` / `retired`。`adopted` 必须有 `adoption.date`（YYYY-MM-DD）、`by`、`statement`（用户原话）
  与 `scope`。可选 `adoption.resources` 记录采用时所用资源文件的身份：

  ```json
  "resources": {"frame-demo": [{"path": "resources/image/frame-demo/frame-demo.png", "size": 6063, "mtime_ns": 0}]}
  ```

  之后文件的大小或修改时间变化，`check` 给出 `changed_since_adoption` 警告：重新采用，或出一个新版本。
  修改时间是本机事实，把库拷到另一台机器会让所有快照都显示为已变化。

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

样片是证据，不是模板：`demonstrates` 写它示范了什么，`not_reusable` 写不能照搬什么（人物、字幕内容、时间码……）。
`templates` 用 `id@vN` 指回它所示范的模板版本。
