"""Cross-platform CJK and display font resolution plus @2x Pillow text helpers."""

from __future__ import annotations

import functools
import glob
import shutil
import subprocess
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
except ImportError:  # pragma: no cover - isolated python -I without Pillow
    Image = ImageDraw = ImageFilter = ImageFont = None  # type: ignore[assignment]

_FONT_SEARCH_GLOBS = (
    "/System/Library/AssetsV2/com_apple_MobileAsset_Font7/*/AssetData/*",
    "/System/Library/Fonts/*",
    "/System/Library/Fonts/Supplemental/*",
    "/Library/Fonts/*",
)

_LINUX_CANDIDATES = {
    "sans_heavy": (
        "/usr/share/fonts/google-noto-cjk/NotoSansCJKsc-Black.otf",
        "/usr/share/fonts/google-noto-cjk/NotoSansCJKsc-Bold.otf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ),
    "sans_medium": (
        "/usr/share/fonts/google-noto-cjk/NotoSansCJKsc-Medium.otf",
        "/usr/share/fonts/google-noto-cjk/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ),
    "calligraphy": (
        "/usr/share/fonts/google-noto-cjk/NotoSerifCJKsc-Bold.otf",
        "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
        "/usr/share/fonts/google-noto-cjk/NotoSansCJKsc-Bold.otf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    ),
    "hand": (
        "/usr/share/fonts/google-noto-cjk/NotoSerifCJKsc-SemiBold.otf",
        "/usr/share/fonts/opentype/noto/NotoSerifCJK-SemiBold.ttc",
        "/usr/share/fonts/google-noto-cjk/NotoSansCJKsc-Medium.otf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc",
    ),
    "numeric": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ),
}

_WINDOWS_CANDIDATES = {
    "sans_heavy": ("C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/simhei.ttf"),
    "sans_medium": ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simsun.ttc"),
    "calligraphy": ("C:/Windows/Fonts/STXINGKA.TTF", "C:/Windows/Fonts/simkai.ttf"),
    "hand": ("C:/Windows/Fonts/simkai.ttf", "C:/Windows/Fonts/msyhbd.ttc"),
    "numeric": ("C:/Windows/Fonts/bahnschrift.ttf", "C:/Windows/Fonts/arialbd.ttf"),
}

_MACOS_ROLE_PREFS = {
    "sans_heavy": ("Lantinghei.ttc", "PingFang.ttc", "Hiragino Sans GB.ttc", "STHeiti Medium.ttc"),
    "sans_medium": ("PingFang.ttc", "Hiragino Sans GB.ttc", "STHeiti Medium.ttc"),
    "calligraphy": ("Xingkai.ttc", "WeibeiSC-Bold.otf", "Kaiti.ttc", "Songti.ttc"),
    "hand": ("Hannotate.ttc", "Kaiti.ttc", "PingFang.ttc"),
    "numeric": ("DIN Alternate Bold.ttf", "Avenir Next Condensed.ttc", "Avenir Next.ttc"),
}

_WEIGHT_PREFS = {
    "sans_heavy": ("heavy", "black", "semibold", "demibold", "bold", "w6", "medium"),
    "sans_medium": ("medium", "regular", "w3", "demibold"),
    "calligraphy": ("bold", "black", "semibold", "regular"),
    "hand": ("bold", "regular", "medium"),
    "numeric": ("bold", "heavy", "demi bold", "semibold"),
}


@functools.lru_cache(maxsize=1)
def _macos_font_index() -> dict[str, str]:
    found: dict[str, str] = {}
    home = Path.home()
    patterns = (
        *_FONT_SEARCH_GLOBS,
        str(home / ".fonts/*"),
        str(home / ".local/share/fonts/*"),
    )
    for pattern in patterns:
        for raw in sorted(glob.glob(pattern)):
            name = Path(raw).name
            found.setdefault(name, raw)
    return found


def _best_ttc_index(font_path: str, role: str) -> int:
    if ImageFont is None or not font_path.lower().endswith((".ttc", ".otc")):
        return 0
    prefs = _WEIGHT_PREFS.get(role, ("bold", "regular"))
    best_idx, best_score = 0, -100
    for idx in range(16):
        try:
            face = ImageFont.truetype(font_path, 32, index=idx)
            family, style = (s.lower() for s in face.getname())
        except Exception:  # noqa: BLE001
            break
        if family.startswith("."):
            continue
        score = 0
        if " sc" in family or family.endswith("sc") or "gb" in family:
            score += 40
        elif " tc" in family or " hk" in family or " mo" in family:
            score -= 15
        if "italic" in style or "oblique" in style:
            score -= 25
        for rank, token in enumerate(prefs):
            if token in style:
                score += 30 - rank * 3
                break
        if score > best_score:
            best_score = score
            best_idx = idx
    return best_idx


@functools.lru_cache(maxsize=32)
def resolve_font_spec(role: str = "sans_heavy", explicit_path: str = "") -> tuple[str | None, int]:
    """Return (font_path, face_index) for a typography role, or (None, 0) for PIL fallback."""
    if explicit_path:
        p = Path(explicit_path).expanduser()
        if p.is_file():
            return str(p.resolve()), _best_ttc_index(str(p.resolve()), role)

    mac_map = _macos_font_index()
    for fname in _MACOS_ROLE_PREFS.get(role, _MACOS_ROLE_PREFS["sans_heavy"]):
        if fname in mac_map:
            path = mac_map[fname]
            return path, _best_ttc_index(path, role)

    for cand in _LINUX_CANDIDATES.get(role, ()) + _WINDOWS_CANDIDATES.get(role, ()):
        if Path(cand).is_file():
            return cand, _best_ttc_index(cand, role)

    if shutil.which("fc-match"):
        query = {
            "sans_heavy": "sans-serif:lang=zh-cn:weight=bold",
            "sans_medium": "sans-serif:lang=zh-cn:weight=medium",
            "calligraphy": "serif:lang=zh-cn:weight=bold",
            "hand": "sans-serif:lang=zh-cn",
            "numeric": "sans-serif:weight=bold",
        }.get(role, "sans-serif:lang=zh-cn:weight=bold")
        res = subprocess.run(
            ["fc-match", "-f", "%{file}", query],
            capture_output=True, text=True, timeout=5, check=False,
        )
        matched = (res.stdout or "").strip()
        if matched and Path(matched).is_file():
            return matched, _best_ttc_index(matched, role)

    if role != "sans_heavy":
        return resolve_font_spec("sans_heavy", "")
    return None, 0


@functools.lru_cache(maxsize=32)
def resolve_font_family(role: str = "sans_heavy", explicit_path: str = "") -> tuple[str | None, str | None]:
    """Return (family_name, font_path) suitable for ASS Fontname + libass fontsdir."""
    path, index = resolve_font_spec(role, explicit_path)
    if not path:
        return None, None
    if ImageFont is not None:
        try:
            face = ImageFont.truetype(path, 32, index=index)
            family = str(face.getname()[0] or "").replace(",", " ").strip()
            if family and not family.startswith("."):
                return family, path
        except Exception:  # noqa: BLE001
            pass
    name = Path(path).name
    for token, family in (
        ("NotoSansCJK", "Noto Sans CJK SC"),
        ("NotoSerifCJK", "Noto Serif CJK SC"),
        ("wqy-microhei", "WenQuanYi Micro Hei"),
        ("Lantinghei", "Lantinghei SC"),
        ("PingFang", "PingFang SC"),
        ("Hiragino", "Hiragino Sans GB"),
        ("STHeiti", "Heiti SC"),
        ("DejaVuSans", "DejaVu Sans"),
        ("msyh", "Microsoft YaHei"),
        ("simhei", "SimHei"),
    ):
        if token in name:
            return family, path
    return None, path


@functools.lru_cache(maxsize=128)
def load_font(role: str, size_px: int, explicit_path: str = ""):
    """Load a Pillow font at exact pixel height."""
    if ImageFont is None:
        raise RuntimeError("生成精装包装图层需要安装 Pillow (pip install Pillow)")
    size_px = max(8, int(round(size_px)))
    path, index = resolve_font_spec(role, explicit_path)
    if path:
        try:
            return ImageFont.truetype(path, size_px, index=index)
        except Exception:  # noqa: BLE001
            pass
    try:
        return ImageFont.load_default(size=size_px)
    except TypeError:
        return ImageFont.load_default()


def parse_rgba(color, default_alpha: int = 255) -> tuple[int, int, int, int]:
    """Parse '#RRGGBB', '#RRGGBBAA', or (r,g,b[,a]) into an RGBA tuple."""
    if isinstance(color, (tuple, list)):
        if len(color) == 4:
            return tuple(int(c) for c in color)  # type: ignore[return-value]
        if len(color) == 3:
            return (int(color[0]), int(color[1]), int(color[2]), int(default_alpha))
    raw = str(color or "#FFFFFF").strip().lstrip("#")
    if len(raw) == 6:
        return (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16), int(default_alpha))
    if len(raw) == 8:
        return (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16), int(raw[6:8], 16))
    raise ValueError(f"无法解析颜色值: {color!r}")


def split_highlight_spans(text: str, highlights: list[str] | tuple[str, ...] = ()) -> list[tuple[str, bool]]:
    """Split text into [(substring, is_highlighted)] for up to 2 non-overlapping phrases."""
    clean_hl = [str(h).strip() for h in (highlights or ()) if str(h).strip()]
    if not text or not clean_hl:
        return [(text, False)]
    chosen: list[tuple[int, int]] = []
    for phrase in clean_hl:
        if len(chosen) >= 2:
            break
        pos = text.find(phrase)
        while pos != -1:
            end = pos + len(phrase)
            if all(end <= s or pos >= e for s, e in chosen):
                chosen.append((pos, end))
                break
            pos = text.find(phrase, pos + 1)
    if not chosen:
        return [(text, False)]
    chosen.sort()
    spans: list[tuple[str, bool]] = []
    cursor = 0
    for s, e in chosen:
        if s > cursor:
            spans.append((text[cursor:s], False))
        spans.append((text[s:e], True))
        cursor = e
    if cursor < len(text):
        spans.append((text[cursor:], False))
    return spans


def measure_text(draw: ImageDraw.ImageDraw, text: str, font, stroke_width: int = 0) -> tuple[int, int]:
    """Return (width, height) of single-line text."""
    if not text:
        return 0, 0
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
    return max(0, bbox[2] - bbox[0]), max(0, bbox[3] - bbox[1])


def draw_styled_line(
    base_img: Image.Image,
    x: int,
    y: int,
    text: str,
    font,
    fill: tuple[int, int, int, int],
    *,
    highlights: list[str] | tuple[str, ...] = (),
    highlight_fill: tuple[int, int, int, int] | None = None,
    stroke_width: int = 0,
    stroke_fill: tuple[int, int, int, int] = (0, 0, 0, 220),
    shadow_blur: int = 0,
    shadow_offset: tuple[int, int] = (0, 4),
    shadow_color: tuple[int, int, int, int] = (0, 0, 0, 165),
) -> int:
    """Draw a crisp text line with optional optical soft shadow and inline highlight spans."""
    spans = split_highlight_spans(text, highlights)
    hl_fill = highlight_fill or fill
    if shadow_blur > 0 or any(shadow_offset):
        shadow_layer = Image.new("RGBA", base_img.size, (0, 0, 0, 0))
        sdraw = ImageDraw.Draw(shadow_layer)
        sx = x + shadow_offset[0]
        sy = y + shadow_offset[1]
        for chunk, _is_hl in spans:
            sdraw.text(
                (sx, sy), chunk, font=font, fill=shadow_color,
                stroke_width=stroke_width, stroke_fill=shadow_color,
            )
            sx += int(round(sdraw.textlength(chunk, font=font)))
        if shadow_blur > 0:
            shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=shadow_blur))
        base_img.alpha_composite(shadow_layer)

    draw = ImageDraw.Draw(base_img)
    cx = x
    for chunk, is_hl in spans:
        draw.text(
            (cx, y),
            chunk,
            font=font,
            fill=hl_fill if is_hl else fill,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill if stroke_width > 0 else None,
        )
        cx += int(round(draw.textlength(chunk, font=font)))
    return cx - x
