"""Bind adopted narration bytes through snapshot, placement, mix, and final output."""

import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess

from frozen_audio import probe_audio_packets
from strict_inputs import (
    SHA256_RE as SHA256, read_json_bytes, require_digest, require_fields,
    require_local_path, sha256_file, write_json_atomic,
)


ARTIFACT = "narration_input_binding"
FILENAME = "narration_input_binding.json"
# The conservative default an adoption gets when it declares nothing stronger.
TEMPO_POLICY = {
    "global_atempo": 1.0,
    "bounded_segment_fit": False,
    "segment_tempo_max": 1.0,
    "cumulative_tempo_max": 1.0,
    "cumulative_tempo_hard_max": 1.0,
}
TEMPO_NUMBERS = ("global_atempo", "segment_tempo_max", "cumulative_tempo_max",
                 "cumulative_tempo_hard_max")


def validate_tempo_policy(value):
    """Accept any adoption-declared tempo policy whose shape and bounds hold.

    The adoption, not this module, decides how fast its own narration may be
    played; the module only refuses policies that are malformed or that would
    let a segment exceed the cumulative hard ceiling the policy itself declares.
    """
    require_fields(value, sorted(TEMPO_POLICY), "tempo_policy")
    policy = {}
    for key in TEMPO_NUMBERS:
        number = value[key]
        if type(number) not in (int, float) or not math.isfinite(number):
            raise ValueError(f"tempo_policy {key} must be a finite number")
        policy[key] = float(number)
    if type(value["bounded_segment_fit"]) is not bool:
        raise ValueError("tempo_policy bounded_segment_fit must be a boolean")
    policy["bounded_segment_fit"] = value["bounded_segment_fit"]
    if policy["cumulative_tempo_max"] < 1.0:
        raise ValueError("tempo_policy cumulative_tempo_max must be at least 1.0")
    if policy["cumulative_tempo_hard_max"] < policy["cumulative_tempo_max"]:
        raise ValueError("tempo_policy cumulative_tempo_hard_max must not be below "
                         "cumulative_tempo_max")
    if policy["segment_tempo_max"] < 1.0:
        raise ValueError("tempo_policy segment_tempo_max must be at least 1.0")
    if not 0 < policy["global_atempo"] <= policy["cumulative_tempo_hard_max"]:
        raise ValueError("tempo_policy global_atempo must be positive and within "
                         "cumulative_tempo_hard_max")
    return policy


def load_adoption(path, *, tts_meta_path, tts_segments):
    """Load strict v1 adoption and bind it to exact current tts_meta bytes/memory."""
    if tts_meta_path is None:
        raise ValueError("narration adoption requires explicit tts_meta_path")
    adoption_path, adoption_raw, adoption = read_json_bytes(path, "narration adoption")
    require_fields(adoption, ["artifact", "schema_version", "tts_meta_sha256", "segments",
                       "tempo_policy"], "narration adoption")
    if adoption["artifact"] != "narration_adoption" or type(adoption["schema_version"]) is not int \
            or adoption["schema_version"] != 1:
        raise ValueError("unsupported narration_adoption schema")
    tts_meta_path, tts_raw, tts_meta = read_json_bytes(tts_meta_path, "tts_meta")
    expected_meta_hash = require_digest(adoption["tts_meta_sha256"], "tts_meta_sha256")
    if hashlib.sha256(tts_raw).hexdigest() != expected_meta_hash:
        raise ValueError("tts_meta bytes do not match narration adoption")
    if not isinstance(tts_meta, dict) or not isinstance(tts_meta.get("segments"), list):
        raise ValueError("tts_meta requires a segments list")
    if tts_meta["segments"] != tts_segments:
        raise ValueError("in-memory narration segments differ from bound tts_meta")
    tempo_policy = validate_tempo_policy(adoption["tempo_policy"])
    if not isinstance(adoption["segments"], list) or len(adoption["segments"]) != len(tts_segments):
        raise ValueError("adoption segments must exactly cover tts_meta segments")
    normalized_segments = []
    for adopted, actual in zip(adoption["segments"], tts_segments):
        require_fields(adopted, ["index", "spoken_text", "processed_wav_sha256",
                          "requested_provider", "requested_voice"], "adoption segment")
        if type(adopted["index"]) is not int or adopted["index"] != actual.get("index"):
            raise ValueError("adoption segment index/order differs from tts_meta")
        spoken = actual.get("spoken_text", actual.get("narration"))
        if not isinstance(adopted["spoken_text"], str) or adopted["spoken_text"] != spoken:
            raise ValueError("adoption spoken_text differs from tts_meta")
        require_digest(adopted["processed_wav_sha256"], "adoption processed_wav_sha256")
        if adopted["processed_wav_sha256"] != actual.get("processed_wav_sha256"):
            raise ValueError("adoption segment hash differs from bound tts_meta")
        for key in ("requested_provider", "requested_voice"):
            if not isinstance(adopted[key], str) or not adopted[key]:
                raise ValueError(f"adoption {key} must be a non-empty string")
        normalized_segments.append(dict(adopted))
    return {
        "path": str(adoption_path), "sha256": hashlib.sha256(adoption_raw).hexdigest(),
        "tts_meta": {"path": str(tts_meta_path), "sha256": expected_meta_hash},
        "segments": normalized_segments, "tempo_policy": tempo_policy,
    }


def _receipt_evidence(segment, adopted):
    receipt = segment.get("provider_receipt")
    if receipt is None:
        return "UNKNOWN"
    if not isinstance(receipt, dict):
        raise ValueError("provider_receipt must be an object when present")
    comparisons = {
        "processed_wav_sha256": adopted["processed_wav_sha256"] if adopted else segment.get(
            "processed_wav_sha256"
        ),
        "provider": adopted["requested_provider"] if adopted else None,
        "requested_voice": adopted["requested_voice"] if adopted else None,
    }
    checked = False
    for key, expected in comparisons.items():
        if key in receipt and expected is not None:
            checked = True
            if receipt[key] != expected:
                raise ValueError(f"provider receipt {key} contradicts adopted narration")
    return "RECEIPT_MATCHED" if checked else "UNKNOWN"


def _copy_snapshot(source, destination):
    """Copy seam kept small so tests can inject a post-preflight source mutation."""
    shutil.copyfile(source, destination)


def prepare_binding(tts_segments, work_dir, *, narration_adoption_path=None,
                    tts_meta_path=None):
    """Validate first, then snapshot identity-constrained narration inputs."""
    if not isinstance(tts_segments, list):
        raise ValueError("tts_segments must be a list")
    adoption = (
        load_adoption(narration_adoption_path, tts_meta_path=tts_meta_path,
                      tts_segments=tts_segments)
        if narration_adoption_path is not None else None
    )
    supplied_hashes = [segment.get("processed_wav_sha256") for segment in tts_segments]
    any_hash = any(value is not None for value in supplied_hashes)
    if any_hash and not all(value is not None for value in supplied_hashes):
        raise ValueError("processed_wav_sha256 coverage must be complete or entirely legacy")
    identity_status = (
        "BOUND_TO_ADOPTION" if adoption else
        "DECLARED_HASH_BOUND_UNADOPTED" if any_hash else
        "LEGACY_UNVERIFIED"
    )
    if not adoption and not any_hash:
        originals = []
        for position, segment in enumerate(tts_segments):
            path = Path(str(segment.get("audio_path", ""))).resolve()
            originals.append({
                "index": segment.get("index", position), "source": path,
                "sha256": sha256_file(path) if path.is_file() else None,
                "spoken_text": segment.get("spoken_text", segment.get("narration")),
            })
        return {
            "identity_status": identity_status, "adoption": None, "tempo_policy": None,
            "originals": originals, "active": False, "segments": [],
        }
    originals = []
    seen_indices = set()
    for position, segment in enumerate(tts_segments):
        if not isinstance(segment, dict) or type(segment.get("index")) is not int:
            raise ValueError("each narration segment requires an integer index")
        if segment["index"] in seen_indices:
            raise ValueError("narration segment indices must be unique")
        seen_indices.add(segment["index"])
        adopted = adoption["segments"][position] if adoption else None
        declared = segment.get("processed_wav_sha256")
        if declared is not None:
            require_digest(declared, "processed_wav_sha256")
        if adopted and declared != adopted["processed_wav_sha256"]:
            raise ValueError("tts_meta processed hash differs from narration adoption")
        source = require_local_path(segment.get("audio_path"), "narration audio")
        actual_hash = sha256_file(source)
        if declared is not None and actual_hash != declared:
            raise ValueError("narration audio hash identity mismatch")
        if adopted and actual_hash != adopted["processed_wav_sha256"]:
            raise ValueError("adopted narration audio hash identity mismatch")
        originals.append({
            "index": segment["index"], "source": source, "sha256": actual_hash,
            "spoken_text": segment.get("spoken_text", segment.get("narration")),
            "requested_provider": adopted["requested_provider"] if adopted else None,
            "requested_voice": adopted["requested_voice"] if adopted else None,
            "request_evidence": _receipt_evidence(segment, adopted),
        })
    context = {
        "identity_status": identity_status, "adoption": adoption,
        "tempo_policy": adoption["tempo_policy"] if adoption else None,
        "originals": originals, "active": adoption is not None or any_hash,
        "segments": [], "sealed": None,
    }
    snapshot_dir = Path(work_dir).resolve() / ".narration_input_snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=False)
    for position, (segment, original) in enumerate(zip(tts_segments, originals)):
        suffix = original["source"].suffix or ".audio"
        snapshot = snapshot_dir / f"segment_{position:04d}_{segment['index']}{suffix}"
        _copy_snapshot(original["source"], snapshot)
        if sha256_file(snapshot) != original["sha256"]:
            raise ValueError("narration snapshot differs from validated input")
        segment["audio_path"] = str(snapshot)
        segment["narration_input_original"] = {
            "path": str(original["source"]), "sha256": original["sha256"]
        }
        segment["narration_input_snapshot"] = {
            "path": str(snapshot), "sha256": original["sha256"]
        }
        context["segments"].append({
            "index": original["index"], "spoken_text": original["spoken_text"],
            "requested_provider": original["requested_provider"],
            "requested_voice": original["requested_voice"],
            "request_evidence": original["request_evidence"],
            "original": dict(segment["narration_input_original"]),
            "snapshot": dict(segment["narration_input_snapshot"]),
        })
    assert_current(context)
    return context


def assert_current(context):
    if not context.get("active"):
        return
    adoption = context.get("adoption")
    if adoption:
        if sha256_file(adoption["path"]) != adoption["sha256"]:
            raise ValueError("narration adoption changed during assembly")
        if sha256_file(adoption["tts_meta"]["path"]) != adoption["tts_meta"]["sha256"]:
            raise ValueError("tts_meta changed during assembly")
    for item in context["segments"]:
        if sha256_file(item["original"]["path"]) != item["original"]["sha256"]:
            raise ValueError("original narration input changed during assembly")
        if sha256_file(item["snapshot"]["path"]) != item["snapshot"]["sha256"]:
            raise ValueError("narration snapshot changed during assembly")
    sealed = context.get("sealed")
    if sealed:
        for item in sealed["segments"]:
            assets = [item["placed"]]
            if item["conversion"]["applied"]:
                assets.append(item["conversion"])
            for asset in assets:
                if sha256_file(asset["path"]) != asset["sha256"]:
                    raise ValueError("sealed narration derivative changed during assembly")
        bus = sealed["narration_bus"]
        if sha256_file(bus["path"]) != bus["sha256"]:
            raise ValueError("sealed narration bus changed during assembly")


def _pcm(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_streams",
         "-show_entries", "stream=codec_name,sample_fmt,sample_rate,channels,channel_layout",
         "-of", "json", str(path)], capture_output=True, text=True, timeout=600,
    )
    if result.returncode:
        raise ValueError(f"audio probe failed: {result.stderr.strip()}")
    streams = json.loads(result.stdout).get("streams", [])
    if len(streams) != 1:
        raise ValueError("expected exactly one audio stream")
    stream = streams[0]
    return {key: stream.get(key) for key in (
        "codec_name", "sample_fmt", "sample_rate", "channels", "channel_layout"
    )}


def _asset(path, *, pcm=False):
    path = require_local_path(path, "binding asset")
    value = {"path": str(path), "sha256": sha256_file(path)}
    if pcm:
        value["pcm"] = _pcm(path)
    return value


def seal_render_inputs(context, tts_segments, narration_wav):
    """Seal every derived audio byte that the final FFmpeg command will consume."""
    if not context.get("active"):
        return None
    by_index = {segment.get("index", position): segment
                for position, segment in enumerate(tts_segments)}
    sealed_segments = []
    for item in context["segments"]:
        segment = by_index[item["index"]]
        placed = segment.get("placed_audio_path")
        if not placed:
            raise RuntimeError("active narration binding has no complete placed audio")
        conversion = segment.get("narration_conversion_path")
        conversion_policy = segment.get("narration_conversion_policy")
        conversion_asset = (
            {"applied": True, **_asset(conversion, pcm=True)}
            if conversion else {"applied": False}
        )
        if conversion_policy:
            conversion_asset["policy"] = conversion_policy
        sealed_segments.append({
            "index": item["index"],
            "conversion": conversion_asset,
            "placed": _asset(placed, pcm=True),
        })
    context["sealed"] = {
        "segments": sealed_segments,
        "narration_bus": _asset(narration_wav, pcm=True),
    }
    assert_current(context)
    return context["sealed"]


def _active_report(context, rendered_output, final_output):
    if not context.get("sealed"):
        raise RuntimeError("active narration inputs must be sealed before finalization")
    assert_current(context)
    sealed = {item["index"]: item for item in context["sealed"]["segments"]}
    segments = []
    for item in context["segments"]:
        segments.append({
            **item,
            "original": _asset(item["original"]["path"], pcm=True),
            "snapshot": _asset(item["snapshot"]["path"], pcm=True),
            "conversion": sealed[item["index"]]["conversion"],
            "placed": sealed[item["index"]]["placed"],
        })
    rendered_path = require_local_path(rendered_output, "rendered output")
    final_audio = probe_audio_packets(rendered_path, 0)
    return {
        "artifact": ARTIFACT, "schema_version": 1, "status": "FINALIZED",
        "identity_status": context["identity_status"], "adoption": context["adoption"],
        "segments": segments, "narration_bus": context["sealed"]["narration_bus"],
        "final_output": {
            "path": str(Path(final_output).resolve()), "sha256": sha256_file(rendered_path),
            "audio_stream_identity": {
                "decoder": final_audio["decoder"], "packet_count": final_audio["packet_count"],
                "payload_sha256": final_audio["payload_sha256"],
                "start_time": final_audio["start_time"], "duration": final_audio["duration"],
            },
        },
        "voice_authentication": "NOT_CHECKED", "direct_listening": "NOT_CHECKED",
    }


def stage_final_binding(context, tts_segments, narration_wav, rendered_output, final_output):
    """Write a complete binding beside staged media without publishing it."""
    del tts_segments, narration_wav  # already sealed; reject post-render substitutions
    report = _active_report(context, rendered_output, final_output)
    destination = Path(final_output).resolve().parent / ".narration_input_binding.rendering.json"
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report, destination


def staged_binding_fingerprint(report, staged_path, final_path):
    if not isinstance(report, dict) or report.get("status") != "FINALIZED":
        raise RuntimeError("active finalized narration binding is missing")
    staged_path = require_local_path(staged_path, "staged narration binding")
    return {
        "path": str(Path(final_path).resolve()), "sha256": sha256_file(staged_path),
        "identity_status": report["identity_status"],
        "tempo_policy": (
            report["adoption"]["tempo_policy"] if report.get("adoption") else None
        ),
        "publication_status": "STAGED_UNPUBLISHED",
    }


def finalize_binding(context, tts_segments, narration_wav, final_output, *, staged_path=None):
    assert_current(context)
    if context.get("active") and staged_path is not None:
        destination = Path(narration_wav).resolve().parent / FILENAME
        Path(staged_path).replace(destination)
        return json.loads(destination.read_text(encoding="utf-8"))
    if not context.get("active"):
        report = {
            "artifact": ARTIFACT, "schema_version": 1, "status": "FINALIZED",
            "identity_status": "LEGACY_UNVERIFIED", "adoption": None,
            "segments": [
                {
                    "index": original["index"], "spoken_text": original["spoken_text"],
                    "requested_provider": None, "requested_voice": None,
                    "request_evidence": "UNKNOWN",
                    "original": {"path": str(original["source"]),
                                 "sha256": original["sha256"]},
                    "snapshot": None, "conversion": {"applied": False},
                    "placed": None,
                }
                for original in context["originals"]
            ],
            "narration_bus": ({"path": str(Path(narration_wav).resolve()),
                               "sha256": sha256_file(narration_wav)}
                              if Path(narration_wav).is_file() else None),
            "final_output": ({"path": str(Path(final_output).resolve()),
                              "sha256": sha256_file(final_output)}
                             if Path(final_output).is_file() else None),
            "voice_authentication": "NOT_CHECKED", "direct_listening": "NOT_CHECKED",
        }
        write_json_atomic(Path(narration_wav).resolve().parent / FILENAME, report)
        return report
    report = _active_report(context, final_output, final_output)
    write_json_atomic(Path(narration_wav).resolve().parent / FILENAME, report)
    return report


def binding_fingerprint(work_dir):
    path = Path(work_dir) / FILENAME
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(value, dict)
        or value.get("artifact") != ARTIFACT
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or value.get("status") != "FINALIZED"
        or value.get("identity_status") not in {
            "LEGACY_UNVERIFIED", "DECLARED_HASH_BOUND_UNADOPTED", "BOUND_TO_ADOPTION"
        }
    ):
        return None
    if value["identity_status"] == "BOUND_TO_ADOPTION":
        adoption = value.get("adoption")
        if not isinstance(adoption, dict):
            return None
        try:
            validate_tempo_policy(adoption.get("tempo_policy"))
        except ValueError:
            return None
    final_output = value.get("final_output")
    if not isinstance(final_output, dict):
        return None
    final_path = Path(str(final_output.get("path", "")))
    expected = final_output.get("sha256")
    if not final_path.is_file() or not SHA256.fullmatch(str(expected)) \
            or sha256_file(final_path) != expected:
        return None
    return {"path": str(path.resolve()), "sha256": sha256_file(path),
            "identity_status": value["identity_status"],
            "tempo_policy": (
                value["adoption"]["tempo_policy"]
                if value["identity_status"] == "BOUND_TO_ADOPTION" else None
            )}
