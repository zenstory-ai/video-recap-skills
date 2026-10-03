"""The guohuo-60s example's assemble snapshots may only use fields video-assemble still writes.

The example records a real past production, so its values are never regenerated; but when
assemble stops writing a field, the snapshot must drop it too, or the example keeps teaching
a contract that no longer exists (as happened after the QC mirrors and ducking modes went).
Fields added to the writer later are allowed to be missing from the snapshot.
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[2] / "skills" / "video-assemble" / "scripts"),
)
import assembly_contract  # noqa: E402
from assembly_settings import assembly_settings_payload  # noqa: E402
from tts_fixtures import tts_segment  # noqa: E402

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "guohuo-60s"
_DELIVERY = {
    "video_encode_passes": 0,
    "reencode_reason": [],
    "audio_sample_rate": 48000,
    "final_compat_notes": ["video_copy", "aac_48000", "faststart"],
}


def _load(name):
    return json.loads((EXAMPLE / name).read_text(encoding="utf-8"))


def _key_paths(value, prefix=""):
    """Dotted key paths of nested dicts; list items share one `[]` path."""
    paths = set()
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else key
            paths.add(path)
            paths |= _key_paths(child, path)
    elif isinstance(value, list):
        for child in value:
            paths |= _key_paths(child, f"{prefix}[]")
    return paths


def _segment():
    return tts_segment(
        placed_audio_duration=0.0,
        placed_audio_path=None,
        source_duck_end=None,
        source_restore_at=None,
        source_handoff_status=None,
        source_entry_status=None,
    )


def test_guohuo_assembly_manifest_uses_only_fields_assemble_still_writes(tmp_path):
    example = _load("assembly_manifest.json")
    shutil.copy(EXAMPLE / "timeline.json", tmp_path / "timeline.json")

    written = assembly_contract._assembly_manifest_payload(
        tmp_path / "edited_source.mp4",
        [_segment()],
        tmp_path,
        tmp_path / "output.mp4",
        tts_meta_path=tmp_path / "tts_meta.json",
        final_output=tmp_path / "final.mp4",
        settings_payload=assembly_settings_payload,
    )

    stale = _key_paths(example) - _key_paths(written)
    assert not stale, f"example keys assemble no longer writes: {sorted(stale)}"


def test_guohuo_assembly_qc_uses_only_fields_assemble_still_writes(tmp_path):
    example = _load("assembly_qc.json")

    written = assembly_contract._build_assembly_qc(
        [_segment()],
        example["duration"],
        audio_operations={},
        render_delivery=_DELIVERY,
        output_path=tmp_path / "output.mp4",
        source_has_audio=True,
        loudnorm_measurement=example["loudnorm_measurement"],
        visual_qc={"verdict": "PASS", "blocking": False, "blocking_codes": []},
    )

    stale = _key_paths(example) - _key_paths(written)
    assert not stale, f"example keys assemble no longer writes: {sorted(stale)}"
