"""Removed delivery routes fail with v0.6.2 named; surviving routes and work_dirs are untouched."""

import json
import sys

import pytest

import recap_runner
import recap_source


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
    "run_manifest_mode": _marker_json(
        "recap_run_manifest.json", {"audio": {"mode": "adopted-packet-copy"}}
    ),
    "run_manifest_local_adoption": _marker_json(
        "recap_run_manifest.json", {"audio": {"mode": "narration", "local_adoption": {}}}
    ),
    "run_manifest_failed_adoption": _marker_json(
        "recap_run_manifest.json", {"audio": {"mode": "narration"}, "local_adoption_run": {}}
    ),
}


def _stub_stages(monkeypatch):
    calls = []
    monkeypatch.setattr(recap_runner, "_run", lambda *args: calls.append(args))
    monkeypatch.setattr(recap_runner, "_run_or_restore_understanding",
                        lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(recap_runner, "_preflight_burn_subtitles", lambda _args: None)
    return calls


@pytest.mark.parametrize("marker", MARKERS)
@pytest.mark.parametrize("audio_mode", ["narration", "source-mix"])
def test_single_video_rejects_marker_before_any_stage(monkeypatch, tmp_path, marker, audio_mode):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    work = tmp_path / "work"
    work.mkdir()
    MARKERS[marker](work)
    calls = _stub_stages(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["recap.py", str(video), "--work-dir", str(work),
                                      "--audio-mode", audio_mode])
    with pytest.raises(SystemExit, match="v0.6.2"):
        recap_runner.main()
    assert calls == []


@pytest.mark.parametrize("marker", MARKERS)
def test_multi_video_rejects_marker_before_any_stage(monkeypatch, tmp_path, marker):
    videos = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
    for video in videos:
        video.write_bytes(b"video")
    work = tmp_path / "work"
    work.mkdir()
    MARKERS[marker](work)
    calls = _stub_stages(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["recap.py", *map(str, videos), "--work-dir", str(work),
                                      "--edit-mode", "cut"])
    with pytest.raises(SystemExit, match="v0.6.2"):
        recap_runner.main()
    assert calls == []


@pytest.mark.parametrize("argv", [
    ["--audio-mode", "adopted-packet-copy"],
    ["--tts-meta", "tts.json"],
    ["--narration-adoption", "n.json"],
    ["--audio-mix-adoption", "m.json"],
    ["--tts-meta=tts.json"],
])
def test_removed_cli_inputs_name_the_fallback(monkeypatch, capsys, tmp_path, argv):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    calls = _stub_stages(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["recap.py", str(video), *argv])
    with pytest.raises(SystemExit) as exc:
        recap_runner.main()
    assert exc.value.code == 2
    assert "v0.6.2" in capsys.readouterr().err
    assert calls == []


def test_removed_options_are_hidden_from_help(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["recap.py", "--help"])
    with pytest.raises(SystemExit):
        recap_runner.main()
    output = capsys.readouterr().out
    for hidden in ("--tts-meta", "--narration-adoption", "--audio-mix-adoption", "adopted-packet-copy"):
        assert hidden not in output


@pytest.mark.parametrize("files", [
    {"recap_run_manifest.json": {"audio": {"mode": "narration", "selected_stream_index": 0}}},
    {"recap_run_manifest.json": {"audio": {"mode": "source-mix", "selected_stream_index": 0}},
     "assembly_qc.json": {"audio_mode": "source-mix"},
     "assembly_manifest.json": {"audio_mode": "source-mix"}},
    {"narration_input_binding.json": {"identity_status": "UNADOPTED"},
     "assembly_qc.json": {"audio_mode": "narration"}},
])
def test_current_work_dirs_are_not_rejected(tmp_path, files):
    for name, value in files.items():
        (tmp_path / name).write_text(json.dumps(value), encoding="utf-8")
    recap_source.reject_removed_delivery_workdir(tmp_path)


def test_malformed_json_is_not_a_marker(tmp_path):
    for name in ("recap_run_manifest.json", "assembly_qc.json", "narration_input_binding.json"):
        (tmp_path / name).write_text("not-json", encoding="utf-8")
    recap_source.reject_removed_delivery_workdir(tmp_path)


@pytest.mark.parametrize("argv", [["--doctor"], ["--doctor", "--json"], ["input.mp4", "--doctor"]])
def test_removed_doctor_flag_points_at_doctor_py(monkeypatch, capsys, argv):
    calls = _stub_stages(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["recap.py", *argv])
    with pytest.raises(SystemExit) as exc:
        recap_runner.main()
    assert exc.value.code == 2
    assert "doctor.py" in capsys.readouterr().err
    assert calls == []
