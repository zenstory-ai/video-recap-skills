#!/usr/bin/env python3
"""Render an explicitly adopted prepared PCM bed onto an immutable picture."""

import argparse
import array
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys

from assemble_constants import frame_clock_samples
from frozen_audio import probe_audio_packets, verify_adopted_audio
import pair_media
import source_score


RATE = 48_000
CHANNELS = 2
CODEC = "pcm_f32le"
FINAL_NAME = "prepared_audio.mp4"
REPORT_NAME = "prepared_audio_run.json"


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _save(path, value):
    temporary = path.with_suffix(".writing.json")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def _fields(value, required, label):
    if not isinstance(value, dict) or set(value) != set(required):
        raise ValueError(f"{label} requires exactly fields {required}")


def _reference(value, label):
    _fields(value, ["path", "sha256"], label)
    if not isinstance(value["path"], str) or not value["path"] or \
            "://" in value["path"]:
        raise ValueError(f"{label} requires a local path")
    if not isinstance(value["sha256"], str) or len(value["sha256"]) != 64 or \
            any(char not in "0123456789abcdef" for char in value["sha256"]):
        raise ValueError(f"{label} requires SHA256")
    path = Path(value["path"]).resolve()
    if not path.is_file() or _sha256(path) != value["sha256"]:
        raise ValueError(f"{label} identity mismatch or file missing")
    return {"path": str(path), "sha256": value["sha256"]}


def _gain(value):
    if type(value) not in (int, float) or not math.isfinite(value) or \
            not -24 <= value <= 24:
        raise ValueError("master_gain_db must be finite in [-24,24]")
    return float(value)


def _picture_format(picture):
    if picture["start"] != "0":
        raise ValueError("picture presentation must start at zero")
    return {
        "sample_rate": RATE, "channels": CHANNELS,
        "total_samples": frame_clock_samples(picture["frame_count"], picture["fps"], RATE),
    }


def load_adoption(path):
    adoption_path = Path(path).resolve()
    raw = adoption_path.read_bytes()
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("prepared audio adoption is not valid JSON") from exc
    _fields(value, ["artifact", "schema_version", "picture", "prepared_receipt",
                    "master_gain_db"], "prepared audio adoption")
    if value["artifact"] != "prepared_audio_adoption" or \
            type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("unsupported prepared_audio_adoption schema")
    picture_ref = _reference(value["picture"], "picture")
    picture = pair_media.probe_picture(picture_ref["path"])
    expected_format = _picture_format(picture)
    receipt = source_score.validate_prepared_receipt(
        value["prepared_receipt"], expected_format
    )
    return {
        "path": str(adoption_path), "sha256": hashlib.sha256(raw).hexdigest(),
        "picture": picture_ref, "picture_identity": picture,
        "prepared_receipt": receipt["reference"],
        "prepared": receipt["outputs"], "format": expected_format,
        "master_gain_db": _gain(value["master_gain_db"]),
    }


def _assert_hash(reference, label):
    if _sha256(reference["path"]) != reference["sha256"]:
        raise ValueError(f"{label} changed after its identity was sealed")


def assert_current(context, runtime=None):
    for reference, label in (
        ({"path": context["path"], "sha256": context["sha256"]}, "adoption"),
        (context["picture"], "picture"),
        (context["prepared_receipt"], "prepared receipt"),
    ):
        _assert_hash(reference, label)
    for name, identity in context["prepared"].items():
        _assert_hash(identity, name)
    if runtime:
        for name in ("master", "aac"):
            if name in runtime:
                _assert_hash(runtime[name], name)


def _run(command, directory, label, report, report_path):
    report.setdefault("commands", {})[label] = command
    _save(report_path, report)
    result = subprocess.run(command, capture_output=True, text=True, timeout=3600)
    (directory / f"{label}.log").write_text(result.stderr)
    if result.returncode:
        raise RuntimeError(f"{label} FFmpeg failed")


def _exact_pcm(path, expected_samples):
    initial_hash = _sha256(path)
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-map", "0:a:0",
         "-f", "f32le", "-acodec", CODEC, "-ar", str(RATE), "-ac", str(CHANNELS), "-"],
        capture_output=True, timeout=3600,
    )
    if result.returncode:
        raise ValueError("master PCM decode failed")
    values = array.array("f")
    values.frombytes(result.stdout)
    if sys.byteorder != "little":
        values.byteswap()
    if len(values) != expected_samples * CHANNELS:
        raise ValueError("master PCM sample count differs from picture clock")
    if not all(math.isfinite(value) for value in values):
        raise ValueError("master PCM contains non-finite samples")
    peak = max((abs(value) for value in values), default=0.0)
    if peak > 1:
        raise ValueError("master PCM sample peak exceeds 1")
    if _sha256(path) != initial_hash:
        raise ValueError("master PCM changed while being checked")
    return {"sample_peak": peak, "sample_peak_policy": "FINITE_ABS_LE_1"}


def _validate_aac(path, picture, total_samples):
    audio = probe_audio_packets(path, 0)
    pair_media.validate_pair_timing(picture, audio)
    if audio["codec"] != "aac" or audio["sample_rate"] != RATE or \
            audio["channels"] != CHANNELS:
        raise ValueError("delivery audio must be 48 kHz stereo AAC")
    if audio["start_time"] in (None, "N/A") or Fraction(audio["start_time"]) != 0:
        raise ValueError("AAC presentation must start at zero")
    packets = audio["packets"]
    if not packets:
        raise ValueError("AAC has no packets")
    first_pts = Fraction(packets[0]["pts"])
    if first_pts >= 0:
        raise ValueError("AAC encoder priming packet must remain negative")
    priming_samples = -first_pts * RATE
    if priming_samples.denominator != 1:
        raise ValueError("AAC priming PTS is not an integral 48 kHz sample offset")
    skip = packets[0]["side_data_list"]
    expected_skip = int(priming_samples)
    if not skip or skip[0].get("side_data_type") != "Skip Samples" or \
            skip[0].get("skip_samples") != expected_skip:
        raise ValueError("AAC negative priming and skip_samples disagree")
    expected_end = Fraction(total_samples, RATE)
    header_end = Fraction(audio["start_time"]) + Fraction(audio["duration"])
    packet_end = Fraction(packets[-1]["pts"]) + Fraction(packets[-1]["duration"])
    tolerance = Fraction(1, RATE)
    if abs(header_end - expected_end) > tolerance or \
            abs(packet_end - expected_end) > tolerance or \
            abs(packet_end - header_end) > tolerance:
        raise ValueError("AAC packet/header endpoint differs from PCM/picture by over one sample")
    return {
        **audio, "presentation_start": "0", "pcm_picture_end": str(expected_end),
        "header_end": str(header_end), "packet_end": str(packet_end),
        "endpoint_tolerance": str(tolerance), "priming_skip_samples": expected_skip,
    }


def render_prepared_audio(adoption_path, output_dir):
    directory = Path(output_dir).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    report_path = directory / REPORT_NAME
    final = directory / FINAL_NAME
    staged_final = directory / ".prepared_audio.rendering.mp4"
    staging = directory / ".prepared_audio_staging"
    report = {
        "artifact": "prepared_audio_run", "schema_version": 1,
        "status": "PREPARING", "direct_listening": "NOT_CHECKED",
        "normal_speed_review": "NOT_CHECKED", "release_approved": False,
        "aac_pcm_identity_claimed": False,
    }
    _save(report_path, report)
    runtime = {}
    try:
        context = load_adoption(adoption_path)
        report.update(
            adoption={"path": context["path"], "sha256": context["sha256"]},
            picture=context["picture"], prepared_receipt=context["prepared_receipt"],
            prepared=context["prepared"], format=context["format"],
            master_gain_db=context["master_gain_db"],
        )
        assert_current(context)
        staging.mkdir()
        master = staging / "master.wav"
        prepared = Path(context["prepared"]["prepared_bed.wav"]["path"])
        gain_linear = 10 ** (context["master_gain_db"] / 20)
        if context["master_gain_db"] == 0:
            shutil.copyfile(prepared, master)
            report.setdefault("commands", {})["master"] = [
                "exact-file-copy", str(prepared), str(master)
            ]
        else:
            command = [
                "ffmpeg", "-nostdin", "-v", "error", "-n", "-i", str(prepared),
                "-map", "0:a:0", "-af", f"volume={gain_linear:.17g},"
                "aformat=sample_fmts=flt:sample_rates=48000:channel_layouts=stereo",
                "-c:a", CODEC, str(master),
            ]
            _run(command, staging, "master", report, report_path)
        master_identity = source_score._output_identity(master)
        master_identity.update(_exact_pcm(master, context["format"]["total_samples"]))
        if context["master_gain_db"] == 0 and \
                master_identity["pcm_payload_sha256"] != \
                context["prepared"]["prepared_bed.wav"]["pcm_payload_sha256"]:
            raise ValueError("zero-gain master must preserve prepared PCM payload")
        runtime["master"] = master_identity
        runtime["master_gain_linear"] = gain_linear
        assert_current(context, runtime)

        aac = staging / "master.m4a"
        _run(
            ["ffmpeg", "-nostdin", "-v", "error", "-n", "-i", str(master),
             "-map", "0:a:0", "-c:a", "aac", "-b:a", "192k",
             "-movie_timescale", str(RATE), str(aac)],
            staging, "aac", report, report_path,
        )
        runtime["aac"] = {"path": str(aac), "sha256": _sha256(aac)}
        runtime["aac_identity"] = _validate_aac(
            aac, context["picture_identity"], context["format"]["total_samples"]
        )
        assert_current(context, runtime)

        pair_plan = staging / "pair_plan.json"
        pair_plan.write_text(json.dumps({
            "artifact": "media_pair", "schema_version": 1,
            "picture": context["picture"],
            "audio": {"path": str(aac), "sha256": runtime["aac"]["sha256"],
                      "selected_stream": 0},
        }))
        pair_dir = staging / "pair"
        pair_report = pair_media.run_pair(pair_plan, pair_dir)
        nested = pair_dir / "paired.mp4"
        assert_current(context, runtime)
        pair_output = pair_report.get("output")
        if pair_report.get("status") != "PAIR_RENDERED" or \
                not isinstance(pair_output, dict) or \
                Path(pair_output.get("path", "")).resolve() != nested or \
                pair_output.get("sha256") != _sha256(nested):
            raise ValueError("nested pair receipt does not bind its current output")
        nested_hash = pair_output["sha256"]
        if pair_media.probe_picture(nested) != context["picture_identity"]:
            raise ValueError("paired picture identity changed")
        if _sha256(nested) != nested_hash:
            raise ValueError("paired output changed after picture verification")
        pair_audio = verify_adopted_audio(aac, nested, 0, 0)
        if _sha256(nested) != nested_hash:
            raise ValueError("paired output changed after audio verification")
        packet_identity_keys = (
            "decoder", "packet_count", "payload_sha256", "packets",
            "start_time", "duration",
        )
        expected_pair_identity = {
            key: runtime["aac_identity"][key] for key in packet_identity_keys
        }
        actual_pair_identity = {
            key: pair_audio["output"][key] for key in packet_identity_keys
        }
        if actual_pair_identity != expected_pair_identity:
            raise ValueError("paired AAC identity changed")
        assert_current(context, runtime)
        if (pair_dir / "mux.command.json").exists():
            report.setdefault("commands", {})["pair"] = json.loads(
                (pair_dir / "mux.command.json").read_text()
            )
        if _sha256(nested) != nested_hash:
            raise ValueError("paired output changed before publication")
        shutil.copyfile(nested, staged_final)
        if _sha256(nested) != nested_hash or _sha256(staged_final) != nested_hash:
            raise ValueError("published copy differs from sealed paired output")
        staged_final.replace(final)
        if _sha256(nested) != nested_hash or _sha256(final) != nested_hash:
            raise ValueError("final output differs from sealed paired output")
        report.update(
            status="PREPARED_AUDIO_RENDERED",
            master={**master_identity, "gain_db": context["master_gain_db"],
                    "gain_linear": gain_linear,
                    "aac_true_peak": "NOT_MEASURED"},
            aac={**runtime["aac"], "identity": runtime["aac_identity"]},
            pair={"status": pair_report["status"], "hidden_staging": str(pair_dir),
                  "output": {"path": str(nested), "sha256": nested_hash}},
            output={"path": str(final), "sha256": _sha256(final),
                    "picture_identity": "EXACT", "aac_packet_identity": "EXACT",
                    "full_decode": "PASS"},
        )
        _save(report_path, report)
        return report
    except Exception as exc:
        pair_command = staging / "pair" / "mux.command.json"
        if pair_command.exists():
            report.setdefault("commands", {})["pair"] = json.loads(pair_command.read_text())
        staged_final.unlink(missing_ok=True)
        final.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)
        report.pop("output", None)
        report.update(status="FAILED", error=f"{type(exc).__name__}: {exc}")
        _save(report_path, report)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("adoption")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    print(json.dumps(render_prepared_audio(args.adoption, args.output_dir),
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
