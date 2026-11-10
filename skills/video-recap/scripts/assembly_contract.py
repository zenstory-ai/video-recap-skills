"""Assembly manifest/QC persistence and delivery contract helpers."""

import json
from fractions import Fraction
import math
import subprocess
import wave
from pathlib import Path

from lib import CONFIG
from assemble_constants import ASSEMBLY_MANIFEST, ASSEMBLY_QC
from loudness import _loudness_mode
from subtitles.track_binding import manifest_subtitle_evidence
from artifacts import _source_video_identity, _timeline_provenance_status


def _assembly_manifest_payload(input_video, tts_segments, work_dir, output_path,
                               tts_meta_path=None, final_output=None, *, settings_payload,
                               audio_mode="narration", audio_stream_index=0,
                               narration_input_binding=None, audio_mix_binding=None):
    """Slim render record. The orchestrator reads `final_output` to report the result;
    `source_video` is set only when cut mode passed --source-video. QC results live in
    assembly_qc.json (`qc_path`), not here."""
    input_video = Path(input_video)
    output_path = Path(output_path)
    source_video_identity = _source_video_identity()
    qc_path = Path(work_dir) / ASSEMBLY_QC
    settings = settings_payload(
        work_dir, audio_mode=audio_mode, audio_stream_index=audio_stream_index
    )
    payload = {
        "schema_version": 2,
        "input_video": str(input_video.resolve()),
        "source_video": source_video_identity["path"] if source_video_identity else None,
        "source_video_identity": source_video_identity,
        "tts_meta": str(Path(tts_meta_path).resolve()) if tts_meta_path else None,
        "tts_segments": len(tts_segments),
        "audio_mode": audio_mode,
        "selected_audio_stream_index": audio_stream_index,
        "assembly_settings": settings,
        "output_path": str(output_path.resolve()),
        "qc_path": str(qc_path.resolve()),
        "narration_input_binding": narration_input_binding,
        "audio_mix_binding": audio_mix_binding,
        "audio_segments": [
            {
                "index": seg["index"],
                # The loudness measurements are None whenever voiceover skipped
                # normalization (strict adoption fixtures in tests/orchestrator omit them).
                "narration": seg["narration"],
                "spoken_text": seg["spoken_text"],
                "truncated": seg["truncated"],
                "truncate_reason": seg["truncate_reason"],
                "fit_status": seg["fit_status"],
                "blocking": seg["blocking"],
                "audio_duration": seg["audio_duration"],
                "placed_audio_duration": seg["placed_audio_duration"],
                "placed_audio_path": seg.get("placed_audio_path"),
                "actual_place_start": seg.get("actual_place_start"),
                "actual_place_end": seg.get("actual_place_end"),
                "source_duck_end": seg.get("source_duck_end"),
                "source_restore_at": seg.get("source_restore_at"),
                "source_handoff_status": seg.get("source_handoff_status"),
                "source_entry_status": seg.get("source_entry_status"),
                "written_start": seg.get("written_start"),
                "global_narration_speed": seg["global_narration_speed"],
                "segment_tempo_factor": seg["segment_tempo_factor"],
                "effective_tempo": seg["effective_tempo"],
                "rms_dbfs_before": seg.get("rms_dbfs_before"),
                "rms_dbfs_after": seg.get("rms_dbfs_after"),
                "peak_after": seg.get("peak_after"),
                "output_start_sample": seg.get("output_start_sample"),
                "output_end_sample": seg.get("output_end_sample"),
                "adopted_gain": seg.get("adopted_gain"),
            }
            for seg in tts_segments
        ],
    }
    if final_output is not None:
        payload["final_output"] = str(Path(final_output).resolve())
    provenance = _timeline_provenance_status(work_dir)
    if provenance:
        payload["timeline_provenance"] = provenance
    subtitle_evidence = manifest_subtitle_evidence(work_dir, input_video, output_path)
    if subtitle_evidence is not None:
        payload["subtitle_track"] = subtitle_evidence
    return payload


def _write_assembly_manifest(work_dir, manifest):
    path = Path(work_dir) / ASSEMBLY_MANIFEST
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _placed_audio_matches_timeline(seg):
    """True when the persisted per-beat WAV is exactly what the serialized timeline window plays."""
    placed_path = Path(seg["placed_audio_path"])
    if not placed_path.exists():
        return False
    try:
        with wave.open(str(placed_path), "rb") as placed_wav:
            placed_duration = placed_wav.getnframes() / placed_wav.getframerate()
            tolerance = 1.0 / placed_wav.getframerate()
    except wave.Error:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
             "stream=sample_rate,time_base,duration_ts", "-of", "json", str(placed_path)],
            capture_output=True, text=True, timeout=600,
        )
        streams = json.loads(result.stdout).get("streams", []) if not result.returncode else []
        if len(streams) != 1:
            return False
        stream = streams[0]
        try:
            rate = int(stream["sample_rate"])
            placed_duration = float(Fraction(stream["duration_ts"]) * Fraction(stream["time_base"]))
            tolerance = 1.0 / rate
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            return False
    timeline_start = math.floor(float(seg["actual_place_start"]) * 10_000 + 1e-9) / 10_000
    timeline_end = math.ceil(float(seg["actual_place_end"]) * 10_000 - 1e-9) / 10_000
    serialized_span = timeline_end - timeline_start
    return (
        abs(placed_duration - seg["placed_audio_duration"]) <= tolerance + 1e-9
        and serialized_span + 1e-9 >= placed_duration
    )


def _build_assembly_qc(tts_segments, video_duration, *, audio_operations, render_delivery,
                       output_path=None, source_has_audio=None, loudness_mode=None,
                       loudnorm_measurement=None, loudnorm_final_pass=None,
                       visual_qc=None, audio_mode="narration",
                       adopted_audio=None,
                       narration_input_binding=None, audio_mix_binding=None,
                       source_audio_status=None):
    """Machine-readable assembly release gate.

    Only visual_qc.json's verdict and blocking codes are carried here; its facts stay in
    visual_qc.json. Delivery/render facts live here, never in visual_qc.json.
    """
    hard_max = CONFIG["narration_cumulative_tempo_hard_max"]
    segments = tts_segments
    no_safe = [
        s["index"] for s in segments
        if s["fit_status"] == "no_safe_fit"
        or s["truncate_reason"] in {"no_safe_boundary", "no_room"}
        or s["blocking"]
    ]
    skipped = [s["index"] for s in segments if s["fit_status"] == "skipped"]
    tempo_exceeded = []
    truncated = []
    handoff_failed = []
    timeline_audio_failed = []
    max_effective = 0.0
    for s in segments:
        eff = float(s["effective_tempo"])
        max_effective = max(max_effective, eff)
        if eff > hard_max + 1e-6:
            tempo_exceeded.append(s["index"])
        if s["truncated"] or s["truncate_reason"] == "tail_trim_tolerance":
            truncated.append(s["index"])
        if s.get("source_handoff_blocking", False):
            handoff_failed.append(s["index"])
        if s["placed_audio_duration"] > 0 and not _placed_audio_matches_timeline(s):
            timeline_audio_failed.append(s["index"])

    placed = [s["placed_audio_duration"] for s in segments]
    # Information only: how long original audio stays ducked after each narration run ends.
    duck_holds = [
        float(s["source_restore_at"]) - float(s["actual_place_end"])
        for s in segments
        if s.get("source_restore_at") is not None and s.get("actual_place_end") is not None
    ]
    blocking_codes = []
    if audio_mode == "narration" and not segments:
        blocking_codes.append("missing_narration")
    if skipped:
        blocking_codes.append("skipped_segments")
    if no_safe:
        blocking_codes.append("no_safe_fit")
    if tempo_exceeded:
        blocking_codes.append("effective_tempo_exceeded")
    if truncated:
        blocking_codes.append("truncated_speech")
    if handoff_failed:
        blocking_codes.append("unsafe_source_handoff")
    if timeline_audio_failed:
        blocking_codes.append("timeline_audio_mismatch")
    if placed and max(placed) <= 0.0 and not no_safe:
        blocking_codes.append("empty_narration")
    delivered_peak = ((loudnorm_final_pass or {}).get("delivered") or {}).get("true_peak")
    if delivered_peak is not None and delivered_peak > float(CONFIG["target_true_peak"]):
        # The encoded file, not the PCM mix, is what must stay under TP (codec_peak.py).
        blocking_codes.append("delivered_true_peak_over_target")
    visual_verdict = (
        {"verdict": visual_qc["verdict"], "blocking_codes": list(visual_qc["blocking_codes"])}
        if visual_qc is not None
        else {"verdict": "NOT_RUN", "blocking_codes": []}
    )
    if visual_qc is not None and visual_qc["blocking"]:
        blocking_codes.append("visual_qc_failed")
    if source_audio_status is not None:
        source_audio = source_audio_status
    elif source_has_audio is False:
        # Not blocking: assemble can synthesize a silent original track.
        source_audio = "synthetic_silence"
    elif source_has_audio is True:
        source_audio = "present"
    else:
        source_audio = "unknown"

    output = {}
    if output_path is not None:
        output_path = Path(output_path)
        output = {
            "path": str(output_path),
            "exists": output_path.exists(),
            "bytes": output_path.stat().st_size if output_path.exists() else 0,
        }
        if output_path.exists() and output["bytes"] <= 0:
            blocking_codes.append("empty_output")

    return {
        "schema_version": 1,
        "artifact": ASSEMBLY_QC,
        "verdict": "FAIL" if blocking_codes else "PASS",
        "blocking": bool(blocking_codes),
        "blocking_codes": blocking_codes,
        "duration": round(float(video_duration), 4),
        "audio_mode": audio_mode,
        "audio_operations": audio_operations,
        "adopted_audio": adopted_audio,
        "narration_input_binding": narration_input_binding,
        "audio_mix_binding": audio_mix_binding,
        "source_audio": source_audio,
        "loudness_mode": loudness_mode or _loudness_mode(loudnorm_measurement),
        "loudnorm_measurement": loudnorm_measurement,
        "loudnorm_final_pass": loudnorm_final_pass,
        "visual_qc": visual_verdict,
        "delivery_qc": {
            "video_encode_passes": render_delivery["video_encode_passes"],
            "reencode_reason": render_delivery["reencode_reason"],
            "audio_sample_rate": render_delivery["audio_sample_rate"],
            "final_compat_notes": render_delivery["final_compat_notes"],
            "color_tags": render_delivery.get("color_tags"),
        },
        "summary": {
            "segments": len(segments),
            "placed_segments": sum(1 for x in placed if x > 0.0),
            "skipped_segments": skipped,
            "no_safe_fit_segments": no_safe,
            "tempo_exceeded_segments": tempo_exceeded,
            "max_effective_tempo": round(max_effective, 4),
            "truncated_segments": truncated,
            "unsafe_source_handoff_segments": handoff_failed,
            "timeline_audio_mismatch_segments": timeline_audio_failed,
            "max_source_duck_hold_seconds": (
                round(max(0.0, max(duck_holds)), 4) if duck_holds else None
            ),
        },
        "output": output,
    }


def _write_assembly_qc(work_dir, qc):
    path = Path(work_dir) / ASSEMBLY_QC
    path.write_text(json.dumps(qc, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _resolve_final_output(base, stem):
    """The recap output is the stable human alias recap_<stem>.mp4, overwritten in place
    on every run so the iterate-on-narration loop always refreshes the same file."""
    return Path(base) / f"recap_{stem}.mp4"
