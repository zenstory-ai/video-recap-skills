# 剪映 / CapCut 多轨草稿导出

`export_jianying.py` 是单向配套工具：仅读取标准 `timeline.json`（以及本技能内置的 `references/jianying/` 协议骨架），输出自包含、可跨机打开的剪映 / CapCut 多轨草稿目录；核心渲染链路不反向依赖本工具。

## 命令用法

```bash
python3 scripts/export_jianying.py timeline.json --out-dir out/jianying_draft
# 仅在本机同路径调试、不拷贝大体积媒体时可加 --no-bundle-media
python3 scripts/export_jianying.py timeline.json --out-dir out/jianying_draft --no-bundle-media
```

## 导出内容与限制

- **多轨映射**：
  - 视频主轨：`video[]` 按顺序排布，保留源入出点与原声音量；`crop` / `focus` / `canvas.viewport` 不换算，片段按原画面铺满画布，裁切构图需在剪映里手调。
  - 叠加图层轨：`overlays[]`（PNG 或 `.mov`/`.webm` 透明视频）按 `rect` 换算为剪映中心原点坐标与缩放并保留起止时间；`fade_in` / `fade_out` / `motion` 不导出。
  - 独立音轨：原片保留音、旁白 `audio.narration[]`、配乐 `audio.bgm[]` 与音效 `audio.sfx[]` 分别独立成轨。
  - 音量闪避关键帧：用 `_audio.ducking_keyframes` 生成，与 `render.py` 的 `ducking_filters` 是同一条曲线。
  - 字幕轨：`subtitles[]` 逐条转为可编辑文本（正确处理 UTF-16 字符范围），统一白色字；`subtitle_style` 只取 `safe_y` 定垂直位置，不折行、不去行尾标点。
- **跨机便携打包**：默认把引用的音视频与图层按真实路径去重（同一文件只拷一次）拷贝至 `Resources/local/`，并在 `draft_meta_info.json` 记下各文件 MD5，生成 `draft_content.json`、`draft_info.json` 与 `draft_meta_info.json`，复制到其他电脑打开不丢素材。

## 常见注意事项

- 修改 `timeline.json` 后重跑一次 `export_jianying.py` 即可重新生成草稿；在剪映里手动改过的工程不会被自动回写到 `timeline.json`。
- 协议骨架来源与开源许可见 `references/jianying/SOURCE.md` 与 `references/jianying/LICENSE.duo-video`。
