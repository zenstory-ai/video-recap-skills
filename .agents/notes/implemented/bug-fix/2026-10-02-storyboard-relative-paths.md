# Agent Note: storyboard sidecar 存相对 work_dir 的路径

Status: implemented

## Problem

`storyboard/source_storyboard.json` 和 `edited_storyboard.json` 把 `page_images`（以及 `edited_video_path`）存成写入时的绝对路径。复制 work_dir（`cp -a` 保留 mtime）后，stage 缓存按 `{size, mtime_ns}` 判定仍然命中，命中分支直接返回缓存的 JSON，于是新 work_dir 的 brief 头部"源时间线 storyboard"仍指向原目录；原目录被删后路径就是悬空的。`--work-dir` 写相对路径时，存下的路径还相对于当时的 cwd。

## Decision

- `storyboard.work_dir_relative_pages(pages)` 把页面写成 `storyboard/<文件名>`：页面总在 `<work_dir>/storyboard/` 下，只保留文件名。`build_source_storyboard` / `build_edited_storyboard` 都用它写 `page_images`；`edited_video_path` 写 `edited_source.mp4`（相对 work_dir），`video_path` / `source_video_path` 是原片路径，不在 work_dir 里，保持原样。
- `understanding_storyboard._reuse_cached_storyboard`：缓存命中时把 `page_images` 和 `edited_video_path` 按文件名重新推导；与磁盘上不同（旧版本写的绝对路径）就改写 JSON 并重新盖 sidecar，下次仍然命中。
- brief 头部写"路径相对 work_dir"。
- 测试：`tests/understanding/test_storyboard.py` 断言新写的 `page_images` 是相对路径且相对 work_dir 存在；构造旧版绝对路径的缓存，断言命中时不重建、返回值与磁盘都改成相对路径、再次调用仍命中。

## Alternatives considered

- **把 work_dir 身份加进缓存键，复制后重建。** 最强理由：不改落盘格式。没采用：重建只是为了改一个路径字符串，还要重新跑 ffmpeg 拼图；而且绝对路径在原目录被删或移动后照样悬空。
- **只在缓存命中时改写返回值，不改磁盘。** 最强理由：不碰已有文件。没采用：直接读 JSON 的 Agent 仍会看到旧目录。

## Consequences

- 收益：复制或移动的 work_dir，brief 与 JSON 都指向自己的 storyboard 页面。
- 代价：`page_images` 从绝对路径变成相对 work_dir 的路径，按绝对路径直接打开的外部脚本要先拼上 work_dir（仓库里没有这样的读取方）。
