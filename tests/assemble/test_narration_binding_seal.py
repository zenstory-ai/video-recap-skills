"""Real consumption and publication checks independent of the binding implementation."""

import json
import os
from pathlib import Path
import shutil
import subprocess
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
import narration_binding  # noqa: E402

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


def test_blocking_qc_never_observes_a_published_strict_output(
    render_media, tmp_path, monkeypatch
):
    source, work = render_media
    segments = [_segment(_tone(tmp_path / "voice.wav"))]
    adoption, meta = _adoption(tmp_path, segments)
    original = assemble.assembly_contract._build_assembly_qc
    observations = []

    def failed_qc(*args, **kwargs):
        observations.append((work / "output.mp4").exists())
        report = original(*args, **kwargs)
        report.update(blocking=True, verdict="FAIL", blocking_codes=["injected_qc_block"])
        return report

    monkeypatch.setattr(assemble.assembly_contract, "_build_assembly_qc", failed_qc)
    with pytest.raises(RuntimeError, match="(?i)QC"):
        _render(source, segments, work, adoption, meta)
    assert observations == [False], "strict final name must remain invisible until QC passes"
    assert not (work / "output.mp4").exists()
    assert not (work / narration_binding.FILENAME).exists()


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


def test_copied_skill_strict_cli_runs_with_isolated_python(render_media, tmp_path):
    source, _ = render_media
    copied = tmp_path / "standalone"
    shutil.copytree(SCRIPTS.parent, copied, ignore=shutil.ignore_patterns("__pycache__"))
    work = tmp_path / "isolated-work"
    work.mkdir()
    segments = [_segment(_tone(tmp_path / "voice.wav"))]
    adoption, meta = _adoption(tmp_path, segments)
    env = {**os.environ, "NARRATION_SPEED": "1.15", "NARRATION_TIGHTEN": "0",
           "NARRATION_TAIL_PAD_SECONDS": "0", "EXPORT_JIANYING": "0",
           "BGM_PATH": "", "FINAL_LOUDNORM": "0", "OUTPUT_MAX_HEIGHT": "0"}
    # -I removes caller/repo imports. Only the explicitly copied script directory
    # is made importable by the isolated launcher.
    launcher = "import runpy,sys;sys.path.insert(0,sys.argv[1]);sys.argv=sys.argv[2:];runpy.run_path(sys.argv[0],run_name='__main__')"
    result = subprocess.run([
        sys.executable, "-I", "-X", "utf8", "-c", launcher, str(copied / "scripts"),
        str(copied / "scripts/assemble.py"), str(source), "--work-dir", str(work),
        "--tts-meta", str(meta), "--narration-adoption", str(adoption),
        "--no-burn-subtitles", "--output-dir", str(tmp_path / "delivery"),
    ], cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8",
       errors="replace", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((work / narration_binding.FILENAME).read_text())
    output = Path(report["final_output"]["path"])
    assert report["identity_status"] == "BOUND_TO_ADOPTION"
    assert output.is_file() and output.stat().st_size > 0
    assert report["adoption"]["tempo_policy"]["global_atempo"] == 1.0


def test_second_qc_block_is_not_returned_as_success(render_media, tmp_path, monkeypatch):
    source, work = render_media
    segments = [_segment(_tone(tmp_path / "voice.wav"))]
    adoption, meta = _adoption(tmp_path, segments)
    build_qc = assemble.assembly_contract._build_assembly_qc
    calls = []

    def second_pass_fails(*args, **kwargs):
        report = build_qc(*args, **kwargs)
        calls.append(report)
        if len(calls) == 2:
            report.update(blocking=True, verdict="FAIL", blocking_codes=["second_qc_block"])
        return report

    monkeypatch.setattr(assemble.assembly_contract, "_build_assembly_qc", second_pass_fails)
    with pytest.raises(RuntimeError, match="(?i)QC"):
        _render(source, segments, work, adoption, meta)
    assert len(calls) == 2
    assert not (work / "output.mp4").exists()
    assert not (work / narration_binding.FILENAME).exists()
