# 字幕与画面精装包装工序

永远先把画面、切点、旁白和混音彻底锁定，再去叠加画面包装与点缀音效。任何字体排印、画框和音效都服务于叙事清晰度与视听沉浸感，绝不拿来掩盖剧情跳脱、切点闪帧或人声打架。

## 1. 原生精装包装方案 `packaging_plan.json`

无需依赖任何外部前端框架或重型运行时，直接在 `work_dir/packaging_plan.json` 声明 `video-recap.packaging_plan.v1`，`assemble.py` 即可通过内置 `@2x` 超采样字卡排版引擎（Pillow LANCZOS 降采样）与单遍 FFmpeg 滤镜图完成商业级成片包装、双层柔光重点词字幕及转场音效合成，并同步写入 `timeline.json`（支持导出剪映草稿继续精修）。

### 三种核心画幅与版式预设（`preset`）

1. **`portrait_cinema`（`1080×1920` 竖版剧集上下金线栏）**
   - 适用：竖构图或近竖构图（`3:4` / `9:16`）剧集、电影切条，或希望最大化保留人物中近景纵深感的竖屏交付。
   - 结构：顶部暖深褐/炭黑渐变眉题栏（配流光细金线、剧名胶丸 `kicker` 与 1–2 行高亮钩子主标题）+ 中央高占比画面视窗（支持 `source_crop_bottom_ratio` 直接裁净原片底部硬字幕，告别生硬黑块遮罩）+ 底部专属字幕栏（双层柔光字幕居中落座）+ 画面上沿安全槽交替浮现书法/粗黑花字。

2. **`portrait_stage`（`1080×1920` 竖版宽银幕舞台精装）**
   - 适用：`16:9` 横屏剧集连剪、体育纪实、商业人物故事转制移动端 `9:16` 成片。
   - 结构：`blurred_frame`（动态高斯暗化虚焦同源背景 + 电影感暗角）或 `studio_dark`（深邃聚光暗场舞台）+ 中央精镶金线视窗（支持 `16:9` / `16:10` / `4:3` / `1:1` 与 `source_crop_bottom_ratio` 裁除原片硬字幕）+ 视窗底沿实时金色进度条（`progress_bar`）+ 顶部常驻双行钩子大标题（`kicker` + `title` + `highlights` + 可选半透磨砂 `subhead`）+ 定时人物铭牌 `name_tag`（`姓名｜身份`）、数据卡 `data_card`（`核心数字｜标签`）、金句卡 `quote_card` 与情绪花字 `callouts` + 下方独立安全区双层柔光字幕（严格避让移动端右侧交互栏与底部信息流遮挡区）。

3. **`landscape_cinema`（`16:9` 横版影院级精装）**
   - 适用：`1920×1080` / `1280×720` 横版深度解说或剧集精剪。
   - 结构：左上角书法片名印章标 `title_mark`（片头渐显 + 可选片尾呼应 `reprise_tail_seconds`）+ 两种字幕呈现模式：
     - `subtitle_card_mode: "editorial_paper"`：暖米白纸感字幕条（`#F2EDE7` 圆角阴影卡 + 左侧鎏金竖签 + 右侧朱砂印记），**仅在连续旁白区间平滑渐入并跨短气口稳态保持**（自然遮盖原片底部硬字幕，搭配深浓墨色正文字幕与朱砂金重点词），而在保留原声对白的留白段自动平滑隐去，完整释放电影原画与原声台词。
     - `subtitle_card_mode: "cinema_glow"`：底部柔和羽化暗角渐变垫底 + 双层光学柔光暖金旁白字幕与纯白「原声台词」分色。

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
    "kicker": "《服务区烧烤店》三集连剪",
    "title": "接盘七万网贷烂摊子\n反手靠系统情报日入破万",
    "highlights": ["七万网贷", "日入破万"],
    "subhead": "高速封路夜 · 第一桶金逆转实录"
  },
  "subtitle_highlights": ["七万", "系统", "一万四"],
  "cards": [
    {"kind": "name_tag", "start": 0.4, "end": 4.2, "name": "李阳", "role": "接盘烧烤店主", "side": "left"},
    {"kind": "data_card", "start": 5.0, "end": 9.0, "value": "70,000元", "label": "前任留下的网贷啤酒库存", "side": "right"}
  ],
  "callouts": [
    {"start": 10.5, "end": 14.0, "text": "电话当场反杀", "style": "gold", "slot": "auto"},
    {"start": 28.0, "end": 31.8, "text": "系统首单暴击", "style": "coral", "slot": "auto"}
  ],
  "sfx": [
    {"preset": "whoosh_soft", "start": 0.08, "gain": 0.24},
    {"preset": "impact_warm", "start": 27.85, "gain": 0.28}
  ]
}
```

## 2. 字幕质感、重点词点亮与防频闪

- **双层柔光与微渐入渐出**：启用 `packaging_plan.json`（或配置 `SUBTITLE_GLOW=3.2`、`SUBTITLE_FADE_MS=70`）后，ASS 自动生成底层光学柔光衬底（`Style: Glow` `\blur`）与前景锐利描边双层结构，配合轻微渐显渐隐，消除硬切生涩感。
- **句内重点词高亮（`highlights`）**：支持在 `narration.json` 单段写入 `"highlights": ["关键词"]` 或在 `packaging_plan.json` 写入全局 `"subtitle_highlights"`；每条字幕最多点亮 1–2 个关键数字或核心转折词（暖亮金或朱砂红），原声台词（`「…」`）始终保持纯白不染色，外挂 `.srt` 保持纯文本。
- **短气口无缝桥接（`bridge_gap_seconds`）**：相邻旁白句间 `<= 0.34s` 的微停顿自动延续前一条字幕至下一条起点（绝不跨越原声对白留白段），彻底消除句间 3–8 帧的空白频闪。

## 3. 点缀音效（`sfx`）与人声避让门禁

- 支持直接引用本地音频文件 `path`（如资源库中的 `sfx` 素材）或使用内置合成工作室音效预设 `preset`（`whoosh_soft`、`whoosh_crisp`、`impact_warm`、`riser_subtle`）。
- **克制与对位纪律**：每分钟最多点缀 2–4 处，仅放在开场钩子切入、大跨度时空转场或核心底牌揭晓瞬间，转场风声峰值对准画面硬切点。
- **原声台词零干扰保护**：`sfx_mix.py` 会自动比对保留的原声台词区间；任何侵入原声对白的音效（除非显式声明 `allow_over_dialogue: true`）会自动微移避开或安全跳过，且整条音效轨统一纳入两遍线性响度与真峰值（`-1.0 dBTP`）控制。

## 4. 静态包装图层 `packaging_layers.json`

若仅需在原画布上叠加全片固定不变的 PNG 贴图（或由 `--project` 绑定 `packaging` 模板写出），仍可使用 `work_dir/packaging_layers.json`：

```json
{
  "canvas": {"width": 1080, "height": 1920},
  "layers": [
    {"name": "frame", "path": "/abs/frame.png", "rect": {"x": 0, "y": 0, "width": 1080, "height": 1920}}
  ]
}
```

## 5. 审美与版面安全红线

- **克制第一**：单支 1 分钟成片花字控制在 3–5 处，每条花字不超过 8–10 字且自动剥离句末标点，补充潜台词而非逐字复读旁白；人物铭牌上了画面，旁白就不再重复报简历。
- **零碰撞硬校验**：`visual_qc.json` 的 `geometry.packaging` 会自动校验所有定时字卡、花字与字幕安全带之间是否存在时空重叠；一旦发生槽位碰撞或超长溢出，直接触发 `packaging_overlay_collision` / `packaging_callout_too_long` 阻断渲染，确保成片版面干净利落。
