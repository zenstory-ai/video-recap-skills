"""Static packaging layers (frame / header / logo images) burned over the whole recap.

The caller writes ``work_dir/packaging_layers.json`` (the orchestrator does so from a bound
``packaging`` template). Each layer is a local image scaled into a rect on a declared
canvas. ffmpeg reads the images with ``movie=`` sources, so the render stays a single-input
video filter; ``timeline.json`` gets matching image segments for editable export.
"""

import json
from pathlib import Path

import lib
from artifacts import file_identity
from visual_render import _escape_subtitle_filter_path

PACKAGING_LAYERS = "packaging_layers.json"
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def load_packaging_layers(work_dir, canvas):
    """Validated layers for this canvas, or [] when the work_dir declares none."""
    path = Path(work_dir) / PACKAGING_LAYERS
    if not path.exists():
        return []
    plan = json.loads(path.read_text(encoding="utf-8"))
    declared = plan["canvas"]
    if (declared["width"], declared["height"]) != (canvas["width"], canvas["height"]):
        raise RuntimeError(
            f"{PACKAGING_LAYERS} 按 {declared['width']}x{declared['height']} 设计，"
            f"成片画布是 {canvas['width']}x{canvas['height']}"
        )
    layers = []
    for layer in plan["layers"]:
        image = Path(layer["path"]).expanduser().resolve()
        rect = layer["rect"]
        if not image.is_file() or image.suffix.lower() not in _IMAGE_EXTS:
            raise RuntimeError(f"包装图层 {layer['name']} 的图片不可用: {image}")
        if (min(rect["x"], rect["y"]) < 0 or rect["width"] <= 0 or rect["height"] <= 0
                or rect["x"] + rect["width"] > canvas["width"]
                or rect["y"] + rect["height"] > canvas["height"]):
            raise RuntimeError(f"包装图层 {layer['name']} 超出画布")
        layers.append({"name": layer["name"], "path": str(image), "rect": dict(rect),
                       "image_size": _image_size(image)})
    return layers


def _image_size(image):
    """Pixel size of a layer image; the editor fits by it, the render stretches to the rect."""
    res = lib.run_cmd(["ffprobe", "-v", "error", "-select_streams", "v:0",
                       "-show_entries", "stream=width,height", "-of", "json", str(image)])
    try:
        stream = json.loads(res.stdout)["streams"][0]
        return {"width": int(stream["width"]), "height": int(stream["height"])}
    except (ValueError, KeyError, IndexError):
        raise RuntimeError(f"无法读取包装图层尺寸: {image}") from None


def compose_video_filter(chain, layers, *, mask_first):
    """Join the existing filter chain, inserting packaging layers after the source mask.

    Order: source-subtitle mask → packaging layers → text overlays / burned subtitles →
    scaling. Without layers this is the plain comma-joined chain.
    """
    if not layers:
        return ",".join(chain)
    head, tail = (chain[:1], chain[1:]) if mask_first else ([], list(chain))
    graph = [
        f"movie=filename='{_escape_subtitle_filter_path(layer['path'])}',"
        f"scale={layer['rect']['width']}:{layer['rect']['height']},format=rgba[pk{i}]"
        for i, layer in enumerate(layers)
    ]
    current = "in"
    if head:
        graph.append(f"[in]{head[0]}[pm]")
        current = "pm"
    for i, layer in enumerate(layers):
        out = "out" if i == len(layers) - 1 and not tail else f"po{i}"
        graph.append(f"[{current}][pk{i}]overlay=x={layer['rect']['x']}:y={layer['rect']['y']}[{out}]")
        current = out
    if tail:
        graph.append(f"[{current}]{','.join(tail)}[out]")
    return ";".join(graph)


def timeline_image_segments(layers, canvas, duration_s):
    """Full-length image segments in the timeline's center-origin, Y-up transform.

    The editor shows an image fitted inside the canvas (keeping its own aspect) at scale 1;
    the render stretches it to the rect, so x and y scales are derived separately.
    """
    width, height = canvas["width"], canvas["height"]
    segments = []
    for layer in layers:
        rect, size = layer["rect"], layer["image_size"]
        fit = min(width / size["width"], height / size["height"])
        segments.append({
            "source_path": layer["path"],
            "timeline_start": 0.0,
            "timeline_end": duration_s,
            "scale": {"x": round(rect["width"] / (size["width"] * fit), 6),
                      "y": round(rect["height"] / (size["height"] * fit), 6)},
            "position": {
                "x": round((rect["x"] + rect["width"] / 2 - width / 2) / (width / 2), 6),
                "y": round((height / 2 - rect["y"] - rect["height"] / 2) / (height / 2), 6),
            },
        })
    return segments


def packaging_settings(work_dir):
    """What the manifest records: the plan file and each layer image's identity."""
    path = Path(work_dir) / PACKAGING_LAYERS if work_dir is not None else None
    if path is None or not path.exists():
        return {"artifact": PACKAGING_LAYERS, "present": False, "layers": []}
    plan = json.loads(path.read_text(encoding="utf-8"))
    layers = []
    for layer in plan["layers"]:
        image = Path(layer["path"]).expanduser().resolve()
        layers.append({"name": layer["name"], "path": str(image), "rect": layer["rect"],
                       **(file_identity(image) if image.is_file() else {})})
    return {"artifact": PACKAGING_LAYERS, "present": True, "identity": file_identity(path),
            "template": plan.get("template"), "layers": layers}
