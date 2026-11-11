"""SRT/ASS serialization for narration and original-dialogue subtitles."""

from lib import CONFIG
from packaging_fonts import split_highlight_spans
from packaging_studio import has_packaging_plan, load_raw_packaging_plan, resolve_output_canvas
from source_subtitles import _combined_subtitle_entries
from subtitles.core import (
    _effective_subtitle_style,
    _normalize_subtitle_text,
    _seconds_to_ass_time,
    _seconds_to_srt_time,
)


def _entries_for_style(narration, work_dir, video_duration, style):
    if style is None:
        return _combined_subtitle_entries(narration, work_dir, video_duration)
    max_chars = int(style["max_chars"])
    bridge = float(style.get("bridge_gap_seconds", 0.0))
    if max_chars == int(CONFIG["subtitle_max_chars"]) and bridge <= 0.0 and not has_packaging_plan(work_dir):
        return _combined_subtitle_entries(narration, work_dir, video_duration)
    return _combined_subtitle_entries(
        narration, work_dir, video_duration,
        max_chars=max_chars, bridge_gap_seconds=bridge,
    )


def _generate_srt(narration, work_dir, video_duration, canvas=None):
    """将解说脚本转为 SRT 字幕文件，使用实际音频放置时间；原声留白处补烧原声字幕。"""
    if canvas is None and has_packaging_plan(work_dir):
        canvas = resolve_output_canvas(work_dir, {"width": 1080, "height": 1920, "fps": 25.0})
    style = _effective_subtitle_style(canvas, work_dir) if canvas is not None else None
    srt_lines = []
    for idx, entry in enumerate(_entries_for_style(narration, work_dir, video_duration, style), start=1):
        srt_lines.append(str(idx))
        srt_lines.append(
            f"{_seconds_to_srt_time(entry['start'])} --> {_seconds_to_srt_time(entry['end'])}"
        )
        srt_lines.append(_normalize_subtitle_text(entry["text"]))
        srt_lines.append("")
    srt_path = work_dir / "subtitles.srt"
    srt_path.write_text("\n".join(srt_lines), encoding="utf-8")
    return srt_path


def _escape_ass_text(text):
    """Escape user text for an ASS dialogue Text field."""
    return (
        text
        .replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "\\N")
    )


def _ass_inline_bgr(ass_color: str) -> str:
    """Convert '&HAABBGGRR' or '&HBBGGRR' to ASS inline '\\c&HBBGGRR&' token."""
    digits = str(ass_color or "&H00FFFFFF").strip().lstrip("&Hh").rstrip("&")
    bgr = digits[-6:].upper().zfill(6)
    return f"&H{bgr}&"


def _format_highlighted_ass_text(raw_text: str, highlights, hl_color: str, base_color: str) -> str:
    """Escape text and wrap up to 2 highlighted phrases in ASS inline color overrides."""
    spans = split_highlight_spans(raw_text, highlights)
    if len(spans) == 1 and not spans[0][1]:
        return _escape_ass_text(raw_text)
    hl_tag = _ass_inline_bgr(hl_color)
    base_tag = _ass_inline_bgr(base_color)
    parts = []
    for chunk, is_hl in spans:
        esc = _escape_ass_text(chunk)
        if is_hl:
            parts.append(f"{{\\c{hl_tag}}}{esc}{{\\c{base_tag}}}")
        else:
            parts.append(esc)
    return "".join(parts)


def _generate_ass(narration, work_dir, video_duration, canvas):
    """Generate an ASS subtitle file for readable hard-sub rendering, including the original
    dialogue during the original-audio gaps. canvas ({"width","height"}) scales the style to the
    real frame so portrait/竖屏 subtitles are not stretched."""
    style = _effective_subtitle_style(canvas, work_dir)
    glow = float(style.get("glow", 0.0))
    fade_ms = int(style.get("fade_ms", 0))
    hl_color = str(style.get("highlight_color") or "&H004CD2FF")
    dlg_outline_color = str(style.get("dialogue_outline_color") or style["outline_color"])
    dlg_outline = style.get("dialogue_outline", style["outline"])
    dlg_shadow = style.get("dialogue_shadow", style["shadow"])

    ass_lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        f"PlayResX: {int(style['play_res_x'])}",
        f"PlayResY: {int(style['play_res_y'])}",
        "",
        "[V4+ Styles]",
        (
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding"
        ),
        (
            "Style: Default,"
            f"{style['font_name']},{style['font_size']},{style['primary_color']},&H000000FF,"
            f"{style['outline_color']},&H64000000,-1,0,0,0,100,100,0,0,1,"
            f"{style['outline']},{style['shadow']},{style['alignment']},"
            f"{style['margin_l']},{style['margin_r']},{style['margin_v']},1"
        ),
        (
            "Style: Dialogue,"
            f"{style['font_name']},{style['font_size']},&H00FFFFFF,&H000000FF,"
            f"{dlg_outline_color},&H64000000,-1,0,0,0,100,100,0,0,1,"
            f"{dlg_outline},{dlg_shadow},{style['alignment']},"
            f"{style['margin_l']},{style['margin_r']},{style['margin_v']},1"
        ),
    ]
    if glow > 0.0:
        glow_outline = round(max(3.6, float(style["outline"]) + 2.2), 2)
        ass_lines.append(
            "Style: Glow,"
            f"{style['font_name']},{style['font_size']},&H96000000,&H000000FF,"
            f"&H88000000,&H00000000,-1,0,0,0,100,100,0,0,1,"
            f"{glow_outline},0,{style['alignment']},"
            f"{style['margin_l']},{style['margin_r']},{style['margin_v']},1"
        )

    ass_lines.extend([
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ])

    raw_plan = load_raw_packaging_plan(work_dir) or {}
    plan_highlights = list(raw_plan.get("subtitle_highlights") or ()) + list(
        (raw_plan.get("subtitle_style") or {}).get("highlights") or ()
    )
    entries = _entries_for_style(narration, work_dir, video_duration, style)
    for entry in entries:
        raw_text = _normalize_subtitle_text(entry["text"])
        is_dialogue = bool(entry.get("is_dialogue")) or raw_text.startswith("「")
        style_name = "Dialogue" if is_dialogue else "Default"
        s_ts = _seconds_to_ass_time(entry["start"])
        e_ts = _seconds_to_ass_time(entry["end"])
        plain_escaped = _escape_ass_text(raw_text)
        merged_hl = list(entry.get("highlights") or ()) + plan_highlights
        rich_text = (
            plain_escaped
            if is_dialogue
            else _format_highlighted_ass_text(
                raw_text, merged_hl, hl_color, style["primary_color"]
            )
        )
        fade_out = max(0, round(fade_ms * 0.8))
        fad_tag = f"\\fad({fade_ms},{fade_out})" if fade_ms > 0 else ""

        if glow > 0.0:
            ass_lines.append(
                f"Dialogue: 0,{s_ts},{e_ts},Glow,,0,0,0,,"
                f"{{\\blur{glow:.1f}{fad_tag}}}{plain_escaped}"
            )
            fg_prefix = f"{{{fad_tag}}}" if fad_tag else ""
            ass_lines.append(
                f"Dialogue: 1,{s_ts},{e_ts},{style_name},,0,0,0,,{fg_prefix}{rich_text}"
            )
        else:
            fg_prefix = f"{{{fad_tag}}}" if fad_tag else ""
            ass_lines.append(
                f"Dialogue: 0,{s_ts},{e_ts},{style_name},,0,0,0,,{fg_prefix}{rich_text}"
            )

    ass_path = work_dir / "subtitles.ass"
    ass_path.write_text("\n".join(ass_lines) + "\n", encoding="utf-8")
    return ass_path
