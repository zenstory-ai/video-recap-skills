# 剪映 / CapCut 多轨草稿导出

`export_jianying.py` 是单向叶工具：仅读取标准 `timeline.json`（以及本技能内置的 `references/jianying/` 协议骨架），输出自包含、可跨机打开的剪映 / CapCut 多轨草稿目录；核心渲染链路从不反向依赖本工具。

## 命令用法

```bash
python3 scripts/export_jianying.py timeline.json --out-dir out/jianying_draft
# 仅在本机同路径调试、不拷贝大体积媒体时可加 --no-bundle-media
python3 scripts/export_jianying.py timeline.json --out-dir out/jianying_draft --no-bundle-media
```

## 导出内容与对齐保证

- **多轨完整映射**：
  - 视频主轨：`video[]` 片段按顺序排布，自动换算画布比例、裁切与焦点位置。
  - 叠加图层轨：`overlays[]`（PNG 图层或 `.mov`/`.webm` 透明动态视频）按归一化坐标换算为剪映中心原点坐标，保留进出时长与淡入淡出。
  - 独立音轨：原片保留音、旁白 `audio.narration[]`、配乐 `audio.bgm[]` 与音效 `audio.sfx[]` 分别独立成轨。
  - 音量闪避关键帧：直接复用与 `render.py` 同源的闪避计算，导进剪映后曲线与成片混音保持一致。
  - 字幕轨：`subtitles[]` 与 `subtitle_style` 转为剪映可二次编辑文本轨（正确处理 UTF-16 字符范围）。
- **跨机便携打包**：默认将 `timeline.json` 引用的音视频与图层按 MD5 去重拷贝至草稿目录的 `Resources/local/`，生成 `draft_content.json`、`draft_info.json` 与 `draft_meta_info.json`，复制到其他电脑打开不丢素材。

## 常见注意事项

- 修改 `timeline.json` 后重跑一次 `export_jianying.py` 即可重新生成草稿；在剪映里手动改过的工程不会被自动回写到 `timeline.json`。
- 协议骨架来源与开源许可见 `references/jianying/SOURCE.md` 与 `references/jianying/LICENSE.duo-video`。
