---
name: video-reference
user-invocable: true
description: Analyze a finished reference video locally to extract shot rhythm, loudness/audio density, and contact sheets, then distill reusable editing and storytelling patterns into plain Markdown REFERENCE_NOTES.md without leaking source plot details. 按需拆解优秀成片：本地实测镜头节奏、响度密度并抽帧拼图，提炼脱敏的可迁移剪辑参考笔记 REFERENCE_NOTES.md。
---

# Video Reference — 成片节奏与手法拆解

本技能按需拆解一部**已完成的优秀成片**，先用本地工具拿到客观节奏与响度指标、再由你亲眼查看拼图，提炼一份脱敏、可复用的 Markdown 参考笔记 `REFERENCE_NOTES.md`。

## 1. 运行边界与本地声明

- **全程 100% 本地运行**：仅使用 Python 3.10+ 标准库与 `ffmpeg` / `ffprobe`，绝不上传视频、音频或帧图到任何远程服务。
- **事实与方法分家**：原片的人名、具体剧情与台词只留在本地观察笔记里；最终交付的 `REFERENCE_NOTES.md` 只保留通用剪辑、叙事、声音与版式方法，以及可核查的实测区间。
- 本技能只产出参考笔记，不充当生产门禁，也不把参考片的切点密度或响度当作新素材的硬指标。

## 2. 拆片步骤

1. **运行本地测量与拼图**（路径相对于本技能目录）：

```bash
python3 scripts/analyze_reference.py <reference_video> --out-dir <out_dir> [--grid 4x4]
```

该命令生成：
- `<out_dir>/reference_metrics.json`：画幅、总时长、镜头切点（`shots.cuts`）、中位/P10/P90 镜头时长、每分钟切点数、每 10 秒切点密度曲线、待复核切点窗口（`shots.review_windows`），以及整体响度（LUFS / LRA / True Peak）与有效声音占比（`audio.active_audio_share`）。
- `<out_dir>/contact_sheet.jpg`：全片均匀抽帧拼图，供你直接观察构图、景别切换、字幕位置与包装版式。

2. **亲眼看图与核对指标**：打开 `contact_sheet.jpg` 并对照 `reference_metrics.json`，识别开场钩子、段落推进、高潮留白与结尾收束；若存在 `review_windows` 密集区，结合画面判断是快切还是运动镜头。
3. **撰写脱敏参考笔记（`<out_dir>/REFERENCE_NOTES.md`）**：用平实中文或用户指定语言整理五节：
   - **成片画像**：画幅、时长、中位镜长、每分钟切点数、响度与有效声占比（直接引用实测值）。
   - **叙事与开场结构**：前 5 秒如何抛出悬念或看点、中段如何推进转折、结尾如何收束。
   - **镜头与节奏手法**：何时密集切镜、何时放慢留长镜头、景别与人物反应如何衔接。
   - **声音与字幕版式**：人声/原声/配乐如何错峰让位、字幕位置字号与重点词强调方式。
   - **适用与避坑边界**：这套打法适合什么题材，换素材时哪些做法绝不能生搬硬套。

写完后自查 `REFERENCE_NOTES.md`：确保不含原片角色名、具体台词摘抄或绝对时间码剧透，换一部新素材也能直接照用。
