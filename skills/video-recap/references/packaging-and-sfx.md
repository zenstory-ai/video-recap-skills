# 竖横屏视觉包装与音效规范

视觉包装与音效由数据驱动，所有运行时布局写入 `timeline.json`（`canvas`、`overlays[]`、`audio.sfx[]`、`subtitle_style`），供 `render.py` 渲染并由 `export_jianying.py` 导出。宣发标题与封面选帧写在 `PROMO.md`，不与渲染配置混用。

## 1. Remotion 原创动态包装（推荐路线）

需本机可用 `node`/`npx`（本地无头渲染，联网说明见 `references/remote-services.md`）：

```bash
python3 scripts/packaging.py remotion-init timeline.json --out-dir media/remotion \
  --theme drama-recap --title "纯中文剧名或看点" [--kicker "极简副标"] \
  [--cards cards.json] [--callouts callouts.json]

python3 scripts/packaging.py remotion-render media/remotion --timeline timeline.json \
  [--out-video media/plates/remotion_overlay.mov] [--codec prores|vp9] [--out timeline.json]
```

产出的透明视频以 `id: "remotion_packaging"` 写入 `overlays[]`，`render.py` 直接带 Alpha 叠加，`export_jianying.py` 同步导出为视频轨。

- **把 `src/PackagingOverlay.tsx` 当作可定制画布，绝不照搬千篇一律的模板**：`remotion-init` 生成的是起点，应按每部片子的气质（悬疑冷调、甜宠暖金、喜剧明快、纪录片克制）直接微调配色、衬线/无衬线字重搭配与动效节奏，发挥 Remotion 独有的物理缓动、遮罩揭幕与 SVG 笔触优势，避免套用生硬黑框、英文科技 pill 或模仿单一爆款样板。
- **剧集解说默认起点（`drama-recap` 主题）**：`1080×1440`（3:4）满屏 `fit="cover"` + 逐镜头 `focus` 裁切（不把宽屏剧孤悬在 9:16 大黑边里），内置上下柔光保护暗角（`scrim`），采用现代杂志风**双层极简无衬线刊头**（粗黑主标 + 28px 暖金短杠 + 干净白字副标，`show_progress_bar: false`，零 `|` 竖线、零 `◆` 圆点、零多余角标）。
  - `kind: "huazi"`：无底框**上下双层错落动态排版**（前句细白轻入 + 紧随放大的象牙暖金渐变后句与柔和阴影，无 `◆` 符号、无波浪下划线、无生硬盒子），每处 **4–8 字无末尾标点**，专写潜台词/反差，停留 3.0–4.5s、全片分散 3–5 处，置于左上不压脸与字幕。
  - `kind: "reaction"`：无底框粗描边反应花字（2–7 字口语吐槽/震惊），后置半拍不抢包袱。
- **泛知识/人物故事**：可选 `portrait-cinema`、`portrait-stage`、`landscape-editorial` 的半透明磨砂 `chapter`/`quote`/`scene` 卡片与 `callouts`。

## 2. 静态 PNG 包装（快速/纯离线兜底）

无需 Node/Remotion 时用 `packaging.py apply`（Pillow → ImageMagick → `ffmpeg drawtext` 逐级渲染，按 `id` 幂等覆盖并校验碰撞与 `safe_y`）：

```bash
python3 scripts/packaging.py apply timeline.json --theme portrait-stage \
  --title "主标题" [--cards cards.json] [--callouts callouts.json] \
  [--plates-dir media/plates] [--out timeline.json]
```

- `--cards`：`[{"title": "...", "subtitle": "...", "kind": "huazi|reaction|chapter|quote|scene", "side": "left|right", "start": 1.0, "end": 4.0}]`
- `--callouts`：`[{"slot": "top|bottom", "text": "...", "start": 5.0, "end": 7.5}]`

## 3. 真实素材音效与防撞人声

音效一律使用真实音频素材（通过 `library.py` 或用户目录检索），不手写合成波形：

```bash
python3 scripts/packaging.py place-sfx timeline.json --sfx sfx.json --out timeline.json
```

`place-sfx` 自动避开 `audio.narration` 与 `video[].keep_windows`；完成后用 `frames.py` 抽帧核对排版与主体人脸无遮挡。
