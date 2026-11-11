"""Local ffmpeg capability preflight for subtitle burn-in."""

import shutil

from lib import CONFIG, ffmpeg_filters, log

LIBASS_MISSING = "ffmpeg_missing_libass"


def _ffmpeg_filters():
    """Return ffmpeg's compiled-in filter names, or empty set on probe failure."""
    try:
        return ffmpeg_filters()
    except RuntimeError:
        return set()


def _preflight_burn_subtitles():
    """Settle subtitle burn-in before the render when ffmpeg lacks the libass `subtitles`
    filter. An explicit request (--burn-subtitles or BURN_SUBTITLES in the environment)
    fails here. The implicit default degrades: burn turns off, the reason is recorded in
    CONFIG["burn_subtitles_degraded"], and the run delivers the .srt sidecar instead."""
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
            "或加 --no-burn-subtitles。"
        )
    CONFIG["burn_subtitles"] = False
    CONFIG["burn_subtitles_degraded"] = LIBASS_MISSING
    log(
        "⚠️ 当前 ffmpeg 不支持 subtitles/libass 滤镜：本次不烧录字幕，改为输出 .srt 外挂字幕"
        "（visual_qc.json / assembly_manifest.json 记录 subtitle_burn_degraded）。"
        "要烧录字幕请安装带 libass 的 ffmpeg。"
    )
