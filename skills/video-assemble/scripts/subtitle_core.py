"""Subtitle text shaping, timing, and measured-canvas geometry."""

import os
import re
from decimal import Decimal

from lib import CONFIG
from assemble_constants import (
    SUBTITLE_STYLE_REF_H,
    SUBTITLE_STYLE_REF_W,
    _SUBTITLE_CLOSING_QUOTES,
    _SUBTITLE_TERMINAL_PUNCTUATION,
)
from media import _ratio_to_float

def _seconds_to_srt_time(seconds):
    """Floor times to SRT milliseconds without float remainder artifacts.

    Coercing first keeps Fraction/Decimal/str inputs working and clamps a negative
    time to zero instead of emitting a negative-component SRT stamp.
    """
    seconds = max(0.0, float(seconds))
    total_ms = int(Decimal(str(seconds)) * 1000)
    h, remainder = divmod(total_ms, 3_600_000)
    m, remainder = divmod(remainder, 60_000)
    s, ms = divmod(remainder, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _seconds_to_ass_time(seconds):
    """将秒数转为 ASS 时间格式 H:MM:SS.cc"""
    centiseconds = int(round(float(seconds) * 100))
    h = centiseconds // 360000
    centiseconds %= 360000
    m = centiseconds // 6000
    centiseconds %= 6000
    s = centiseconds // 100
    cs = centiseconds % 100
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _subtitle_style_config(canvas=None):
    """Return the internal default burn-in subtitle style.

    When ``canvas`` ({"width","height"}) is given AND the user has not pinned PlayRes via
    SUBTITLE_PLAY_RES_X/Y, the style is scaled to that canvas: PlayRes is set to the frame
    dimensions (so libass never stretches glyphs — the old hardcoded 1280x720 squished
    portrait text), horizontal metrics scale with width, vertical metrics with height, and
    the font is additionally capped so a full ``max_chars`` line fits the usable width. A
    16:9 source (or the 1280x720 default) reproduces the legacy values exactly.
    """
    style = {
        "font_name": CONFIG["subtitle_font_name"],
        "font_size": CONFIG["subtitle_font_size"],
        "primary_color": CONFIG["subtitle_primary_color"],
        "outline_color": CONFIG["subtitle_outline_color"],
        "outline": CONFIG["subtitle_outline"],
        "shadow": CONFIG["subtitle_shadow"],
        "alignment": CONFIG["subtitle_alignment"],
        "margin_l": CONFIG["subtitle_margin_l"],
        "margin_r": CONFIG["subtitle_margin_r"],
        "margin_v": CONFIG["subtitle_margin_v"],
        "max_chars": CONFIG["subtitle_max_chars"],
        "play_res_x": CONFIG["subtitle_play_res_x"],
        "play_res_y": CONFIG["subtitle_play_res_y"],
    }
    pinned = "SUBTITLE_PLAY_RES_X" in os.environ or "SUBTITLE_PLAY_RES_Y" in os.environ
    if canvas is None or pinned:
        return style  # legacy / manually-pinned: unchanged

    cw, ch = canvas["width"], canvas["height"]
    base_font = float(style["font_size"])
    kx = cw / float(SUBTITLE_STYLE_REF_W)  # horizontal metrics ∝ width
    ky = ch / float(SUBTITLE_STYLE_REF_H)  # vertical metrics ∝ height
    margin_l = round(float(style["margin_l"]) * kx)
    margin_r = round(float(style["margin_r"]) * kx)
    margin_v = round(float(style["margin_v"]) * ky)
    # height-proportional size, then cap so a full line of CJK glyphs (≈1em wide) fits the
    # usable width — this is what keeps portrait text on-screen instead of overflowing.
    usable_w = max(1.0, cw - margin_l - margin_r)
    width_cap = usable_w / int(style["max_chars"])
    font_size = max(1, int(min(base_font * ky, width_cap)))  # floor so a full line never overflows
    font_scale = font_size / base_font
    style.update({
        "font_size": font_size,
        "outline": max(0, round(float(style["outline"]) * font_scale)),
        "shadow": max(0, round(float(style["shadow"]) * font_scale)),
        "margin_l": margin_l,
        "margin_r": margin_r,
        "margin_v": margin_v,
        "play_res_x": cw,
        "play_res_y": ch,
    })
    return style


def _measured_subtitle_band(canvas):
    """Explicit (y_top, y_bot) display-frame subtitle band validated against the canvas, or None."""
    y_top = CONFIG["subtitle_y_top"]
    y_bot = CONFIG["subtitle_y_bot"]
    if y_top < 0 and y_bot < 0:
        return None
    sar_text = canvas["sample_aspect_ratio"]
    if abs(_ratio_to_float(sar_text, 0.0) - 1.0) >= 1e-9:
        raise ValueError(
            f"字幕带坐标仅支持方形像素画布 (SAR 1:1)；当前 SAR={sar_text}"
        )
    canvas_h = canvas["height"]
    if not 0 <= y_top < y_bot <= canvas_h:
        raise ValueError(
            f"字幕带坐标无效: top={y_top}, bot={y_bot}, 画布高度={canvas_h}；"
            "必须满足 0 <= top < bot <= height"
        )
    return y_top, y_bot


def _style_for_measured_subtitle_band(style, canvas):
    """Fit the ASS baseline and font into explicit auto-rotated display-frame Y coordinates."""
    style = dict(style)
    safe_area = _measured_subtitle_safe_area(style, canvas)
    if safe_area is None:
        return style
    alignment = style["alignment"]
    if alignment not in {1, 2, 3}:
        raise ValueError(
            "measured subtitle coordinates require a bottom-aligned ASS style "
            f"(SUBTITLE_ALIGNMENT 1/2/3); got {alignment}"
        )
    canvas_h = canvas["height"]
    scale_y = float(style["play_res_y"]) / canvas_h
    style["margin_v"] = max(0, round((canvas_h - CONFIG["subtitle_y_bot"]) * scale_y))
    current_font = int(style["font_size"])
    current_outline = float(style["outline"])
    current_shadow = float(style["shadow"])
    available_height = safe_area["height"]
    for candidate in range(current_font, 7, -1):
        scale = candidate / current_font
        outline = max(1 if current_outline > 0 else 0, round(current_outline * scale))
        shadow = max(0, round(current_shadow * scale))
        if candidate * 1.25 + outline * 2 + shadow <= available_height + 1e-6:
            fitted_font = candidate
            break
    else:
        # Keep the renderer's minimum readable size; visual QC will block because it cannot fit.
        fitted_font = min(current_font, 8)
    if fitted_font < current_font:
        scale = fitted_font / current_font
        style["font_size"] = fitted_font
        style["outline"] = max(
            1 if current_outline > 0 else 0, round(current_outline * scale)
        )
        style["shadow"] = max(0, round(current_shadow * scale))
    return style


def _measured_subtitle_safe_area(style, canvas):
    """Return the padded measured band in ASS PlayRes coordinates, or None."""
    band = _measured_subtitle_band(canvas)
    if band is None:
        return None
    y_top, y_bot = band
    canvas_h = canvas["height"]
    safe_top = max(0, y_top - CONFIG["subtitle_mask_padding"])
    # The ASS style remains bottom-anchored at the measured y_bot. Bottom mask padding hides
    # source glyph edges but is not usable subtitle layout space; only top padding can extend
    # the line box without moving its baseline below the measured band.
    play_x = int(style["play_res_x"])
    scale_y = int(style["play_res_y"]) / canvas_h
    margin_l = int(style["margin_l"])
    margin_r = int(style["margin_r"])
    return {
        "x": margin_l,
        "y": round(safe_top * scale_y),
        "width": max(1, play_x - margin_l - margin_r),
        "height": max(1, round((y_bot - safe_top) * scale_y)),
        "bottom_margin": max(0, round((canvas_h - y_bot) * scale_y)),
    }


def _subtitle_display_text(text):
    """Return display-only subtitle text with trailing sentence punctuation removed.

    Narration/TTS source text stays untouched; this is applied only to SRT/ASS cue text.
    Closing quotes/brackets are preserved, so 「原声台词。」 renders as 「原声台词」.
    """
    text = text.strip()
    suffix = ""
    while text and text[-1] in _SUBTITLE_CLOSING_QUOTES:
        suffix = text[-1] + suffix
        text = text[:-1].rstrip()
    text = text.rstrip(_SUBTITLE_TERMINAL_PUNCTUATION).rstrip()
    return (text + suffix).strip()


def _subtitle_chunk_weight(text):
    """Weight raw subtitle chunks for timing, independent of display punctuation cleanup."""
    return max(1, len(re.sub(r"\s+", "", text)))


def _subtitle_entry_chunks(raw_chunks):
    """Pair raw chunks used for timing with their final display text.

    Timing remains based on the raw split topology. Terminal punctuation is stripped only
    on the emitted text, while quote-only suffix chunks are folded into the previous cue
    so a closing bracket never renders alone.
    """
    out = []
    for chunk in raw_chunks:
        display = _subtitle_display_text(chunk)
        if not display:
            continue
        if all(ch in _SUBTITLE_CLOSING_QUOTES for ch in display):
            if out:
                out[-1]["text"] += display
            continue
        out.append({"raw": chunk, "text": display})
    return out


def _normalize_subtitle_text(text):
    """Normalize Chinese em-dashes in burned subtitle text: a run of one-or-more "—" (incl. "——")
    collapses to a single "，". Then collapse any resulting double commas ("，，"→"，") so the dash
    swap never leaves a doubled comma."""
    return re.sub(r"，{2,}", "，", re.sub(r"—+", "，", text))


def _split_subtitle_chunks(text, max_chars):
    """Split one narration block (often several sentences) into short display chunks.

    A block is synthesized as one continuous TTS utterance for fluent prosody, but showing the
    whole paragraph as a single subtitle would force a tall multi-line band and lag the picture.
    So we cut the block at punctuation into clauses, then greedily pack adjacent clauses into
    chunks of at most `max_chars` — each chunk renders as ONE readable line synced to its slice of
    the block's audio. Punctuation stays attached here for lossless splitting; the display layer
    strips terminal sentence marks per subtitle-cue style."""
    text = text.strip()
    if not text:
        return []
    breakers = "，。！？、；：…—,.!?;:"
    clauses, buf = [], ""
    for ch in text:
        buf += ch
        if ch in breakers:
            clauses.append(buf)
            buf = ""
    if buf.strip():
        clauses.append(buf)
    # Any single clause longer than max_chars is hard-wrapped so no chunk ever exceeds one line.
    # Balance those pieces instead of slicing exactly at max_chars: a 21-character clause must
    # not become a readable 20-character cue followed by a 1-character flash.
    sized = []
    for clause in clauses:
        if len(clause) <= max_chars:
            sized.append(clause)
        else:
            piece_count = (len(clause) + max_chars - 1) // max_chars
            base, extra = divmod(len(clause), piece_count)
            cursor = 0
            for piece_index in range(piece_count):
                width = base + (1 if piece_index < extra else 0)
                sized.append(clause[cursor:cursor + width])
                cursor += width
    chunks, cur = [], ""
    for clause in sized:
        sentence_closed = cur.rstrip().endswith(tuple(_SUBTITLE_TERMINAL_PUNCTUATION))
        if cur and (sentence_closed or len(cur) + len(clause) > max_chars):
            chunks.append(cur)
            cur = clause
        else:
            cur += clause
    if cur.strip():
        chunks.append(cur)
    return [c.strip() for c in chunks if c.strip()]


def _subtitle_entries(narration):
    """Collect subtitle entries from final TTS segment placement.

    Each placed segment is split into short one-line chunks and its played window
    [actual_place_start, actual_place_end] is distributed across them in proportion to character
    count — karaoke-style timing that keeps each line on screen only while it is roughly being
    spoken, instead of holding a whole paragraph for the segment's full duration. Segments that
    were not placed have a zero-width window and therefore produce no cue."""
    max_chars = CONFIG["subtitle_max_chars"]
    entries = []
    for seg in narration:
        text = seg["spoken_text"]
        start, end = float(seg["actual_place_start"]), float(seg["actual_place_end"])
        entries.extend(_distribute_chunks(_split_subtitle_chunks(text, max_chars), start, end))
    return entries


def _distribute_chunks(chunks, start, end):
    """Distribute [start,end] across raw chunks while emitting display-clean text.

    Terminal subtitle punctuation is visual-only: it is stripped from final cue text,
    but the raw split chunks remain the timing topology. A slice too short to show on its
    own is folded into the previous line of the same block, so no chunk is ever dropped.
    """
    chunks = _subtitle_entry_chunks(chunks)
    if not chunks or end - start < 0.1:
        return []
    if len(chunks) == 1:
        return [{"start": start, "end": end, "text": chunks[0]["text"]}]
    total_chars = sum(_subtitle_chunk_weight(c["raw"]) for c in chunks)
    span = end - start
    out, cursor = [], start
    for i, chunk in enumerate(chunks):
        weight = _subtitle_chunk_weight(chunk["raw"])
        chunk_end = end if i == len(chunks) - 1 else cursor + span * (weight / total_chars)
        if out and chunk_end - cursor < 0.05:
            out[-1]["text"] += chunk["text"]
            out[-1]["end"] = chunk_end
        else:
            out.append({"start": cursor, "end": chunk_end, "text": chunk["text"]})
        cursor = chunk_end
    return out


def _bracketed_original_chunks(text, start, end, max_chars):
    """Split original dialogue into timed chunks wrapped in 「」 for visual distinction."""
    raw = text.strip()
    if raw.startswith("「") and raw.endswith("」"):
        raw = raw[1:-1].strip()
    chunks = _split_subtitle_chunks(raw, max_chars)
    if chunks:
        chunks[0] = "「" + chunks[0]
        chunks[-1] = chunks[-1] + "」"
    return _distribute_chunks(chunks, start, end)
