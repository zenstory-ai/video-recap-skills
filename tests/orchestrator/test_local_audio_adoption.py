"""Strict local full-sound adoption enters recap only at the assembly boundary."""

import hashlib
import json
import os
import sys
from argparse import Namespace
from pathlib import Path
import shutil
import subprocess

import pytest


SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "video-recap" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import recap_runner  # noqa: E402
import recap_source  # noqa: E402


def _bundle(tmp_path):
    paths = {}
    for name, payload in (
        ("tts_meta", b'{"segments":[]}'),
        ("narration_adoption", b'{"artifact":"narration_adoption"}'),
        ("audio_mix_adoption", b'{"artifact":"audio_mix_adoption"}'),
    ):
        path = tmp_path / f"{name}.json"
        path.write_bytes(payload)
        paths[name] = path
    return paths


def _cli(video, work, output, bundle, *extra):
    return [
        "recap.py", str(video), "--work-dir", str(work), "--output-dir", str(output),
        "--tts-meta", str(bundle["tts_meta"]),
        "--narration-adoption", str(bundle["narration_adoption"]),
        "--audio-mix-adoption", str(bundle["audio_mix_adoption"]),
        *extra,
    ]


def _finish_stubs(monkeypatch, work, calls):
    def fake_run(skill, script, *args):
        calls.append((skill, script, [str(item) for item in args]))
        assert script == "assemble.py"
        final = Path(args[args.index("--output-dir") + 1]) / "recap_input.mp4"
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_bytes(b"final")
        (work / "assembly_manifest.json").write_text(
            json.dumps({"final_output": str(final)}), encoding="utf-8"
        )
        values = {
            flag: Path(args[args.index(flag) + 1])
            for flag in ("--tts-meta", "--narration-adoption", "--audio-mix-adoption")
        }

        def digest(path):
            return hashlib.sha256(path.read_bytes()).hexdigest()

        final_identity = {"path": str(final), "sha256": digest(final)}
        (work / "narration_input_binding.json").write_text(json.dumps({
            "adoption": {
                "sha256": digest(values["--narration-adoption"]),
                "tts_meta": {"sha256": digest(values["--tts-meta"])},
            },
            "final_output": final_identity,
        }))
        (work / "audio_mix_binding.json").write_text(json.dumps({
            "adoption": {"sha256": digest(values["--audio-mix-adoption"])},
            "picture": {"sha256": digest(Path(args[0]))},
            "final_output": final_identity,
        }))

    monkeypatch.setattr(recap_runner, "_run", fake_run)
    monkeypatch.setattr(recap_runner, "_preflight_burn_subtitles", lambda _args: None)
    monkeypatch.setattr(recap_runner, "_write_final_qc_reports", lambda *_: {})
    monkeypatch.setattr(recap_runner, "_print_final_qc_pointer", lambda *_: None)
    return fake_run


@pytest.mark.parametrize("present", [("tts_meta",), ("tts_meta", "narration_adoption")])
def test_local_bundle_is_all_or_none_before_any_stage(monkeypatch, tmp_path, present):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    bundle = _bundle(tmp_path)
    options = []
    for name in present:
        options += ["--" + name.replace("_", "-"), str(bundle[name])]
    calls = []
    monkeypatch.setattr(recap_runner, "_run", lambda *args: calls.append(args))
    monkeypatch.setattr(sys, "argv", ["recap.py", str(video), *options])

    with pytest.raises(SystemExit):
        recap_runner.main()
    assert calls == []


@pytest.mark.parametrize(
    "extra",
    [
        ("--edit-mode", "cut"), ("--audio-stream-index", "1"),
        ("--tts-provider", "auto"), ("--mimo-tts-voice", "chosen"),
        ("--voice-ref", "voice.wav"), ("--allow-partial-tts",),
        ("--review-narration",), ("--require-narration-review",),
        ("--mimo-qc", "pre-assemble"), ("--export-jianying",),
    ],
)
def test_local_bundle_rejects_unsupported_modes_and_authoring_flags(
    monkeypatch, tmp_path, extra
):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    bundle = _bundle(tmp_path)
    calls = []
    monkeypatch.setattr(recap_runner, "_run", lambda *args: calls.append(args))
    monkeypatch.setattr(
        sys, "argv", _cli(video, tmp_path / "new-work", tmp_path / "out", bundle, *extra)
    )

    with pytest.raises(SystemExit):
        recap_runner.main()
    assert calls == []


def test_local_bundle_runs_only_assemble_with_resolved_paths_and_no_narration(
    monkeypatch, tmp_path
):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    bundle = _bundle(tmp_path)
    work = tmp_path / "new-work"
    output = tmp_path / "delivery"
    calls = []
    _finish_stubs(monkeypatch, work, calls)
    monkeypatch.setattr(sys, "argv", _cli(video, work, output, bundle))

    recap_runner.main()

    assert [script for _, script, _ in calls] == ["assemble.py"]
    assemble = calls[0][2]
    assert assemble[0] == str(video.resolve())
    for name in bundle:
        flag = "--" + name.replace("_", "-")
        assert assemble[assemble.index(flag) + 1] == str(bundle[name].resolve())
    assert not (work / "narration.json").exists()
    ledger = json.loads((work / "preflight_qc.json").read_text(encoding="utf-8"))
    stage = ledger["metadata"]["stages"]["pre_assemble"]["metadata"]
    assert stage["tts"] == "adopted_local_not_generated"
    assert stage["narration_review"] == "not_run"
    assert stage["semantic_validation"] == "video-assemble"


def test_local_bundle_ignores_hostile_ambient_tts_and_voice(monkeypatch, tmp_path):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    bundle = _bundle(tmp_path)
    work = tmp_path / "work"
    calls = []
    _finish_stubs(monkeypatch, work, calls)
    monkeypatch.setenv("TTS_PROVIDER", "not-a-provider")
    monkeypatch.setenv("VOICE_REF", str(tmp_path / "missing.wav"))
    monkeypatch.setenv("MIMO_TTS_VOICE", "hostile-ambient-voice")
    monkeypatch.setattr(sys, "argv", _cli(video, work, tmp_path / "out", bundle))

    recap_runner.main()

    assert [script for _, script, _ in calls] == ["assemble.py"]


def test_audio_binding_hashes_each_local_artifact_without_changing_analysis(tmp_path):
    bundle = _bundle(tmp_path)
    args = Namespace(
        audio_mode="narration", audio_stream_index=0,
        tts_meta=str(bundle["tts_meta"]),
        narration_adoption=str(bundle["narration_adoption"]),
        audio_mix_adoption=str(bundle["audio_mix_adoption"]),
    )

    first = recap_source.audio_binding(args)
    bundle["tts_meta"].write_bytes(b"changed")
    second = recap_source.audio_binding(args)

    assert first["mode"] == second["mode"] == "narration"
    assert first["local_adoption"]["tts_meta"]["sha256"] != second["local_adoption"]["tts_meta"]["sha256"]
    assert first["local_adoption"]["narration_adoption"] == second["local_adoption"]["narration_adoption"]
    assert first["local_adoption"]["audio_mix_adoption"] == second["local_adoption"]["audio_mix_adoption"]


@pytest.mark.parametrize("precreate", ["work", "delivery"])
def test_local_bundle_requires_fresh_work_and_delivery_before_any_stage(
    monkeypatch, tmp_path, precreate
):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    bundle = _bundle(tmp_path)
    work = tmp_path / "work"
    output = tmp_path / "out"
    if precreate == "work":
        work.mkdir()
    else:
        output.mkdir()
        (output / "recap_input.mp4").write_bytes(b"old")
    calls = []
    monkeypatch.setattr(recap_runner, "_run", lambda *args: calls.append(args))
    monkeypatch.setattr(recap_runner, "_preflight_burn_subtitles", lambda _args: None)
    monkeypatch.setattr(sys, "argv", _cli(video, work, output, bundle))

    with pytest.raises(SystemExit):
        recap_runner.main()
    assert calls == []


def test_local_bundle_does_not_reuse_existing_assembly_manifest(monkeypatch, tmp_path):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    bundle = _bundle(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    (work / "assembly_manifest.json").write_text('{"final_output":"old.mp4"}')
    calls = []
    monkeypatch.setattr(recap_runner, "_run", lambda *args: calls.append(args))
    monkeypatch.setattr(sys, "argv", _cli(video, work, tmp_path / "out", bundle))

    with pytest.raises(SystemExit):
        recap_runner.main()
    assert calls == []


def test_doctor_rejects_any_local_adoption_input_without_running_stage(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(recap_runner, "_run", lambda *args: calls.append(args))
    monkeypatch.setattr(
        sys, "argv", ["recap.py", "--doctor", "--tts-meta", str(tmp_path / "tts.json")]
    )

    with pytest.raises(SystemExit):
        recap_runner.main()
    assert calls == []


def test_parent_rejects_assembler_binding_that_does_not_match_manifest(
    monkeypatch, tmp_path
):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    bundle = _bundle(tmp_path)
    work = tmp_path / "work"
    calls = []
    valid_run = _finish_stubs(monkeypatch, work, calls)

    def mismatched_run(*args):
        valid_run(*args)
        binding_path = work / "audio_mix_binding.json"
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        binding["adoption"]["sha256"] = "0" * 64
        binding_path.write_text(json.dumps(binding), encoding="utf-8")

    monkeypatch.setattr(recap_runner, "_run", mismatched_run)
    monkeypatch.setattr(
        recap_runner, "_write_final_qc_reports",
        lambda *_: (_ for _ in ()).throw(AssertionError("post QC ran")),
    )
    monkeypatch.setattr(sys, "argv", _cli(video, work, tmp_path / "out", bundle))

    with pytest.raises(SystemExit, match="bindings do not match"):
        recap_runner.main()
    manifest = json.loads((work / "recap_run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["local_adoption_run"]["status"] == "FAILED"
    assert not (tmp_path / "out" / "recap_input.mp4").exists()


def test_parent_does_not_delete_delivery_without_child_ownership_proof(
    monkeypatch, tmp_path
):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    bundle = _bundle(tmp_path)
    work = tmp_path / "work"
    output = tmp_path / "out"
    calls = []
    valid_run = _finish_stubs(monkeypatch, work, calls)

    def unproven_run(*args):
        valid_run(*args)
        (work / "audio_mix_binding.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(recap_runner, "_run", unproven_run)
    monkeypatch.setattr(sys, "argv", _cli(video, work, output, bundle))

    with pytest.raises(SystemExit):
        recap_runner.main()
    assert (output / "recap_input.mp4").read_bytes() == b"final"


@pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="requires local media tools",
)
def test_copied_skill_python_i_cli_routes_offline_bundle_only_to_assemble(tmp_path):
    root = Path(__file__).resolve().parents[2]
    copied = tmp_path / "bundle" / "skills"
    shutil.copytree(root / "skills/video-recap", copied / "video-recap")
    assemble_scripts = copied / "video-assemble" / "scripts"
    assemble_scripts.mkdir(parents=True)
    (assemble_scripts / "assemble.py").write_text(
        """#!/usr/bin/env python3
import argparse,hashlib,json,shutil
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('video'); p.add_argument('--work-dir',required=True)
p.add_argument('--recap-stem',required=True); p.add_argument('--output-dir',required=True)
p.add_argument('--tts-meta',required=True); p.add_argument('--narration-adoption',required=True)
p.add_argument('--audio-mix-adoption',required=True); p.add_argument('--no-burn-subtitles',action='store_true')
a=p.parse_args(); work=Path(a.work_dir); out=Path(a.output_dir)/f'recap_{a.recap_stem}.mp4'
out.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(a.video,out)
(work/'assembly_manifest.json').write_text(json.dumps({'final_output':str(out)}))
d=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
(work/'narration_input_binding.json').write_text(json.dumps({'adoption':{'sha256':d(a.narration_adoption),'tts_meta':{'sha256':d(a.tts_meta)}}}))
(work/'audio_mix_binding.json').write_text(json.dumps({'adoption':{'sha256':d(a.audio_mix_adoption)},'picture':{'sha256':d(a.video)}}))
""",
        encoding="utf-8",
    )
    picture = tmp_path / "input.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         "color=black:s=64x64:r=24:d=1", "-c:v", "libx264", str(picture)],
        check=True, capture_output=True,
    )
    bundle = _bundle(tmp_path)
    work = tmp_path / "isolated-work"
    output = tmp_path / "isolated-delivery"
    env = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "TMPDIR", "LANG")
        if key in os.environ
    }
    env.update(
        TTS_PROVIDER="invalid-ambient-provider",
        VOICE_REF="/missing/ambient.wav",
        MIMO_TTS_VOICE="unused-ambient-voice",
    )
    bootstrap = (
        "import runpy,sys;"
        f"sys.path.insert(0,{str(copied / 'video-recap/scripts')!r});"
        f"sys.argv=['recap.py',*sys.argv[1:]];"
        f"runpy.run_path({str(copied / 'video-recap/scripts/recap.py')!r},run_name='__main__')"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", bootstrap,
         *_cli(picture, work, output, bundle, "--no-burn-subtitles")[1:]],
        env=env, capture_output=True, text=True, timeout=60,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("video-assemble/assemble.py") == 1
    for forbidden in ("video-understanding/", "video-script/", "video-voiceover/"):
        assert forbidden not in result.stdout
    manifest = json.loads((work / "recap_run_manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["audio"]["local_adoption"]) == {
        "tts_meta", "narration_adoption", "audio_mix_adoption"
    }
    assert (output / "recap_input.mp4").is_file()
