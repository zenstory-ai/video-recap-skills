"""Voiceover-shaped tts_meta segments for assemble tests.

video-voiceover writes every one of these fields on each segment
(``_build_tts_segment_result``), and video-assemble reads them by direct access.
Tests that hand-build segments go through ``tts_segment`` so they carry the
producer's contract instead of relying on consumer-side defaults.
"""


def tts_segment(**fields):
    narration = fields.get("narration", "解说")
    segment = {
        "segment_audio_schema_version": 1,
        "index": 0,
        "start": 0.0,
        "end": 1.0,
        "narration": narration,
        "spoken_text": narration,
        "truncated": False,
        "truncate_reason": "none",
        "fit_status": "pending_assembly",
        "blocking": False,
        "audio_path": "",
        "audio_duration": 0.0,
        "placed_audio_duration": None,
        "actual_place_start": None,
        "actual_place_end": None,
        "global_narration_speed": 1.0,
        "segment_tempo_factor": 1.0,
        "effective_tempo": 1.0,
        "rms_dbfs_before": None,
        "rms_dbfs_after": None,
        "peak_after": None,
        "tts_rate_offset": 0.0,
        "pause_after_ms": 250,
        "overlaps_speech": True,
    }
    segment.update(fields)
    return segment
