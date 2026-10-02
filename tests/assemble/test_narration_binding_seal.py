"""Real consumption and publication checks independent of the binding implementation."""

import json
from pathlib import Path
import sys

import pytest

from test_narration_adoption import (
    _adoption, _segment, _tone,
    render_media as _render_media,
)

SCRIPTS = Path(__file__).resolve().parents[2] / "skills/video-assemble/scripts"
sys.path.insert(0, str(SCRIPTS))
import assemble  # noqa: E402
import assembly_settings  # noqa: E402
import adoption.narration_binding as narration_binding  # noqa: E402

# Reuse the real media setup without importing its collected tests.
_media_fixture = pytest.fixture(name="render_media")(_render_media.__wrapped__)


def _render(source, segments, work, adoption, meta):
    return assemble.assemble_video(
        source, segments, work, work / "output.mp4",
        narration_adoption_path=adoption, tts_meta_path=meta,
    )


@pytest.mark.parametrize("field,value", [
    ("artifact", "something_else"), ("schema_version", 999),
    ("status", "PREPARING"), ("identity_status", "APPROVED_BY_MAGIC"),
    ("final_output", {"path": "/nonexistent/output.mp4"}),
])
def test_binding_record_rejects_invalid_report(tmp_path, field, value):
    output = tmp_path / "output.mp4"
    output.write_bytes(b"actual final bytes")
    report = {
        "artifact": "narration_input_binding", "schema_version": 1,
        "status": "FINALIZED", "identity_status": "BOUND_TO_ADOPTION",
        "adoption": {"tempo_policy": narration_binding.TEMPO_POLICY},
        "final_output": {"path": str(output)},
    }
    assert narration_binding.binding_record(tmp_path) is None
    (tmp_path / narration_binding.FILENAME).write_text(json.dumps(report))
    assert narration_binding.binding_record(tmp_path) == {
        "path": str((tmp_path / narration_binding.FILENAME).resolve()),
        "identity_status": "BOUND_TO_ADOPTION",
        "tempo_policy": narration_binding.TEMPO_POLICY,
    }
    report[field] = value
    (tmp_path / narration_binding.FILENAME).write_text(json.dumps(report))
    assert narration_binding.binding_record(tmp_path) is None


def test_deleted_binding_cannot_publish_strict_output(render_media, tmp_path, monkeypatch):
    source, work = render_media
    segments = [_segment(_tone(tmp_path / "voice.wav"))]
    adoption, meta = _adoption(tmp_path, segments)
    finalize = narration_binding.finalize_binding

    def deleted(*args, **kwargs):
        result = finalize(*args, **kwargs)
        (work / narration_binding.FILENAME).unlink(missing_ok=True)
        return result

    monkeypatch.setattr(narration_binding, "finalize_binding", deleted)
    with pytest.raises((ValueError, RuntimeError), match="(?i)(binding|identity|身份)"):
        _render(source, segments, work, adoption, meta)
    assert not (work / "output.mp4").exists()


def test_settings_record_adopted_not_ignored_ambient_speed(render_media, tmp_path):
    source, work = render_media
    segments = [_segment(_tone(tmp_path / "voice.wav"))]
    adoption, meta = _adoption(tmp_path, segments)
    _render(source, segments, work, adoption, meta)
    settings = assembly_settings.assembly_settings_payload(work)
    assert settings["narration_timing"]["narration_speed"] == 1.0
