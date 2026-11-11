"""Subtitle layout QC, source-subtitle masking, and video filter helpers."""

import json
import re
from pathlib import Path

from assemble_constants import (
    SUBTITLE_STYLE_REF_H,
    VISUAL_QC,
)
from audio_automation import coalesce_duck_windows
from audio_mix import _seg_place_window
from lib import CONFIG
from source_subtitles import (
    _combined_subtitle_entries,
    _source_subtitle_mask,
)
from subtitles.core import (
    _effective_subtitle_style,
    _frame_row,
    _measured_subtitle_band,
    _measured_subtitle_safe_area,
    _normalize_subtitle_text,
    _style_for_measured_subtitle_band,  # noqa: F401 — re-exported for assemble tests
    _subtitle_style_config,
)


def _visual_text_units(text):
    """Approximate visual text width in em units for deterministic geometry QC."""
    units = 0.0
    for ch in text:
        if ch.isspace():
            units += 0.35
        elif ord(ch) < 128:
            units += 0.56
        else:
            units += 1.0
    return units


def _subtitle_layout_qc(entries, style, safe_area=None):
    """Machine-check subtitle safe-area/multiline/overflow facts for visual_qc.json."""
    play_x = int(style["play_res_x"])
    play_y = int(style["play_res_y"])
    margin_l = int(style["margin_l"])
    margin_r = int(style["margin_r"])
    margin_v = int(style["margin_v"])
    font_size = float(style["font_size"])
    max_lines = CONFIG["subtitle_max_lines"]
    if safe_area is None:
        safe_area = {
            "x": margin_l,
            "y": margin_v,
            "width": max(1, play_x - margin_l - margin_r),
            "height": max(1, play_y - 2 * margin_v),
            "bottom_margin": margin_v,
        }
    usable_w = float(safe_area["width"])
    line_h = font_size * 1.25
    overflow_entries = []
    entry_facts = []
    for i, entry in enumerate(entries):
        raw_text = _normalize_subtitle_text(entry["text"])
        lines = [ln for ln in re.split(r"(?:\\N|\n)+", raw_text) if ln != ""] or [""]
        line_count = len(lines)
        max_w = max(_visual_text_units(line) * font_size for line in lines)
        band_h = line_count * line_h + float(style["outline"]) * 2 + float(style["shadow"])
        overflow_reasons = []
        if line_count > max_lines:
            overflow_reasons.append("max_lines_exceeded")
        if max_w > usable_w + 1e-6:
            overflow_reasons.append("safe_width_exceeded")
        if band_h > safe_area["height"] + 1e-6:
            overflow_reasons.append("safe_height_exceeded")
        fact = {
            "index": i,
            "start": round(float(entry["start"]), 3),
            "end": round(float(entry["end"]), 3),
            "line_count": line_count,
            "max_line_width": round(max_w, 2),
            "safe_width": round(usable_w, 2),
            "band_height": round(band_h, 2),
            "overflow": bool(overflow_reasons),
            "overflow_reasons": overflow_reasons,
        }
        entry_facts.append(fact)
        if overflow_reasons:
            overflow_entries.append(fact)
    return {
        "enabled": CONFIG["burn_subtitles"],
        "renderer": "ass" if CONFIG["burn_subtitles"] else "sidecar_srt",
        "burn_degraded_reason": CONFIG["burn_subtitles_degraded"],
        "style": {
            "font_size": int(font_size),
            "max_chars": int(style["max_chars"]),
            "max_lines": max_lines,
            "play_res_x": play_x,
            "play_res_y": play_y,
            "alignment": int(style["alignment"]),
            "margin_l": margin_l,
            "margin_r": margin_r,
            "margin_v": margin_v,
        },
        "safe_area": safe_area,
        "entries": len(entry_facts),
        "overflow": bool(overflow_entries),
        "overflow_entries": overflow_entries,
        "entry_facts": entry_facts,
    }


def _subtitle_delivery_warnings(mask, *, has_subtitles):
    """Non-blocking, machine-readable record of a default burn that degraded to the sidecar."""
    reason = CONFIG["burn_subtitles_degraded"]
    if not reason or not has_subtitles:
        return []
    return [{
        "code": "subtitle_burn_degraded",
        "reason": reason,
        "delivered": "sidecar_srt",
        "mask_dropped": mask["trigger"] == "burn_subtitles_degraded",
        "message": (
            "ffmpeg lacks the libass subtitles filter: subtitles are not burned into the "
            "video and ship as a .srt sidecar next to it"
        ),
        "next_action": "install an ffmpeg build with libass and rerun to burn, or deliver the .srt sidecar",
    }]


def _build_visual_qc(
    tts_segments, work_dir, video_duration, canvas, *, mask_filter=None, packaging_qc=None
):
    style = _effective_subtitle_style(canvas, work_dir)
    if (
        int(style["max_chars"]) == int(CONFIG["subtitle_max_chars"])
        and float(style.get("bridge_gap_seconds", 0.0)) <= 0.0
        and packaging_qc is None
    ):
        entries = _combined_subtitle_entries(tts_segments, work_dir, video_duration)
    else:
        entries = _combined_subtitle_entries(
            tts_segments,
            work_dir,
            video_duration,
            max_chars=int(style["max_chars"]),
            bridge_gap_seconds=float(style.get("bridge_gap_seconds", 0.0)),
        )
    subtitle_layout = _subtitle_layout_qc(
        entries, style, safe_area=_measured_subtitle_safe_area(style, canvas)
    )
    mask = _source_subtitle_mask()
    mask.update({
        "ratio": min(0.5, CONFIG["source_subtitle_mask_ratio"]) if mask["active"] else None,
        "filter": "drawbox" if mask_filter else None,
        "opacity": CONFIG["subtitle_mask_opacity"],
        "timing": CONFIG["source_subtitle_mask_timing"],
        "subtitle_y_top": CONFIG["subtitle_y_top"],
        "subtitle_y_bot": CONFIG["subtitle_y_bot"],
    })
    warnings = _subtitle_delivery_warnings(mask, has_subtitles=bool(entries))
    blocking_codes = []
    if subtitle_layout["overflow"]:
        blocking_codes.append("subtitle_overflow")
    if packaging_qc and packaging_qc.get("blocking_codes"):
        blocking_codes.extend(packaging_qc["blocking_codes"])
    geometry = {
        "canvas": {
            "width": canvas["width"],
            "height": canvas["height"],
            "fps": canvas["fps"],
        },
        "storage": {
            "width": canvas["storage_width"],
            "height": canvas["storage_height"],
        },
        "rotation": canvas["rotation"],
        "sample_aspect_ratio": canvas["sample_aspect_ratio"],
        "display_aspect_ratio": canvas["display_aspect_ratio"],
    }
    if packaging_qc is not None:
        geometry["packaging"] = {
            k: v for k, v in packaging_qc.items() if k != "blocking_codes"
        }
    return {
        "schema_version": 1,
        "artifact": VISUAL_QC,
        "verdict": "FAIL" if blocking_codes else "PASS",
        "blocking": bool(blocking_codes),
        "blocking_codes": blocking_codes,
        "warnings": warnings,
        "geometry": geometry,
        "subtitles": subtitle_layout,
        "mask": mask,
    }


def _write_visual_qc(work_dir, qc):
    path = Path(work_dir) / VISUAL_QC
    path.write_text(json.dumps(qc, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _escape_subtitle_filter_path(path):
    """Escape a path for ffmpeg subtitle/ass video filter arguments."""
    text = str(path).replace("\\", "/")
    for raw, escaped in (
        ("\\", "\\\\"),
        (":", "\\:"),
        ("'", "\\'"),
        (",", "\\,"),
        ("[", "\\["),
        ("]", "\\]"),
    ):
        text = text.replace(raw, escaped)
    return text


def _subtitle_burn_filter(subtitle_path, font_file=None):
    """Build the ffmpeg video filter used for hard-sub rendering."""
    filt = f"subtitles=filename='{_escape_subtitle_filter_path(subtitle_path)}'"
    effective_font_file = font_file if font_file is not None else CONFIG["subtitle_font_file"]
    if effective_font_file:
        fonts_dir = Path(effective_font_file).expanduser().resolve().parent
        filt += f":fontsdir='{_escape_subtitle_filter_path(fonts_dir)}'"
    return filt


def _output_downscale_filter(max_h):
    """Lanczos downscale that forces BOTH output dimensions even (libx264/yuv420p need it)."""
    return f"scale=-2:'2*trunc(min(ih,{max_h})/2)':flags=lanczos"


def _source_subtitle_mask_filter(canvas, work_dir, tts_segments, video_duration):
    """Return source-subtitle drawbox filters, optionally scoped to narration windows."""
    if not _source_subtitle_mask()["active"]:
        return None
    opacity = CONFIG["subtitle_mask_opacity"]
    timing = CONFIG["source_subtitle_mask_timing"]
    if timing not in {"all", "narration"}:
        raise ValueError(f"SOURCE_SUBTITLE_MASK_TIMING 必须是 all 或 narration，当前为 {timing!r}")

    band = _measured_subtitle_band(canvas)
    if band is not None:
        y_top, y_bot = band
        padding = CONFIG["subtitle_mask_padding"]
        mask_top = _frame_row(canvas, max(0, y_top - padding))
        mask_bot = _frame_row(canvas, min(canvas["height"], y_bot + padding))
        geometry = f"x=0:y={mask_top}:w=iw:h={mask_bot - mask_top}"
    else:
        style = _subtitle_style_config(canvas)
        play_res_y = float(style["play_res_y"])
        line_h = float(style["font_size"]) * 1.25
        pad = 10.0 * play_res_y / SUBTITLE_STYLE_REF_H
        sub_band = (float(style["margin_v"]) + line_h + pad) / play_res_y
        ratio = min(0.5, max(CONFIG["source_subtitle_mask_ratio"], sub_band))
        geometry = f"x=0:y=ih-ih*{ratio:.3f}:w=iw:h=ih*{ratio:.3f}"

    base = f"drawbox={geometry}:color=black@{opacity:.2f}:t=fill"
    filters = []
    if opacity > 0:
        if timing == "all":
            filters.append(base)
        else:
            windows = [
                (start, end, 0.0)
                for start, end in map(_seg_place_window, tts_segments)
                if end > start
            ]
            filters.extend(
                f"{base}:enable='between(t,{start:.3f},{end:.3f})'"
                for start, end, _ in coalesce_duck_windows(windows, bridge=0.001)
            )
    return ",".join(filters) if filters else None
