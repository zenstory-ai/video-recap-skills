"""Visual overlays, subtitle layout QC, masking, and video filter helpers."""

import json
import re
from pathlib import Path

from assemble_constants import (
    SUBTITLE_STYLE_REF_H,
    VISUAL_OVERLAYS,
    VISUAL_QC,
    _SUPPORTED_VISUAL_OVERLAY_TYPES,
)
from audio_automation import coalesce_duck_windows
from audio_mix import _seg_place_window
from lib import CONFIG
from source_subtitles import (
    _combined_subtitle_entries,
    _original_gap_subtitle_entries,
    _source_subtitle_mask_policy,
)
from subtitles.core import (
    _measured_subtitle_band,
    _measured_subtitle_safe_area,
    _normalize_subtitle_text,
    _style_for_measured_subtitle_band,
    _subtitle_style_config,
)

_OVERFLOW_KINDS = {
    "max_lines_exceeded": "line_count",
    "safe_width_exceeded": "line_width",
    "safe_height_exceeded": "safe_area",
}


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
    violations = []
    multi_line_entries = []
    max_observed_lines = 0
    entry_facts = []
    for i, entry in enumerate(entries):
        raw_text = _normalize_subtitle_text(entry["text"])
        lines = [ln for ln in re.split(r"(?:\\N|\n)+", raw_text) if ln != ""] or [""]
        line_count = len(lines)
        max_observed_lines = max(max_observed_lines, line_count)
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
        if line_count > 1:
            multi_line_entries.append(i)
        if overflow_reasons:
            overflow_entries.append(fact)
            violations.extend(
                {"index": i, "kind": _OVERFLOW_KINDS[reason], "reason": reason}
                for reason in overflow_reasons
            )
    return {
        "enabled": CONFIG["burn_subtitles"],
        "renderer": "ass" if CONFIG["burn_subtitles"] else "sidecar_srt",
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
        "max_lines": max_observed_lines,
        "max_observed_lines": max_observed_lines,
        "multi_line": bool(multi_line_entries),
        "multi_line_entries": multi_line_entries,
        "overflow": bool(overflow_entries),
        "overflow_entries": overflow_entries,
        "violations": violations,
        "entry_facts": entry_facts,
    }


def _load_visual_overlays(work_dir):
    """Return (overlays, source) from the canonical visual_overlays.json handoff."""
    path = Path(work_dir) / VISUAL_OVERLAYS
    if not path.exists():
        return [], {"present": False, "path": str(path)}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: JSON 无效") from exc
    if (
        not isinstance(data, dict)
        or type(data.get("schema_version")) is not int
        or data["schema_version"] != 1
        or not isinstance(data.get("overlays"), list)
        or not all(isinstance(item, dict) for item in data["overlays"])
    ):
        raise ValueError(f"{path}: visual_overlays.json schema 无效")
    source = {
        "present": True,
        "path": str(path),
        "schema_version": 1,
    }
    return data["overlays"], source


def _escape_drawtext_text(text):
    return (
        text
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("%", "\\%")
        .replace("\n", "\\n")
    )


def _overlay_time_window(overlay, video_duration):
    start = float(overlay.get("start", 0.0))
    end = float(overlay.get("end", video_duration))
    return start, max(start, end)


def _overlay_bbox(overlay, canvas, *, default_y):
    width = canvas["width"]
    height = canvas["height"]
    text = overlay["text"]
    font_size = int(overlay.get("font_size", max(18, round(height * 0.045))))
    lines = [ln for ln in text.splitlines() if ln.strip()] or [text]
    max_w = max(_visual_text_units(ln) * font_size for ln in lines)
    text_h = len(lines) * font_size * 1.25
    if overlay["type"] == "top_title":
        x = max(0.0, (width - max_w) / 2)
        y = float(overlay.get("y", default_y))
    else:
        # Fractions of the canvas in [0, 1] are normalized coordinates; larger values are pixels.
        x = float(overlay.get("x", 0.08))
        y = float(overlay.get("y", 0.25))
        if 0.0 <= x <= 1.0:
            x *= width
        if 0.0 <= y <= 1.0:
            y *= height
    return {
        "x": round(x, 2),
        "y": round(y, 2),
        "width": round(max_w, 2),
        "height": round(text_h, 2),
        "font_size": font_size,
        "line_count": len(lines),
        "overflow": x < 0 or y < 0 or x + max_w > width or y + text_h > height,
    }


def _visual_overlay_filters(work_dir, canvas, video_duration):
    """Render the first-release canonical visual_overlays.json contract.

    Only two semantic renderers are supported: top_title and inline_label_or_callout.
    Unsupported types are QC-blocking and deliberately do not silently render.
    """
    overlays, source = _load_visual_overlays(work_dir)
    default_top_y = max(24, round(canvas["height"] * 0.05))
    filters = []
    facts = []
    unsupported = []
    overflow = []
    for idx, overlay in enumerate(overlays):
        typ = overlay.get("type")
        text = overlay.get("text", "").strip()
        if typ not in _SUPPORTED_VISUAL_OVERLAY_TYPES:
            unsupported.append({"index": idx, "type": typ, "reason": "unsupported_overlay_type"})
            continue
        if not text:
            unsupported.append({"index": idx, "type": typ, "reason": "missing_text"})
            continue
        start, end = _overlay_time_window(overlay, video_duration)
        bbox = _overlay_bbox(overlay, canvas, default_y=default_top_y)
        if bbox["overflow"]:
            overflow.append({"index": idx, "type": typ, "bbox": bbox})
        font_size = bbox["font_size"]
        safe_text = _escape_drawtext_text(text)
        enable = f"between(t\\,{start:.3f}\\,{end:.3f})"
        if typ == "top_title":
            filt = (
                "drawtext="
                f"text='{safe_text}':x=(w-text_w)/2:y={int(bbox['y'])}:"
                f"fontsize={font_size}:fontcolor=white:borderw=2:bordercolor=black@0.85:"
                f"box=1:boxcolor=black@0.35:boxborderw=12:enable='{enable}'"
            )
        else:
            filt = (
                "drawtext="
                f"text='{safe_text}':x={int(bbox['x'])}:y={int(bbox['y'])}:"
                f"fontsize={font_size}:fontcolor=white:borderw=2:bordercolor=black@0.85:"
                f"box=1:boxcolor=black@0.45:boxborderw=8:enable='{enable}'"
            )
        filters.append(filt)
        facts.append({
            "index": idx,
            "type": typ,
            "text_chars": len(text),
            "start": round(start, 3),
            "end": round(end, 3),
            "bbox": bbox,
        })
    qc = {
        "source": source,
        "supported_types": sorted(_SUPPORTED_VISUAL_OVERLAY_TYPES),
        "present": source["present"],
        "count": len(overlays),
        "rendered": len(facts),
        "facts": facts,
        "unsupported": unsupported,
        "overflow": overflow,
    }
    return filters, qc


def _build_visual_qc(tts_segments, work_dir, video_duration, canvas, *, overlay_qc=None, mask_filter=None):
    entries = _combined_subtitle_entries(tts_segments, work_dir, video_duration)
    style = _style_for_measured_subtitle_band(_subtitle_style_config(canvas), canvas)
    subtitle_layout = _subtitle_layout_qc(
        entries, style, safe_area=_measured_subtitle_safe_area(style, canvas)
    )
    mask = _source_subtitle_mask_policy(work_dir)
    mask.update({
        "ratio": min(0.5, CONFIG["source_subtitle_mask_ratio"]) if mask["active"] else None,
        "filter": "drawbox" if mask_filter else None,
        "opacity": CONFIG["subtitle_mask_opacity"],
        "timing": CONFIG["source_subtitle_mask_timing"],
        "subtitle_y_top": CONFIG["subtitle_y_top"],
        "subtitle_y_bot": CONFIG["subtitle_y_bot"],
    })
    if overlay_qc is None:
        overlay_qc = _visual_overlay_filters(work_dir, canvas, video_duration)[1]
    blocking_codes = []
    if mask["blocking"]:
        blocking_codes.append("mask_policy_not_explicit")
    if subtitle_layout["overflow"]:
        blocking_codes.append("subtitle_overflow")
    if overlay_qc["unsupported"]:
        blocking_codes.append("unsupported_visual_overlay")
    if overlay_qc["overflow"]:
        blocking_codes.append("visual_overlay_overflow")
    return {
        "schema_version": 1,
        "artifact": VISUAL_QC,
        "verdict": "FAIL" if blocking_codes else "PASS",
        "blocking": bool(blocking_codes),
        "blocking_codes": blocking_codes,
        "geometry": {
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
        },
        "subtitles": subtitle_layout,
        "mask": mask,
        "overlays": overlay_qc,
        "summary": {
            "subtitle_entries": subtitle_layout["entries"],
            "subtitle_overflow": subtitle_layout["overflow"],
            "subtitle_multi_line": subtitle_layout["multi_line"],
            "mask_policy": mask["policy"],
            "mask_active": mask["active"],
            "overlay_rendered": overlay_qc["rendered"],
            "overlay_unsupported": len(overlay_qc["unsupported"]),
        },
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


def _subtitle_burn_filter(subtitle_path):
    """Build the ffmpeg video filter used for hard-sub rendering."""
    return f"subtitles=filename='{_escape_subtitle_filter_path(subtitle_path)}'"


def _output_downscale_filter(max_h):
    """Lanczos downscale that forces BOTH output dimensions even (libx264/yuv420p need it).

    -2 keeps the aspect ratio with an even width; 2*trunc(min(ih,H)/2) caps the height at H
    yet forces it even, so an odd OUTPUT_MAX_HEIGHT (e.g. 721) cannot produce an odd height
    that makes libx264 abort with an empty output file. 'min(ih,H)' only ever shrinks.
    """
    return f"scale=-2:'2*trunc(min(ih,{max_h})/2)':flags=lanczos"


def _source_subtitle_mask_filter(canvas, work_dir, tts_segments, video_duration):
    """Return source-subtitle drawbox filters, optionally scoped to narration windows.

    Many source videos (e.g. 庆余年) ship hardcoded subtitles; without this the recap
    shows the original subs AND our narration subs stacked. Once masking is explicitly enabled,
    the enhanced default is a measured, translucent narration-only band; opacity and timing
    remain configurable.
    """
    policy = _source_subtitle_mask_policy(work_dir)
    if not policy["active"]:
        return None
    opacity = CONFIG["subtitle_mask_opacity"]
    timing = CONFIG["source_subtitle_mask_timing"]
    if timing not in {"all", "narration"}:
        raise ValueError(f"SOURCE_SUBTITLE_MASK_TIMING 必须是 all 或 narration，当前为 {timing!r}")

    band = _measured_subtitle_band(canvas)
    if band is not None:
        y_top, y_bot = band
        padding = CONFIG["subtitle_mask_padding"]
        mask_top = max(0, y_top - padding)
        mask_bot = min(canvas["height"], y_bot + padding)
        geometry = f"x=0:y={mask_top}:w=iw:h={mask_bot - mask_top}"
    else:
        # Our subtitle cues are one line. Keep the mask large enough for that line and its
        # margin, but never regress to the old two-line bar that hid ~23% of the image.
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
            # Avoid overlapping drawboxes: stacking two 60%-black masks would darken the
            # overlap to 84%. Coalescing also keeps long filter chains smaller.
            filters.extend(
                f"{base}:enable='between(t,{start:.3f},{end:.3f})'"
                for start, end, _ in coalesce_duck_windows(windows, bridge=0.001)
            )

    # A translucent mask deliberately leaves the source glyphs visible. Whenever we burn a
    # replacement original-dialogue subtitle into a gap, cover that exact window opaquely first;
    # otherwise the source hard-sub and replacement text are stacked on top of each other.
    if not (timing == "all" and opacity >= 1.0 - 1e-9):
        replacement_windows = [
            (entry["start"], entry["end"], 0.0)
            for entry in _original_gap_subtitle_entries(tts_segments, work_dir, video_duration)
        ]
        opaque = f"drawbox={geometry}:color=black@1.00:t=fill"
        filters.extend(
            f"{opaque}:enable='between(t,{start:.3f},{end:.3f})'"
            for start, end, _ in coalesce_duck_windows(replacement_windows, bridge=0.001)
        )
    return ",".join(filters) if filters else None
