# 合成示例资源库

演示 `skills/video-recap/references/resource-library.md` 定义的目录结构与记录格式。全部媒体都是合成的
（正弦波、纯色画面、透明 PNG），画布 900x1600 也是刻意选的非交付尺寸；真实资源库放在你自己的目录里，不进仓库。

```bash
python3 skills/video-recap/scripts/library.py --library-dir examples/resource-library check
python3 skills/video-recap/scripts/library.py --library-dir examples/resource-library list
python3 skills/video-recap/scripts/library.py --library-dir examples/resource-library show clean-white@v1
```

`check` 会报一条警告：演示音色的授权状态是 `unknown`，这是有意留下的示例。
