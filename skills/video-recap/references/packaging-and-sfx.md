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

- **把 `src/PackagingOverlay.tsx` 当作可定制画布，按影片气质微调**：`remotion-init` 生成的是排版起点，可按影片风格（悬疑冷调、甜宠暖金、喜剧明快、纪录片克制）直接调整配色、字重搭配与入场节奏，发挥 Remotion 的缓动、遮罩揭幕与矢量线条优势，避免套用厚重黑底框或固定样式。
- **剧集解说默认起点（`drama-recap` 主题）**：`1080×1440`（3:4）满画幅 `fit="cover"` + 逐镜头 `focus` 裁切（避免将宽屏剧留在 9:16 大黑边里），内置上下压暗渐变（`scrim`），采用现代简约**双层无衬线片头**（粗体主标 + 28px 暖金短线 + 简洁白字副标，`show_progress_bar: false`，无多余竖线与角标）。
  - `kind: "huazi"`：无底框**上下双层错落动效排版**（前句细白轻入 + 后句暖金渐变放大配柔和阴影，无多余装饰符号与底框），每处 **4–8 字无行尾标点**，用于补充潜台词或反差，停留 3.0–4.5s、全片分散 3–5 处，置于左上避开人脸与字幕。
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
- `--callouts`：`[{"slot": "callout_upper|callout_lower", "text": "...", "start": 5.0, "end": 7.5, "motion": "none|slide_up"}]`（`slot` 省略时上下交替，`motion` 默认 `none`）

## 3. 真实素材音效与防撞人声

音效一律使用真实音频素材（通过 `library.py` 或用户目录检索），不手写合成波形：

```bash
python3 scripts/packaging.py place-sfx timeline.json --sfx sfx.json --out timeline.json
```

`place-sfx` 自动避开 `audio.narration` 与 `video[].keep_windows`；完成后用 `frames.py` 抽帧核对排版与主体人脸无遮挡。
