"""Validate, render, and bind an explicitly adopted prepared-bed plus narration mix."""

import hashlib
import json
import math
from pathlib import Path
import re
import subprocess

from assemble_constants import frame_clock_samples
from frozen_audio import probe_audio_packets
import narration_binding
from pair_media import probe_picture, validate_pair_timing
import source_score


ARTIFACT = "audio_mix_binding"
FILENAME = "audio_mix_binding.json"
RATE = 48_000
CHANNELS = 2
CODEC = "pcm_f32le"
CONVERSION_POLICY = "mono_equal_power_stereo_identity"
SHA256 = re.compile(r"[a-f0-9]{64}")


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fields(value, required, label):
    if not isinstance(value, dict) or set(value) != set(required):
        raise ValueError(f"{label} requires exactly fields {required}")


def _digest(value, label):
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA256")
    return value


def _integer(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")
    return value


def _number(value, label, minimum, maximum):
    if type(value) not in (int, float) or not math.isfinite(value) \
            or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be finite in [{minimum},{maximum}]")
    return float(value)


def _local(path, label):
    if not isinstance(path, (str, Path)) or not str(path) or "://" in str(path):
        raise ValueError(f"{label} requires a local path")
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise ValueError(f"{label} is missing: {resolved}")
    return resolved


def _json(path, label):
    resolved = _local(path, label)
    raw = resolved.read_bytes()
    try:
        return resolved, raw, json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid JSON") from exc


def _assert_hash(path, expected, label):
    if _sha256(path) != expected:
        raise ValueError(f"{label} changed or has the wrong identity")


def _picture_format(picture):
    samples = frame_clock_samples(picture["frame_count"], picture["fps"], RATE)
    return samples, {
        "fps": picture["fps"], "frame_count": picture["frame_count"],
        "duration": picture["duration"], "start": picture["start"],
        "decoder": picture["decoder"],
    }


def load_adoption(path, *, input_video, narration_adoption_path, tts_segments):
    """Strict read-only preflight; no work artifacts are created here."""
    adoption_path, raw, value = _json(path, "audio mix adoption")
    _fields(value, ["artifact", "schema_version", "picture_sha256", "prepared_receipt",
                    "narration_adoption_sha256", "format", "segments", "master_gain_db"],
            "audio mix adoption")
    if value["artifact"] != "audio_mix_adoption" or type(value["schema_version"]) is not int \
            or value["schema_version"] != 1:
        raise ValueError("unsupported audio_mix_adoption schema")
    input_video = _local(input_video, "picture")
    picture_hash = _digest(value["picture_sha256"], "picture_sha256")
    _assert_hash(input_video, picture_hash, "picture")
    picture = probe_picture(input_video)
    picture_samples, picture_summary = _picture_format(picture)

    narration_path = _local(narration_adoption_path, "narration adoption")
    narration_hash = _digest(value["narration_adoption_sha256"],
                             "narration_adoption_sha256")
    _assert_hash(narration_path, narration_hash, "narration adoption")
    _fields(value["format"], ["sample_rate", "channels", "total_samples"], "mix format")
    mix_format = value["format"]
    if mix_format != {"sample_rate": RATE, "channels": CHANNELS,
                      "total_samples": picture_samples}:
        raise ValueError("mix format differs from the actual picture sample clock")
    prepared_receipt = source_score.validate_prepared_receipt(
        value["prepared_receipt"], mix_format
    )

    if not isinstance(value["segments"], list) or len(value["segments"]) != len(tts_segments):
        raise ValueError("audio mix segments must exactly cover narration segments")
    normalized = []
    seen = set()
    for adopted, segment in zip(value["segments"], tts_segments):
        _fields(adopted, ["index", "processed_wav_sha256", "output_start_sample", "gain"],
                "audio mix segment")
        index = _integer(adopted["index"], "audio mix segment index")
        if index in seen or index != segment.get("index"):
            raise ValueError("audio mix segment index/order differs from narration")
        seen.add(index)
        digest = _digest(adopted["processed_wav_sha256"], "processed_wav_sha256")
        if digest != segment.get("processed_wav_sha256"):
            raise ValueError("audio mix segment hash differs from narration")
        normalized.append({**adopted,
                           "output_start_sample": _integer(
                               adopted["output_start_sample"], "output_start_sample"),
                           "gain": _number(adopted["gain"], "narration gain", 0, 16)})
    return {
        "path": str(adoption_path), "sha256": hashlib.sha256(raw).hexdigest(),
        "picture": {"path": str(input_video), "sha256": picture_hash,
                    "clock": picture_summary},
        "picture_identity": picture,
        "prepared_receipt": prepared_receipt["reference"],
        "prepared": prepared_receipt["outputs"],
        "narration_adoption": {"path": str(narration_path), "sha256": narration_hash},
        "format": dict(mix_format), "segments": normalized,
        "master_gain_db": _number(value["master_gain_db"], "master_gain_db", -24, 24),
        "conversion_policy": CONVERSION_POLICY, "runtime": None,
    }


def assert_current(context):
    for item, label in (
        ({"path": context["path"], "sha256": context["sha256"]}, "audio mix adoption"),
        (context["picture"], "picture"),
        (context["prepared_receipt"], "prepared receipt"),
        (context["narration_adoption"], "narration adoption"),
    ):
        _assert_hash(item["path"], item["sha256"], label)
    for name, identity in context["prepared"].items():
        _assert_hash(identity["path"], identity["sha256"], name)
    runtime = context.get("runtime")
    if runtime:
        for item in runtime["segments"]:
            _assert_hash(item["placed"]["path"], item["placed"]["sha256"], "placed narration")
        for key in ("voice_bus", "premaster", "master"):
            _assert_hash(runtime[key]["path"], runtime[key]["sha256"], key)
        if "final_decoded_pcm" in runtime:
            _assert_hash(runtime["final_decoded_pcm"]["path"],
                         runtime["final_decoded_pcm"]["sha256"], "final decoded PCM")


def _run(command, work_dir, label):
    (work_dir / f"explicit_{label}.command.json").write_text(
        json.dumps(command, ensure_ascii=False, indent=2) + "\n"
    )
    result = subprocess.run(command, capture_output=True, text=True, timeout=3600)
    (work_dir / f"explicit_{label}.log").write_text(result.stderr)
    if result.returncode:
        raise RuntimeError(f"explicit {label} FFmpeg failed")


def _channels(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
         "stream=channels", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, timeout=600,
    )
    try:
        channels = int(result.stdout.strip())
    except ValueError as exc:
        raise ValueError("narration channel probe failed") from exc
    if result.returncode or channels not in {1, 2}:
        raise ValueError("explicit narration supports only mono or stereo input")
    return channels


def render_explicit_mix(context, narration_context, tts_segments, work_dir):
    """Render complete direct 48 kHz placements, voice bus, premaster, and fixed master."""
    assert_current(context)
    narration_binding.assert_current(narration_context)
    directory = Path(work_dir).resolve() / ".explicit_audio_mix"
    directory.mkdir(parents=True, exist_ok=False)
    by_index = {item["index"]: item for item in narration_context["segments"]}
    segment_memory = {item["index"]: item for item in tts_segments}
    rendered = []
    for position, adopted in enumerate(context["segments"]):
        source = by_index[adopted["index"]]["snapshot"]["path"]
        channels = _channels(source)
        placed = directory / f"placed_{position:04d}_{adopted['index']}.wav"
        if channels == 1:
            channel_filter = (
                "aformat=sample_fmts=flt,"
                "pan=stereo|c0=0.7071067811865476*c0|c1=0.7071067811865476*c0,"
                "aresample=48000:async=0:first_pts=0,aformat=sample_fmts=flt:"
                "sample_rates=48000:channel_layouts=stereo"
            )
            matrix = "mono_equal_power"
        else:
            channel_filter = (
                "aresample=48000:async=0:first_pts=0,aformat=sample_fmts=flt:"
                "sample_rates=48000:channel_layouts=stereo"
            )
            matrix = "stereo_identity"
        _run(["ffmpeg", "-nostdin", "-v", "error", "-n", "-i", str(source),
              "-map", "0:a:0", "-af", channel_filter, "-c:a", CODEC, str(placed)],
             directory, f"place_{position:04d}")
        identity = source_score._output_identity(placed)
        start = adopted["output_start_sample"]
        end = start + identity["pcm"]["samples"]
        if end > context["format"]["total_samples"]:
            raise ValueError("complete narration segment does not fit the adopted mix clock")
        rendered.append({**adopted, "output_end_sample": end, "input_channels": channels,
                         "channel_matrix": matrix, "placed": identity})
        memory = segment_memory[adopted["index"]]
        memory.update({
            "placed_audio_path": str(placed),
            "narration_conversion_path": str(placed),
            "narration_conversion_policy": CONVERSION_POLICY,
            "audio_duration": identity["pcm"]["samples"] / RATE,
            "placed_audio_duration": identity["pcm"]["samples"] / RATE,
            "actual_place_start": start / RATE, "actual_place_end": end / RATE,
            "output_start_sample": start, "output_end_sample": end,
            "adopted_gain": adopted["gain"], "conversion_policy": CONVERSION_POLICY,
            "global_narration_speed": 1.0, "segment_tempo_factor": 1.0,
            "effective_tempo": 1.0, "fit_status": "placed", "blocking": False,
            "truncated": False, "truncate_reason": "none",
        })
    ordered = sorted(rendered, key=lambda item: item["output_start_sample"])
    if any(current["output_start_sample"] < previous["output_end_sample"]
           for previous, current in zip(ordered, ordered[1:])):
        raise ValueError("explicit narration placements overlap")

    total = context["format"]["total_samples"]
    voice_bus = directory / "voice_bus.wav"
    command = ["ffmpeg", "-nostdin", "-v", "error", "-n"]
    for item in rendered:
        command += ["-i", item["placed"]["path"]]
    filters = [f"anullsrc=r={RATE}:cl=stereo,atrim=end_sample={total}[base]"]
    labels = ["[base]"]
    for position, item in enumerate(rendered):
        filters.append(
            f"[{position}:a]volume={item['gain']:.17g},"
            f"adelay={item['output_start_sample']}S:all=1[v{position}]"
        )
        labels.append(f"[v{position}]")
    filters.append("".join(labels) + f"amix=inputs={len(labels)}:duration=first:normalize=0,"
                   f"atrim=end_sample={total},aformat=sample_fmts=flt:sample_rates={RATE}:"
                   "channel_layouts=stereo[out]")
    command += ["-filter_complex", ";".join(filters), "-map", "[out]", "-c:a", CODEC,
                str(voice_bus)]
    for item in rendered:
        _assert_hash(item["placed"]["path"], item["placed"]["sha256"], "placed narration")
    _run(command, directory, "voice_bus")
    for item in rendered:
        _assert_hash(item["placed"]["path"], item["placed"]["sha256"], "placed narration")
    voice_identity = source_score._output_identity(voice_bus)
    narration_binding.seal_render_inputs(narration_context, tts_segments, voice_bus)
    narration_context["sealed"]["narration_bus"]["consumption_status"] = \
        "CONSUMED_BY_EXPLICIT_MIX"

    prepared = context["prepared"]["prepared_bed.wav"]["path"]
    premaster = directory / "premaster.wav"
    assert_current(context)
    narration_binding.assert_current(narration_context)
    _run(["ffmpeg", "-nostdin", "-v", "error", "-n", "-i", prepared, "-i", str(voice_bus),
          "-filter_complex", f"[0:a][1:a]amix=inputs=2:duration=first:normalize=0,"
          f"atrim=end_sample={total},aformat=sample_fmts=flt:sample_rates={RATE}:"
          "channel_layouts=stereo[out]", "-map", "[out]", "-c:a", CODEC, str(premaster)],
         directory, "premaster")
    assert_current(context)
    narration_binding.assert_current(narration_context)
    premaster_identity = source_score._output_identity(premaster)
    master = directory / "master.wav"
    gain = 10 ** (context["master_gain_db"] / 20)
    _assert_hash(premaster, premaster_identity["sha256"], "premaster")
    _run(["ffmpeg", "-nostdin", "-v", "error", "-n", "-i", str(premaster),
          "-af", f"volume={gain:.17g},aformat=sample_fmts=flt:sample_rates={RATE}:"
          "channel_layouts=stereo", "-c:a", CODEC, str(master)], directory, "master")
    _assert_hash(premaster, premaster_identity["sha256"], "premaster")
    runtime = {
        "segments": rendered, "voice_bus": voice_identity,
        "premaster": premaster_identity,
        "master": source_score._output_identity(master), "master_gain_linear": gain,
    }
    if any(runtime[key]["pcm"]["samples"] != total
           for key in ("voice_bus", "premaster", "master")):
        raise ValueError("explicit mix derivative sample count differs from picture clock")
    context["runtime"] = runtime
    assert_current(context)
    narration_binding.assert_current(narration_context)
    return runtime


def stage_final_binding(context, narration_fingerprint, rendered_output, final_output):
    if not context.get("runtime"):
        raise RuntimeError("explicit audio mix must be rendered and sealed before finalization")
    assert_current(context)
    rendered = _local(rendered_output, "rendered output")
    rendered_digest = _sha256(rendered)
    output_picture = probe_picture(rendered)
    input_clock = _picture_format(context["picture_identity"])[1]
    output_clock = _picture_format(output_picture)[1]
    for key in ("fps", "frame_count", "duration", "start"):
        if output_clock[key] != input_clock[key]:
            raise ValueError("rendered output picture frame clock changed")
    packet_identity = (
        "EXACT" if output_picture == context["picture_identity"]
        else "REENCODED_CLOCK_MATCH"
    )
    audio = probe_audio_packets(rendered, 0)
    validate_pair_timing(output_picture, audio)
    decoded = Path(context["runtime"]["master"]["path"]).parent / "final_aac_decoded.wav"
    _run(["ffmpeg", "-nostdin", "-v", "error", "-n", "-i", str(rendered),
          "-map", "0:a:0", "-af", "aformat=sample_fmts=flt:sample_rates=48000:"
          "channel_layouts=stereo", "-c:a", CODEC, str(decoded)], decoded.parent,
         "final_decode")
    context["runtime"]["final_decoded_pcm"] = source_score._output_identity(decoded)
    _assert_hash(rendered, rendered_digest, "rendered output")
    assert_current(context)
    report = {
        "artifact": ARTIFACT, "schema_version": 1, "status": "FINALIZED",
        "adoption": {"path": context["path"], "sha256": context["sha256"]},
        "picture": context["picture"],
        "output_picture": {
            **output_clock, "packet_identity": packet_identity,
        },
        "prepared_receipt": context["prepared_receipt"],
        "prepared": context["prepared"], "format": context["format"],
        "conversion_policy": context["conversion_policy"],
        "segments": context["runtime"]["segments"],
        "voice_bus": context["runtime"]["voice_bus"],
        "premaster": context["runtime"]["premaster"],
        "master": {**context["runtime"]["master"], "gain_db": context["master_gain_db"],
                   "gain_linear": context["runtime"]["master_gain_linear"]},
        "narration_input_binding": {
            "path": narration_fingerprint["path"],
            "sha256": narration_fingerprint["sha256"], "status": "FINALIZED",
        },
        "final_output": {
            "path": str(Path(final_output).resolve()), "sha256": rendered_digest,
            "decoded_pcm": context["runtime"]["final_decoded_pcm"],
            "audio_stream_identity": {
                "decoder": audio["decoder"], "packet_count": audio["packet_count"],
                "payload_sha256": audio["payload_sha256"],
                "start_time": audio["start_time"], "duration": audio["duration"],
            },
        },
        "direct_listening": "NOT_CHECKED", "normal_speed_review": "NOT_CHECKED",
        "release_approved": False,
    }
    staged = Path(final_output).resolve().parent / ".audio_mix_binding.rendering.json"
    staged.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report, staged


def staged_binding_fingerprint(report, staged_path, final_path):
    if not isinstance(report, dict) or report.get("artifact") != ARTIFACT \
            or report.get("schema_version") != 1 or report.get("status") != "FINALIZED":
        raise RuntimeError("finalized audio mix binding is missing")
    staged = _local(staged_path, "staged audio mix binding")
    return {"path": str(Path(final_path).resolve()), "sha256": _sha256(staged),
            "status": "FINALIZED", "publication_status": "STAGED_UNPUBLISHED"}


def finalize_binding(staged_path, work_dir):
    destination = Path(work_dir).resolve() / FILENAME
    Path(staged_path).replace(destination)
    return json.loads(destination.read_text(encoding="utf-8"))


def binding_fingerprint(work_dir):
    path = Path(work_dir) / FILENAME
    if not path.is_file():
        return None
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(report, dict) or report.get("artifact") != ARTIFACT \
            or report.get("schema_version") != 1 or report.get("status") != "FINALIZED":
        return None
    final = report.get("final_output")
    if not isinstance(final, dict) or not SHA256.fullmatch(str(final.get("sha256", ""))):
        return None
    output = Path(str(final.get("path", "")))
    if not output.is_file() or _sha256(output) != final["sha256"]:
        return None
    narration = report.get("narration_input_binding")
    if not isinstance(narration, dict) or not SHA256.fullmatch(
        str(narration.get("sha256", ""))
    ):
        return None
    narration_path = Path(str(narration.get("path", "")))
    if not narration_path.is_file() or _sha256(narration_path) != narration["sha256"]:
        return None
    return {"path": str(path.resolve()), "sha256": _sha256(path), "status": "FINALIZED"}
