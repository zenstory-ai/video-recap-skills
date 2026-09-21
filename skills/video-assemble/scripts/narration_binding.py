"""Record which narration inputs an assembly consumed: adoption, snapshot, placement, output."""

import json
import math
from pathlib import Path
import shutil
import subprocess

from frozen_audio import probe_audio_packets
from strict_inputs import (
    read_json_bytes, require_fields, require_local_path, without_digests, write_json_atomic,
)


ARTIFACT = "narration_input_binding"
FILENAME = "narration_input_binding.json"
IDENTITY_STATUSES = frozenset({"UNADOPTED", "BOUND_TO_ADOPTION"})
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
    """Load a strict v1 adoption and check it against the current tts_meta segments."""
    if tts_meta_path is None:
        raise ValueError("narration adoption requires explicit tts_meta_path")
    adoption_path, _, adoption = read_json_bytes(path, "narration adoption")
    adoption = without_digests(adoption, "narration adoption")
    require_fields(adoption, ["artifact", "schema_version", "segments", "tempo_policy"],
                   "narration adoption")
    if adoption["artifact"] != "narration_adoption" or type(adoption["schema_version"]) is not int \
            or adoption["schema_version"] != 1:
        raise ValueError("unsupported narration_adoption schema")
    tts_meta_path, _, tts_meta = read_json_bytes(tts_meta_path, "tts_meta")
    if not isinstance(tts_meta, dict) or not isinstance(tts_meta.get("segments"), list):
        raise ValueError("tts_meta requires a segments list")
    if tts_meta["segments"] != tts_segments:
        raise ValueError("in-memory narration segments differ from bound tts_meta")
    tempo_policy = validate_tempo_policy(adoption["tempo_policy"])
    if not isinstance(adoption["segments"], list) or len(adoption["segments"]) != len(tts_segments):
        raise ValueError("adoption segments must exactly cover tts_meta segments")
    normalized_segments = []
    for adopted, actual in zip(adoption["segments"], tts_segments):
        adopted = without_digests(adopted, "adoption segment")
        require_fields(adopted, ["index", "spoken_text", "requested_provider", "requested_voice"],
                       "adoption segment")
        if type(adopted["index"]) is not int or adopted["index"] != actual.get("index"):
            raise ValueError("adoption segment index/order differs from tts_meta")
        spoken = actual.get("spoken_text", actual.get("narration"))
        if not isinstance(adopted["spoken_text"], str) or adopted["spoken_text"] != spoken:
            raise ValueError("adoption spoken_text differs from tts_meta")
        for key in ("requested_provider", "requested_voice"):
            if not isinstance(adopted[key], str) or not adopted[key]:
                raise ValueError(f"adoption {key} must be a non-empty string")
        normalized_segments.append(dict(adopted))
    return {
        "path": str(adoption_path),
        "tts_meta": {"path": str(tts_meta_path)},
        "segments": normalized_segments, "tempo_policy": tempo_policy,
    }


def _copy_snapshot(source, destination):
    """Copy seam kept small so tests can inject a post-preflight source mutation."""
    shutil.copyfile(source, destination)


def prepare_binding(tts_segments, work_dir, *, narration_adoption_path=None,
                    tts_meta_path=None):
    """Validate first, then snapshot the adopted narration inputs into work_dir."""
    if not isinstance(tts_segments, list):
        raise ValueError("tts_segments must be a list")
    adoption = (
        load_adoption(narration_adoption_path, tts_meta_path=tts_meta_path,
                      tts_segments=tts_segments)
        if narration_adoption_path is not None else None
    )
    if not adoption:
        originals = [
            {"index": segment["index"], "source": Path(segment["audio_path"]).resolve(),
             "spoken_text": segment["spoken_text"]}
            for segment in tts_segments
        ]
        return {
            "identity_status": "UNADOPTED", "adoption": None, "tempo_policy": None,
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
        adopted = adoption["segments"][position]
        source = require_local_path(segment["audio_path"], "narration audio")
        originals.append({
            "index": segment["index"], "source": source,
            "spoken_text": segment["spoken_text"],
            "requested_provider": adopted["requested_provider"],
            "requested_voice": adopted["requested_voice"],
        })
    context = {
        "identity_status": "BOUND_TO_ADOPTION", "adoption": adoption,
        "tempo_policy": adoption["tempo_policy"],
        "originals": originals, "active": True, "segments": [], "sealed": None,
    }
    snapshot_dir = Path(work_dir).resolve() / ".narration_input_snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=False)
    for position, (segment, original) in enumerate(zip(tts_segments, originals)):
        suffix = original["source"].suffix or ".audio"
        snapshot = snapshot_dir / f"segment_{position:04d}_{segment['index']}{suffix}"
        _copy_snapshot(original["source"], snapshot)
        segment["audio_path"] = str(snapshot)
        segment["narration_input_original"] = {"path": str(original["source"])}
        segment["narration_input_snapshot"] = {"path": str(snapshot)}
        context["segments"].append({
            "index": original["index"], "spoken_text": original["spoken_text"],
            "requested_provider": original["requested_provider"],
            "requested_voice": original["requested_voice"],
            "original": dict(segment["narration_input_original"]),
            "snapshot": dict(segment["narration_input_snapshot"]),
        })
    return context


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
    value = {"path": str(path)}
    if pcm:
        value["pcm"] = _pcm(path)
    return value


def seal_render_inputs(context, tts_segments, narration_wav):
    """Record every derived audio file that the final FFmpeg command will consume."""
    if not context.get("active"):
        return None
    by_index = {segment["index"]: segment for segment in tts_segments}
    sealed_segments = []
    for item in context["segments"]:
        segment = by_index[item["index"]]
        placed = segment.get("placed_audio_path")
        if not placed:
            raise RuntimeError("active narration binding has no complete placed audio")
        conversion = segment.get("narration_conversion_path")
        conversion_asset = (
            {"applied": True, **_asset(conversion, pcm=True)}
            if conversion else {"applied": False}
        )
        sealed_segments.append({
            "index": item["index"],
            "conversion": conversion_asset,
            "placed": _asset(placed, pcm=True),
        })
    context["sealed"] = {
        "segments": sealed_segments,
        "narration_bus": _asset(narration_wav, pcm=True),
    }
    return context["sealed"]


def _active_report(context, rendered_output, final_output):
    if not context.get("sealed"):
        raise RuntimeError("active narration inputs must be sealed before finalization")
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
            "path": str(Path(final_output).resolve()),
            "audio_stream": {
                "decoder": final_audio["decoder"], "packet_count": final_audio["packet_count"],
                "payload_bytes": final_audio["payload_bytes"],
                "start_time": final_audio["start_time"], "duration": final_audio["duration"],
            },
        },
        "voice_authentication": "NOT_CHECKED", "direct_listening": "NOT_CHECKED",
    }


def finalize_binding(context, tts_segments, narration_wav, final_output, *, rendered_output=None):
    """Write ``narration_input_binding.json`` beside ``narration_wav`` and return it.

    ``rendered_output`` is the candidate file to probe when the published
    ``final_output`` path does not exist yet; it defaults to ``final_output``.
    """
    del tts_segments  # already sealed; the record describes what was consumed
    if not context.get("active"):
        report = {
            "artifact": ARTIFACT, "schema_version": 1, "status": "FINALIZED",
            "identity_status": "UNADOPTED", "adoption": None,
            "segments": [
                {
                    "index": original["index"], "spoken_text": original["spoken_text"],
                    "requested_provider": None, "requested_voice": None,
                    "original": {"path": str(original["source"])},
                    "snapshot": None, "conversion": {"applied": False},
                    "placed": None,
                }
                for original in context["originals"]
            ],
            "narration_bus": ({"path": str(Path(narration_wav).resolve())}
                              if Path(narration_wav).is_file() else None),
            "final_output": ({"path": str(Path(final_output).resolve())}
                             if Path(final_output).is_file() else None),
            "voice_authentication": "NOT_CHECKED", "direct_listening": "NOT_CHECKED",
        }
    else:
        report = _active_report(
            context, final_output if rendered_output is None else rendered_output, final_output
        )
    write_json_atomic(Path(narration_wav).resolve().parent / FILENAME, report)
    return report


def record_of(report, path):
    """The manifest/QC summary of one finalized binding report stored at ``path``."""
    return {"path": str(Path(path).resolve()),
            "identity_status": report["identity_status"],
            "tempo_policy": (
                report["adoption"]["tempo_policy"]
                if report["identity_status"] == "BOUND_TO_ADOPTION" else None
            )}


def binding_record(work_dir):
    """The finalized binding's path, identity status and tempo policy; None when absent/invalid."""
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
        or value.get("identity_status") not in IDENTITY_STATUSES
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
    if not isinstance(final_output, dict) or not Path(str(final_output.get("path", ""))).is_file():
        return None
    return record_of(value, path)
