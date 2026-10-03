"""Media probing and source-clip provenance for video-assemble."""

import json
import os
from pathlib import Path

from lib import CONFIG, log, run_cmd

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
            "source_start": float(entry["source_start"]), "source_end": float(entry["source_end"]),
            "output_start": float(entry["output_start"]), "output_end": float(entry["output_end"]),
            "entry": entry,
        }
        for entry in plan["clips"]
    ]


def _ratio_to_float(value, default=1.0):
    """Parse an ffprobe ratio ("4:3", "16/9" or a bare number); unknown ratios yield `default`."""
    value = value.strip()
    if value in {"", "0:1", "0/1", "N/A"}:
        return default
    if ":" in value:
        num, den = value.split(":", 1)
    elif "/" in value:
        num, den = value.split("/", 1)
    else:
        return float(value)
    return float(num) / float(den) if float(den) else default


def _fps_from_rate(value, default=30.0):
    """Parse an ffprobe frame rate ("30000/1001" or a bare number); a 0/0 rate yields `default`."""
    if "/" in value:
        num, den = value.split("/", 1)
        return round(float(num) / float(den), 3) if float(den) else default
    return round(float(value), 3)


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
        # ffprobe sources are not consistent: some report DAR before rotation
        # (landscape value > 1 for a 90° stream), while some containers report the
        # already-rotated portrait DAR (< 1). Only apply DAR before swapping when it
        # describes the stored orientation.
        if dar > 0 and not (rotation in {90, 270} and dar < 1.0):
            # Preserve height and adjust width. This keeps legacy square-pixel landscape
            # byte-identical while honoring non-square pixel DAR metadata.
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
    """Return rotation/SAR/DAR-aware canvas facts for a video.

    ``width``/``height`` are the display canvas used by subtitle/overlay geometry.
    For legacy square-pixel landscape sources, these remain the raw storage dimensions.
    """
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


def _has_audio_stream(video_path):
    """Return True when the input has an audio stream usable as [0:a]."""
    result = run_cmd([
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=index", "-of", "csv=p=0", str(video_path),
    ])
    return result.returncode == 0 and bool(result.stdout.strip())


def _build_video_clips(input_video, work_dir, duration_s):
    """Video-track clips for the timeline.

    In cut mode each plan entry becomes a clip referencing the ORIGINAL source
    range. Multi-source validated plans carry per-clip source_path and do not
    require --source-video. Without any declared source (full
    mode, or cut mode rendered without --source-video) the rendered input is one clip.
    """
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
            # Degrade ONLY this clip — point it at the rendered cut for its own output
            # window — and keep real provenance for every present source, instead of
            # collapsing the whole multi-source timeline.
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


# Delivered-picture format. `_probe_video_format`, `_output_color_tags`,
# `_color_tag_filter` and `_color_tag_args` are intentional function-level copies shared
# with the cut render (tests/orchestrator/test_render_format_parity.py keeps them identical).
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


def _video_copy_safe(stream):
    """True when the source picture can be stream-copied and still match a re-encoded
    delivery: H.264, 8-bit 4:2:0 (yuvj420p is the same layout in full range), even size."""
    width, height = stream.get("width"), stream.get("height")
    return (
        stream.get("codec_name") == "h264"
        and stream.get("pix_fmt") in {"yuv420p", "yuvj420p"}
        and isinstance(width, int) and isinstance(height, int)
        and width % 2 == 0 and height % 2 == 0
    )
