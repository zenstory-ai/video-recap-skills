"""Local ffmpeg capability preflight for subtitle burn-in and drawtext overlays."""

import shutil
import subprocess
from pathlib import Path

from assemble_constants import VISUAL_OVERLAYS
from lib import CONFIG, log
from visual_render import _load_visual_overlays

def _ffmpeg_filters():
    """Return ffmpeg's compiled-in filter names (caller has confirmed ffmpeg exists)."""
    result = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                            text=True, capture_output=True, timeout=20)
    if result.returncode != 0:
        return set()
    filters = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] and parts[0][0] in ".TSCAPN|":
            filters.add(parts[1])
    return filters


SUBTITLE_BURN_DEGRADED = "subtitle_burn_degraded"
LIBASS_MISSING = "ffmpeg_missing_libass"


def _preflight_burn_subtitles():
    """Settle subtitle burn-in before the render when ffmpeg lacks the libass `subtitles`
    filter. An explicit request (--burn-subtitles or BURN_SUBTITLES in the environment)
    fails here. The implicit default degrades: burn turns off, the reason is recorded in
    CONFIG["burn_subtitles_degraded"], and the run delivers the .srt sidecar instead (the
    warning lands in visual_qc.json and assembly_manifest.json). Only fires when ffmpeg
    EXISTS but can't burn; an absent ffmpeg fails the render regardless."""
    if not CONFIG["burn_subtitles"]:
        return
    if shutil.which("ffmpeg") is None:
        return
    if "subtitles" in _ffmpeg_filters():
        return
    if CONFIG["burn_subtitles_explicit"]:
        raise SystemExit(
            "已显式要求烧录字幕（--burn-subtitles 或 BURN_SUBTITLES），但当前 ffmpeg 不支持"
            " subtitles/libass 滤镜，渲染会在最后一步失败。\n"
            "  解决：安装带 libass 的 ffmpeg；或去掉显式烧录要求，默认运行会改为输出 .srt 外挂字幕；"
            "或加 --no-burn-subtitles。")
    CONFIG["burn_subtitles"] = False
    CONFIG["burn_subtitles_degraded"] = LIBASS_MISSING
    log("⚠️ 当前 ffmpeg 不支持 subtitles/libass 滤镜：本次不烧录字幕，改为输出 .srt 外挂字幕"
        "（visual_qc.json / assembly_manifest.json 记录 subtitle_burn_degraded）。"
        "要烧录字幕请安装带 libass 的 ffmpeg。")


def _preflight_visual_overlays(work_dir):
    """Fail before the render when visual_overlays.json asks for text but this ffmpeg has no
    drawtext (libfreetype). Overlays are authored content, never a default, so they are not
    silently dropped: remove them or install an ffmpeg with drawtext. An unreadable overlay
    file is left to the render's own validation."""
    if shutil.which("ffmpeg") is None:
        return
    try:
        overlays, _source = _load_visual_overlays(work_dir)
    except ValueError:
        return
    if not any(str(item.get("text", "")).strip() for item in overlays):
        return
    if "drawtext" in _ffmpeg_filters():
        return
    raise SystemExit(
        f"{Path(work_dir) / VISUAL_OVERLAYS} 有 {len(overlays)} 个画面文字叠加，但当前 ffmpeg 不支持"
        " drawtext 滤镜（需要 libfreetype），渲染会在最后一步失败。\n"
        "  解决：安装带 drawtext/libfreetype 的 ffmpeg，或删掉这些叠加"
        "（recap 运行时删掉 narration.json 各段的 visual_overlays）后重跑。")
