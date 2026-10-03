"""Render edited source media."""

from fractions import Fraction
from pathlib import Path

from frame_grid import frame_count, parse_frame_rate
from lib import CONFIG, filter_file_args, get_video_duration, log, run_cmd

from cut_contract import _write_edited_source_meta
from media_geometry import _has_audio_stream
from sentence_boundaries import _continuous_source_join


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
    join_fade_ms = CONFIG["clip_join_audio_fade_ms"]
    qc["join_fade_ms"] = round(join_fade_ms, 3)

    parts = []
    concat_inputs = []
    extra_inputs = []
    vnorm = ""
    if len(source_paths) > 1:
        # Distinct sources almost always differ in resolution/SAR/fps/pixel-format (and
        # some may lack audio), which the bare concat filter rejects. Normalize every video
        # segment to one canvas and give every clip an audio segment (real or synthesized
        # silence) so concat always succeeds with a continuous track and no source's audio
        # is dropped just because a sibling source is silent.
        canvas_w, canvas_h = geometry_qc["width"], geometry_qc["height"]
        vnorm = (
            f"scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=decrease,"
            f"pad={canvas_w}:{canvas_h}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p,"
        )
    has_audio = len(source_paths) > 1 or all(audio_by_input.values())
    total_frames = 0
    for clip_pos, clip in enumerate(clips):
        idx = clip["clip_id"]
        clip_source = clip.get("source_path", str(input_video))
        input_idx = source_index[clip_source]
        start = clip["source_start"]
        frames = frame_count(clip["duration"], out_rate)
        total_frames += frames
        dur = float(frames / out_rate)
        end = start + dur
        parts.append(_video_segment_filter(
            f"[{input_idx}:v]", f"[v{idx}]", start, end, frames, out_rate,
            source_rates.get(clip_source), vnorm,
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
        str(output_path),
    ]
    result = run_cmd(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"剪辑源视频失败: {result.stderr}")

    _write_edited_source_meta(output_path, validated_plan, input_video)
    duration = get_video_duration(output_path)
    log(f"剪辑源视频: {output_path} ({duration:.1f}s, {len(clips)} clips)")
    return output_path
