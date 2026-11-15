# 字幕与画布视觉包装（`packaging_plan.json`）

先将画面切点、旁白文本与混音锁定，再叠加画布包装与点缀音效。版式、字卡和音效只用于提升信息清晰度与观看体验，不用来掩盖剧情跳接、残镜闪帧或人声冲突。

> **与宣发备忘区分**：本文件定义的 `work_dir/packaging_plan.json` 由 `assemble.py` 直接读取并烧录进成片画面；平台发布标题、封面帧与简介备忘请写入 `work_dir/promo_copy.json`（见 `./promotional-copy.md`）。

## 1. 画布包装配置 `packaging_plan.json`

在 `work_dir/packaging_plan.json` 中声明 `video-recap.packaging_plan.v1`，`assemble.py` 会通过内置 `@2x` 超采样字卡渲染（Pillow LANCZOS 降采样；若环境未安装 Pillow 则降级为无字底板并在 `visual_qc.json` 记 `packaging_text_renderer_unavailable` 提示）与单遍 FFmpeg 滤镜图合成画框、重点词高亮字幕及点缀音效（若同目录存在独立的 `sfx_plan.json`，音效优先读 `sfx_plan.json` 混入成片音轨）；画框与花字图片图层会同步写入 `timeline.json`（支持随视频、旁白、BGM、字幕一并导出剪映草稿），成片渲染后还会自动抽帧生成 `work_dir/cover.jpg` 封面图。

### 三种画幅与版式预设（`preset`）

1. **`portrait_cinema`（`1080×1920` 竖版上下分栏）**
   - 适用：竖构图或近竖构图（`3:4` / `9:16`）剧集、电影切条，需保留较大人物中近景比例的竖屏成片。
   - 结构：顶部深色渐变眉题栏（含细金分割线、剧名标签 `kicker` 与 1–2 行主标题）+ 中央画面视窗（可通过 `source_crop_bottom_ratio` 裁掉原片底部硬字幕）+ 底部独立字幕区 + 视窗上沿安全槽定时花字。

2. **`portrait_stage`（`1080×1920` 竖版横画幅舞台）**
   - 适用：`16:9` 横屏剧集连剪、纪实或人物故事转制 `9:16` 竖屏成片。
   - 结构：`blurred_frame`（同源高斯暗化虚焦背景）或 `studio_dark`（暗色纯底背景）+ 中央视窗（支持 `16:9` / `16:10` / `4:3` / `1:1` 及 `source_crop_bottom_ratio` 裁除底部硬字幕）+ 视窗底沿进度条（`progress_bar`）+ 顶部双行主标题（`kicker` + `title` + `highlights` + 可选 `subhead`）+ 定时人物铭牌 `name_tag`、数据卡 `data_card`、金句卡 `quote_card` 与花字 `callouts` + 下方独立安全区字幕（避开移动端右侧与底部交互遮挡区）。

3. **`landscape_cinema`（`16:9` 横版宽画幅）**
   - 适用：`1920×1080` / `1280×720` 横版深度解说或剧集精剪。
   - 结构：左上角片名角标 `title_mark`（片头渐显 + 可选片尾呼应 `reprise_tail_seconds`）+ 两种字幕底栏模式：
     - `subtitle_card_mode: "editorial_paper"`：浅米色圆角字幕条（`#F2EDE7`），仅在连续旁白区间渐入并跨短气口保持稳定（同时遮住原片底部硬字幕），进入原声对白留白段时自动隐去以完整露出原画。
     - `subtitle_card_mode: "cinema_glow"`：底部柔和暗角衬底 + 双层柔光暖金旁白字幕与纯白「原声台词」分色。

### 示例 `packaging_plan.json`

```json
{
  "schema": "video-recap.packaging_plan.v1",
  "preset": "portrait_stage",
  "backdrop_mode": "blurred_frame",
  "viewport_aspect": "16:9",
  "source_crop_bottom_ratio": 0.13,
  "focus_y": 0.42,
  "progress_bar": true,
  "header": {
    "kicker": "《暗夜追光》三集连剪",
    "title": "三年前结案报告被换签名\n他深夜重查旧档揪出内鬼",
    "highlights": ["被换签名", "揪出内鬼"],
    "subhead": "档案室夜查 · 关键物证浮出水面"
  },
  "subtitle_highlights": ["三年前", "签名", "密信"],
  "cards": [
    {"kind": "name_tag", "start": 0.4, "end": 4.2, "name": "林砚", "role": "刑侦队长", "side": "left"},
    {"kind": "data_card", "start": 5.0, "end": 9.0, "value": "3年", "label": "旧案尘封时间", "side": "right"}
  ],
  "callouts": [
    {"start": 10.5, "end": 14.0, "text": "笔迹当场露馅", "style": "gold", "slot": "auto"},
    {"start": 28.0, "end": 31.8, "text": "底牌亮出", "style": "coral", "slot": "auto"}
  ],
  "sfx": [
    {"preset": "whoosh_soft", "start": 0.08, "gain": 0.24},
    {"preset": "impact_warm", "start": 27.85, "gain": 0.28}
  ]
}
```

## 2. 字幕样式、重点词点亮与短气口桥接

- **双层柔光与渐入渐出**：启用 `packaging_plan.json`（或配置 `SUBTITLE_GLOW=3.2`、`SUBTITLE_FADE_MS=70`）后，ASS 生成底层柔光衬底与前景描边双层字幕，带轻微渐显渐隐。
- **句内重点词高亮（`highlights`）**：可在 `narration.json` 单段写 `"highlights": ["关键词"]` 或在 `packaging_plan.json` 写全局 `"subtitle_highlights"`；每条字幕点亮 1–2 个关键数字或转折词，原声台词（`「…」`）保持纯白，外挂 `.srt` 保持纯文本。
- **短气口桥接（`bridge_gap_seconds`）**：相邻旁白句间 `<= 0.34s` 的短停顿自动延续前一条字幕至下一条起点（不跨越原声留白段），减少句间几帧空窗引起的闪烁。

## 3. 点缀音效（`sfx`）与对白避让

- 可引用本地音频路径 `path`（如资源库中的 `sfx` 素材）或内置预设 `preset`（`whoosh_soft`、`whoosh_crisp`、`impact_warm`、`riser_subtle`）。
- **克制点缀**：每分钟建议不超过 2–4 处，仅用于开场切入、大跨度时空转换或关键信息揭晓，转场音效峰值对齐硬切点。
- **原声台词避让**：`sfx_mix.py` 会比对保留的原声对白区间；未显式设置 `allow_over_dialogue: true` 时，落入对白区的音效会自动微移避让或跳过，并纳入全片线性响度与真峰值（`-1.0 dBTP`）控制。

## 4. 静态贴图图层 `packaging_layers.json`

若只需在画布上叠加全片固定的 PNG 贴图（或由 `--project` 绑定 `packaging` 模板生成），可使用 `work_dir/packaging_layers.json`：

```json
{
  "canvas": {"width": 1080, "height": 1920},
  "layers": [
    {"name": "frame", "path": "/abs/frame.png", "rect": {"x": 0, "y": 0, "width": 1080, "height": 1920}}
  ]
}
```

## 5. 版面密度与防碰撞门禁

- **控制字量**：1 分钟短片花字建议控制在 3–5 处，单条建议 8–10 字以内（`16` 加权字宽以内使用标准字号，`16–26` 字宽自动缩字号紧凑排版并记 `packaging_callout_compact_scaled` 提示，超过 `26` 字宽触发 `packaging_callout_too_long` 硬阻断；句末标点自动剥离），只补潜台词不复读旁白；人物铭牌已标出身份时，旁白无需重复念履历。
- **时空几何防碰撞检查**：`visual_qc.json` 的 `geometry.packaging` 会按活跃时间段检查定时字卡、花字与实际出现的字幕安全区是否发生时空重叠（`slot: "auto"` 会自动避让同时间段活跃的字幕安全区），一旦碰撞即报 `packaging_overlay_collision` 阻断渲染。
