"""Standalone assemble refuses what the removed delivery routes left or asked for, naming v0.6.2."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills/video-assemble/scripts"
sys.path.insert(0, str(SCRIPTS))

import assembly_contract  # noqa: E402

ASSEMBLE = str(SCRIPTS / "assemble.py")


def _assemble(*args):
    return subprocess.run([sys.executable, ASSEMBLE, *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8", timeout=120,
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def _marker_file(name):
    def write(work):
        (work / name).write_text("{}", encoding="utf-8")
    return write


def _marker_dir(name):
    return lambda work: (work / name).mkdir()


def _marker_json(name, value):
    return lambda work: (work / name).write_text(json.dumps(value), encoding="utf-8")


MARKERS = {
    "subtitle_track": _marker_file("subtitle_track.json"),
    "subtitle_track_validation": _marker_file("subtitle_track_validation.json"),
    "audio_mix_binding": _marker_file("audio_mix_binding.json"),
    "narration_snapshots": _marker_dir(".narration_input_snapshots"),
    "explicit_mix_dir": _marker_dir(".explicit_audio_mix"),
    "bound_narration_binding": _marker_json(
        "narration_input_binding.json", {"identity_status": "BOUND_TO_ADOPTION"}
    ),
    "assembly_manifest_mode": _marker_json(
        "assembly_manifest.json", {"audio_mode": "adopted-packet-copy"}
    ),
    "assembly_qc_mode": _marker_json("assembly_qc.json", {"audio_mode": "adopted-packet-copy"}),
}


@pytest.mark.parametrize("marker", MARKERS)
def test_every_marker_rejects_the_work_dir(tmp_path, marker):
    MARKERS[marker](tmp_path)
    with pytest.raises(RuntimeError, match="v0.6.2"):
        assembly_contract.reject_removed_delivery_inputs(tmp_path)


@pytest.mark.parametrize("marker", MARKERS)
def test_cli_rejects_marker_before_reading_tts_meta(tmp_path, marker):
    MARKERS[marker](tmp_path)
    result = _assemble("missing.mp4", "--work-dir", tmp_path)
    assert result.returncode != 0
    assert "v0.6.2" in result.stderr and "Traceback" not in result.stderr


def test_current_artifacts_and_malformed_json_are_not_markers(tmp_path):
    (tmp_path / "narration_input_binding.json").write_text(
        json.dumps({"identity_status": "UNADOPTED"}), encoding="utf-8"
    )
    (tmp_path / "assembly_manifest.json").write_text(
        json.dumps({"audio_mode": "source-mix"}), encoding="utf-8"
    )
    (tmp_path / "assembly_qc.json").write_text("not-json", encoding="utf-8")
    assembly_contract.reject_removed_delivery_inputs(tmp_path, "narration")


def test_programmatic_removed_mode_names_the_fallback(tmp_path):
    with pytest.raises(RuntimeError, match="v0.6.2"):
        assembly_contract.reject_removed_delivery_inputs(tmp_path, "adopted-packet-copy")


@pytest.mark.parametrize("argv", [
    ["--audio-mode", "adopted-packet-copy"],
    ["--narration-adoption", "x.json"],
    ["--audio-mix-adoption", "x.json"],
])
def test_removed_cli_inputs_name_the_fallback(tmp_path, argv):
    result = _assemble("missing.mp4", "--work-dir", tmp_path, *argv)
    assert result.returncode == 2
    assert "v0.6.2" in result.stderr


def test_removed_options_are_hidden_from_help():
    result = _assemble("--help")
    assert result.returncode == 0
    for hidden in ("--narration-adoption", "--audio-mix-adoption", "adopted-packet-copy"):
        assert hidden not in result.stdout


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="requires media tools")
def test_source_mix_rerun_in_its_own_work_dir_is_not_rejected(tmp_path):
    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=160x120:r=12:d=1",
        "-f", "lavfi", "-i", "sine=d=1", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(source),
    ], check=True, capture_output=True)
    work = tmp_path / "work"
    work.mkdir()
    args = (source, "--work-dir", work, "--audio-mode", "source-mix", "--no-burn-subtitles",
            "--output-dir", tmp_path / "delivery")
    assert _assemble(*args).returncode == 0
    second = _assemble(*args)
    assert second.returncode == 0, second.stderr


def test_programmatic_render_refuses_removed_mode_before_any_render(tmp_path, monkeypatch):
    import assemble

    monkeypatch.setattr(assemble.lib, "get_video_duration", lambda _p: pytest.fail("reached the render"))
    with pytest.raises(RuntimeError, match="v0.6.2"):
        assemble.assemble_video(tmp_path / "in.mp4", [], tmp_path, tmp_path / "out.mp4",
                                audio_mode="adopted-packet-copy")


def test_source_mix_with_tts_meta_is_refused_before_any_render(tmp_path, monkeypatch):
    import assemble

    monkeypatch.setattr(assemble.lib, "get_video_duration", lambda _p: pytest.fail("reached the render"))
    with pytest.raises(RuntimeError, match="tts_meta"):
        assemble.assemble_video(tmp_path / "in.mp4", [], tmp_path, tmp_path / "out.mp4",
                                audio_mode="source-mix", tts_meta_path=tmp_path / "tts_meta.json")
