"""Probe display geometry and select a stable output canvas."""

import json

from frame_grid import canvas_frame_rate, output_frame_rate, parse_frame_rate
from lib import run_cmd


def _has_audio_stream(video_path):
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=index",
        "-of",
        "csv=p=0",
        str(video_path),
    ]
    result = run_cmd(cmd)
    return result.returncode == 0 and bool(result.stdout.strip())


class VideoGeometry(tuple):
    """Tuple-compatible (width, height, fps) with probe facts attached for QC callers."""

    def __new__(cls, width, height, fps, facts):
        obj = super().__new__(cls, (width, height, fps))
        obj.facts = facts
        return obj


def _parse_ratio(value):
    """ffprobe aspect ratios are 'N:D' (or 'N/D'); '0:1' and 'N/A' mean unknown."""
    if value in (None, "", "0:1", "0/1", "N/A"):
        return None
    left, _, right = str(value).replace("/", ":").partition(":")
    num, den = float(left), float(right)
    return num / den if den > 0 and num > 0 else None


def _stream_rotation(stream):
    """Rotation from the legacy `rotate` tag or the display-matrix side data, else 0."""
    tags = stream.get("tags", {})
    if "rotate" in tags:
        return int(round(float(tags["rotate"]))) % 360
    for side_data in stream.get("side_data_list", []):
        if "rotation" in side_data:
            return int(round(float(side_data["rotation"]))) % 360
    return 0


def _fps_from_rate(rate):
    """ffprobe frame rates are 'N/D' fractions; '0/0' means unknown."""
    num, _, den = rate.partition("/")
    return float(num) / float(den) if float(den) > 0 else 0.0


def _frame_rate_text(stream):
    """The stream's real frame grid as an 'N/D' string.

    Interlaced streams often report the field rate as r_frame_rate (50/1 for 25 frames a
    second); when r_frame_rate is exactly twice avg_frame_rate the average is the grid.
    A variable-rate phone clip can report r_frame_rate well above its real average (60/1
    for ~29.6 frames a second); rendering that at 60 fps CFR would double every frame, so
    the common rate nearest the average is used instead, as it is when r_frame_rate is
    unusable (`0/0`) but the average is not.
    """
    r_rate = stream.get("r_frame_rate", "0/0")
    avg = stream.get("avg_frame_rate", "0/0")
    avg_fps = _fps_from_rate(avg)
    if 0 < avg_fps <= 120:
        ratio = _fps_from_rate(r_rate) / avg_fps
        if abs(ratio - 2) < 0.01:
            return avg
        if ratio > 1.5 or parse_frame_rate(r_rate) is None:
            rate = canvas_frame_rate(_fps_bucket(avg_fps))
            return f"{rate.numerator}/{rate.denominator}"
    return r_rate


def _video_start_offset(stream, format_start):
    """Seconds from the file start (ffmpeg's input zero) to the first video frame."""
    try:
        return round(max(0.0, float(stream.get("start_time")) - float(format_start or 0.0)), 6)
    except (TypeError, ValueError):
        return 0.0


def _geometry_from_stream(stream, format_start=0.0):
    coded_width, coded_height = stream["width"], stream["height"]
    parsed_sar = _parse_ratio(stream.get("sample_aspect_ratio"))
    dar = _parse_ratio(stream.get("display_aspect_ratio"))
    rotation = _stream_rotation(stream)
    display_height = float(coded_height)
    if parsed_sar:
        sar = parsed_sar
        display_width = float(coded_width) * sar
        aspect_source = "sample_aspect_ratio"
    elif dar:
        sar = 1.0
        display_width = display_height * dar
        aspect_source = "display_aspect_ratio_fallback"
    else:
        sar = 1.0
        display_width = float(coded_width)
        aspect_source = "square_pixel_fallback"
    rotation_swaps_axes = rotation in {90, 270}
    if rotation_swaps_axes:
        display_width, display_height = display_height, display_width

    width, height = _clamp_even_geometry(round(display_width), round(display_height))
    frame_rate = _frame_rate_text(stream)
    # The canvas fps bucket follows the same frame grid (not an interlaced field rate).
    fps = _fps_from_rate(frame_rate) or _fps_from_rate(stream.get("avg_frame_rate", "0/0"))
    if not 0 < fps <= 120:
        fps = 30.0
    facts = {
        "coded_width": coded_width,
        "coded_height": coded_height,
        "width": width,
        "height": height,
        "fps": round(fps, 3),
        # Exact rate and first-frame time: the grid video-cut snaps clip edges onto.
        "frame_rate": frame_rate,
        "video_start_offset": _video_start_offset(stream, format_start),
        "sample_aspect_ratio": stream.get("sample_aspect_ratio", "1:1"),
        "sample_aspect_ratio_float": round(sar, 6),
        "display_aspect_ratio": stream.get("display_aspect_ratio"),
        "display_aspect_ratio_float": round(dar or 0.0, 6),
        "display_aspect_source": aspect_source,
        "display_width": width,
        "display_height": height,
        "rotation": rotation,
        "rotation_swaps_axes": rotation_swaps_axes,
    }
    return VideoGeometry(width, height, round(fps, 3), facts)


def _probe_video_geometry(video_path):
    """Display geometry (width, height, fps) of the first video stream, rotation/SAR/DAR-aware.

    Unpacks like a 3-tuple while exposing `.facts` for QC. Used to normalize heterogeneous
    multi-source segments to one square-pixel geometry before concat (ffmpeg's concat filter
    rejects mismatched width/height/SAR/pixel-format/fps).
    """
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,avg_frame_rate,start_time,sample_aspect_ratio,"
        "display_aspect_ratio:stream_tags=rotate:stream_side_data=rotation:format=start_time",
        "-of",
        "json",
        str(video_path),
    ]
    result = run_cmd(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe 无法读取视频几何信息: {video_path}: {result.stderr.strip()}")
    payload = json.loads(result.stdout)
    streams = payload.get("streams", [])
    if not streams:
        raise RuntimeError(f"没有视频流: {video_path}")
    return _geometry_from_stream(streams[0], payload.get("format", {}).get("start_time"))


def _orientation(width, height):
    if width > height:
        return "landscape"
    if height > width:
        return "portrait"
    return "square"


def _fps_bucket(fps):
    common = [23.976, 24.0, 25.0, 29.97, 30.0, 50.0, 59.94, 60.0]
    nearest = min(common, key=lambda x: abs(fps - x))
    bucket = nearest if abs(fps - nearest) <= 0.15 else round(fps)
    return max(1.0, min(60.0, bucket))


def _clamp_even_geometry(width, height):
    return max(2, width - width % 2), max(2, height - height % 2)


def _select_output_geometry(source_paths, clips):
    """Deterministically select canvas/fps from all used sources, not just the first."""
    used = {}
    for clip in clips:
        if "source_path" in clip:
            used[clip["source_path"]] = used.get(clip["source_path"], 0.0) + clip["duration"]
    if not used:  # single-source plans carry no per-clip source_path
        used = {str(path): 0.0 for path in source_paths}
    rows = []
    for path in sorted(used):
        probed = _probe_video_geometry(path)
        width, height, fps = probed
        facts = probed.facts
        rows.append(
            {
                "path": path,
                "source_id": next(
                    (c["source_id"] for c in clips if c.get("source_path") == path), None
                ),
                "used_duration": round(used[path], 3),
                "width": width,
                "height": height,
                "coded_width": facts["coded_width"],
                "coded_height": facts["coded_height"],
                "display_width": facts["display_width"],
                "display_height": facts["display_height"],
                "area": width * height,
                "fps": fps,
                "frame_rate": facts["frame_rate"],
                "video_start_offset": facts["video_start_offset"],
                "fps_bucket": _fps_bucket(fps),
                "orientation": _orientation(width, height),
                "rotation": facts["rotation"],
                "sample_aspect_ratio": facts["sample_aspect_ratio"],
                "sample_aspect_ratio_float": facts["sample_aspect_ratio_float"],
                "display_aspect_ratio": facts["display_aspect_ratio"],
                "rotation_swaps_axes": facts["rotation_swaps_axes"],
            }
        )

    orientation_duration = {}
    for row in rows:
        orientation_duration[row["orientation"]] = (
            orientation_duration.get(row["orientation"], 0.0) + row["used_duration"]
        )
    chosen_orientation = sorted(
        orientation_duration.items(),
        key=lambda kv: (
            kv[1],
            max(r["area"] for r in rows if r["orientation"] == kv[0]),
            kv[0],
        ),
        reverse=True,
    )[0][0]
    eligible = [r for r in rows if r["orientation"] == chosen_orientation]
    selected = sorted(
        eligible, key=lambda r: (-r["area"], r["source_id"] or "", r["path"])
    )[0]

    fps_duration = {}
    for row in rows:
        fps_duration[row["fps_bucket"]] = (
            fps_duration.get(row["fps_bucket"], 0.0) + row["used_duration"]
        )
    fps = sorted(fps_duration.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)[0][0]

    width, height = selected["width"], selected["height"]
    reason = {
        "width": width,
        "height": height,
        "fps": round(fps, 3),
        # The exact rate edited_source.mp4 is rendered at (one source keeps its own rate).
        "frame_rate": str(output_frame_rate(rows, fps)),
        "reason": "weighted_orientation_area_fps",
        "source_id": selected["source_id"],
        "source_path": selected["path"],
        "orientation": chosen_orientation,
        "orientation_used_duration": round(orientation_duration[chosen_orientation], 3),
        "fps_bucket_used_duration": round(fps_duration[fps], 3),
        "rotation": selected["rotation"],
        "sample_aspect_ratio": selected["sample_aspect_ratio"],
        "display_aspect_ratio": selected["display_aspect_ratio"],
        "coded_width": selected["coded_width"],
        "coded_height": selected["coded_height"],
        "display_width": selected["display_width"],
        "display_height": selected["display_height"],
        "sources": rows,
    }
    return width, height, round(fps, 3), reason


# Delivered-picture format. `_probe_video_format`, `_output_color_tags`,
# `_color_tag_filter` and `_color_tag_args` are intentional function-level copies shared
# with the final render (tests/orchestrator/test_render_format_parity.py keeps them identical).
_COLOR_FIELDS = (
    ("color_space", "colorspace"),
    ("color_primaries", "color_primaries"),
    ("color_transfer", "color_trc"),
)
_UNTAGGED_COLOR = {None, "", "unknown", "unspecified", "reserved", "N/A"}
# ffprobe's colour space for an RGB picture (PNG, libx264rgb, FFV1 RGB). ffmpeg rejects
# `-colorspace gbr`, and the delivered picture is YUV anyway, so it is never written.
_RGB_COLOR_SPACE = "gbr"
# Pixel-format name prefixes of ffmpeg's RGB family (packed, planar `gbr*`, paletted, Bayer).
# Some RGB sources report no colour space at all (QuickTime RLE `argb`, raw `bgr24`, GIF
# `pal8`), so the pixel format decides too; tests/assemble/test_render_delivery.py checks
# the prefixes against every format a real ffprobe lists.
_RGB_PIX_FMT_PREFIXES = (
    "rgb", "bgr", "gbr", "argb", "abgr", "0rgb", "0bgr", "x2rgb", "x2bgr", "pal8", "bayer_",
)
# Names ffprobe prints that setparams and the -colorspace/-color_primaries/-color_trc
# output options accept and libx264 writes back (tests/assemble/test_render_delivery.py
# runs each through a real ffmpeg); anything else is not written explicitly.
_KNOWN_COLOR_VALUES = {
    "colorspace": {
        "bt709", "fcc", "bt470bg", "smpte170m", "smpte240m", "ycgco", "bt2020nc", "bt2020c",
        "smpte2085", "chroma-derived-nc", "chroma-derived-c", "ictcp",
    },
    "color_primaries": {
        "bt709", "bt470m", "bt470bg", "smpte170m", "smpte240m", "film", "bt2020",
        "smpte428", "smpte431", "smpte432",
    },
    "color_trc": {
        "bt709", "bt470m", "bt470bg", "smpte170m", "smpte240m", "linear", "log100",
        "log316", "iec61966-2-4", "bt1361e", "iec61966-2-1", "bt2020-10", "bt2020-12",
        "smpte2084", "smpte428", "arib-std-b67",
    },
}
# setparams only knows the BT.470 transfers by their ffprobe names, while ffmpeg 8's
# -color_trc output option only knows them as gamma22/gamma28 (ffmpeg 9 takes both).
_COLOR_OPTION_SPELLING = {"color_trc": {"bt470m": "gamma22", "bt470bg": "gamma28"}}


def _probe_video_format(video_path):
    """Codec, pixel format, size and colour tags of the first video stream ({} if unreadable)."""
    result = run_cmd([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries",
        "stream=codec_name,pix_fmt,width,height,color_space,color_primaries,color_transfer,color_range",
        "-of", "json", str(video_path),
    ])
    if result.returncode != 0:
        return {}
    try:
        streams = json.loads(result.stdout).get("streams") or []
    except (ValueError, AttributeError):
        return {}
    return streams[0] if streams and isinstance(streams[0], dict) else {}


def _output_color_tags(stream):
    """Colour tags to write on the delivered picture, as {ffmpeg option: value}.

    An untagged or BT.709 source is labelled BT.709, which is how players already decode
    untagged web video. Any other declared colour space passes through as declared. The
    range follows the source: full range stays `pc`, everything else is limited `tv`.
    For a YUV source nothing here converts pixels; it only fixes the labels.

    An RGB source (colour space `gbr`, or an RGB pixel format with or without a colour
    space) has no YUV matrix to keep: it is converted to BT.709 limited range, and
    `from_rgb` tells `_color_tag_filter` to do that conversion explicitly instead of
    letting ffmpeg pick a matrix.
    """
    declared = {option: stream.get(key) for key, option in _COLOR_FIELDS}
    from_rgb = (
        declared["colorspace"] == _RGB_COLOR_SPACE
        or str(stream.get("pix_fmt") or "").startswith(_RGB_PIX_FMT_PREFIXES)
    )
    if from_rgb:
        declared["colorspace"] = "bt709"
    if all(value in _UNTAGGED_COLOR or value == "bt709" for value in declared.values()):
        tags = {option: "bt709" for _, option in _COLOR_FIELDS}
    else:
        tags = {
            option: value for option, value in declared.items()
            if value in _KNOWN_COLOR_VALUES[option]
        }
    if from_rgb:
        tags["color_range"] = "tv"
        tags["from_rgb"] = True
    else:
        tags["color_range"] = "pc" if stream.get("color_range") == "pc" else "tv"
    return tags


def _color_tag_filter(tags):
    """setparams filter that stamps the tags on every frame before the encoder.

    Output options alone are not enough when re-encoding: ffmpeg 8/9 let the (untagged)
    frame properties win for primaries and transfer, so the bitstream would stay untagged.
    An RGB frame is first converted to BT.709 yuv420p explicitly: stamping a YUV colour
    space on an RGB frame makes ffmpeg's own later conversion use the BT.601 matrix
    while the file says BT.709.
    """
    parts = [f"{option}={tags[option]}" for _, option in _COLOR_FIELDS if option in tags]
    parts.append(f"range={tags['color_range']}")
    setparams = "setparams=" + ":".join(parts)
    if tags.get("from_rgb"):
        return (
            f"scale=out_color_matrix=bt709:out_range={tags['color_range']},"
            f"format=yuv420p,{setparams}"
        )
    return setparams


def _color_tag_args(tags):
    """Output options that write the tags into the container (and encoder) metadata."""
    args = []
    for _, option in _COLOR_FIELDS:
        if option in tags:
            value = _COLOR_OPTION_SPELLING.get(option, {}).get(tags[option], tags[option])
            args += [f"-{option}", value]
    return args + ["-color_range", tags["color_range"]]

