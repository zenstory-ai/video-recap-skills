#!/usr/bin/env python3
"""Data-driven visual packaging, Remotion animated overlay bridge & speech-safe SFX over timeline.json v3.

Subcommands: list-themes | apply | place-sfx | remotion-init | remotion-render.
Pure stdlib by default; probes Pillow -> ImageMagick -> ffmpeg drawtext -> stdlib PNG for static
plates, and Node/npx Remotion for transparent ProRes 4444 / VP9 animated overlays.
"""
from __future__ import annotations

import argparse
import copy
import functools
import glob
import json
import os
import shutil
import struct
import subprocess
import sys
import zlib
from fractions import Fraction
from pathlib import Path

__all__ = [
    "list_themes", "load_theme", "render_plate_png", "apply_theme",
    "place_sfx", "remotion_init", "remotion_render", "main",
]

_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
_BUILTIN_THEMES = _ASSETS_DIR / "themes"
_REMOTION_TEMPLATE = _ASSETS_DIR / "remotion"
_FONT_GLOBS = ("/System/Library/AssetsV2/com_apple_MobileAsset_Font7/*/AssetData/*",
               "/System/Library/Fonts/*", "/System/Library/Fonts/Supplemental/*", "/Library/Fonts/*")
_MAC_FONTS = ("Lantinghei.ttc", "PingFang.ttc", "Hiragino Sans GB.ttc", "STHeiti Medium.ttc")
_LINUX_FONTS = ("/usr/share/fonts/google-noto-cjk/NotoSansCJKsc-Bold.otf",
                "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
                "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
_WIN_FONTS = ("C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/arialbd.ttf")


def _parse_rgba(color: str, default: tuple[int, int, int, int] = (18, 22, 34, 230)) -> tuple[int, int, int, int]:
    raw = str(color or "").strip().lstrip("#")
    if len(raw) in {6, 8}:
        return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16), (int(raw[6:8], 16) if len(raw) == 8 else 255)
    return default


def _parse_fps(value) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    return float(Fraction(value.strip())) if isinstance(value, str) and value.strip() else 30.0


def _best_ttc_index(font_path: str) -> int:
    if not font_path.lower().endswith((".ttc", ".otc")):
        return 0
    try:
        from PIL import ImageFont  # type: ignore[import-not-found]
    except ImportError:
        return 0
    best_idx, best_score = 0, -100
    for idx in range(16):
        try:
            family, style = (s.lower() for s in ImageFont.truetype(font_path, 32, index=idx).getname())
        except Exception:  # noqa: BLE001
            break
        if family.startswith("."):
            continue
        score = (40 if (" sc" in family or family.endswith("sc") or "gb" in family) else 0) + (
            20 if any(k in style for k in ("bold", "heavy", "semibold", "medium")) else 0) - (20 if "italic" in style else 0)
        if score > best_score:
            best_score, best_idx = score, idx
    return best_idx


@functools.lru_cache(maxsize=8)
def _resolve_font(explicit: str = "") -> tuple[str | None, int]:
    if explicit and Path(explicit).expanduser().is_file():
        return str(Path(explicit).expanduser().resolve()), 0
    if sys.platform == "darwin":
        found = {Path(raw).name: raw for pat in _FONT_GLOBS for raw in sorted(glob.glob(pat))}
        for name in _MAC_FONTS:
            if name in found:
                return found[name], _best_ttc_index(found[name])
    for cand in (_WIN_FONTS if sys.platform == "win32" else _LINUX_FONTS):
        if Path(cand).is_file():
            return cand, _best_ttc_index(cand)
    return None, 0


def list_themes(themes_dir: str | Path | None = None) -> list[dict]:
    """List available JSON theme specs from `themes_dir` (or built-in `assets/themes/`)."""
    root = Path(themes_dir).expanduser().resolve() if themes_dir else _BUILTIN_THEMES
    if not root.is_dir():
        return []
    return [{"name": str(d.get("name") or p.stem), "path": str(p),
             "description": str(d.get("description") or ""), "canvas": d.get("canvas") or {}}
            for p in sorted(root.glob("*.json")) for d in (json.loads(p.read_text(encoding="utf-8")),)]


def load_theme(name_or_path: str | Path, *, themes_dir: str | Path | None = None) -> dict:
    """Load a theme JSON by built-in name or explicit file path."""
    cand = Path(name_or_path).expanduser()
    if cand.is_file():
        return json.loads(cand.read_text(encoding="utf-8"))
    root = Path(themes_dir).expanduser().resolve() if themes_dir else _BUILTIN_THEMES
    named = root / (str(name_or_path) if str(name_or_path).endswith(".json") else f"{name_or_path}.json")
    if named.is_file():
        return json.loads(named.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"theme not found: {name_or_path!r} (checked {named})")


def _write_stdlib_png(out_path: Path, width: int, height: int, bg: tuple[int, int, int, int], border: tuple[int, int, int, int]) -> None:
    w, h = max(2, int(width)), max(2, int(height))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    b_row, i_row = b"\x00" + bytes(border) * w, b"\x00" + bytes(border) * 3 + bytes(bg) * max(0, w - 6) + bytes(border) * min(3, w)
    rows = [b_row if (y < 3 or y >= h - 3) else i_row for y in range(h)]
    chunk = lambda tag, data: struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)  # noqa: E731
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    out_path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"".join(rows), level=1)) + chunk(b"IEND", b""))


def _render_with_pillow(out_path: Path, width: int, height: int, lines: list[str], palette: dict, font_path: str | None, font_idx: int) -> bool:
    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore[import-not-found]
    except ImportError:
        return False
    w2, h2 = max(4, width * 2), max(4, height * 2)
    bg, border = _parse_rgba(palette.get("bg", "#111622E6")), _parse_rgba(palette.get("border", "#D9B460EB"))
    title_col, accent_col = _parse_rgba(palette.get("title", "#FFF8EAFF")), _parse_rgba(palette.get("accent", "#F6D684FF"))
    img = Image.new("RGBA", (w2, h2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([4, 4, w2 - 5, h2 - 5], radius=min(28, h2 // 5), fill=bg, outline=border, width=4)
    clean = [ln.strip() for ln in lines if ln and ln.strip()]
    if clean:
        base_sz = max(24, min(int(h2 * 0.42 / max(1, len(clean) * 0.72)), 92))
        fonts = [ImageFont.truetype(font_path, int(base_sz * (0.72 if (i == 0 and len(clean) > 1) else 1.0)), index=font_idx)
                 if font_path else ImageFont.load_default() for i in range(len(clean))]
        boxes = [draw.textbbox((0, 0), t, font=f) for t, f in zip(clean, fonts)]
        heights, gap = [b[3] - b[1] for b in boxes], max(10, base_sz // 4)
        y = (h2 - (sum(heights) + gap * max(0, len(clean) - 1))) // 2
        for i, (text, font, box, lh) in enumerate(zip(clean, fonts, boxes, heights)):
            draw.text((max(24, (w2 - (box[2] - box[0])) // 2), y - box[1]), text, font=font,
                      fill=accent_col if (i == 0 and len(clean) > 1) else title_col)
            y += lh + gap
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.resize((max(2, width), max(2, height)), Image.Resampling.LANCZOS).save(out_path, format="PNG")
    return True


def _render_with_magick(out_path: Path, width: int, height: int, lines: list[str], palette: dict, font_path: str | None) -> bool:
    tool = shutil.which("magick") or shutil.which("convert")
    if not tool:
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(ln.strip() for ln in lines if ln and ln.strip()) or " "
    cmd = [tool, "-size", f"{width}x{height}", f"xc:{palette.get('bg', '#111622E6')}", "-gravity", "center", "-fill", palette.get("title", "#FFF8EA")]
    cmd += (["-font", font_path] if font_path else []) + ["-pointsize", str(max(18, height // max(3, len(lines) + 1))), "-annotate", "+0+0", text, str(out_path)]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30).returncode == 0 and out_path.is_file()


def _esc_dt(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace("%", "\\%")


def _ff_color(rgba: tuple[int, int, int, int]) -> str:
    return f"0x{rgba[0]:02x}{rgba[1]:02x}{rgba[2]:02x}@{rgba[3] / 255.0:.2f}"


def _render_with_ffmpeg_drawtext(out_path: Path, width: int, height: int, lines: list[str], palette: dict, font_path: str | None) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bg, border = _parse_rgba(palette.get("bg", "#111622E6")), _parse_rgba(palette.get("border", "#D9B460EB"))
    title_col, accent_col = _parse_rgba(palette.get("title", "#FFF8EAFF")), _parse_rgba(palette.get("accent", "#F6D684FF"))
    filters = ["format=rgba", f"drawbox=x=0:y=0:w=iw:h=ih:color={_ff_color(bg)}:t=fill", f"drawbox=x=2:y=2:w=iw-4:h=ih-4:color={_ff_color(border)}:t=3"]
    clean = [ln.strip() for ln in lines if ln and ln.strip()]
    if clean:
        base_sz = max(14, min(int(height * 0.40 / max(1, len(clean) * 0.78)), 48))
        step, start_y, f_opt = int(base_sz * 1.25), max(8, (height - int(base_sz * 1.25) * len(clean)) // 2), (f"fontfile='{_esc_dt(font_path)}':" if font_path else "")
        for i, ln in enumerate(clean):
            sz, col = int(base_sz * (0.76 if (i == 0 and len(clean) > 1) else 1.0)), _ff_color(accent_col if (i == 0 and len(clean) > 1) else title_col)
            filters.append(f"drawtext={f_opt}text='{_esc_dt(ln)}':fontsize={sz}:fontcolor={col}:x=(w-text_w)/2:y={start_y + i * step}")
    cmd = [ffmpeg, "-y", "-f", "lavfi", "-i", f"color=c=black@0.0:s={max(2, width)}x{max(2, height)}:d=0.1", "-vf", ",".join(filters), "-frames:v", "1", str(out_path)]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20).returncode == 0 and out_path.is_file()


def render_plate_png(out_path: str | Path, width: int, height: int, lines: list[str], palette: dict, *, font_file: str = "") -> dict:
    """Render an RGBA overlay plate PNG via Pillow -> ImageMagick -> ffmpeg drawtext -> stdlib fallback."""
    out, (font_path, font_idx) = Path(out_path).expanduser().resolve(), _resolve_font(font_file)
    if _render_with_pillow(out, width, height, lines, palette, font_path, font_idx):
        return {"path": str(out), "width": width, "height": height, "renderer": "pillow"}
    if _render_with_magick(out, width, height, lines, palette, font_path):
        return {"path": str(out), "width": width, "height": height, "renderer": "imagemagick"}
    if _render_with_ffmpeg_drawtext(out, width, height, lines, palette, font_path):
        return {"path": str(out), "width": width, "height": height, "renderer": "ffmpeg_drawtext"}
    _write_stdlib_png(out, width, height, _parse_rgba(palette.get("bg", "#111622E6")), _parse_rgba(palette.get("border", "#D9B460EB")))
    return {"path": str(out), "width": width, "height": height, "renderer": "stdlib_fallback"}


def _rects_overlap(a: dict, b: dict, pad: float = 0.005) -> bool:
    return not (a["x"] + a["w"] + pad <= b["x"] or b["x"] + b["w"] + pad <= a["x"] or a["y"] + a["h"] + pad <= b["y"] or b["y"] + b["h"] + pad <= a["y"])


def _timeline_duration(timeline: dict) -> float:
    return sum(max(0.0, float(c["out"]) - float(c["in"])) for c in timeline.get("video") or [])


def _upsert_overlays(existing: list[dict], new_items: list[dict]) -> list[dict]:
    new_ids = {item["id"] for item in new_items if "id" in item}
    return [o for o in existing if o.get("id") not in new_ids] + list(new_items)


def _apply_canvas_and_subs(updated: dict, theme: dict, *, font_file: str = "") -> None:
    canvas = dict(updated.get("canvas") or {})
    for k in ("width", "height", "backdrop", "viewport"):
        if k in (theme.get("canvas") or {}):
            canvas[k] = copy.deepcopy(theme["canvas"][k])
    canvas.setdefault("width", 1080)
    canvas.setdefault("height", 1920)
    canvas.setdefault("fps", 30)
    updated["canvas"] = canvas
    sub_style = dict(theme.get("subtitle_style") or {})
    sub_style.update(updated.get("subtitle_style") or {})
    if font_file:
        sub_style["font_file"] = str(Path(font_file).expanduser().resolve())
    updated["subtitle_style"] = sub_style


def apply_theme(
    timeline: dict, theme: dict, *, plates_dir: str | Path,
    title: str = "", kicker: str = "", subhead: str = "",
    cards: list[dict] | None = None, callouts: list[dict] | None = None, font_file: str = "",
) -> tuple[dict, dict]:
    """Idempotently apply a JSON theme + generated static PNG plates onto `timeline.json` v3."""
    updated = copy.deepcopy(timeline)
    _apply_canvas_and_subs(updated, theme, font_file=font_file)
    pdir = Path(plates_dir).expanduser().resolve()
    pdir.mkdir(parents=True, exist_ok=True)
    sources, existing_overlays = dict(updated.get("sources") or {}), list(updated.get("overlays") or [])
    slots, palette = theme.get("slots") or {}, theme.get("palette") or {}
    cw, ch, total_s = int(updated["canvas"]["width"]), int(updated["canvas"]["height"]), _timeline_duration(updated) or 10.0
    renderers, warnings, generated = set(), [], []

    def _emit(pid: str, slot_name: str, lines: list[str], start: float, end: float, motion: str = "none", rect_ov: dict | None = None):
        rect = copy.deepcopy(rect_ov or slots.get(slot_name) or {"x": 0.05, "y": 0.05, "w": 0.90, "h": 0.12})
        info = render_plate_png(pdir / f"{pid}.png", max(2, int(round(rect["w"] * cw))), max(2, int(round(rect["h"] * ch))), lines, palette, font_file=font_file)
        renderers.add(info["renderer"])
        sources[pid] = info["path"]
        generated.append({"id": pid, "source": pid, "start": round(start, 3), "end": round(end, 3), "rect": rect,
                          "fade_in": 0.2 if start > 0 else 0.0, "fade_out": 0.2 if end < total_s else 0.0, "motion": motion})

    if title or kicker or subhead:
        _emit("plate_header", "header", [kicker, *title.splitlines(), subhead], 0.0, total_s)
    for i, card in enumerate(cards or []):
        slot = "card_right" if str(card.get("side") or "left") == "right" else "card_left"
        lines = [str(card.get("title") or card.get("name") or card.get("value") or ""), str(card.get("subtitle") or card.get("role") or card.get("label") or "")]
        _emit(f"plate_card_{i + 1}", slot, lines, float(card["start"]), float(card["end"]), str(card.get("motion") or "slide_up"), card.get("rect"))
    for i, item in enumerate(callouts or []):
        slot = str(item.get("slot") or ("callout_upper" if i % 2 == 0 else "callout_lower"))
        _emit(f"plate_callout_{i + 1}", slot, [str(item.get("text") or "")], float(item["start"]), float(item["end"]), str(item.get("motion") or "zoom_in"), item.get("rect"))

    overlays = _upsert_overlays(existing_overlays, generated)
    safe_y = (updated.get("subtitle_style") or {}).get("safe_y")
    sub_rect = {"x": 0.05, "y": float(safe_y[0]), "w": 0.90, "h": max(0.01, float(safe_y[1]) - float(safe_y[0]))} if (isinstance(safe_y, list) and len(safe_y) == 2) else None
    for i, a in enumerate(overlays):
        ra = a.get("rect")
        if not isinstance(ra, dict):
            continue
        if sub_rect and _rects_overlap(ra, sub_rect):
            warnings.append({"code": "overlay_subtitle_overlap", "id": a["id"], "detail": "overlay overlaps subtitle safe_y"})
        for b in overlays[i + 1:]:
            rb = b.get("rect")
            if isinstance(rb, dict) and (min(a["end"], b["end"]) - max(a["start"], b["start"]) > 0.05) and _rects_overlap(ra, rb):
                warnings.append({"code": "overlay_collision", "ids": [a["id"], b["id"]], "detail": "overlays overlap in time and space"})
    if "stdlib_fallback" in renderers:
        warnings.append({"code": "stdlib_plate_fallback", "detail": "No Pillow/ImageMagick/ffmpeg drawtext available; rendered simple RGBA boxes"})
    updated["sources"], updated["overlays"] = sources, overlays
    return updated, {"theme": theme.get("name"), "renderers": sorted(renderers), "overlay_count": len(overlays), "warnings": warnings}


def remotion_init(
    timeline: dict, out_dir: str | Path, *, theme: dict | None = None,
    title: str = "", kicker: str = "", subhead: str = "",
    cards: list[dict] | None = None, callouts: list[dict] | None = None, show_progress: bool = True,
) -> dict:
    """Scaffold a customizable Remotion transparent overlay project from `timeline.json` v3."""
    dest = Path(out_dir).expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)
    for src in _REMOTION_TEMPLATE.rglob("*"):
        if not src.is_dir():
            rel = src.relative_to(_REMOTION_TEMPLATE)
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists() or rel.name != "PackagingOverlay.tsx":
                shutil.copy2(src, target)

    t_canvas, canvas = (theme or {}).get("canvas") or {}, timeline.get("canvas") or {}
    width, height = int(t_canvas.get("width") or canvas.get("width") or 1080), int(t_canvas.get("height") or canvas.get("height") or 1920)
    fps = max(1, round(_parse_fps(canvas.get("fps", 30))))
    clips, cursor = [], 0.0
    for idx, c in enumerate(timeline.get("video") or []):
        dur = max(0.0, float(c["out"]) - float(c["in"]))
        clips.append({"id": str(c.get("id") or f"v{idx + 1}"), "start": round(cursor, 3), "end": round(cursor + dur, 3)})
        cursor += dur
    safe_y = (timeline.get("subtitle_style") or {}).get("safe_y") or ((theme or {}).get("subtitle_style") or {}).get("safe_y") or [0.84, 0.96]
    norm_cards = [{"title": str(c.get("title") or c.get("name") or ""), "subtitle": str(c.get("subtitle") or c.get("role") or ""),
                   "side": str(c.get("side") or "left"), "start": float(c["start"]), "end": float(c["end"]),
                   **({k: c[k] for k in ("kicker", "kind", "rect") if c.get(k)})} for c in (cards or [])]
    norm_callouts = [{"text": str(c.get("text") or ""), "slot": c.get("slot"), "start": float(c["start"]), "end": float(c["end"]),
                      **({k: c[k] for k in ("kicker", "rect") if c.get(k)})} for c in (callouts or [])]
    rem_cfg = (theme or {}).get("remotion") or {}
    palette = dict((theme or {}).get("palette") or {})
    if rem_cfg.get("accent"):
        palette.setdefault("accent", rem_cfg["accent"])
    if rem_cfg.get("text"):
        palette.setdefault("title", rem_cfg["text"])
    effective_progress = bool(show_progress and rem_cfg.get("show_progress_bar", True))
    props = {
        "width": width, "height": height, "fps": fps, "durationInFrames": max(1, round((cursor or 10.0) * fps)),
        "themeName": (theme or {}).get("name") or "custom", "theme": theme or {}, "safeY": safe_y,
        "headerStyle": str(rem_cfg.get("header_style") or "frosted"),
        "viewport": t_canvas.get("viewport") or canvas.get("viewport") or {"x": 0.0, "y": 0.1, "w": 1.0, "h": 0.8},
        "palette": palette, "slots": (theme or {}).get("slots") or {},
        "header": {"kicker": kicker, "title": title, "subhead": subhead} if (title or kicker or subhead) else None,
        "cards": norm_cards, "callouts": norm_callouts, "clips": clips, "showProgress": effective_progress,
    }
    props_path = dest / "src" / "props.json"
    props_path.write_text(json.dumps(props, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"remotion_dir": str(dest), "props": str(props_path), "duration_frames": props["durationInFrames"], "fps": fps}


def remotion_render(
    remotion_dir: str | Path, timeline: dict, *, out_video: str | Path,
    codec: str = "prores", node_modules: str | Path | None = None, runner=subprocess.run,
) -> tuple[dict, dict]:
    """Render transparent PackagingOverlay via Remotion CLI and register it idempotently in `timeline.json`."""
    if codec not in {"prores", "vp9"}:
        raise ValueError(f"codec must be 'prores' or 'vp9'; got {codec!r}")
    npx = shutil.which("npx")
    if not shutil.which("node") or not npx:
        raise RuntimeError("Node.js (`node` and `npx`) is required for remotion-render; install Node or use static `packaging.py apply`.")
    rdir = Path(remotion_dir).expanduser().resolve()
    if not (rdir / "package.json").is_file():
        raise FileNotFoundError(f"remotion project not found (missing package.json): {rdir}")
    nm = rdir / "node_modules"
    if not nm.exists():
        if node_modules and Path(node_modules).expanduser().is_dir():
            os.symlink(str(Path(node_modules).expanduser().resolve()), str(nm))
        else:
            npm = shutil.which("npm")
            if not npm:
                raise RuntimeError("`npm` is required to install Remotion dependencies (or pass --node-modules <cached_dir>).")
            inst = runner([npm, "install", "--no-audit", "--no-fund"], cwd=str(rdir), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
            if inst.returncode != 0:
                raise RuntimeError(f"npm install failed: {(inst.stderr or inst.stdout)[-400:]}")

    out_path = Path(out_video).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    codec_args = (["--codec=prores", "--prores-profile=4444", "--image-format=png", "--pixel-format=yuva444p10le"]
                  if codec == "prores" else ["--codec=vp9", "--image-format=png", "--pixel-format=yuva420p"])
    cmd = [npx, "remotion", "render", "PackagingOverlay", str(out_path), *codec_args, "--overwrite"]
    res = runner(cmd, cwd=str(rdir), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    if res.returncode != 0:
        raise RuntimeError(f"remotion render failed: {(res.stderr or res.stdout)[-400:]}")

    updated = copy.deepcopy(timeline)
    props_file = rdir / "src" / "props.json"
    if props_file.is_file():
        props = json.loads(props_file.read_text(encoding="utf-8"))
        if isinstance(props.get("theme"), dict) and props["theme"]:
            _apply_canvas_and_subs(updated, props["theme"])
    total_s = _timeline_duration(updated) or 10.0
    updated.setdefault("sources", {})["remotion_packaging"] = str(out_path)
    ovl = {"id": "remotion_packaging", "source": "remotion_packaging", "start": 0.0, "end": round(total_s, 3),
           "rect": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}, "fade_in": 0.0, "fade_out": 0.0, "motion": "none"}
    updated["overlays"] = _upsert_overlays(list(updated.get("overlays") or []), [ovl])
    return updated, {"overlay": str(out_path), "codec": codec, "command": cmd}


def _protected_speech_windows(timeline: dict) -> list[tuple[float, float]]:
    wins = [(float(s.get("at", 0.0)), float(s.get("at", 0.0)) + float(s.get("duration") or 0.0))
            for s in ((timeline.get("audio") or {}).get("narration") or []) if float(s.get("duration") or 0.0) > 0]
    cursor = 0.0
    for clip in timeline.get("video") or []:
        c_in, c_out = float(clip["in"]), float(clip["out"])
        for kw in clip.get("keep_windows") or []:
            ks, ke = max(c_in, float(kw[0])), min(c_out, float(kw[1]))
            if ke > ks:
                wins.append((cursor + (ks - c_in), cursor + (ke - c_in)))
        cursor += max(0.0, c_out - c_in)
    return sorted(wins)


def place_sfx(timeline: dict, events: list[dict], *, shift_window_s: float = 0.35) -> tuple[dict, dict]:
    """Place real audio SFX files onto `timeline.audio.sfx`, avoiding protected speech windows."""
    updated = copy.deepcopy(timeline)
    sources, audio = dict(updated.get("sources") or {}), dict(updated.get("audio") or {})
    sfx_list, protected, total_s = list(audio.get("sfx") or []), _protected_speech_windows(updated), _timeline_duration(updated) or 1e9
    placed_log, skipped_log = [], []
    hit_fn = lambda t: next(((s, e) for s, e in protected if s - 0.05 <= t <= e + 0.05), None)  # noqa: E731

    for idx, ev in enumerate(events):
        src_ref = str(ev.get("source") or ev.get("path") or "").strip()
        if not src_ref:
            skipped_log.append({"index": idx, "reason": "missing_source"})
            continue
        at, allow_over, gain, status = float(ev.get("at", 0.0)), bool(ev.get("allow_over_dialogue", False)), float(ev.get("gain", 0.7)), "placed"
        hit = None if allow_over else hit_fn(at)
        if hit is not None:
            safe = next((c for c in (max(0.0, hit[0] - 0.12), min(total_s, hit[1] + 0.12))
                         if abs(c - at) <= shift_window_s and hit_fn(c) is None), None)
            if safe is None:
                skipped_log.append({"index": idx, "source": src_ref, "at": at, "reason": "overlaps_speech"})
                continue
            at, status = round(safe, 3), "shifted"
        sid = str(ev.get("id") or f"sfx_{len(sfx_list) + 1}")
        source_key = src_ref if src_ref in sources else sid
        if source_key == sid:
            sources[sid] = str(Path(src_ref).expanduser().resolve()) if Path(src_ref).expanduser().exists() else src_ref
        entry = {"id": sid, "source": source_key, "at": round(at, 3), "gain": round(gain, 4)}
        sfx_list.append(entry)
        placed_log.append({**entry, "status": status})

    audio["sfx"], updated["audio"], updated["sources"] = sfx_list, audio, sources
    return updated, {"placed": placed_log, "skipped": skipped_log}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Visual packaging, Remotion overlay bridge & speech-safe SFX over timeline.json v3.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list-themes").add_argument("--themes-dir", default=None)
    for cname in ("apply", "remotion-init"):
        p = sub.add_parser(cname)
        p.add_argument("timeline")
        p.add_argument("--theme", required=(cname == "apply"), default=None)
        for opt in ("--themes-dir", "--cards", "--callouts"):
            p.add_argument(opt, default=None)
        for opt in ("--title", "--kicker", "--subhead"):
            p.add_argument(opt, default="")
        if cname == "apply":
            p.add_argument("--font-file", default="")
            p.add_argument("--plates-dir", default=None)
            p.add_argument("--out", default=None)
            p.add_argument("--strict", action="store_true")
        else:
            p.add_argument("--out-dir", required=True)
            p.add_argument("--no-progress", action="store_true")
    p_rr = sub.add_parser("remotion-render")
    p_rr.add_argument("remotion_dir")
    p_rr.add_argument("--timeline", required=True)
    p_rr.add_argument("--codec", choices=("prores", "vp9"), default="prores")
    for opt in ("--out-video", "--node-modules", "--out"):
        p_rr.add_argument(opt, default=None)
    p_sfx = sub.add_parser("place-sfx")
    p_sfx.add_argument("timeline")
    p_sfx.add_argument("--sfx", required=True)
    p_sfx.add_argument("--out", default=None)

    args = ap.parse_args(argv)
    if args.cmd == "list-themes":
        print(json.dumps(list_themes(args.themes_dir), ensure_ascii=False, indent=2))
        return 0
    tl_path = Path(args.timeline).expanduser().resolve()
    tl = json.loads(tl_path.read_text(encoding="utf-8"))
    if args.cmd == "remotion-init":
        theme = load_theme(args.theme, themes_dir=args.themes_dir) if args.theme else None
        cards = json.loads(Path(args.cards).read_text(encoding="utf-8")) if args.cards else None
        callouts = json.loads(Path(args.callouts).read_text(encoding="utf-8")) if args.callouts else None
        print(json.dumps(remotion_init(tl, args.out_dir, theme=theme, title=args.title, kicker=args.kicker,
                                       subhead=args.subhead, cards=cards, callouts=callouts, show_progress=not args.no_progress), ensure_ascii=False, indent=2))
        return 0
    out_p, rc = (Path(args.out).expanduser().resolve() if args.out else tl_path), 0
    if args.cmd == "apply":
        cards = json.loads(Path(args.cards).read_text(encoding="utf-8")) if args.cards else None
        callouts = json.loads(Path(args.callouts).read_text(encoding="utf-8")) if args.callouts else None
        updated, report = apply_theme(tl, load_theme(args.theme, themes_dir=args.themes_dir),
                                      plates_dir=Path(args.plates_dir) if args.plates_dir else (out_p.parent / "plates"),
                                      title=args.title, kicker=args.kicker, subhead=args.subhead, cards=cards, callouts=callouts, font_file=args.font_file)
        rc = 1 if (args.strict and any(w["code"] in {"overlay_collision", "overlay_subtitle_overlap"} for w in report["warnings"])) else 0
    elif args.cmd == "remotion-render":
        ext = "mov" if args.codec == "prores" else "webm"
        out_vid = Path(args.out_video) if args.out_video else (out_p.parent / "media" / "plates" / f"remotion_overlay.{ext}")
        updated, report = remotion_render(args.remotion_dir, tl, out_video=out_vid, codec=args.codec, node_modules=args.node_modules)
    else:
        events = json.loads(Path(args.sfx).read_text(encoding="utf-8"))
        updated, report = place_sfx(tl, events if isinstance(events, list) else events.get("events", []))
    out_p.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return rc


if __name__ == "__main__":
    sys.exit(main())
