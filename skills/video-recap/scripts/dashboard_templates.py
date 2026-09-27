"""Template parameters as labelled rows plus a schematic preview, for the read-only dashboard.

``param_rows`` turns a template's ``params`` into ``{key, label, value, unit, provenance}``
rows the UI shows as a table; anything it does not know stays in the raw JSON only.
``template_preview`` returns the geometry for a scaled mini canvas: the subtitle band with
one line of exactly ``max_chars`` characters (the "calibrate with the longest line" check),
or the packaging layers at their rects plus the safe area. It is a schematic, not a render.
"""
from __future__ import annotations

import re
from pathlib import Path

from dashboard_io import media_entry

PROVENANCE_LABELS = {"measured": "实测", "fitted": "调出", "specified": "指定", "unknown": "未知"}
SIDE_MARGIN_PX = 40  # video-assemble's default left/right subtitle margin; library.py checks against it
SAMPLE_TEXT = "这是按每行字数校准的最长一行字幕示意"
SUBTITLE_ROWS = (
    ("font", "字体", ""),
    ("size_px", "字号", "px"),
    ("max_chars", "每行字数", "字"),
    ("outline_px", "描边", "px"),
    ("shadow_px", "阴影", "px"),
    ("primary_color", "主色", ""),
    ("outline_color", "描边色", ""),
    ("max_lines", "最多行数", "行"),
    ("band", "字幕带", "px"),
)
ASS_COLOR_RE = re.compile(r"^&H([0-9A-Fa-f]{2})?([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})&?$")


def ass_to_hex(value) -> str | None:
    """``#RRGGBB`` for an ASS ``&HAABBGGRR`` / ``&HBBGGRR`` colour, else None."""
    match = ASS_COLOR_RE.match(str(value or "").strip())
    if not match:
        return None
    _, blue, green, red = match.groups()
    return f"#{red}{green}{blue}".upper()


def _number(params: dict, key: str):
    node = params.get(key)
    value = node.get("value") if isinstance(node, dict) else None
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _band(params: dict):
    node = params.get("band")
    value = node.get("value") if isinstance(node, dict) else None
    if isinstance(value, dict) and all(isinstance(value.get(k), (int, float)) for k in ("y_top", "y_bot")):
        return value
    return None


def _rect_text(rect) -> str:
    if not isinstance(rect, dict):
        return "—"
    return f"x {rect.get('x')} · y {rect.get('y')} · {rect.get('width')}×{rect.get('height')}"


def _row(key, label, value, unit, node, swatch=None) -> dict:
    provenance = node.get("provenance") if isinstance(node, dict) else None
    return {"key": key, "label": label, "value": value, "unit": unit, "swatch": swatch,
            "provenance": provenance, "provenance_label": PROVENANCE_LABELS.get(provenance, "")}


def param_rows(kind: str, params, resources: dict) -> list:
    """Known parameters as display rows; ``resources`` maps id -> {title, kind}."""
    if not isinstance(params, dict):
        return []
    rows = []
    if kind == "subtitle_style":
        for key, label, unit in SUBTITLE_ROWS:
            node = params.get(key)
            if node is None:
                continue
            if key == "font":
                ref = node.get("resource") if isinstance(node, dict) else None
                value = (f"{resources[ref]['title']}（{ref}）" if ref in resources else ref) if ref else (node or {}).get("family")
                rows.append(_row(key, label, str(value or "—"), "", node))
            elif key == "band":
                band = _band(params)
                value = f"y {band['y_top']}–{band['y_bot']}" if band else "—"
                rows.append(_row(key, label, value, unit if band else "", node))
            elif key.endswith("_color"):
                raw = node.get("value") if isinstance(node, dict) else node
                rows.append(_row(key, label, str(raw), "", node, ass_to_hex(raw)))
            else:
                value = node.get("value") if isinstance(node, dict) else node
                rows.append(_row(key, label, str(value), unit, node))
    elif kind == "packaging":
        layers = params.get("layers") if isinstance(params.get("layers"), list) else []
        rows.append(_row("layers", "图层", str(len(layers)), "层", None))
        if "safe_rect" in params:
            rows.append(_row("safe_rect", "安全区", _rect_text(params["safe_rect"]), "px", params["safe_rect"]))
    return rows


def _pct(value, total) -> float:
    return round(value / total * 100, 3) if total else 0.0


def _subtitle_preview(canvas: dict, params: dict) -> dict:
    width, height = canvas["width"], canvas["height"]
    size, per_line = _number(params, "size_px"), _number(params, "max_chars")
    band = _band(params)
    chars = int(per_line) if per_line else 0
    sample = (SAMPLE_TEXT * (chars // len(SAMPLE_TEXT) + 1))[:chars]
    margin_v = height - band["y_bot"] if band else None
    return {
        "type": "subtitle_style", "canvas": {"width": width, "height": height},
        "band": ({"top": _pct(band["y_top"], height), "height": _pct(band["y_bot"] - band["y_top"], height)}
                 if band else None),
        "line": {"text": sample, "chars": chars,
                 "font_size": _pct(size, width) if size else None,  # percent of canvas width
                 "bottom": _pct(margin_v, height) if margin_v is not None else None,
                 "width_px": size * chars if size and chars else None,
                 "usable_px": width - 2 * SIDE_MARGIN_PX},
        "band_px": band, "margin_v": margin_v, "side": _pct(SIDE_MARGIN_PX, width),
    }


def _box(rect, canvas) -> dict | None:
    if not isinstance(rect, dict) or not all(isinstance(rect.get(k), (int, float)) for k in ("x", "y", "width", "height")):
        return None
    return {"left": _pct(rect["x"], canvas["width"]), "top": _pct(rect["y"], canvas["height"]),
            "width": _pct(rect["width"], canvas["width"]), "height": _pct(rect["height"], canvas["height"])}


def _packaging_preview(root: Path, canvas: dict, params: dict, resources: dict) -> dict:
    layers = []
    for layer in params.get("layers") if isinstance(params.get("layers"), list) else []:
        if not isinstance(layer, dict):
            continue
        ref = (layer.get("image") or {}).get("resource") if isinstance(layer.get("image"), dict) else None
        resource = resources.get(ref) or {}
        files = resource.get("files") or []
        layers.append({"name": layer.get("name"), "rect": layer.get("rect"), "rect_text": _rect_text(layer.get("rect")),
                       "box": _box(layer.get("rect"), canvas), "resource": ref, "title": resource.get("title"),
                       "media": media_entry(root, Path(files[0]["path"])) if files else None})
    return {"type": "packaging", "canvas": {"width": canvas["width"], "height": canvas["height"]},
            "safe": _box(params.get("safe_rect"), canvas), "layers": layers}


def template_preview(root: Path, kind: str, canvas, params, resources: dict) -> dict | None:
    """Geometry for the mini canvas, in percent of the template canvas; None when unusable."""
    if not (isinstance(canvas, dict) and isinstance(params, dict)
            and all(isinstance(canvas.get(k), int) and canvas[k] > 0 for k in ("width", "height"))):
        return None
    if kind == "subtitle_style":
        return _subtitle_preview(canvas, params)
    if kind == "packaging":
        return _packaging_preview(root, canvas, params, resources)
    return None
