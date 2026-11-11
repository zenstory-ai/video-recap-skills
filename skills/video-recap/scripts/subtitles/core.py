"""Subtitle text shaping, timing, and measured-canvas geometry."""

import os
import re
from decimal import Decimal

from lib import CONFIG
from assemble_constants import (
    SUBTITLE_STYLE_REF_H,
    SUBTITLE_STYLE_REF_W,
    _SUBTITLE_CLOSING_QUOTES,
    _SUBTITLE_DISPLAY_STRIP_PUNCTUATION,
    _SUBTITLE_TERMINAL_PUNCTUATION,
)
from media import _ratio_to_float

# Measured subtitle bands accept near-square pixels: Y rows are converted between the display
# canvas and the decoded frame exactly (_frame_row), so the only SAR effect left is a burned
# ASS line stretched horizontally by the same factor — invisible within 2%. Kept equal to the
# orchestrator's preflight copy and the measuring tool's (parity-tested).
SUBTITLE_BAND_SAR_TOLERANCE = 0.02


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


_HEX_COLOR_RE = re.compile(r"^#([0-9a-fA-F]{6})([0-9a-fA-F]{2})?$")
_ASS_COLOR_RE = re.compile(r"^&H([0-9a-fA-F]{6}|[0-9a-fA-F]{8})&?$", re.IGNORECASE)


def _normalize_ass_color(value):
    raw = str(value or "").strip()
    m_hex = _HEX_COLOR_RE.match(raw)
    if m_hex:
        rgb = m_hex.group(1).upper()
        rr, gg, bb = rgb[0:2], rgb[2:4], rgb[4:6]
        alpha_hex = m_hex.group(2)
        ass_alpha = f"{255 - int(alpha_hex, 16):02X}" if alpha_hex else "00"
        return f"&H{ass_alpha}{bb}{gg}{rr}"
    m_ass = _ASS_COLOR_RE.match(raw)
    if m_ass:
        digits = m_ass.group(1).upper()
        return f"&H00{digits}" if len(digits) == 6 else f"&H{digits}"
    return raw


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
        "font_file": CONFIG["subtitle_font_file"],
        "font_size": CONFIG["subtitle_font_size"],
        "primary_color": _normalize_ass_color(CONFIG["subtitle_primary_color"]),
        "outline_color": _normalize_ass_color(CONFIG["subtitle_outline_color"]),
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
        "outline": max(0.0, round(float(style["outline"]) * font_scale, 2)),
        "shadow": max(0.0, round(float(style["shadow"]) * font_scale, 2)),
        "margin_l": margin_l,
        "margin_r": margin_r,
        "margin_v": margin_v,
        "play_res_x": cw,
        "play_res_y": ch,
    })
    return style


def _effective_subtitle_style(canvas=None, work_dir=None):
    """Return subtitle style after measured-band fitting and optional studio packaging overrides."""
    base = _style_for_measured_subtitle_band(_subtitle_style_config(canvas), canvas)
    style = {
        **base,
        "glow": float(CONFIG.get("subtitle_glow", 0.0)),
        "fade_ms": int(CONFIG.get("subtitle_fade_ms", 0)),
        "highlight_color": _normalize_ass_color(CONFIG.get("subtitle_highlight_color", "&H004CD2FF")),
        "bridge_gap_seconds": float(CONFIG.get("subtitle_bridge_gap_seconds", 0.0)),
    }
    if work_dir is not None and canvas is not None:
        from packaging_studio import subtitle_overrides_for_plan

        overrides = subtitle_overrides_for_plan(work_dir, canvas)
        if overrides:
            style.update(overrides)
            for color_key in ("primary_color", "outline_color", "highlight_color", "dialogue_outline_color"):
                if color_key in style and style[color_key]:
                    style[color_key] = _normalize_ass_color(style[color_key])
    return style


def _measured_subtitle_band(canvas):
    """Explicit (y_top, y_bot) display-frame subtitle band validated against the canvas, or None."""
    y_top = CONFIG["subtitle_y_top"]
    y_bot = CONFIG["subtitle_y_bot"]
    if y_top < 0 and y_bot < 0:
        return None
    sar_text = canvas["sample_aspect_ratio"]
    # Unspecified SAR ("0:1"/"N/A") is square, exactly as the canvas probe reads it.
    if abs(_ratio_to_float(sar_text, 1.0) - 1.0) > SUBTITLE_BAND_SAR_TOLERANCE + 1e-9:
        raise ValueError(
            "字幕带坐标仅支持方形或近方形像素画布 "
            f"(SAR 与 1:1 相差不超过 {SUBTITLE_BAND_SAR_TOLERANCE:.0%})；当前 SAR={sar_text}"
        )
    canvas_h = canvas["height"]
    if not 0 <= y_top < y_bot <= canvas_h:
        raise ValueError(
            f"字幕带坐标无效: top={y_top}, bot={y_bot}, 画布高度={canvas_h}；"
            "必须满足 0 <= top < bot <= height"
        )
    return y_top, y_bot


def _frame_row(canvas, display_y):
    """Display-canvas row → row of the decoded, auto-rotated frame that drawbox paints on.

    The canvas keeps the frame's pixel width and stretches the height by the SAR only for a
    90°/270° stream (its stored width becomes the height); otherwise both share rows exactly.
    """
    rotated = canvas["rotation"] in {90, 270}
    frame_h = canvas["storage_width"] if rotated else canvas["storage_height"]
    if frame_h == canvas["height"]:
        return display_y
    return round(display_y * frame_h / canvas["height"])


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
    """Return display-only subtitle text with trailing clause/sentence punctuation removed.

    Narration/TTS source text stays untouched; this is applied only to SRT/ASS cue text.
    Closing quotes/brackets are preserved, so 「原声台词，」 renders as 「原声台词」;
    interrupted ASCII words like ``Sor…`` keep their attached ellipsis.
    """
    text = text.strip()
    suffix = ""
    while text and text[-1] in _SUBTITLE_CLOSING_QUOTES:
        suffix = text[-1] + suffix
        text = text[:-1].rstrip()
    if len(text) >= 2 and text.endswith("…") and text[-2].isascii() and text[-2].isalpha():
        return (text + suffix).strip()
    text = text.rstrip(_SUBTITLE_DISPLAY_STRIP_PUNCTUATION).rstrip()
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


_LATIN_APOSTROPHE_RE = re.compile(r"(?<=[A-Za-z])[‘’](?=[A-Za-z])")
_ATOMIC_ASCII_TOKEN_RE = re.compile(r"[A-Za-z0-9.%+-]+")
_ATOMIC_CJK_COMPOUND_RE = re.compile(
    r"(?:服务区|烧烤店|大排档|为什么|怎么会|怎么办|只不过|没想到|是不是|能不能|"
    r"黑骑|长公主|尚公主|宰相府|尚书省|大理寺|锦衣卫|太医院|"
    r"总决赛|季后赛|常规赛|三分球|罚球线|独行侠|得分后卫|控球后卫|大前锋|小前锋)"
)


def _normalize_subtitle_text(text):
    """Normalize burned subtitle text: straighten Latin curly apostrophes so CJK fonts never
    render full-width gaps inside contractions, collapse Chinese em-dashes ("—" / "——") to "，",
    collapse doubled commas, and strip any dangling comma left if a cue ended with an em-dash."""
    normalized = _LATIN_APOSTROPHE_RE.sub("'", text)
    normalized = re.sub(r"，{2,}", "，", re.sub(r"—+", "，", normalized))
    if normalized.endswith("，"):
        normalized = normalized[:-1].rstrip()
    elif len(normalized) >= 2 and normalized[-1] in _SUBTITLE_CLOSING_QUOTES and normalized[-2] == "，":
        normalized = normalized[:-2].rstrip() + normalized[-1]
    return normalized


def _hard_wrap_clause(clause, max_chars):
    """Balance an overlong clause across lines without slicing inside ASCII words, numbers, or CJK compounds."""
    n = len(clause)
    if n <= max_chars:
        return [clause]
    piece_count = (n + max_chars - 1) // max_chars
    atomic_spans = sorted(
        m.span()
        for rx in (_ATOMIC_ASCII_TOKEN_RE, _ATOMIC_CJK_COMPOUND_RE)
        for m in rx.finditer(clause)
        if m.end() - m.start() <= max_chars
    )
    pieces, cursor = [], 0
    for piece_index in range(piece_count - 1):
        rem_len = n - cursor
        rem_pieces = piece_count - piece_index
        ideal = cursor + (rem_len + rem_pieces - 1) // rem_pieces
        cut = ideal
        for ts, te in atomic_spans:
            if ts < ideal < te:
                candidates = [
                    c for c in (ts, te)
                    if cursor + 2 <= c <= cursor + max_chars
                    and 2 * (rem_pieces - 1) <= n - c <= (rem_pieces - 1) * max_chars
                ]
                if candidates:
                    cut = min(candidates, key=lambda c: abs(c - ideal))
                break
        pieces.append(clause[cursor:cut])
        cursor = cut
    pieces.append(clause[cursor:])
    return pieces


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
    sized = []
    for clause in clauses:
        sized.extend(_hard_wrap_clause(clause, max_chars))
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


def _subtitle_entries(narration, *, max_chars=None, highlights_by_index=None):
    """Collect subtitle entries from final TTS segment placement."""
    limit = int(max_chars or CONFIG["subtitle_max_chars"])
    hl_map = highlights_by_index or {}
    entries = []
    for pos, seg in enumerate(narration):
        text = seg["spoken_text"]
        start, end = float(seg["actual_place_start"]), float(seg["actual_place_end"])
        seg_idx = seg.get("index", pos)
        seg_hl = seg.get("highlights") or hl_map.get(seg_idx) or ()
        seg_entries = _distribute_chunks(_split_subtitle_chunks(text, limit), start, end)
        if seg_hl:
            for item in seg_entries:
                item["highlights"] = list(seg_hl)
        entries.extend(seg_entries)
    return entries


def _bridge_narration_micro_gaps(entries, bridge_seconds=0.0):
    """Hold consecutive narrator cues across tiny inter-sentence pauses to prevent blank flashes."""
    bridge = float(bridge_seconds or 0.0)
    if bridge <= 0.0 or len(entries) < 2:
        return entries
    out = [dict(e) for e in entries]
    for i in range(len(out) - 1):
        cur, nxt = out[i], out[i + 1]
        if (
            cur.get("is_dialogue")
            or nxt.get("is_dialogue")
            or str(cur["text"]).startswith("「")
            or str(nxt["text"]).startswith("「")
        ):
            continue
        gap = float(nxt["start"]) - float(cur["end"])
        if 0.0 < gap <= bridge:
            cur["end"] = float(nxt["start"])
    return out


def _distribute_chunks(chunks, start, end):
    """Distribute [start,end] across raw chunks while emitting display-clean text."""
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
    entries = _distribute_chunks(chunks, start, end)
    for entry in entries:
        entry["is_dialogue"] = True
    return entries
