# 合成演示资源库

本目录用于演示 `skills/video-recap/references/resource-library.md` 定义的资源库目录结构与 JSON 记录规范。库内所有媒体文件均由脚本用数学公式合成（正弦波音频、纯色测试视频、带透明通道的简单色块 PNG），画布尺寸 `900×1600` 也是刻意避开常规交付规格的测试画幅；日常生产使用的真实 BGM、音效、字体与样片请存放在你自己的本地目录中，不要提交进代码仓库。

```bash
python3 skills/video-recap/scripts/library.py --library-dir examples/resource-library check
python3 skills/video-recap/scripts/library.py --library-dir examples/resource-library list
python3 skills/video-recap/scripts/library.py --library-dir examples/resource-library show clean-white@v1
```

其中 `templates/production_reference/demo-pacing/v1/` 展示了制作参考模板的登记方式：目录下的 `production_reference.json` 由 `video-reference` 对合成测试数据导出，只包含可迁移方法与实测数值，不含任何原片剧情事实，当前状态设为 `draft`。

运行 `check` 时终端会报出一条警告（提示演示音色 `narrator-demo` 的版权状态 `license.status` 为 `unknown`），这是为了演示未确认授权时的告警效果而刻意保留的测试项。
