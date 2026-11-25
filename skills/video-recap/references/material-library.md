# 审片工作台与素材资源库

配套提供两支只读/轻量工具，均围绕 `timeline.json` 与普通目录运作，不绑定专有数据库或跨机失效的 `mtime_ns` 文件锁。

## 1. 本地审片台（`dashboard.py`）

支持两种无侵入审片方式：

```bash
# 方式 A：零服务端，为单个工作目录生成自包含静态审片页 review.html
python3 scripts/dashboard.py build <work_dir> --out <work_dir>/out/review.html

# 方式 B：只读本地服务（严格绑定 127.0.0.1，仅允许 GET/HEAD，路径越界拦截 + Range 流式播放）
python3 scripts/dashboard.py serve --root <projects_or_library_dir> [--port 0] [--open]
```

- 单片视图：同步展示成片播放器、可点击跳转的字幕/镜头时间轴、`EDIT_PLAN.md` 与 `check.json` 体检状态。
- 素材库视图：在线试听 `catalog.json` 中的 BGM / SFX / 音色样本，浏览跨项目缓存的素材笔记。

## 2. 跨项目素材与资源库（`library.py`）

以单一扁平可扩展的 `catalog.json` 管理 BGM、SFX、音色、字体、图片与主题，并在 `<lib>/materials/<slug>-<bytes>/` 缓存分析笔记与转写（按文件名与字节数索引，跨机移动不失效，自动脱敏密钥）：

```bash
python3 scripts/library.py list <lib_dir> [--kind bgm|sfx|voice|font|image|theme]
python3 scripts/library.py find <lib_dir> <query>
python3 scripts/library.py cache-save <lib_dir> --source <video> --from-dir <work_dir>
python3 scripts/library.py cache-restore <lib_dir> --source <video> --to-dir <work_dir>
python3 scripts/library.py audit timeline.json --library <lib_dir> [--out audit.json]  # 省略 --library 时读 VIDEO_RECAP_LIBRARY_DIR
```

- **合规与授权审计（`audit`）**：直接扫描 `timeline.json` 引用的所有素材与人声，标记未登记资产（`unregistered`）、版权状态不明/受限（`license_unknown` / `restricted`）以及未确认音色克隆同意（`consent_unknown` / `denied`），提醒在交付前补齐授权。
- **系列频道预设**：可把常用画幅、字幕样式、BGM 与主题存为普通 JSON 预设，直接合并进新项目的 `timeline.json`，不依赖环境变量翻译层。
