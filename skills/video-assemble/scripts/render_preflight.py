"""Local ffmpeg capability preflight for subtitle burn-in."""

import shutil
import subprocess

from lib import CONFIG

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


def _preflight_burn_subtitles():
    """Fail before the (re-encoding) render when burn-in is on but ffmpeg lacks the libass
    `subtitles` filter. Only fires when ffmpeg EXISTS but can't burn — an absent ffmpeg fails
    the render regardless."""
    if not CONFIG["burn_subtitles"]:
        return
    if shutil.which("ffmpeg") is None:
        return
    if "subtitles" not in _ffmpeg_filters():
        raise SystemExit(
            "字幕烧录已开启，但当前 ffmpeg 不支持 subtitles/libass 滤镜，渲染会在最后一步失败。\n"
            "  解决：安装带 libass 的 ffmpeg，或加 --no-burn-subtitles 关闭烧录（仍输出 .srt 外挂字幕）。")
