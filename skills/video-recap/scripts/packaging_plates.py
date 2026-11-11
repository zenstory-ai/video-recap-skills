"""@2x supersampled Pillow RGBA plate generators for studio video packaging."""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFilter
except ImportError:  # pragma: no cover - bare stdlib Python without Pillow
    Image = ImageDraw = ImageFilter = None  # type: ignore[assignment]

from packaging_fonts import (
    draw_styled_line,
    load_font,
    measure_text,
    parse_rgba,
)

_CALLOUT_PALETTES = {
    "gold": {
        "fill": (246, 214, 132, 255),
        "highlight": (255, 238, 178, 255),
        "accent": (217, 180, 96, 235),
        "stroke": (20, 16, 12, 235),
    },
    "coral": {
        "fill": (242, 168, 144, 255),
        "highlight": (255, 222, 166, 255),
        "accent": (224, 138, 112, 235),
        "stroke": (24, 15, 14, 235),
    },
    "ivory": {
        "fill": (248, 244, 235, 255),
        "highlight": (245, 206, 114, 255),
        "accent": (214, 182, 110, 230),
        "stroke": (18, 18, 22, 235),
    },
    "calligraphy": {
        "fill": (245, 224, 168, 255),
        "highlight": (255, 240, 190, 255),
        "accent": (206, 166, 92, 230),
        "stroke": (18, 14, 12, 240),
    },
}


def _approx_text_px(text: str, font_px: int) -> tuple[int, int]:
    """Deterministic text bounding estimate for stdlib-only PNG fallback when Pillow is absent."""
    if not text:
        return 0, 0
    units = sum(0.58 if ord(ch) < 128 else 1.0 for ch in text)
    return max(2, int(round(units * font_px))), max(2, int(round(font_px * 1.15)))


def _write_stdlib_rgba_png(
    out_path: Path,
    width: int,
    height: int,
    fill: tuple[int, int, int, int] = (18, 16, 14, 215),
    *,
    bands: list[tuple[int, int, tuple[int, int, int, int]]] | None = None,
) -> dict:
    """Write a valid 8-bit RGBA PNG using pure Python stdlib when Pillow is unavailable."""
    w, h = max(2, int(width)), max(2, int(height))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    base_row = b"\x00" + bytes(fill) * w
    rows = [base_row] * h
    for y0, y1, rgba in bands or ():
        band_row = b"\x00" + bytes(rgba) * w
        for y in range(max(0, y0), min(h, y1)):
            rows[y] = band_row

    def _chunk(tag: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(tag + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(b"".join(rows), level=1))
        + _chunk(b"IEND", b"")
    )
    out_path.write_bytes(png)
    return {"path": str(out_path.resolve()), "width": w, "height": h}


def _save_downsampled(img2x: Image.Image, out_path: Path, size_1x: tuple[int, int]) -> dict:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    final = img2x.resize(size_1x, Image.Resampling.LANCZOS)
    final.save(out_path, format="PNG")
    return {"path": str(out_path.resolve()), "width": size_1x[0], "height": size_1x[1]}


def _draw_gold_hairline(draw: ImageDraw.ImageDraw, width: int, y: int, thickness: int = 4) -> None:
    """Draw a luminous tapered champagne-gold hairline on a 2x canvas."""
    for x in range(0, width, 4):
        u = x / max(1, width - 1)
        edge = math.sin(math.pi * u) ** 0.55
        alpha = int(65 + 170 * edge)
        r = int(198 + 46 * edge)
        g = int(164 + 48 * edge)
        b = int(96 + 44 * edge)
        draw.rectangle([x, y, min(width, x + 4), y + thickness - 1], fill=(r, g, b, alpha))


def _draw_header_typography(
    img2x: Image.Image,
    header: dict,
    *,
    center_x: int,
    top_y: int,
    max_width: int,
    scale: float,
) -> None:
    """Render kicker pill + 1-2 line heavy hook headline + optional subhead on a 2x canvas."""
    draw = ImageDraw.Draw(img2x)
    cursor_y = top_y
    kicker = str(header.get("kicker") or "").strip()
    if kicker:
        k_font = load_font("sans_heavy", round(24 * scale * 2))
        kw, kh = measure_text(draw, kicker, k_font)
        pad_x, pad_y = round(22 * scale * 2), round(8 * scale * 2)
        pw, ph = kw + pad_x * 2, kh + pad_y * 2
        px = center_x - pw // 2
        draw.rounded_rectangle(
            [px, cursor_y, px + pw, cursor_y + ph],
            radius=ph // 2,
            fill=(28, 23, 19, 215),
            outline=(217, 184, 112, 195),
            width=max(2, round(1.5 * scale * 2)),
        )
        draw.text(
            (center_x - kw // 2, cursor_y + pad_y - round(2 * scale * 2)),
            kicker,
            font=k_font,
            fill=(238, 206, 136, 255),
        )
        cursor_y += ph + round(16 * scale * 2)

    raw_lines = header.get("lines")
    if not raw_lines:
        title = str(header.get("title") or "").strip()
        raw_lines = [ln.strip() for ln in title.split("\n") if ln.strip()]
    highlights = list(header.get("highlights") or ())
    base_size = int(header.get("font_size") or (48 if len(raw_lines or ()) <= 1 else 44))
    fill_rgba = parse_rgba(header.get("color") or "#FAF6EE")
    hl_rgba = parse_rgba(header.get("highlight_color") or "#F6CE6E")

    for line in (raw_lines or ())[:2]:
        f_px = round(base_size * scale * 2)
        font = load_font("sans_heavy", f_px)
        lw, lh = measure_text(draw, line, font, stroke_width=round(2 * scale * 2))
        while lw > max_width and f_px > 28:
            f_px -= 4
            font = load_font("sans_heavy", f_px)
            lw, lh = measure_text(draw, line, font, stroke_width=round(2 * scale * 2))
        lx = center_x - lw // 2
        draw_styled_line(
            img2x,
            lx,
            cursor_y,
            line,
            font,
            fill_rgba,
            highlights=highlights,
            highlight_fill=hl_rgba,
            stroke_width=max(2, round(1.8 * scale * 2)),
            stroke_fill=(10, 8, 7, 230),
            shadow_blur=max(3, round(5 * scale)),
            shadow_offset=(0, round(5 * scale)),
            shadow_color=(0, 0, 0, 185),
        )
        cursor_y += lh + round(14 * scale * 2)

    subhead = str(header.get("subhead") or "").strip()
    if subhead:
        s_font = load_font("sans_medium", round(25 * scale * 2))
        sw, sh = measure_text(draw, subhead, s_font)
        pad_x, pad_y = round(24 * scale * 2), round(9 * scale * 2)
        bw, bh = min(max_width, sw + pad_x * 2), sh + pad_y * 2
        bx = center_x - bw // 2
        cursor_y += round(4 * scale * 2)
        draw.rounded_rectangle(
            [bx, cursor_y, bx + bw, cursor_y + bh],
            radius=round(12 * scale * 2),
            fill=(255, 255, 255, 24),
            outline=(255, 255, 255, 52),
            width=2,
        )
        draw.text(
            (center_x - sw // 2, cursor_y + pad_y - round(2 * scale * 2)),
            subhead,
            font=s_font,
            fill=(232, 228, 220, 242),
        )


def render_portrait_cinema_frame_plate(
    out_path: Path,
    canvas: dict,
    *,
    header_h: int,
    footer_h: int,
    header: dict,
    theme_color: str = "#16110E",
) -> dict:
    """Full-canvas static frame for portrait_cinema: espresso/charcoal bands + gold hairlines + inner feather."""
    w, h = int(canvas["width"]), int(canvas["height"])
    base_r, base_g, base_b, _ = parse_rgba(theme_color)
    if Image is None:
        band_rgba = (base_r, base_g, base_b, 255)
        return _write_stdlib_rgba_png(
            out_path, w, h, (0, 0, 0, 0),
            bands=[(0, header_h, band_rgba), (h - footer_h, h, band_rgba)],
        )
    w2, h2 = w * 2, h * 2
    hh2, fh2 = header_h * 2, footer_h * 2
    scale = w / 1080.0

    img = Image.new("RGBA", (w2, h2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Top header band + soft inner feather into viewport
    for y in range(hh2):
        u = y / max(1, hh2 - 1)
        r = int(base_r * (1.18 - 0.22 * u))
        g = int(base_g * (1.18 - 0.22 * u))
        b = int(base_b * (1.18 - 0.22 * u))
        draw.line([(0, y), (w2, y)], fill=(min(255, r), min(255, g), min(255, b), 255))

    # Subtle warm radial spotlight behind title
    glow = Image.new("RGBA", (w2, hh2), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    gdraw.ellipse(
        [int(w2 * 0.18), int(-hh2 * 0.35), int(w2 * 0.82), int(hh2 * 0.95)],
        fill=(196, 152, 78, 42),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(radius=max(12, round(36 * scale))))
    img.alpha_composite(glow, (0, 0))

    # Bottom subtitle band
    bot_start = h2 - fh2
    for i in range(fh2):
        u = i / max(1, fh2 - 1)
        r = int(base_r * (0.96 + 0.20 * u))
        g = int(base_g * (0.96 + 0.20 * u))
        b = int(base_b * (0.96 + 0.20 * u))
        draw.line([(0, bot_start + i), (w2, bot_start + i)], fill=(min(255, r), min(255, g), min(255, b), 255))

    # Soft inner viewport vignette (top & bottom 26px @1x)
    feather = max(8, round(26 * scale * 2))
    for i in range(feather):
        alpha = int(120 * ((1.0 - i / feather) ** 1.8))
        draw.line([(0, hh2 + i), (w2, hh2 + i)], fill=(8, 6, 6, alpha))
        draw.line([(0, bot_start - 1 - i), (w2, bot_start - 1 - i)], fill=(8, 6, 6, alpha))

    _draw_gold_hairline(draw, w2, hh2 - max(3, round(2 * scale * 2)), max(3, round(2 * scale * 2)))
    _draw_gold_hairline(draw, w2, bot_start, max(3, round(2 * scale * 2)))

    if header:
        has_kicker = bool(str(header.get("kicker") or "").strip())
        lines = header.get("lines") or [
            ln.strip() for ln in str(header.get("title") or "").split("\n") if ln.strip()
        ]
        top_pad = round((24 if (has_kicker or len(lines) > 1) else 52) * scale * 2)
        _draw_header_typography(
            img, header,
            center_x=w2 // 2,
            top_y=max(round(16 * scale * 2), top_pad),
            max_width=round(w2 * 0.88),
            scale=scale,
        )
    return _save_downsampled(img, out_path, (w, h))


def render_portrait_stage_overlay_plate(
    out_path: Path,
    canvas: dict,
    *,
    viewport: dict,
    header: dict,
    backdrop_mode: str = "blurred_frame",
) -> dict:
    """Stage atmosphere, viewport shadow + champagne hairlines + top hook header for portrait_stage."""
    w, h = int(canvas["width"]), int(canvas["height"])
    if Image is None:
        vy, vbot = int(viewport["y"]), int(viewport["y"] + viewport["height"])
        vig = (8, 10, 15, 175 if backdrop_mode == "blurred_frame" else 0)
        return _write_stdlib_rgba_png(
            out_path, w, h, (0, 0, 0, 0),
            bands=[(0, vy, vig), (vbot, h, vig)],
        )
    w2, h2 = w * 2, h * 2
    scale = w / 1080.0
    vx2, vy2 = viewport["x"] * 2, viewport["y"] * 2
    vw2, vh2 = viewport["width"] * 2, viewport["height"] * 2
    vbot2 = vy2 + vh2

    img = Image.new("RGBA", (w2, h2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Top and bottom cinema vignette over backdrop (leaving viewport punchy!)
    for y in range(vy2):
        u = y / max(1, vy2 - 1)
        alpha = int(205 - 70 * u) if backdrop_mode == "blurred_frame" else 0
        if alpha > 0:
            draw.line([(0, y), (w2, y)], fill=(8, 10, 15, alpha))
    for y in range(vbot2, h2):
        u = (y - vbot2) / max(1, h2 - vbot2 - 1)
        alpha = int(145 + 88 * (u ** 0.85)) if backdrop_mode == "blurred_frame" else 0
        if alpha > 0:
            draw.line([(0, y), (w2, y)], fill=(8, 10, 15, alpha))

    if backdrop_mode == "studio_dark":
        glow = Image.new("RGBA", (w2, h2), (0, 0, 0, 0))
        gdraw = ImageDraw.Draw(glow)
        gdraw.ellipse(
            [int(w2 * 0.08), vy2 - round(160 * scale * 2), int(w2 * 0.92), vbot2 + round(160 * scale * 2)],
            fill=(54, 78, 122, 52),
        )
        glow = glow.filter(ImageFilter.GaussianBlur(radius=max(16, round(54 * scale))))
        img.alpha_composite(glow)

    # Outer drop shadow above & below viewport
    sh_h = max(10, round(28 * scale * 2))
    for i in range(sh_h):
        a = int(135 * ((1.0 - i / sh_h) ** 2.0))
        if vy2 - 1 - i >= 0:
            draw.line([(vx2, vy2 - 1 - i), (vx2 + vw2, vy2 - 1 - i)], fill=(0, 0, 0, a))
        if vbot2 + i < h2:
            draw.line([(vx2, vbot2 + i), (vx2 + vw2, vbot2 + i)], fill=(0, 0, 0, a))

    line_t = max(3, round(2 * scale * 2))
    _draw_gold_hairline(draw, w2, max(0, vy2 - line_t), line_t)
    _draw_gold_hairline(draw, w2, min(h2 - line_t, vbot2), line_t)

    if header:
        default_top = 124 if viewport["y"] <= round(460 * h / 1920) else 168
        top_y = round(int(header.get("top_y", default_top)) * scale * 2)
        _draw_header_typography(
            img, header,
            center_x=w2 // 2,
            top_y=top_y,
            max_width=round(w2 * 0.88),
            scale=scale,
        )
    return _save_downsampled(img, out_path, (w, h))


def render_landscape_scrim_plate(out_path: Path, canvas: dict) -> dict:
    """Subtle bottom cinema gradient scrim so subtitles stay effortless over bright frames."""
    w, h = int(canvas["width"]), int(canvas["height"])
    scrim_h = max(80, round(h * 0.19))
    if Image is None:
        return _write_stdlib_rgba_png(
            out_path, w, h, (0, 0, 0, 0),
            bands=[(h - scrim_h, h, (8, 8, 10, 72))],
        )
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    y0 = h - scrim_h
    for i in range(scrim_h):
        u = i / max(1, scrim_h - 1)
        alpha = int(118 * (u ** 1.65))
        draw.line([(0, y0 + i), (w, y0 + i)], fill=(8, 8, 10, alpha))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG")
    return {"path": str(out_path.resolve()), "width": w, "height": h}


def render_editorial_paper_strip_plate(
    out_path: Path,
    *,
    width: int,
    height: int,
) -> dict:
    """Warm-ivory (#F2EDE7) editorial subtitle card with drop shadow, gold bar, and seal mark."""
    pad = 16
    pw, ph = width + pad * 2, height + pad * 2
    if Image is None:
        meta = _write_stdlib_rgba_png(
            out_path, pw, ph, (0, 0, 0, 0),
            bands=[(pad, pad + height, (242, 237, 231, 255))],
        )
        meta["pad"] = pad
        return meta
    w2, h2 = pw * 2, ph * 2
    bx, by, bw, bh = pad * 2, pad * 2, width * 2, height * 2
    radius = max(12, round(height * 0.24) * 2)

    img = Image.new("RGBA", (w2, h2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (w2, h2), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(shadow)
    sdraw.rounded_rectangle([bx, by + 8, bx + bw, by + bh + 8], radius=radius, fill=(0, 0, 0, 128))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=12))
    img.alpha_composite(shadow)

    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        [bx, by, bx + bw, by + bh],
        radius=radius,
        fill=(242, 237, 231, 255),
        outline=(206, 196, 182, 255),
        width=3,
    )
    # Left warm-gold vertical bookmark accent
    bar_x = bx + 28
    bar_y1, bar_y2 = by + int(bh * 0.22), by + int(bh * 0.78)
    draw.rounded_rectangle([bar_x, bar_y1, bar_x + 10, bar_y2], radius=5, fill=(198, 148, 66, 255))

    # Right subtle cinnabar-gold diamond seal mark
    cx, cy = bx + bw - 44, by + bh // 2
    r = max(10, int(bh * 0.14))
    draw.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], fill=(190, 88, 52, 215))
    ir = max(4, r // 2)
    draw.polygon([(cx, cy - ir), (cx + ir, cy), (cx, cy + ir), (cx - ir, cy)], fill=(244, 224, 178, 240))

    meta = _save_downsampled(img, out_path, (pw, ph))
    meta["pad"] = pad
    return meta


def render_title_mark_plate(out_path: Path, mark: dict, *, scale: float = 1.0) -> dict:
    """Top-left calligraphic show mark + vertical seal badge + soft dark radial scrim."""
    title = str(mark.get("title") or "").strip()
    badge = str(mark.get("badge") or "").strip()
    if Image is None:
        tw, th = _approx_text_px(title, round(54 * scale))
        bw, bh = _approx_text_px(badge, round(20 * scale)) if badge else (0, 0)
        w = tw + (bw + round(42 * scale) if badge else 0) + round(68 * scale)
        h = max(th, bh + round(16 * scale)) + round(52 * scale)
        return _write_stdlib_rgba_png(out_path, w, h, (10, 10, 12, 132))
    t_font = load_font("calligraphy", round(54 * scale * 2))
    b_font = load_font("sans_heavy", round(20 * scale * 2))
    dummy = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    tw, th = measure_text(dummy, title, t_font, stroke_width=4)
    bw, bh = measure_text(dummy, badge, b_font) if badge else (0, 0)

    pad_x, pad_y = round(34 * scale * 2), round(26 * scale * 2)
    pill_gap = round(18 * scale * 2) if badge else 0
    pill_w = bw + round(24 * scale * 2) if badge else 0
    w2 = tw + pill_gap + pill_w + pad_x * 2
    h2 = max(th, bh + round(16 * scale * 2)) + pad_y * 2

    img = Image.new("RGBA", (w2, h2), (0, 0, 0, 0))
    scrim = Image.new("RGBA", (w2, h2), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(scrim)
    sdraw.rounded_rectangle([8, 8, w2 - 8, h2 - 8], radius=round(24 * scale * 2), fill=(10, 10, 12, 132))
    scrim = scrim.filter(ImageFilter.GaussianBlur(radius=max(8, round(18 * scale))))
    img.alpha_composite(scrim)

    draw_styled_line(
        img,
        pad_x,
        pad_y,
        title,
        t_font,
        (250, 244, 232, 255),
        stroke_width=max(2, round(2 * scale * 2)),
        stroke_fill=(16, 12, 10, 225),
        shadow_blur=max(3, round(5 * scale)),
        shadow_offset=(0, round(4 * scale)),
    )
    if badge:
        draw = ImageDraw.Draw(img)
        px = pad_x + tw + pill_gap
        py = pad_y + max(0, (th - (bh + round(16 * scale * 2))) // 2)
        ph = bh + round(16 * scale * 2)
        draw.rounded_rectangle(
            [px, py, px + pill_w, py + ph],
            radius=round(8 * scale * 2),
            fill=(186, 66, 44, 232),
            outline=(242, 206, 138, 210),
            width=2,
        )
        draw.text((px + round(12 * scale * 2), py + round(6 * scale * 2)), badge, font=b_font, fill=(252, 246, 236, 255))

    return _save_downsampled(img, out_path, (max(2, w2 // 2), max(2, h2 // 2)))


def render_card_plate(out_path: Path, card: dict, *, scale: float = 1.0) -> dict:
    """Render name_tag, data_card, quote_card, or opening_card at @2x."""
    kind = str(card.get("kind") or "name_tag")
    if Image is None:
        top = str(card.get("value") or card.get("quote") or card.get("name") or card.get("title") or "卡").strip()
        sub = str(card.get("label") or card.get("speaker") or card.get("role") or card.get("subtitle") or "").strip()
        tw, th = _approx_text_px(top, round(36 * scale))
        sw, sh = _approx_text_px(sub, round(22 * scale))
        return _write_stdlib_rgba_png(
            out_path, max(tw, sw) + round(92 * scale), th + sh + round(68 * scale), (16, 18, 24, 226)
        )
    dummy = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    pad = round(16 * scale * 2)

    if kind == "data_card":
        val_text = str(card.get("value") or "").strip()
        lbl_text = str(card.get("label") or "").strip()
        v_font = load_font("sans_heavy", round(44 * scale * 2))
        l_font = load_font("sans_medium", round(23 * scale * 2))
        vw, vh = measure_text(dummy, val_text, v_font)
        lw, lh = measure_text(dummy, lbl_text, l_font)
        cw = max(vw, lw) + round(56 * scale * 2)
        ch = vh + lh + round(44 * scale * 2)
        w2, h2 = cw + pad * 2, ch + pad * 2
        img = Image.new("RGBA", (w2, h2), (0, 0, 0, 0))
        sh = Image.new("RGBA", (w2, h2), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle([pad, pad + 6, pad + cw, pad + ch + 6], radius=round(16 * scale * 2), fill=(0, 0, 0, 155))
        img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(radius=10)))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle(
            [pad, pad, pad + cw, pad + ch],
            radius=round(16 * scale * 2),
            fill=(15, 19, 28, 228),
            outline=(218, 184, 108, 195),
            width=3,
        )
        draw.rounded_rectangle(
            [pad + round(18 * scale * 2), pad + round(18 * scale * 2), pad + round(26 * scale * 2), pad + ch - round(18 * scale * 2)],
            radius=4, fill=(246, 206, 98, 255),
        )
        tx = pad + round(40 * scale * 2)
        draw.text((tx, pad + round(14 * scale * 2)), val_text, font=v_font, fill=(255, 214, 76, 255))
        draw.text((tx, pad + round(22 * scale * 2) + vh), lbl_text, font=l_font, fill=(224, 228, 236, 245))
        return _save_downsampled(img, out_path, (max(2, w2 // 2), max(2, h2 // 2)))

    if kind == "quote_card":
        quote = str(card.get("quote") or "").strip()
        speaker = str(card.get("speaker") or "").strip()
        q_font = load_font("sans_heavy", round(30 * scale * 2))
        s_font = load_font("sans_medium", round(21 * scale * 2))
        qw, qh = measure_text(dummy, quote, q_font)
        sw, sh_h = measure_text(dummy, speaker, s_font) if speaker else (0, 0)
        cw = max(qw, sw) + round(68 * scale * 2)
        ch = qh + (sh_h + round(12 * scale * 2) if speaker else 0) + round(40 * scale * 2)
        w2, h2 = cw + pad * 2, ch + pad * 2
        img = Image.new("RGBA", (w2, h2), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle(
            [pad, pad, pad + cw, pad + ch],
            radius=round(16 * scale * 2),
            fill=(18, 20, 26, 226),
            outline=(218, 184, 108, 175),
            width=2,
        )
        mark_font = load_font("sans_heavy", round(48 * scale * 2))
        draw.text((pad + round(18 * scale * 2), pad + round(6 * scale * 2)), "“", font=mark_font, fill=(238, 198, 106, 235))
        tx = pad + round(48 * scale * 2)
        draw.text((tx, pad + round(18 * scale * 2)), quote, font=q_font, fill=(250, 246, 238, 255))
        if speaker:
            draw.text((tx, pad + round(26 * scale * 2) + qh), f"— {speaker}", font=s_font, fill=(214, 190, 138, 235))
        return _save_downsampled(img, out_path, (max(2, w2 // 2), max(2, h2 // 2)))

    # name_tag or opening_card
    primary = str(card.get("name") or card.get("title") or "").strip()
    secondary = str(card.get("role") or card.get("subtitle") or "").strip()
    tag = str(card.get("tag") or "").strip()
    n_font = load_font("sans_heavy", round((36 if kind == "opening_card" else 32) * scale * 2))
    r_font = load_font("sans_medium", round(22 * scale * 2))
    nw, nh = measure_text(dummy, primary, n_font)
    rw, rh = measure_text(dummy, secondary, r_font) if secondary else (0, 0)
    tw, th = (0, 0)
    t_font = load_font("sans_heavy", round(18 * scale * 2))
    if tag:
        tw, th = measure_text(dummy, tag, t_font)
    cw = max(nw, rw, tw + round(20 * scale * 2)) + round(56 * scale * 2)
    ch = nh + (rh + round(10 * scale * 2) if secondary else 0) + (th + round(14 * scale * 2) if tag else 0) + round(32 * scale * 2)
    w2, h2 = cw + pad * 2, ch + pad * 2
    img = Image.new("RGBA", (w2, h2), (0, 0, 0, 0))
    sh = Image.new("RGBA", (w2, h2), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([pad, pad + 6, pad + cw, pad + ch + 6], radius=round(14 * scale * 2), fill=(0, 0, 0, 150))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(radius=9)))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        [pad, pad, pad + cw, pad + ch],
        radius=round(14 * scale * 2),
        fill=(16, 18, 24, 226),
        outline=(216, 184, 112, 185),
        width=2,
    )
    draw.rounded_rectangle(
        [pad + round(16 * scale * 2), pad + round(16 * scale * 2), pad + round(24 * scale * 2), pad + ch - round(16 * scale * 2)],
        radius=4, fill=(238, 198, 104, 255),
    )
    tx = pad + round(38 * scale * 2)
    cy = pad + round(14 * scale * 2)
    if tag:
        draw.text((tx, cy), tag, font=t_font, fill=(238, 198, 104, 245))
        cy += th + round(10 * scale * 2)
    draw.text((tx, cy), primary, font=n_font, fill=(250, 248, 244, 255))
    if secondary:
        draw.text((tx, cy + nh + round(8 * scale * 2)), secondary, font=r_font, fill=(208, 212, 220, 240))
    return _save_downsampled(img, out_path, (max(2, w2 // 2), max(2, h2 // 2)))


def render_callout_plate(out_path: Path, callout: dict, *, scale: float = 1.0) -> dict:
    """Render a timed 花字 / subtext callout with @2x typography, soft shadow, and accent underline."""
    style_key = str(callout.get("style") or "gold")
    pal = _CALLOUT_PALETTES.get(style_key, _CALLOUT_PALETTES["gold"])
    font_role = "calligraphy" if style_key == "calligraphy" else "sans_heavy"
    text = str(callout.get("text") or "").strip().rstrip("，。！？、；：,.!?;:")
    subtext = str(callout.get("subtext") or "").strip().rstrip("，。！？、；：,.!?;:")
    highlights = list(callout.get("highlights") or ())
    if Image is None:
        tw, th = _approx_text_px(text, round(38 * scale))
        sw, sh = _approx_text_px(subtext, round(21 * scale))
        return _write_stdlib_rgba_png(
            out_path, max(tw + round(22 * scale), sw) + round(52 * scale), th + sh + round(52 * scale), (10, 10, 14, 108)
        )

    font = load_font(font_role, round((42 if font_role == "calligraphy" else 36) * scale * 2))
    s_font = load_font("sans_medium", round(21 * scale * 2))
    dummy = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    tw, th = measure_text(dummy, text, font, stroke_width=4)
    sw, sh_h = measure_text(dummy, subtext, s_font) if subtext else (0, 0)

    pad_x, pad_y = round(26 * scale * 2), round(18 * scale * 2)
    dot_gap = round(22 * scale * 2)
    w2 = max(tw + dot_gap, sw) + pad_x * 2
    h2 = th + round(16 * scale * 2) + (sh_h + round(8 * scale * 2) if subtext else 0) + pad_y * 2

    img = Image.new("RGBA", (w2, h2), (0, 0, 0, 0))
    scrim = Image.new("RGBA", (w2, h2), (0, 0, 0, 0))
    ImageDraw.Draw(scrim).rounded_rectangle(
        [10, 10, w2 - 10, h2 - 10], radius=round(18 * scale * 2), fill=(10, 10, 14, 108)
    )
    img.alpha_composite(scrim.filter(ImageFilter.GaussianBlur(radius=max(6, round(12 * scale)))))

    draw = ImageDraw.Draw(img)
    # Diamond accent marker
    cx = pad_x + round(6 * scale * 2)
    cy = pad_y + th // 2
    r = max(5, round(6 * scale * 2))
    draw.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], fill=pal["accent"])

    draw_styled_line(
        img,
        pad_x + dot_gap,
        pad_y,
        text,
        font,
        pal["fill"],
        highlights=highlights,
        highlight_fill=pal["highlight"],
        stroke_width=max(2, round(2.2 * scale * 2)),
        stroke_fill=pal["stroke"],
        shadow_blur=max(3, round(5 * scale)),
        shadow_offset=(0, round(4 * scale)),
    )

    # Tapered brush/gradient accent underline
    ul_y = pad_y + th + round(8 * scale * 2)
    ul_w = tw + dot_gap
    ar, ag, ab, aa = pal["accent"]
    for i in range(0, ul_w, 4):
        u = i / max(1, ul_w - 1)
        a = int(aa * (1.0 - 0.72 * (u ** 1.4)))
        draw.rectangle([pad_x + i, ul_y, pad_x + min(ul_w, i + 4), ul_y + max(3, round(2.5 * scale * 2))], fill=(ar, ag, ab, a))

    if subtext:
        draw.text((pad_x + dot_gap, ul_y + round(10 * scale * 2)), subtext, font=s_font, fill=(234, 232, 226, 235))

    return _save_downsampled(img, out_path, (max(2, w2 // 2), max(2, h2 // 2)))


def render_progress_bar_plate(out_path: Path, width: int, height: int = 4) -> dict:
    """Luminous gold progress bar with bright leading edge."""
    if Image is None:
        return _write_stdlib_rgba_png(out_path, width, height, (226, 192, 108, 240))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for x in range(width):
        u = x / max(1, width - 1)
        tip = 1.0 if u > 0.96 else 0.0
        r = min(255, int(208 + 38 * u + 15 * tip))
        g = min(255, int(172 + 44 * u + 25 * tip))
        b = min(255, int(92 + 36 * u + 60 * tip))
        draw.line([(x, 0), (x, height - 1)], fill=(r, g, b, 240))
    img.save(out_path, format="PNG")
    return {"path": str(out_path.resolve()), "width": width, "height": height}
