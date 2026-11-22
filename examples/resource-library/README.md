# 合成演示素材库

本目录用于演示 [material-library.md](../../skills/video-recap/references/material-library.md) 定义的扁平素材库 `catalog.json` 与频道预设 `presets/*.json` 规范。库内所有媒体文件均由数学公式合成（正弦波音频、纯色测试视频、带透明通道的简单色块 PNG），测试画布 `900×1600` 刻意避开常规交付规格；真实生产用的 BGM、音效、字体与成片请放在本地私有目录，不要提交进代码仓库。

## 常用命令（`library.py`）

```bash
# 查看素材库登记项 / 按关键词检索
python3 skills/video-recap/scripts/library.py list examples/resource-library
python3 skills/video-recap/scripts/library.py find examples/resource-library pulse

# 将频道预设合并到某次运行的 timeline.json v3（不覆盖显式已填字段）
python3 skills/video-recap/scripts/library.py preset examples/resource-library demo-channel \
  --timeline work/timeline.json --out work/timeline.json

# 审计某份 timeline.json 的资源存在性、版权与音色授权状态
python3 skills/video-recap/scripts/library.py audit work/timeline.json \
  --library examples/resource-library
```

库内 `narrator-demo` 刻意将 `license` / `consent` 设为 `unknown`，用于在执行 `audit` 时演示未确认授权时的提醒输出。
