#!/usr/bin/env python3
"""Prepare exact source-audio and continuous-score beds from a strict sample plan."""

import argparse
from fractions import Fraction
import json
import os
import math
from pathlib import Path
import re
import shutil
import subprocess

from assemble_constants import SUPPORTED_PICTURE_CODECS, frame_clock_samples
from adoption.strict_inputs import (
    canonical_fraction, probe_json, read_json_bytes, require_declared_path, require_fields,
    require_integer, require_local_path, require_number, run_logged, without_digests,
    write_json_atomic,
)


RATE = 48_000
CHANNELS = 2
CODEC = "pcm_f32le"
SOURCE_ROLES = {"protected_original", "mixed_original_under_narration"}
FADE_SHAPES = {"linear", "half_cosine"}


def _probe_cfr(path):
    data = probe_json(
        path, "-select_streams", "v:0", "-show_streams", "-show_packets",
        "-show_entries",
        "stream=codec_name,time_base,start_pts,start_time,duration_ts,duration,"
        "avg_frame_rate,r_frame_rate:packet=pts,duration",
    )
    streams = data.get("streams", [])
    if len(streams) != 1:
        raise ValueError("source requires exactly one selected v:0 clock")
    stream = streams[0]
    codec = stream.get("codec_name")
    if codec not in SUPPORTED_PICTURE_CODECS:
        raise ValueError("v1 packet/frame CFR proof supports H264 and HEVC picture sources only")
    average = canonical_fraction(stream.get("avg_frame_rate"), "actual average frame rate")
    real = canonical_fraction(stream.get("r_frame_rate"), "actual real frame rate")
    if average != real:
        raise ValueError("source must be same-speed CFR")
    start_time = Fraction(stream.get("start_time", "0"))
    if start_time != 0:
        raise ValueError("source video frame clock must start at zero")
    time_base = Fraction(stream["time_base"])
    packets = data.get("packets", [])
    try:
        pts = sorted(Fraction(int(packet["pts"])) * time_base for packet in packets)
        durations = [Fraction(int(packet["duration"])) * time_base for packet in packets]
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise ValueError("H26x packet/frame clock proof is incomplete") from exc
    frame_duration = 1 / average
    count = len(pts)
    if not pts or any(value != index * frame_duration for index, value in enumerate(pts)):
        raise ValueError("source picture packets do not prove a zero-origin CFR frame clock")
    if any(duration != frame_duration for duration in durations):
        raise ValueError("source picture packet durations are not one CFR frame")
    return {"fps": str(average), "frame_count": count,
            "time_base": stream.get("time_base"), "start_time": "0",
            "codec_name": codec, "clock_proof": "H26X_ONE_PACKET_PER_FRAME_PTS"}


def _probe_audio(path, ordinal):
    data = probe_json(
        path, "-select_streams", f"a:{ordinal}", "-show_streams",
        "-show_entries", "stream=index,codec_name,sample_fmt,sample_rate,channels,"
        "channel_layout,time_base,start_pts,start_time,duration_ts,duration",
    )
    streams = data.get("streams", [])
    if len(streams) != 1:
        raise ValueError(f"audio stream a:{ordinal} is missing or ambiguous")
    stream = streams[0]
    return {key: stream.get(key) for key in (
        "index", "codec_name", "sample_fmt", "sample_rate", "channels",
        "channel_layout", "time_base", "start_pts", "start_time", "duration_ts", "duration"
    )}


def _probe_pcm(path):
    stream = _probe_audio(path, 0)
    if stream["codec_name"] not in {"pcm_s16le", "pcm_s24le", "pcm_f32le"}:
        raise ValueError("canonical/frozen WAV requires PCM16, PCM24, or PCM float")
    if stream["sample_rate"] != str(RATE) or stream["channels"] != CHANNELS:
        raise ValueError("canonical/frozen WAV must be 48 kHz stereo")
    time_base = Fraction(stream["time_base"])
    samples = int(Fraction(stream["duration_ts"]) * time_base * RATE)
    if Fraction(stream["duration_ts"]) * time_base * RATE != samples:
        raise ValueError("WAV duration is not an integral 48 kHz sample count")
    return {"codec_name": stream["codec_name"], "sample_fmt": stream["sample_fmt"],
            "sample_rate": RATE, "channels": CHANNELS, "samples": samples}


def _validate_fades(value, duration, curves, label):
    fade_in = require_integer(value["fade_in_samples"], f"{label} fade_in_samples")
    fade_out = require_integer(value["fade_out_samples"], f"{label} fade_out_samples")
    if fade_in + fade_out > duration:
        raise ValueError(f"{label} fades overlap")
    if fade_in == 1 or fade_out == 1:
        raise ValueError(f"{label} fades require zero or at least two samples")
    if value["fade_shape"] not in curves:
        raise ValueError(f"unsupported {label} fade_shape")
    return fade_in, fade_out


def load_plan(plan_path):
    plan_path, _, plan = read_json_bytes(plan_path, "source score plan")
    require_fields(plan, ["artifact", "schema_version", "output", "source_segments",
                   "source_silence", "score"], "source score plan")
    if plan["artifact"] != "source_score_plan" or type(plan["schema_version"]) is not int \
            or plan["schema_version"] != 1:
        raise ValueError("unsupported source_score_plan schema")
    require_fields(plan["output"], ["sample_rate", "channels", "total_samples"], "output")
    if plan["output"]["sample_rate"] != RATE or plan["output"]["channels"] != CHANNELS:
        raise ValueError("v1 output is fixed at 48 kHz stereo")
    total = require_integer(plan["output"]["total_samples"], "total_samples", 1)
    if not isinstance(plan["source_segments"], list) or not isinstance(plan["source_silence"], list):
        raise ValueError("source segments and explicit silence must be lists")
    segments = []
    asset_cache = {}
    picture_cache = {}
    ids = set()
    segment_fields = [
        "id", "path", "audio_stream", "source_fps", "source_start_frame",
        "source_end_frame", "output_start_sample", "gain", "fade_in_samples",
        "fade_out_samples", "fade_shape", "role",
    ]
    for value in plan["source_segments"]:
        value = without_digests(value, "source segment")
        require_fields(value, segment_fields, "source segment")
        if not isinstance(value["id"], str) or not value["id"] or value["id"] in ids:
            raise ValueError("source segment id must be unique and non-empty")
        ids.add(value["id"])
        ordinal = require_integer(value["audio_stream"], "audio_stream")
        path = require_declared_path(value, "source")
        fps = canonical_fraction(value["source_fps"], "source_fps")
        start = require_integer(value["source_start_frame"], "source_start_frame")
        end = require_integer(value["source_end_frame"], "source_end_frame", 1)
        if end <= start:
            raise ValueError("source frame interval must be non-empty")
        key = (str(path), ordinal)
        if key not in asset_cache:
            if str(path) not in picture_cache:
                picture_cache[str(path)] = _probe_cfr(path)
            picture = picture_cache[str(path)]
            audio = _probe_audio(path, ordinal)
            asset_cache[key] = {"path": str(path), "audio_stream": ordinal,
                                "picture": picture, "audio": audio}
        facts = asset_cache[key]
        if facts["picture"]["fps"] != str(fps) or end > facts["picture"]["frame_count"]:
            raise ValueError("declared source frame clock/range differs from actual CFR source")
        source_start_sample = frame_clock_samples(start, fps, RATE)
        source_end_sample = frame_clock_samples(end, fps, RATE)
        duration = source_end_sample - source_start_sample
        if duration <= 0:
            raise ValueError("source frame interval is shorter than one 48 kHz sample")
        fade_in, fade_out = _validate_fades(value, duration, {"linear"}, "source")
        output_start = require_integer(value["output_start_sample"], "output_start_sample")
        output_end = output_start + duration
        if output_end > total or value["role"] not in SOURCE_ROLES:
            raise ValueError("source output range or role is unsupported")
        segments.append({**value, "path": str(path),
                         "gain": require_number(value["gain"], "source gain", 0, 16),
                         "source_start_sample": source_start_sample,
                         "source_end_sample": source_end_sample,
                         "output_end_sample": output_end, "fade_in_samples": fade_in,
                         "fade_out_samples": fade_out, "asset_key": key})
    silence = []
    for value in plan["source_silence"]:
        require_fields(value, ["output_start_sample", "output_end_sample", "role"], "source silence")
        start = require_integer(value["output_start_sample"], "silence output_start_sample")
        end = require_integer(value["output_end_sample"], "silence output_end_sample", 1)
        if value["role"] != "silence" or not start < end <= total:
            raise ValueError("invalid explicit source silence range")
        silence.append(dict(value))
    coverage = sorted(
        [(item["output_start_sample"], item["output_end_sample"]) for item in segments]
        + [(item["output_start_sample"], item["output_end_sample"]) for item in silence]
    )
    cursor = 0
    for start, end in coverage:
        if start != cursor:
            raise ValueError("source bed requires exact nonoverlapping coverage with explicit silence")
        cursor = end
    if cursor != total:
        raise ValueError("source bed has an implicit tail gap")
    score = plan["score"]
    if not isinstance(score, dict) or score.get("kind") not in {"raw", "frozen", "none"}:
        raise ValueError("score kind must be raw, frozen, or none")
    score = without_digests(score, "score")
    if score["kind"] == "raw":
        require_fields(score, ["kind", "path", "audio_stream", "source_offset_sample",
                        "gain", "fade_in_samples", "fade_out_samples", "fade_shape"], "raw score")
        score_path = require_declared_path(score, "raw score")
        ordinal = require_integer(score["audio_stream"], "score audio_stream")
        offset = require_integer(score["source_offset_sample"], "score source_offset_sample")
        fade_in, fade_out = _validate_fades(score, total, FADE_SHAPES, "score")
        score = {**score, "path": str(score_path),
                 "audio_stream": ordinal, "source_offset_sample": offset,
                 "gain": require_number(score["gain"], "score gain", 0, 16),
                 "fade_in_samples": fade_in, "fade_out_samples": fade_out,
                 "audio": _probe_audio(score_path, ordinal)}
    elif score["kind"] == "frozen":
        require_fields(score, ["kind", "path", "audio_stream"], "frozen score")
        score_path = require_declared_path(score, "frozen score")
        ordinal = require_integer(score["audio_stream"], "score audio_stream")
        if ordinal != 0:
            raise ValueError("frozen WAV supports only a:0")
        pcm = _probe_pcm(score_path)
        if pcm["samples"] != total:
            raise ValueError("frozen score must exactly match total_samples")
        score = {**score, "path": str(score_path), "pcm": pcm}
    else:
        require_fields(score, ["kind"], "none score")
    return {"plan_path": plan_path,
            "output": {**plan["output"], "codec": CODEC}, "source_segments": segments,
            "source_silence": silence, "source_assets": list(asset_cache.values()),
            "score": score}


_run = run_logged


def _decode_command(path, ordinal, output):
    return ["ffmpeg", "-nostdin", "-v", "error", "-n", "-copyts", "-i", str(path),
            "-map", f"0:a:{ordinal}", "-af",
            "aresample=48000:async=0:first_pts=0,aformat=sample_fmts=flt:"
            "sample_rates=48000:channel_layouts=stereo", "-c:a", CODEC, str(output)]


def _fade_filters(duration, fade_in, fade_out, curve):
    factors = []
    if fade_in:
        if curve == "linear":
            factors.append(f"min(1\\,n/{fade_in - 1})")
        else:
            position = f"max(0\\,min(1\\,n/{fade_in - 1}))"
            factors.append(f"0.5-0.5*cos(PI*{position})")
    if fade_out:
        remaining = f"({duration - 1}-n)"
        if curve == "linear":
            factors.append(f"max(0\\,min(1\\,{remaining}/{fade_out - 1}))")
        else:
            position = f"max(0\\,min(1\\,{remaining}/{fade_out - 1}))"
            factors.append(
                f"0.5-0.5*cos(PI*{position})"
            )
    if not factors:
        return []
    gain = "*".join(f"({factor})" for factor in factors)
    return [f"aeval=val(0)*({gain})|val(1)*({gain}):c=same"]


def _render_source(plan, decoded, output, directory):
    total = plan["output"]["total_samples"]
    command = ["ffmpeg", "-nostdin", "-v", "error", "-n"]
    assets = {tuple(asset_key): index for index, asset_key in enumerate(decoded)}
    for asset_key in decoded:
        command += ["-i", str(decoded[asset_key])]
    filters = [f"anullsrc=r={RATE}:cl=stereo,atrim=end_sample={total}[base]"]
    inputs = ["[base]"]
    for index, segment in enumerate(plan["source_segments"]):
        duration = segment["source_end_sample"] - segment["source_start_sample"]
        chain = [f"[{assets[tuple(segment['asset_key'])]}:a]atrim="
                 f"start_sample={segment['source_start_sample']}:"
                 f"end_sample={segment['source_end_sample']}", "asetpts=PTS-STARTPTS",
                 f"volume={segment['gain']:.17g}"]
        chain += _fade_filters(duration, segment["fade_in_samples"],
                               segment["fade_out_samples"], "linear")
        chain.append(f"adelay={segment['output_start_sample']}S:all=1[src{index}]")
        filters.append(",".join(chain))
        inputs.append(f"[src{index}]")
    filters.append("".join(inputs) + f"amix=inputs={len(inputs)}:duration=first:normalize=0,"
                   f"atrim=end_sample={total},aformat=sample_fmts=flt:"
                   f"sample_rates={RATE}:channel_layouts=stereo[out]")
    command += ["-filter_complex", ";".join(filters), "-map", "[out]", "-c:a", CODEC,
                str(output)]
    _run(command, directory, "source")


def _render_score(plan, decoded_score, output, directory):
    total = plan["output"]["total_samples"]
    score = plan["score"]
    if score["kind"] == "none":
        command = [
            "ffmpeg", "-nostdin", "-v", "error", "-n", "-f", "lavfi", "-i",
            f"anullsrc=r={RATE}:cl=stereo", "-af", f"atrim=end_sample={total}",
            "-c:a", CODEC, str(output),
        ]
    elif score["kind"] == "frozen":
        command = ["ffmpeg", "-nostdin", "-v", "error", "-n", "-i", score["path"],
                   "-map", "0:a:0", "-af", "aformat=sample_fmts=flt:sample_rates=48000:"
                   "channel_layouts=stereo", "-c:a", CODEC, str(output)]
    else:
        chain = [f"atrim=start_sample={score['source_offset_sample']}:"
                 f"end_sample={score['source_offset_sample'] + total}",
                 "asetpts=PTS-STARTPTS", f"volume={score['gain']:.17g}"]
        chain += _fade_filters(total, score["fade_in_samples"], score["fade_out_samples"],
                               score["fade_shape"])
        chain += [f"atrim=end_sample={total}", "aformat=sample_fmts=flt:sample_rates=48000:"
                  "channel_layouts=stereo"]
        command = ["ffmpeg", "-nostdin", "-v", "error", "-n", "-i", str(decoded_score),
                   "-af", ",".join(chain), "-c:a", CODEC, str(output)]
    _run(command, directory, "score")


def _astats(path):
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "info", "-i", str(path), "-af",
         "astats=metadata=0:reset=0", "-f", "null", "-"],
        capture_output=True, text=True, timeout=3600,
    )
    if result.returncode:
        raise ValueError("output astats decode failed")
    peaks = re.findall(r"Peak level dB:\s*(-?inf|[-+0-9.]+)", result.stderr, re.I)
    nan_counts = re.findall(r"Number of NaNs:\s*(\d+)", result.stderr)
    inf_counts = re.findall(r"Number of Infs:\s*(\d+)", result.stderr)
    if not peaks:
        raise ValueError("output peak statistics unavailable")
    peak_db = max(float(value) if value.lower() != "-inf" else -math.inf for value in peaks)
    finite = all(int(value) == 0 for value in nan_counts + inf_counts)
    if not finite:
        raise ValueError("output contains non-finite PCM samples")
    return {"finite": True, "peak": 0.0 if peak_db == -math.inf else 10 ** (peak_db / 20)}


def _output_facts(path):
    """PCM format, sample count, size and peak/finiteness of one 48 kHz stereo float WAV."""
    return {"path": str(path), "bytes": os.stat(path).st_size, "pcm": _probe_pcm(path),
            **_astats(path)}


def validate_prepared_receipt(reference, expected_format):
    """Validate one completed prepared-bed receipt and its three current PCM stems."""
    require_fields(without_digests(reference, "prepared receipt reference"), ["path"],
                   "prepared receipt reference")
    require_fields(expected_format, ["sample_rate", "channels", "total_samples"],
            "expected prepared format")
    if expected_format["sample_rate"] != RATE or expected_format["channels"] != CHANNELS:
        raise ValueError("prepared format must be 48 kHz stereo")
    require_integer(expected_format["total_samples"], "prepared total_samples", 1)
    receipt_path = require_declared_path(reference, "prepared receipt")
    receipt = read_json_bytes(receipt_path, "prepared receipt")[2]
    if not isinstance(receipt, dict) or receipt.get("artifact") != "prepared_bed_receipt" \
            or receipt.get("schema_version") != 1 or receipt.get("status") != "PREPARED":
        raise ValueError("prepared receipt is not a completed v1 artifact")
    required_format = {**expected_format, "codec": CODEC}
    if receipt.get("format") != required_format:
        raise ValueError("prepared receipt format differs from expected picture clock")
    outputs = receipt.get("outputs")
    if not isinstance(outputs, dict) or set(outputs) != {
        "source_bed.wav", "score_bed.wav", "prepared_bed.wav"
    }:
        raise ValueError("prepared receipt requires exactly three named bed outputs")
    prepared = {}
    for name, declared in outputs.items():
        if not isinstance(declared, dict):
            raise ValueError(f"prepared receipt {name} identity is invalid")
        path = require_local_path(declared.get("path"), name)
        stream = _probe_audio(path, 0)
        if stream["codec_name"] != CODEC or stream["sample_rate"] != str(RATE) or \
                stream["channels"] != CHANNELS or \
                (stream["start_time"] not in (None, "N/A") and
                 Fraction(stream["start_time"]) != 0):
            raise ValueError(f"{name} must be zero-origin 48 kHz stereo float PCM")
        actual = _output_facts(path)
        if actual["pcm"]["codec_name"] != CODEC \
                or actual["pcm"]["samples"] != expected_format["total_samples"]:
            raise ValueError(f"{name} does not match the required full PCM format")
        prepared[name] = actual
    return {
        "reference": {"path": str(receipt_path)},
        "format": dict(expected_format), "outputs": prepared, "receipt": receipt,
    }


def prepare_source_score(plan_path, output_dir):
    directory = Path(output_dir).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    finals = {name: directory / name for name in
              ("source_bed.wav", "score_bed.wav", "prepared_bed.wav")}
    staged = {name: directory / f".{Path(name).stem}.rendering.wav" for name in finals}
    receipt_path = directory / "prepared_bed_receipt.json"
    try:
        plan = load_plan(plan_path)
        decoded = {}
        for index, asset in enumerate(plan["source_assets"]):
            key = (asset["path"], asset["audio_stream"])
            path = directory / f".source_{index:03d}.decoded.wav"
            _run(_decode_command(asset["path"], asset["audio_stream"], path),
                 directory, f"decode_source_{index:03d}")
            decoded[key] = path
            asset["canonical_pcm"] = _output_facts(path)
            required = max(
                segment["source_end_sample"] for segment in plan["source_segments"]
                if tuple(segment["asset_key"]) == key
            )
            if asset["canonical_pcm"]["pcm"]["samples"] < required:
                raise ValueError("decoded source audio is too short for a selected frame range")
        score_decode = None
        if plan["score"]["kind"] == "raw":
            score_decode = directory / ".score.decoded.wav"
            _run(_decode_command(plan["score"]["path"], plan["score"]["audio_stream"],
                                 score_decode), directory, "decode_score")
            plan["score"]["canonical_decode"] = _output_facts(score_decode)
            if plan["score"]["canonical_decode"]["pcm"]["samples"] < \
                    plan["score"]["source_offset_sample"] + plan["output"]["total_samples"]:
                raise ValueError("raw score is too short for continuous offset window")
        _render_source(plan, decoded, staged["source_bed.wav"], directory)
        _render_score(plan, score_decode, staged["score_bed.wav"], directory)
        rendered = {
            "source_bed.wav": _output_facts(staged["source_bed.wav"]),
            "score_bed.wav": _output_facts(staged["score_bed.wav"]),
        }
        if plan["score"]["kind"] == "none":
            shutil.copyfile(staged["source_bed.wav"], staged["prepared_bed.wav"])
        else:
            command = [
                "ffmpeg", "-nostdin", "-v", "error", "-n",
                "-i", str(staged["source_bed.wav"]), "-i", str(staged["score_bed.wav"]),
                "-filter_complex", f"[0:a][1:a]amix=inputs=2:duration=first:normalize=0,"
                f"atrim=end_sample={plan['output']['total_samples']},aformat=sample_fmts=flt:"
                "sample_rates=48000:channel_layouts=stereo[out]", "-map", "[out]",
                "-c:a", CODEC, str(staged["prepared_bed.wav"]),
            ]
            _run(command, directory, "prepare")
        outputs = {**rendered, "prepared_bed.wav": _output_facts(staged["prepared_bed.wav"])}
        if any(value["pcm"]["samples"] != plan["output"]["total_samples"]
               for value in outputs.values()):
            raise ValueError("prepared bed sample counts differ from plan")
        if plan["score"]["kind"] == "none" and (
                outputs["prepared_bed.wav"]["bytes"] != outputs["source_bed.wav"]["bytes"]
                or outputs["prepared_bed.wav"]["pcm"] != outputs["source_bed.wav"]["pcm"]):
            raise ValueError("none score must preserve the source bed exactly")
        outputs["prepared_bed.wav"]["headroom_policy"] = "FLOAT_PRESERVED_NO_MASTER"
        receipt = {
            "artifact": "prepared_bed_receipt", "schema_version": 1, "status": "PREPARED",
            "plan": {"path": str(plan["plan_path"])},
            "format": plan["output"], "source_assets": plan["source_assets"],
            "source_segments": [{key: value for key, value in item.items() if key != "asset_key"}
                                for item in plan["source_segments"]],
            "source_silence": plan["source_silence"], "score": plan["score"],
            "outputs": outputs, "direct_listening": "NOT_CHECKED", "release_approved": False,
        }
        for name, path in staged.items():
            path.rename(finals[name])
            receipt["outputs"][name]["path"] = str(finals[name])
        write_json_atomic(receipt_path, receipt)
        return receipt
    except Exception:
        for path in [*staged.values(), *finals.values(), receipt_path]:
            path.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    print(json.dumps(prepare_source_score(args.plan, args.output_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
