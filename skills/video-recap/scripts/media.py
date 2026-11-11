"""Media probing, display geometry, output canvas selection, and colour tags."""

import json
import os
from pathlib import Path

from frame_grid import canvas_frame_rate, output_frame_rate, parse_frame_rate
from lib import CONFIG, log, run_cmd


class VideoGeometry(tuple):
    """Tuple-compatible (width, height, fps) with probe facts attached for QC callers."""

    def __new__(cls, width, height, fps, facts):
        obj = super().__new__(cls, (width, height, fps))
        obj.facts = facts
        return obj


def _plan_clip_spans(work_dir):
    """Cut-mode clip spans [{source_start, source_end, output_start, output_end, entry}], or None.

    Read only clip_plan_validated.json: edited_source.mp4 was rendered from it, and its clips
    carry explicit source/output spans. Without it this is full mode (None = identity mapping).
    A validated plan older than clip_plan.json no longer describes the picture, so it fails.
    """
    work_dir = Path(work_dir)
    validated_path = work_dir / "clip_plan_validated.json"
    if not validated_path.exists():
        return None
    raw_path = work_dir / "clip_plan.json"
    if raw_path.exists() and validated_path.stat().st_mtime_ns < raw_path.stat().st_mtime_ns:
        raise ValueError(
            "clip_plan.json 在剪辑之后被修改，clip_plan_validated.json 已过期；请先重新剪辑再组装"
        )
    plan = json.loads(validated_path.read_text(encoding="utf-8"))
    return [
        {
            "source_start": float(entry["source_start"]),
            "source_end": float(entry["source_end"]),
            "output_start": float(entry["output_start"]),
            "output_end": float(entry["output_end"]),
            "entry": entry,
        }
        for entry in plan["clips"]
    ]


def _ratio_to_float(value, default=1.0):
    """Parse an ffprobe ratio ("4:3", "16/9" or a bare number); unknown ratios yield `default`."""
    value = str(value or "").strip()
    if value in {"", "0:1", "0/1", "N/A"}:
        return default
    if ":" in value:
        num, den = value.split(":", 1)
    elif "/" in value:
        num, den = value.split("/", 1)
    else:
        return float(value)
    return float(num) / float(den) if float(den) else default


def _parse_ratio(value):
    """ffprobe aspect ratios are 'N:D' (or 'N/D'); '0:1' and 'N/A' mean unknown."""
    if value in (None, "", "0:1", "0/1", "N/A"):
        return None
    left, _, right = str(value).replace("/", ":").partition(":")
    num, den = float(left), float(right)
    return num / den if den > 0 and num > 0 else None


def _fps_from_rate(value, default=30.0):
    """Parse an ffprobe frame rate ("30000/1001" or a bare number); a 0/0 rate yields `default`."""
    if "/" in value:
        num, den = value.split("/", 1)
        return round(float(num) / float(den), 3) if float(den) else default
    return round(float(value), 3)


def _raw_fps_from_rate(rate):
    """Unrounded float fps from an ffprobe 'N/D' string; '0/0' yields 0.0."""
    num, _, den = str(rate).partition("/")
    return float(num) / float(den) if den and float(den) > 0 else float(num or 0.0)


def _stream_rotation(stream):
    """Extract rotation from tags or side_data_list in ffprobe JSON."""
    for source in (stream.get("tags", {}).get("rotate"), stream.get("rotation")):
        if source not in (None, ""):
            return int(round(float(source))) % 360
    for item in stream.get("side_data_list", []):
        if item.get("rotation") not in (None, ""):
            return int(round(float(item["rotation"]))) % 360
    return 0


def _canvas_from_stream(stream):
    storage_w = stream["width"]
    storage_h = stream["height"]
    fps = _fps_from_rate(stream["r_frame_rate"])
    sar_text = stream.get("sample_aspect_ratio", "1:1")
    dar_text = stream.get("display_aspect_ratio", "")
    sar = _ratio_to_float(sar_text, 1.0)
    rotation = _stream_rotation(stream)

    display_w = max(1, int(round(storage_w * sar)))
    display_h = max(1, storage_h)
    if dar_text and dar_text not in {"0:1", "N/A"}:
        dar = _ratio_to_float(dar_text, 0.0)
        if dar > 0 and not (rotation in {90, 270} and dar < 1.0):
            display_w = max(1, int(round(display_h * dar)))
    if rotation in {90, 270}:
        display_w, display_h = display_h, display_w

    return {
        "width": display_w,
        "height": display_h,
        "fps": fps,
        "storage_width": storage_w,
        "storage_height": storage_h,
        "rotation": rotation,
        "sample_aspect_ratio": sar_text,
        "display_aspect_ratio": dar_text or f"{display_w}:{display_h}",
    }


def _probe_canvas(video_path):
    """Return rotation/SAR/DAR-aware canvas facts for a video."""
    res = run_cmd([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,avg_frame_rate,sample_aspect_ratio,display_aspect_ratio:stream_tags=rotate:stream_side_data=rotation",
        "-of", "json", str(video_path),
    ])
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe 无法读取视频流 {video_path}: {res.stderr}")
    streams = json.loads(res.stdout)["streams"]
    if not streams:
        raise RuntimeError(f"{video_path} 没有视频流")
    return _canvas_from_stream(streams[0])


def _fps_bucket(fps):
    common = [23.976, 24.0, 25.0, 29.97, 30.0, 50.0, 59.94, 60.0]
    nearest = min(common, key=lambda x: abs(fps - x))
    bucket = nearest if abs(fps - nearest) <= 0.15 else round(fps)
    return max(1.0, min(60.0, bucket))


def _clamp_even_geometry(width, height):
    return max(2, width - width % 2), max(2, height - height % 2)


def _orientation(width, height):
    if width > height:
        return "landscape"
    if height > width:
        return "portrait"
    return "square"


def _frame_rate_text(stream):
    """The stream's real frame grid as an 'N/D' string."""
    r_rate = stream.get("r_frame_rate", "0/0")
    avg = stream.get("avg_frame_rate", "0/0")
    avg_fps = _raw_fps_from_rate(avg)
    if 0 < avg_fps <= 120:
        ratio = _raw_fps_from_rate(r_rate) / avg_fps
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
    fps = _raw_fps_from_rate(frame_rate) or _raw_fps_from_rate(stream.get("avg_frame_rate", "0/0"))
    if not 0 < fps <= 120:
        fps = 30.0
    facts = {
        "coded_width": coded_width,
        "coded_height": coded_height,
        "width": width,
        "height": height,
        "fps": round(fps, 3),
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
    """Display geometry (width, height, fps) of the first video stream, rotation/SAR/DAR-aware."""
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,avg_frame_rate,start_time,sample_aspect_ratio,"
        "display_aspect_ratio:stream_tags=rotate:stream_side_data=rotation:format=start_time",
        "-of", "json", str(video_path),
    ]
    result = run_cmd(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe 无法读取视频几何信息: {video_path}: {result.stderr.strip()}")
    payload = json.loads(result.stdout)
    streams = payload.get("streams", [])
    if not streams:
        raise RuntimeError(f"没有视频流: {video_path}")
    return _geometry_from_stream(streams[0], payload.get("format", {}).get("start_time"))


def _select_output_geometry(source_paths, clips):
    """Deterministically select canvas/fps from all used sources, not just the first."""
    used = {}
    for clip in clips:
        if "source_path" in clip:
            used[clip["source_path"]] = used.get(clip["source_path"], 0.0) + clip["duration"]
    if not used:
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


def _has_audio_stream(video_path):
    """Return True when the input has an audio stream usable as [0:a]."""
    result = run_cmd([
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=index", "-of", "csv=p=0", str(video_path),
    ])
    return result.returncode == 0 and bool(result.stdout.strip())


def _build_video_clips(input_video, work_dir, duration_s):
    """Video-track clips for the timeline."""
    explicit_source_video = CONFIG["source_video"]
    spans = _plan_clip_spans(work_dir)
    multi_source = spans is not None and any(span["entry"].get("source_path") for span in spans)
    if spans is None or not (explicit_source_video or multi_source):
        return [{"source_path": str(input_video), "source_start": 0.0,
                 "source_end": float(duration_s), "timeline_start": 0.0,
                 "timeline_end": float(duration_s)}]
    clips = []
    for span in spans:
        entry = span["entry"]
        source_path = entry.get("source_path") or explicit_source_video
        timeline_start, timeline_end = span["output_start"], span["output_end"]
        if not source_path or not os.path.exists(source_path):
            log(f"  时间线: source_path 不存在，该片段降级为剪后成片片段: {source_path or '(unset)'}")
            clips.append({"source_id": entry.get("source_id"),
                          "source_path": str(input_video),
                          "source_start": timeline_start,
                          "source_end": timeline_end,
                          "timeline_start": timeline_start,
                          "timeline_end": timeline_end,
                          "provenance_degraded": True,
                          "provenance_reason": f"missing_source_path:{source_path or 'unset'}"})
            continue
        clips.append({"source_id": entry.get("source_id"),
                      "source_path": source_path,
                      "source_start": span["source_start"],
                      "source_end": span["source_end"],
                      "timeline_start": timeline_start,
                      "timeline_end": timeline_end})
    return clips


_COLOR_FIELDS = (
    ("color_space", "colorspace"),
    ("color_primaries", "color_primaries"),
    ("color_transfer", "color_trc"),
)
_UNTAGGED_COLOR = {None, "", "unknown", "unspecified", "reserved", "N/A"}
_RGB_COLOR_SPACE = "gbr"
_RGB_PIX_FMT_PREFIXES = (
    "rgb", "bgr", "gbr", "argb", "abgr", "0rgb", "0bgr", "x2rgb", "x2bgr", "pal8", "bayer_",
)
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
    """Colour tags to write on the delivered picture, as {ffmpeg option: value}."""
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
    """setparams filter that stamps the tags on every frame before the encoder."""
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


def _video_copy_safe(stream):
    """True when the source picture can be stream-copied and still match a re-encoded delivery."""
    width, height = stream.get("width"), stream.get("height")
    return (
        stream.get("codec_name") == "h264"
        and stream.get("pix_fmt") in {"yuv420p", "yuvj420p"}
        and isinstance(width, int) and isinstance(height, int)
        and width % 2 == 0 and height % 2 == 0
    )
