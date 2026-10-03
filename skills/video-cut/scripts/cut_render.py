"""Render edited source media."""

from fractions import Fraction
from pathlib import Path

from frame_grid import frame_count, parse_frame_rate
from lib import CONFIG, filter_file_args, get_video_duration, log, run_cmd

from cut_contract import _write_edited_source_meta
from media_geometry import (
    _color_tag_args,
    _color_tag_filter,
    _has_audio_stream,
    _output_color_tags,
    _probe_video_format,
)
from sentence_gate import _continuous_source_join


def _audio_segment_filter(
    label_in, label_out, start, end, duration, fade_in_ms, fade_out_ms, extra_filters=""
):
    max_fade = duration / 2
    fade_in = max(0.0, min(fade_in_ms / 1000.0, max_fade))
    fade_out = max(0.0, min(fade_out_ms / 1000.0, max_fade))
    base = f"{label_in}atrim=start={start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS"
    if fade_in > 0:
        base += f",afade=t=in:st=0:d={fade_in:.3f}"
    if fade_out > 0:
        base += f",afade=t=out:st={max(0.0, duration - fade_out):.3f}:d={fade_out:.3f}"
    if extra_filters:
        base += f",{extra_filters}"
    return f"{base}{label_out}"


def _clip_audio_edge_fades(clips, idx, fade_ms):
    """Do not create an audible dip where adjacent clips are a lossless source continuation."""
    fade_in = (
        0.0
        if idx > 0 and _continuous_source_join(clips[idx - 1], clips[idx])
        else fade_ms
    )
    fade_out = (
        0.0
        if idx + 1 < len(clips) and _continuous_source_join(clips[idx], clips[idx + 1])
        else fade_ms
    )
    return fade_in, fade_out


def _video_segment_filter(label_in, label_out, start, end, frames, out_rate, source_rate, norm=""):
    """Exactly `frames` frames at `out_rate` from source [start, end).

    The trim points sit half a source frame early so the frame ON a grid edge is selected
    despite millisecond rounding or container pts jitter. fps resamples onto the output
    grid; tpad clones the last frame without limit, covering a segment that came up short
    (one frame from resampling, or many when the clip runs past the end of a video stream
    shorter than its audio); and the final trim cuts to the exact count, so every segment
    advances the concat by whole frames and edited_source.mp4 stays constant frame rate.
    """
    half = 0.5 / source_rate if source_rate else 0.0
    return (
        f"{label_in}trim=start={max(0.0, start - half):.6f}:end={end - half:.6f},"
        f"setpts=PTS-STARTPTS,{norm}fps={out_rate},tpad=stop_mode=clone:stop=-1,"
        f"trim=end_frame={frames},setpts=PTS-STARTPTS{label_out}"
    )


def _warn_on_frame_count_mismatch(path, expected):
    """Log when the encoded video holds a different frame count than qc.frame_grid records.

    The exact count rests on fps/tpad/trim behaviour verified on ffmpeg 8 and 9; an older
    ffmpeg that behaves differently would otherwise leave a short or VFR file unnoticed.
    A warning, not a block: final_qc owns blockers.
    """
    result = run_cmd([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
        "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(path),
    ])
    try:
        rendered = int(str(result.stdout).strip())
    except (AttributeError, ValueError):
        return
    if rendered != expected:
        log(f"警告: {path.name} 渲染出 {rendered} 帧，"
            f"qc.frame_grid.frame_count 记录的是 {expected} 帧；"
            "当前 ffmpeg 的 fps/tpad/trim 行为可能不同，请检查帧率是否恒定")


def _edited_source_color_tags(source_formats, *, rgb_converted_per_clip=False):
    """One set of colour tags for the concatenated picture, from each source's probe.

    Sources that agree keep their shared tags; sources that disagree cannot be described by
    one label, so the picture is BT.709 limited, the same default an untagged source gets,
    and `_clip_color_filter` converts every clip into it. With `rgb_converted_per_clip` the
    RGB sources (by colour space or pixel format) are converted to BT.709 limited yuv420p
    clip by clip, so their tags lose `from_rgb` and match a BT.709 limited YUV source.
    """
    tags = [_output_color_tags(fmt) for fmt in source_formats]
    if rgb_converted_per_clip:
        tags = [{k: v for k, v in item.items() if k != "from_rgb"} for item in tags]
    if all(item == tags[0] for item in tags):
        return tags[0]
    log("剪辑源视频: 各来源色彩标记不一致，各段统一转换为 BT.709 limited")
    return _output_color_tags({})


# ffprobe colour-space names the scale filter can convert from/to (in/out_color_matrix).
_SCALE_COLOR_MATRIX = {
    "bt709": "bt709", "bt470bg": "bt470", "smpte170m": "smpte170m", "fcc": "fcc",
    "smpte240m": "smpte240m", "bt2020nc": "bt2020",
}


def _clip_color_filter(source_format, target_tags):
    """Per-clip conversion of one source's picture into the shared yuv420p picture.

    The conversion is explicit for every source: the concat filter needs one pixel format,
    and ffmpeg 8 also negotiates one colour space and range across its inputs, silently
    converting the others with its own pick while the stamped label says something else.
    An RGB source converts with the target's matrix; a YUV source converts from its own
    matrix and range (untagged counts as BT.709, the label it would get alone). Only the
    matrix and range are converted; primaries and transfer are relabelled, not remapped.
    """
    tags = _output_color_tags(source_format)
    out_matrix = _SCALE_COLOR_MATRIX.get(target_tags.get("colorspace"))
    out_range = target_tags["color_range"]
    if tags.get("from_rgb"):
        return f"scale=out_color_matrix={out_matrix or 'bt709'}:out_range={out_range},format=yuv420p,"
    in_matrix = _SCALE_COLOR_MATRIX.get(tags.get("colorspace"))
    if in_matrix and out_matrix:
        return (
            f"scale=in_color_matrix={in_matrix}:in_range={tags['color_range']}"
            f":out_color_matrix={out_matrix}:out_range={out_range},format=yuv420p,"
        )
    if tags != target_tags:
        log(f"剪辑源视频: 色彩空间 {tags.get('colorspace')} 无法转换为 "
            f"{target_tags.get('colorspace')}，该来源只改标记")
    return "format=yuv420p,"


def build_edited_source_video(input_video, validated_plan, work_dir, output_path=None):
    """Build `edited_source.mp4` by concatenating validated source ranges.

    `validated_plan["qc"]["output_geometry"]` is required: the caller (cut_cli, or any
    public user of this API) selects the canvas with `_select_output_geometry` first, so the
    same geometry is recorded in clip_plan_validated.json and used for the render. Each clip
    renders `frame_count(duration, output frame_rate)` frames and exactly that much audio."""
    work_dir = Path(work_dir)
    output_path = Path(output_path or work_dir / "edited_source.mp4")
    clips = validated_plan["clips"]
    qc = validated_plan["qc"]
    if "output_geometry" not in qc:
        raise KeyError(
            "validated_plan['qc']['output_geometry'] is required: select the canvas with "
            "media_geometry._select_output_geometry before build_edited_source_video"
        )
    geometry_qc = qc["output_geometry"]
    out_rate = Fraction(geometry_qc["frame_rate"])
    source_rates = {
        row["path"]: parse_frame_rate(row.get("frame_rate")) for row in geometry_qc["sources"]
    }

    source_paths = []
    for clip in clips:
        source_path = clip.get("source_path", str(input_video))
        if source_path not in source_paths:
            source_paths.append(source_path)
    source_index = {path: idx for idx, path in enumerate(source_paths)}
    audio_by_input = {path: _has_audio_stream(path) for path in source_paths}
    format_by_input = {path: _probe_video_format(path) for path in source_paths}
    join_fade_ms = CONFIG["clip_join_audio_fade_ms"]
    qc["join_fade_ms"] = round(join_fade_ms, 3)

    color_tags = _edited_source_color_tags(
        [format_by_input[path] for path in source_paths],
        rgb_converted_per_clip=len(source_paths) > 1,
    )
    parts = []
    concat_inputs = []
    extra_inputs = []
    vnorm_by_input = {}
    if len(source_paths) > 1:
        # Distinct sources almost always differ in resolution/SAR/fps/pixel-format (and
        # some may lack audio), which the bare concat filter rejects. Normalize every video
        # segment to one canvas and give every clip an audio segment (real or synthesized
        # silence) so concat always succeeds with a continuous track and no source's audio
        # is dropped just because a sibling source is silent.
        canvas_w, canvas_h = geometry_qc["width"], geometry_qc["height"]
        vnorm = (
            f"scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=decrease,"
            f"pad={canvas_w}:{canvas_h}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
        )
        # Each clip becomes yuv420p in the shared colour space and range here, before the
        # shared tags are stamped; left to `format=yuv420p` alone, ffmpeg picks the matrix
        # (BT.601 for RGB) while the file is labelled with the shared tags.
        vnorm_by_input = {
            path: vnorm + _clip_color_filter(fmt, color_tags)
            for path, fmt in format_by_input.items()
        }
    has_audio = len(source_paths) > 1 or all(audio_by_input.values())
    total_frames = 0
    end = None
    for clip_pos, clip in enumerate(clips):
        idx = clip["clip_id"]
        clip_source = clip.get("source_path", str(input_video))
        input_idx = source_index[clip_source]
        # A lossless join starts exactly where the previous clip's render ended, not at the
        # plan's millisecond-rounded copy of it, so the two atrims stay sample-contiguous.
        joined = clip_pos > 0 and _continuous_source_join(clips[clip_pos - 1], clip)
        start = end if joined else clip["source_start"]
        frames = clip.get("frame_count") or frame_count(clip["duration"], out_rate)
        total_frames += frames
        dur = float(frames / out_rate)
        end = start + dur
        parts.append(_video_segment_filter(
            f"[{input_idx}:v]", f"[v{idx}]", start, end, frames, out_rate,
            source_rates.get(clip_source), vnorm_by_input.get(clip_source, ""),
        ))
        concat_inputs.append(f"[v{idx}]")
        if not has_audio:
            continue
        if audio_by_input[clip_source]:
            fade_in_ms, fade_out_ms = _clip_audio_edge_fades(clips, clip_pos, join_fade_ms)
            parts.append(
                _audio_segment_filter(
                    f"[{input_idx}:a]",
                    f"[a{idx}]",
                    start,
                    end,
                    dur,
                    fade_in_ms,
                    fade_out_ms,
                    extra_filters=(
                        "aresample=48000,aformat=sample_rates=48000:channel_layouts=stereo"
                        if len(source_paths) > 1 else ""
                    ),
                )
            )
        else:
            parts.append(
                f"anullsrc=r=48000:cl=stereo,atrim=duration={dur:.6f},asetpts=PTS-STARTPTS,"
                f"aformat=sample_rates=48000:channel_layouts=stereo[a{idx}]"
            )
        concat_inputs.append(f"[a{idx}]")

    if has_audio:
        parts.append("".join(concat_inputs) + f"concat=n={len(clips)}:v=1:a=1[v][a]")
        maps = ["-map", "[v]", "-map", "[a]"]
    else:
        parts.append("".join(concat_inputs) + f"concat=n={len(clips)}:v=1:a=0[v]")
        maps = ["-map", "[v]", "-map", f"{len(source_paths)}:a", "-shortest"]
        extra_inputs = [
            "-f",
            "lavfi",
            "-t",
            f"{float(total_frames / out_rate):.6f}",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
        ]

    parts.append(f"[v]{_color_tag_filter(color_tags)}[vtagged]")
    maps[maps.index("[v]")] = "[vtagged]"
    filter_complex = ";".join(parts)
    if len(filter_complex.encode("utf-8")) > 7000:
        filter_script = work_dir / "edit_filter_complex.txt"
        filter_script.write_text(filter_complex, encoding="utf-8")
        filter_args = filter_file_args("filter_complex", filter_script)
    else:
        filter_args = ["-filter_complex", filter_complex]

    input_args = []
    for source_path in source_paths:
        input_args.extend(["-i", str(source_path)])
    cmd = [
        "ffmpeg",
        "-y",
        *input_args,
        *extra_inputs,
        *filter_args,
        *maps,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        # The source's container/stream tags (a scraper's title, comment or URL) and its
        # chapters would otherwise be copied into edited_source.mp4 and on into the recap.
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        *_color_tag_args(color_tags),
        str(output_path),
    ]
    result = run_cmd(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"剪辑源视频失败: {result.stderr}")

    _warn_on_frame_count_mismatch(output_path, total_frames)
    _write_edited_source_meta(output_path, validated_plan, input_video)
    duration = get_video_duration(output_path)
    log(f"剪辑源视频: {output_path} ({duration:.1f}s, {len(clips)} clips)")
    return output_path
