"""Picture-only FFmpeg execution for validated CFR frame plans."""

from fractions import Fraction
import json
from pathlib import Path
import re
import subprocess

from shot_review import probe_frame_clock, sha256_file


def crop_expression(values):
    """Balanced decisions over exact per-frame samples, no arbitrary filter input."""
    runs = [(0, values[0])]
    for frame, value in enumerate(values[1:], 1):
        if value != runs[-1][1]:
            runs.append((frame, value))

    def build(rows):
        if len(rows) == 1:
            return str(rows[0][1])
        middle = len(rows) // 2
        return (
            f"if(lt(n,{rows[middle][0]}),{build(rows[:middle])},{build(rows[middle:])})"
        )

    return build(runs)


def decoded_selection(log):
    if "showinfo@source" in log:
        log = "\n".join(line for line in log.splitlines() if "showinfo@source" in line)
    bases = set(re.findall(r"config in time_base:\s*([0-9]+/[0-9]+)", log))
    if len(bases) != 1:
        raise ValueError("missing or ambiguous decoded selection time base")
    tb = Fraction(bases.pop())
    return [
        int(v) * tb for v in re.findall(r"\bn:\s*\d+\s+pts:\s*(-?\d+)\s+pts_time:", log)
    ]


def _frame_checksums(log, marker):
    return re.findall(
        r"\bchecksum:([0-9A-F]+)\s+plane_checksum:\[([^]]+)\]",
        "\n".join(line for line in log.splitlines() if f"showinfo@{marker}" in line),
    )


def _resampling_filter(shot, fps):
    start, stop = shot["source_frames"]
    frame_map = shot["source_frame_by_output"]
    if shot["mapping_kind"] == "implicit_identity":
        return "showinfo,setpts=PTS-STARTPTS,"
    # shuffleframes buffers one block. Dummy slots are dropped, and input padding
    # is never addressable by the validated source map. No optical-flow synthesis.
    source_count = stop - start
    output_count = len(frame_map)
    block_size = max(source_count, output_count)
    indexes = [str(n - start) for n in frame_map] + ["-1"] * (block_size - output_count)
    padding = (
        f"tpad=stop_mode=clone:stop={output_count - source_count},"
        if output_count > source_count
        else ""
    )
    return (
        "showinfo@source,setpts=PTS-STARTPTS,"
        f"{padding}shuffleframes={' '.join(indexes)},"
        f"settb={1 / fps},setpts=N,showinfo@mapped,"
    )


def _run(command, log_path):
    Path(str(log_path) + ".command.json").write_text(
        json.dumps(command, indent=2) + "\n"
    )
    result = subprocess.run(command, capture_output=True, text=True, timeout=600)
    log_path.write_text(result.stderr)
    if result.returncode:
        raise RuntimeError(f"picture render failed; see {log_path}")
    return result.stderr


def _probe_output(path, expected_frames, fps, canvas, *, require_color=True):
    pts, end, origin = probe_frame_clock(path)
    if (
        origin != 0
        or len(pts) != expected_frames
        or end != Fraction(expected_frames, fps)
        or any(t != Fraction(i, fps) for i, t in enumerate(pts))
    ):
        raise ValueError("rendered picture frame clock/count mismatch")
    data = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    streams = json.loads(data.stdout)["streams"]
    if len(streams) != 1 or streams[0]["codec_type"] != "video":
        raise ValueError(
            "picture-only output unexpectedly contains audio/extra streams"
        )
    v = streams[0]
    if [v["width"], v["height"]] != canvas or v.get("pix_fmt") != "yuv420p":
        raise ValueError("rendered geometry/pixel format mismatch")
    expected_color = {
        "color_space": "bt709",
        "color_transfer": "bt709",
        "color_primaries": "bt709",
        "color_range": "tv",
    }
    if require_color and any(v.get(k) != value for k, value in expected_color.items()):
        raise ValueError("rendered color metadata mismatch")
    return {
        "sha256": sha256_file(path),
        "frames": len(pts),
        "fps": str(fps),
        "canvas": canvas,
        "duration_exact": str(end),
        "color": {k: v.get(k) for k in expected_color},
        "pixel_format": "yuv420p",
        "decode": "PASS",
        "geometry_algorithm": "crop-scale-rgba-pad-bt709-yuv420p-v1",
    }


def render_picture(plan, facts, clocks, directory, *, crf):
    fps = Fraction(plan["fps"])
    directory = Path(directory)
    chunks = directory / "chunks"
    chunks.mkdir()
    logs = directory / "logs"
    logs.mkdir()
    color_args = [
        "-colorspace",
        "bt709",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-color_range",
        "tv",
    ]
    cw, ch = plan["canvas"]
    paths = []
    records = []
    for i, shot in enumerate(plan["shots"]):
        source = facts[shot["source_id"]]
        start, stop = shot["source_frames"]
        expected = clocks[shot["source_id"]][start:stop]
        tb = Fraction(source["time_base"])
        start_tick = expected[0] / tb
        end_pts = (
            clocks[shot["source_id"]][stop]
            if stop < source["frame_count"]
            else Fraction(source["duration"])
        )
        end_tick = end_pts / tb
        if start_tick.denominator != 1 or end_tick.denominator != 1:
            raise ValueError("source frame clock not representable in stream timebase")
        # Input seek is only a speed optimization. Round DOWN to microseconds so
        # accurate_seek cannot discard the requested first frame. Then prove all PTS.
        micros = int(expected[0] * 1_000_000)
        seek = f"{micros // 1_000_000}.{micros % 1_000_000:06d}"
        c = shot["crop"]
        wx, wy, ww, wh = shot["window"]
        x = crop_expression(shot["crop_x_by_frame"])
        graph = (
            f"[0:v:0]trim=start_pts={start_tick.numerator}:end_pts={end_tick.numerator},"
            f"{_resampling_filter(shot, fps)}crop={c['width']}:{c['height']}:x='{x}':y={c['y']},"
            f"scale={ww}:{wh}:flags=lanczos,setsar=1,format=rgba,"
            f"pad={cw}:{ch}:{wx}:{wy}:black,scale=out_color_matrix=bt709:out_range=tv,"
            "format=yuv420p[v]"
        )
        filter_path = chunks / f"{i:04}.ffscript"
        filter_path.write_text(graph)
        out = chunks / f"{i:04}.mov"
        command = [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-v",
            "info",
            "-xerror",
            "-threads",
            "2",
            "-copyts",
            "-ss",
            seek,
            "-noautorotate",
            "-i",
            source["path"],
            "-filter_complex_threads",
            "2",
            "-filter_complex_script",
            str(filter_path),
            "-map",
            "[v]",
            "-an",
            "-c:v",
            "ffv1",
            "-level",
            "3",
            "-threads",
            "2",
            "-fps_mode",
            "passthrough",
            *color_args,
            str(out),
        ]
        log = _run(command, logs / f"{i:04}.log")
        actual = decoded_selection(log)
        if actual != expected:
            raise ValueError(f"decoded source selection mismatch for shot {shot['id']}")
        # Every source timestamp has been checked, including interior non-keyframes.
        frame_map = shot["source_frame_by_output"]
        resampling_evidence = {"method": "unchanged_identity_path", "buffer_frames": 0}
        if shot["mapping_kind"] == "explicit":
            source_checks = _frame_checksums(log, "source")
            output_checks = _frame_checksums(log, "mapped")
            if len(source_checks) != stop - start or output_checks != [
                source_checks[n - start] for n in frame_map
            ]:
                raise ValueError(f"decoded resampling mismatch for shot {shot['id']}")
            resampling_evidence = {
                "method": "shuffleframes-plane-checksums",
                "buffer_frames": max(stop - start, len(frame_map)),
                "source_checksums": source_checks,
                "mapped_checksums": output_checks,
                "mapped_checksum_sequence_verified": True,
            }
        _probe_output(out, len(frame_map), fps, plan["canvas"], require_color=False)
        records.append(
            {
                "id": shot["id"],
                "source_id": shot["source_id"],
                "source_frames": shot["source_frames"],
                "output_frames": shot["output_frames"],
                "source_pts_exact": [str(t) for t in actual],
                "source_frame_by_output": frame_map,
                "source_pts_by_output_exact": [
                    str(actual[n - start]) for n in frame_map
                ],
                "mapping_kind": shot["mapping_kind"],
                "resampling_evidence": resampling_evidence,
                "output_pts_exact": [
                    str(Fraction(n, fps)) for n in range(*shot["output_frames"])
                ],
                "decoded_source_selection_verified": True,
                "crop": shot["crop"],
                "crop_x_by_frame": shot["crop_x_by_frame"],
                "window": shot["window"],
            }
        )
        paths.append(out)
    if plan["tail_frames"]:
        tail = chunks / "tail.mov"
        _run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"color=c=black:s={cw}x{ch}:r={fps}",
                "-frames:v",
                str(plan["tail_frames"]),
                "-an",
                "-c:v",
                "ffv1",
                "-level",
                "3",
                "-threads",
                "2",
                *color_args,
                str(tail),
            ],
            logs / "tail.log",
        )
        paths.append(tail)
    # Generated relative chunk names avoid escaping user-authored source paths.
    concat = directory / "concat.txt"
    concat.write_text("".join(f"file 'chunks/{p.name}'\n" for p in paths))
    output = directory / "picture.rendering.mp4"
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-v",
            "error",
            "-xerror",
            "-threads",
            "2",
            "-f",
            "concat",
            "-safe",
            "1",
            "-i",
            str(concat),
            "-map",
            "0:v:0",
            "-vf",
            "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            str(crf),
            "-threads",
            "2",
            "-pix_fmt",
            "yuv420p",
            "-fps_mode",
            "passthrough",
            "-video_track_timescale",
            str(fps.numerator * 1000),
            *color_args,
            "-movflags",
            "+faststart",
            str(output),
        ],
        logs / "concat.log",
    )
    observed = _probe_output(output, plan["total_frames"], fps, plan["canvas"])
    return {
        "shots": records,
        "output": observed,
        "encode": {"codec": "libx264", "crf": crf},
    }
