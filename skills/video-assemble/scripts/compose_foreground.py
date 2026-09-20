#!/usr/bin/env python3
"""Compose caller-rendered RGBA pixels over a frozen H264/CFR/AAC base.

This operation validates pixels and immutable provenance. It does not generate or
interpret titles, dialogue, brands, typography, or release approval.
"""

import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
import subprocess

from frozen_audio import probe_audio_packets, verify_adopted_audio
from pair_media import probe_picture, validate_pair_timing


PATTERN = "frame_%06d.png"
SHA256 = re.compile(r"[a-f0-9]{64}")
ENCODING = {
    "video_codec": "libx264", "preset": "fast", "crf": 18,
    "pixel_format": "yuv420p", "color": "bt709_tv", "audio_codec": "copy",
}


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _save(path, value):
    temporary = path.with_suffix(".writing.json")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _fields(value, required):
    if not isinstance(value, dict) or set(value) != set(required):
        raise ValueError(f"Expected exactly these fields: {required}")


def _integer(value, label, *, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")
    return value


def _digest(value, label):
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise ValueError(f"{label} requires lowercase SHA256")
    return value


def _local_file(value, label):
    _fields(value, ["path", "sha256"])
    if not isinstance(value["path"], str) or not value["path"] or "://" in value["path"]:
        raise ValueError(f"{label} requires a local file path")
    expected = _digest(value["sha256"], label)
    path = Path(value["path"]).resolve()
    if not path.is_file() or _sha256(path) != expected:
        raise ValueError(f"{label} identity mismatch or file missing")
    return {"path": str(path), "sha256": expected}


def _sequence_paths(directory, pattern, start, end):
    if pattern != PATTERN:
        raise ValueError(f"Only the literal simple pattern {PATTERN!r} is supported")
    directory = Path(directory).resolve()
    if not directory.is_dir():
        raise FileNotFoundError(f"Sequence directory missing: {directory}")
    expected = [directory / (pattern % index) for index in range(start, end)]
    actual = sorted(directory.iterdir(), key=lambda item: item.name)
    if [item.name for item in actual] != [item.name for item in expected]:
        raise ValueError("Sequence requires exact contiguous files with no missing or extra entries")
    if not all(path.is_file() for path in expected):
        raise FileNotFoundError("Sequence frame missing or not a file")
    return directory, expected


def ordered_sequence_digest(directory, pattern, start, end):
    """Digest ordered frame numbers and resolved content hashes; symlinks may repeat."""
    start = _integer(start, "sequence start")
    end = _integer(end, "sequence end")
    if end <= start:
        raise ValueError("Sequence interval must be non-empty")
    _, paths = _sequence_paths(directory, pattern, start, end)
    manifest = [{"frame": index, "sha256": _sha256(path.resolve())}
                for index, path in zip(range(start, end), paths)]
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _probe_image(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams",
         "-show_entries", "stream=codec_name,width,height,pix_fmt", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=600,
    )
    if result.returncode or result.stderr.strip():
        raise ValueError(f"Image probe failed: {result.stderr.strip()}")
    streams = json.loads(result.stdout).get("streams", [])
    if len(streams) != 1 or streams[0].get("codec_name") != "png":
        raise ValueError("Foreground assets must be PNG images")
    return streams[0]


def _validate_rgba(path, width, height):
    image = _probe_image(path)
    if (image.get("width"), image.get("height")) != (width, height):
        raise ValueError("PNG must exactly match the full output canvas")
    if image.get("pix_fmt") != "rgba":
        raise ValueError("PNG must contain an actual RGBA pixel format")


def _sequence(value, required, *, local_count, width, height):
    _fields(value, required)
    directory = value["directory"]
    if not isinstance(directory, str) or not directory or "://" in directory:
        raise ValueError("Sequence requires a local directory")
    if value["pattern"] != PATTERN:
        raise ValueError(f"Only the literal simple pattern {PATTERN!r} is supported")
    expected_digest = _digest(value["ordered_sha256"], "sequence")
    resolved, paths = _sequence_paths(directory, value["pattern"], 0, local_count)
    if ordered_sequence_digest(resolved, value["pattern"], 0, local_count) != expected_digest:
        raise ValueError("Sequence ordered content digest mismatch")
    for path in paths:
        _validate_rgba(path, width, height)
    return {**value, "directory": str(resolved)}, paths


def _fraction(value, label):
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a canonical rational string")
    try:
        result = Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"Invalid {label}") from exc
    if result <= 0 or value != f"{result.numerator}/{result.denominator}":
        raise ValueError(f"{label} must be a positive canonical N/D rational")
    return result


def validate_endcard(value, *, foreground_end, total_frames, width, height):
    """Validate the exact endcard union and return normalized data and bound paths."""
    if not isinstance(value, dict) or value.get("kind") not in {"none", "still", "sequence"}:
        raise ValueError("Endcard kind must be none, still, or sequence")
    if value["kind"] == "none":
        _fields(value, ["kind"])
        if foreground_end != total_frames:
            raise ValueError("No endcard requires foreground to cover the full frame clock")
        return {"kind": "none"}, []
    if value["kind"] == "still":
        required = ["kind", "path", "sha256", "start_frame", "end_frame"]
    else:
        required = ["kind", "directory", "pattern", "start_frame", "end_frame",
                    "ordered_sha256"]
    _fields(value, required)
    end_start = _integer(value["start_frame"], "endcard start_frame")
    end_end = _integer(value["end_frame"], "endcard end_frame", minimum=1)
    if end_start >= end_end:
        raise ValueError("Endcard interval must be non-empty")
    if end_start != foreground_end or end_end != total_frames:
        raise ValueError("Foreground and endcard must exactly partition the base frame clock")
    if value["kind"] == "still":
        asset = _local_file({"path": value["path"], "sha256": value["sha256"]}, "endcard")
        _validate_rgba(asset["path"], width, height)
        return {**value, **asset}, [Path(asset["path"])]
    return _sequence(
        value, required, local_count=end_end - end_start, width=width, height=height,
    )


def validate_plan(plan_path):
    plan_path = Path(plan_path).resolve()
    raw = plan_path.read_bytes()
    plan_hash = hashlib.sha256(raw).hexdigest()
    plan = json.loads(raw)
    _fields(plan, ["artifact", "schema_version", "base", "video", "foreground",
                   "endcard", "producer_receipt"])
    if plan["artifact"] != "foreground_compose_plan" or type(plan["schema_version"]) is not int \
            or plan["schema_version"] != 1:
        raise ValueError("Unsupported foreground_compose_plan schema")
    base = _local_file(plan["base"], "base")
    receipt = _local_file(plan["producer_receipt"], "producer_receipt")
    _fields(plan["video"], ["fps", "width", "height", "total_frames"])
    fps = _fraction(plan["video"]["fps"], "video fps")
    width = _integer(plan["video"]["width"], "video width", minimum=1)
    height = _integer(plan["video"]["height"], "video height", minimum=1)
    total = _integer(plan["video"]["total_frames"], "video total_frames", minimum=1)
    _fields(plan["foreground"], ["directory", "pattern", "start_frame", "end_frame",
                                 "ordered_sha256"])
    foreground_start = _integer(plan["foreground"]["start_frame"], "foreground start_frame")
    foreground_end = _integer(plan["foreground"]["end_frame"], "foreground end_frame", minimum=1)
    if foreground_start != 0 or foreground_end > total:
        raise ValueError("Foreground must start at frame 0 and not exceed the frame clock")
    foreground, foreground_paths = _sequence(
        plan["foreground"], ["directory", "pattern", "start_frame", "end_frame",
                             "ordered_sha256"],
        local_count=foreground_end, width=width, height=height,
    )
    normalized_endcard, endcard_paths = validate_endcard(
        plan["endcard"], foreground_end=foreground_end, total_frames=total,
        width=width, height=height,
    )
    picture = probe_picture(base["path"])
    decoder = picture["decoder"]
    required_color = {"codec_name": "h264", "pix_fmt": "yuv420p", "color_range": "tv",
                      "color_space": "bt709", "color_transfer": "bt709",
                      "color_primaries": "bt709"}
    if any(decoder.get(key) != value for key, value in required_color.items()):
        raise ValueError("Base must be H264 yuv420p with explicit BT.709 TV color metadata")
    if (decoder.get("width"), decoder.get("height"), picture["frame_count"],
            Fraction(picture["fps"])) != (width, height, total, fps):
        raise ValueError("Declared fps/canvas/count does not match the actual base")
    audio = probe_audio_packets(base["path"], 0)
    validate_pair_timing(picture, audio)
    return {
        "plan_path": plan_path, "plan_sha256": plan_hash, "base": base,
        "video": {"fps": str(fps), "width": width, "height": height, "total_frames": total},
        "foreground": foreground, "foreground_paths": foreground_paths,
        "endcard": normalized_endcard, "endcard_paths": endcard_paths,
        "producer_receipt": receipt, "picture": picture, "audio": audio,
    }


def _assert_current(validated):
    if _sha256(validated["plan_path"]) != validated["plan_sha256"]:
        raise ValueError("Compose plan changed during operation")
    for asset in (validated["base"], validated["producer_receipt"]):
        if _sha256(asset["path"]) != asset["sha256"]:
            raise ValueError("Bound input or producer receipt changed during operation")
    foreground = validated["foreground"]
    if ordered_sequence_digest(foreground["directory"], foreground["pattern"], 0,
                               foreground["end_frame"]) != foreground["ordered_sha256"]:
        raise ValueError("Foreground sequence changed during operation")
    endcard = validated["endcard"]
    if endcard["kind"] == "still":
        if _sha256(endcard["path"]) != endcard["sha256"]:
            raise ValueError("Endcard changed during operation")
    elif endcard["kind"] == "sequence" and ordered_sequence_digest(
            endcard["directory"], endcard["pattern"], 0,
            endcard["end_frame"] - endcard["start_frame"]) != endcard["ordered_sha256"]:
        raise ValueError("Endcard sequence changed during operation")


def _run_ffmpeg(command, directory):
    _save(directory / "compose.command.json", command)
    result = subprocess.run(command, capture_output=True, text=True, timeout=600)
    (directory / "compose.log").write_text(result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError("Foreground composition failed; see compose.log")


def _verify_output(path, validated):
    output_picture = probe_picture(path)
    expected = validated["picture"]
    for key in ("frame_pts", "frame_count", "fps", "duration", "start"):
        if output_picture[key] != expected[key]:
            raise ValueError(f"Output picture {key} differs from the base")
    decoder_keys = ("codec_name", "width", "height", "pix_fmt", "sample_aspect_ratio",
                    "field_order", "color_range", "color_space", "color_transfer",
                    "color_primaries", "chroma_location")
    if {key: output_picture["decoder"].get(key) for key in decoder_keys} != \
            {key: expected["decoder"].get(key) for key in decoder_keys}:
        raise ValueError("Output codec/canvas/color metadata differs from the base")
    proof = verify_adopted_audio(validated["base"]["path"], path, 0, 0)
    if validate_pair_timing(output_picture, proof["output"]) != \
            validate_pair_timing(expected, validated["audio"]):
        raise ValueError("Output audio interval changed")
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-xerror", "-threads", "2", "-i", str(path),
         "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"],
        capture_output=True, text=True, timeout=600,
    )
    if result.returncode or result.stderr.strip():
        raise ValueError("Foreground output full decode failed")
    return output_picture, proof


def run_compose(plan_path, output_dir, *, plan_only=False):
    directory = Path(output_dir).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    report_path = directory / "foreground_run.json"
    staged = directory / "foreground.rendering.mp4"
    output = directory / "foreground.mp4"
    report = {"artifact": "foreground_compose_run", "schema_version": 1,
              "status": "PREPARING", "direct_listening": "NOT_CHECKED",
              "normal_speed_review": "NOT_CHECKED", "release_approved": False,
              "encoding": ENCODING}
    _save(report_path, report)
    try:
        value = validate_plan(plan_path)
        report.update(
            plan={"path": str(value["plan_path"]), "sha256": value["plan_sha256"]},
            base=value["base"], video=value["video"], foreground=value["foreground"],
            endcard=value["endcard"],
            producer_receipt={**value["producer_receipt"],
                              "semantic_validation": "DECLARED_NOT_CHECKED"},
        )
        _assert_current(value)
        if plan_only:
            report["status"] = "PLANNED"
            _save(report_path, report)
            return report
        fps = value["video"]["fps"]
        command = ["ffmpeg", "-nostdin", "-v", "error", "-n", "-copyts",
                   "-i", value["base"]["path"], "-framerate", fps, "-start_number", "0",
                   "-i", str(Path(value["foreground"]["directory"]) / PATTERN)]
        if value["endcard"]["kind"] == "none":
            filters = (
                "[0:v]format=rgb24[base];[1:v]format=rgba,setpts=PTS-STARTPTS[fg];"
                "[base][fg]overlay=0:0:eof_action=pass:format=rgb,"
                "scale=in_range=pc:out_range=tv:"
                "in_color_matrix=bt709:out_color_matrix=bt709,format=yuv420p,"
                f"trim=end_frame={value['video']['total_frames']}[outv]"
            )
        elif value["endcard"]["kind"] == "still":
            command += ["-loop", "1", "-framerate", fps, "-i", value["endcard"]["path"]]
        else:
            command += ["-framerate", fps, "-start_number", "0", "-i",
                        str(Path(value["endcard"]["directory"]) / PATTERN)]
        if value["endcard"]["kind"] != "none":
            end_start = value["endcard"]["start_frame"]
            end_offset = Fraction(end_start, 1) / Fraction(fps)
            filters = (
                f"[0:v]format=rgb24[base];[1:v]format=rgba,setpts=PTS-STARTPTS[fg];"
                f"[2:v]format=rgba,setpts=PTS-STARTPTS+{end_offset.numerator}/"
                f"{end_offset.denominator}/TB[end];"
                f"[base][fg]overlay=0:0:eof_action=pass:format=rgb:"
                f"enable='lt(n,{end_start})'[body];"
                f"[body][end]overlay=0:0:eof_action=repeat:format=rgb:"
                f"enable='gte(n,{end_start})',scale=in_range=pc:out_range=tv:"
                "in_color_matrix=bt709:out_color_matrix=bt709,format=yuv420p,"
                f"trim=end_frame={value['video']['total_frames']}[outv]"
            )
        command += ["-filter_complex", filters, "-map", "[outv]", "-map", "0:a:0",
                    "-c:v", ENCODING["video_codec"], "-preset", ENCODING["preset"],
                    "-crf", str(ENCODING["crf"]), "-threads", "2",
                    "-pix_fmt", ENCODING["pixel_format"],
                    "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709",
                    "-color_trc", "bt709", "-c:a", "copy", "-movie_timescale",
                    str(value["audio"]["sample_rate"]), "-movflags", "+faststart",
                    str(staged)]
        _run_ffmpeg(command, directory)
        _assert_current(value)
        output_picture, audio_proof = _verify_output(staged, value)
        _assert_current(value)
        _save(directory / "picture_identity.json", output_picture)
        _save(directory / "adopted_audio_identity.json", audio_proof)
        report["output"] = {"path": str(output), "sha256": _sha256(staged),
                            "full_decode": "PASS", "frame_clock": "EXACT",
                            "audio_packet_identity": "EXACT"}
        staged.rename(output)
        report["status"] = "FOREGROUND_RENDERED"
        _save(report_path, report)
        return report
    except Exception as exc:
        staged.unlink(missing_ok=True)
        output.unlink(missing_ok=True)
        report.pop("output", None)
        report.update(status="FAILED", error=f"{type(exc).__name__}: {exc}")
        _save(report_path, report)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()
    report = run_compose(args.plan, args.output_dir, plan_only=args.plan_only)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
