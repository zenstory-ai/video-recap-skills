"""Opt-in final-QC exit semantics at the real video-recap entry point."""

from argparse import Namespace
import json
import os
import shutil
import subprocess
import sys

import pytest

import recap_cli
import recap_runner
import recap_runtime
import recap_stage_qc
import recap_timeline
from _helpers import SCRIPTS


def _summary(final=True, final_count=0, golden=True, golden_count=0):
    return {
        "final_qc": {"ok": final, "blocker_count": final_count},
        "golden_eval": {"ok": golden, "blocker_count": golden_count},
    }


@pytest.mark.parametrize(
    "summary",
    [
        {},
        {"final_qc": {}, "golden_eval": {}},
        _summary(final=False),
        _summary(golden=False),
        _summary(final_count=1),
        _summary(golden_count=True),
        _summary(final_count=0.0),
        {"final_qc": [], "golden_eval": {"ok": True, "blocker_count": 0}},
    ],
)
def test_strict_summary_gate_fails_closed_and_prints_report_paths(
    summary, tmp_path, capsys
):
    with pytest.raises(SystemExit):
        recap_stage_qc._require_final_qc(summary, tmp_path)
    output = capsys.readouterr().out
    assert str(tmp_path / "final_qc.json") in output
    assert str(tmp_path / "golden_eval.json") in output


def test_strict_summary_gate_accepts_only_literal_pass(tmp_path, capsys):
    recap_stage_qc._require_final_qc(_summary(), tmp_path)
    output = capsys.readouterr().out
    assert str(tmp_path / "final_qc.json") in output
    assert str(tmp_path / "golden_eval.json") in output


def test_completion_helper_gates_before_success_and_default_stays_advisory(
    monkeypatch, tmp_path, capsys
):
    final = tmp_path / "final.mp4"
    monkeypatch.setattr(recap_runner, "_write_final_qc_reports", lambda *_: _summary(final=False))
    with pytest.raises(SystemExit):
        recap_runner._finish_recap(tmp_path, final, Namespace(require_final_qc=True))
    assert "✅ 完成" not in capsys.readouterr().out

    recap_runner._finish_recap(tmp_path, final, Namespace(require_final_qc=False))
    output = capsys.readouterr().out
    assert "✅ 完成" in output
    assert "仅报告，不阻断" in output


def test_local_adoption_route_strict_failure_exits_before_success(
    monkeypatch, tmp_path, capsys
):
    video = tmp_path / "picture.mp4"
    video.write_bytes(b"picture")
    work = tmp_path / "local-work"
    final = tmp_path / "local-final.mp4"
    args = Namespace(
        output_dir=str(tmp_path), tts_meta="tts.json",
        narration_adoption="narration.json", audio_mix_adoption="mix.json",
        burn_subtitles=False, subtitle_y_top=None, subtitle_y_bot=None,
        require_final_qc=True,
    )
    manifest = {"source_video_fingerprint": "picture-id", "audio": "audio-id"}
    monkeypatch.setattr(recap_runner, "_write_run_manifest", lambda *_: manifest)
    monkeypatch.setattr(
        recap_runner.material_lib, "file_fingerprint", lambda *_: "picture-id"
    )
    monkeypatch.setattr(recap_runner, "audio_binding", lambda *_: "audio-id")
    monkeypatch.setattr(recap_runner, "begin_local_adoption_qc", lambda *_: None)
    monkeypatch.setattr(recap_runner, "_run", lambda *_: None)
    monkeypatch.setattr(recap_runner, "load_local_assembly_evidence", lambda *_: {})
    monkeypatch.setattr(recap_runner, "owned_local_delivery", lambda *_: None)
    monkeypatch.setattr(recap_runner, "verify_local_assembly_evidence", lambda *_: None)
    monkeypatch.setattr(recap_runner, "_read_assembly_output", lambda *_: final)
    monkeypatch.setattr(recap_runner, "_post_render_qc_metadata", lambda *_: {})
    monkeypatch.setattr(recap_runner, "_write_shift_left_stage_qc", lambda *_a, **_k: None)
    monkeypatch.setattr(
        recap_runner, "_write_final_qc_reports", lambda *_: _summary(final=False)
    )

    with pytest.raises(SystemExit):
        recap_runner._run_local_adoption(video, work, args)
    assert "✅ 完成" not in capsys.readouterr().out


def test_multi_cut_route_strict_failure_exits_before_success(
    monkeypatch, tmp_path, capsys
):
    videos = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
    for video in videos:
        video.write_bytes(b"source")
    work = tmp_path / "multi-work"
    work.mkdir()
    (work / "clip_plan.json").write_text('{"clips":[]}')
    final = tmp_path / "multi-final.mp4"
    monkeypatch.setattr(
        sys, "argv",
        [
            "recap.py", *map(str, videos), "--work-dir", str(work),
            "--edit-mode", "cut", "--audio-mode", "source-mix",
            "--no-burn-subtitles", "--require-final-qc",
        ],
    )
    _parser, args = recap_cli.parse_args()
    monkeypatch.setattr(recap_runner, "_build_multi_source_records", lambda *_: [])
    monkeypatch.setattr(
        recap_runner, "_write_multi_source_manifest",
        lambda *_: work / "multi_source_manifest.json",
    )
    monkeypatch.setattr(recap_runner, "_reject_stale_multi_manifest", lambda *_: None)
    monkeypatch.setattr(recap_runner, "begin_non_narration_qc", lambda *_: None)
    monkeypatch.setattr(recap_runner, "_run", lambda *_: None)
    monkeypatch.setattr(recap_runner, "_surface_cut_qc", lambda *_: {"status": "pass"})
    monkeypatch.setattr(recap_runner, "_write_shift_left_stage_qc", lambda *_a, **_k: None)
    monkeypatch.setattr(recap_runner, "_read_assembly_output", lambda *_: final)
    monkeypatch.setattr(recap_runner, "_post_render_qc_metadata", lambda *_: {})
    monkeypatch.setattr(
        recap_runner, "_write_final_qc_reports", lambda *_: _summary(golden=False)
    )

    with pytest.raises(SystemExit):
        recap_runner._run_multi_cut(videos, work, args)
    assert "✅ 完成" not in capsys.readouterr().out


def test_continuation_preserves_strict_flag_without_cache_fingerprint_change(
    monkeypatch
):
    monkeypatch.setattr(
        sys, "argv", ["recap.py", "input.mp4", "--require-final-qc"]
    )
    _parser, strict = recap_cli.parse_args()
    command = recap_timeline._continuation_command("input.mp4", "work", strict)
    assert "--require-final-qc" in command
    baseline = Namespace(**vars(strict))
    baseline.require_final_qc = False
    assert recap_runtime._material_settings_fingerprint(strict) == \
        recap_runtime._material_settings_fingerprint(baseline)


def test_strict_dub_rejected_before_work_or_probe(monkeypatch, tmp_path, capsys):
    work = tmp_path / "must-not-exist"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "recap.py", str(tmp_path / "missing.mp4"), "--work-dir", str(work),
            "--edit-mode", "dub", "--require-final-qc",
        ],
    )
    monkeypatch.setattr(
        recap_runner, "_coerce_videos",
        lambda *_: (_ for _ in ()).throw(AssertionError("video probe reached")),
    )
    monkeypatch.setattr(
        recap_runner, "_run",
        lambda *_: (_ for _ in ()).throw(AssertionError("child work reached")),
    )
    with pytest.raises(SystemExit) as exc:
        recap_runner.main()
    assert exc.value.code == 2
    assert "--require-final-qc" in capsys.readouterr().err
    assert not work.exists()


def test_legacy_dub_without_strict_flag_still_prepares_and_renders(
    monkeypatch, tmp_path, capsys
):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"source")
    work = tmp_path / "dub-work"
    calls = []
    monkeypatch.setattr(recap_runner, "_run", lambda *args: calls.append(args))
    argv = [
        "recap.py", str(video), "--work-dir", str(work), "--edit-mode", "dub",
        "--no-burn-subtitles",
    ]
    monkeypatch.setattr(sys, "argv", argv)
    recap_runner.main()
    assert calls == [
        (
            "video-voiceover", "dub.py", "--stage", "prepare", "--video",
            str(video.resolve()), "--work-dir", str(work.resolve()),
        )
    ]
    assert "写完后重跑继续" in capsys.readouterr().out

    (work / "dub_script.json").write_text("[]")
    calls.clear()
    recap_runner.main()
    assert calls == [
        (
            "video-voiceover", "dub.py", "--stage", "render", "--video",
            str(video.resolve()), "--work-dir", str(work.resolve()),
        )
    ]
    assert "✅ 配音完成" in capsys.readouterr().out


def _minimal_env():
    return {
        key: os.environ[key]
        for key in ("PATH", "HOME", "TMPDIR", "LANG") if key in os.environ
    }


def _make_source(path):
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
            "color=black:size=160x90:rate=24:duration=1", "-f", "lavfi", "-i",
            "sine=frequency=330:sample_rate=48000:duration=1", "-c:v", "libx264",
            "-threads", "2", "-c:a", "aac", "-shortest", str(path),
        ],
        check=True,
    )


def _run_source_cli(source, work, output, env, *, strict):
    command = [
        sys.executable, str(SCRIPTS / "recap.py"), str(source),
        "--work-dir", str(work), "--output-dir", str(output),
        "--audio-mode", "source-mix", "--no-burn-subtitles",
    ]
    if strict:
        command.append("--require-final-qc")
    return subprocess.run(
        command, cwd=SCRIPTS, env=env, capture_output=True, text=True, timeout=90
    )


@pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="requires real ffmpeg and ffprobe",
)
def test_real_source_cli_strict_pass_and_exact_final_probe_failure(tmp_path):
    source = tmp_path / "source.mp4"
    _make_source(source)

    passing = _run_source_cli(
        source, tmp_path / "pass-work", tmp_path / "pass-output", _minimal_env(),
        strict=True,
    )
    assert passing.returncode == 0, passing.stdout + passing.stderr
    assert "✅ 完成" in passing.stdout
    assert json.loads((tmp_path / "pass-work/final_qc.json").read_text())["ok"] is True

    real_ffprobe = shutil.which("ffprobe")
    wrapper_dir = tmp_path / "wrapper-bin"
    wrapper_dir.mkdir()
    wrapper = wrapper_dir / "ffprobe"
    wrapper.write_text(
        """#!/usr/bin/env python3
import json,os,sys
args=sys.argv[1:]
with open(os.environ['FFPROBE_CALLS'], 'a') as stream:
    stream.write(json.dumps(args)+'\\n')
expected=['-v','error','-print_format','json','-show_format','-show_streams',os.environ['FINAL_QC_TARGET']]
if args == expected:
    sys.stderr.write('isolated final QC probe rejection\\n')
    raise SystemExit(73)
os.execv(os.environ['REAL_FFPROBE'], [os.environ['REAL_FFPROBE'], *args])
"""
    )
    wrapper.chmod(0o755)

    def failing_env(target, calls):
        env = _minimal_env()
        env.update(
            PATH=f"{wrapper_dir}{os.pathsep}{env['PATH']}",
            REAL_FFPROBE=real_ffprobe,
            FINAL_QC_TARGET=str(target),
            FFPROBE_CALLS=str(calls),
        )
        return env

    strict_output = tmp_path / "strict-output"
    strict_target = strict_output / "recap_source.mp4"
    strict_work = tmp_path / "strict-work"
    strict_calls = tmp_path / "strict-ffprobe.jsonl"
    failed = _run_source_cli(
        source, strict_work, strict_output,
        failing_env(strict_target, strict_calls), strict=True,
    )
    assert failed.returncode != 0
    assert "✅ 完成" not in failed.stdout
    assert strict_target.is_file()
    assert json.loads((strict_work / "assembly_manifest.json").read_text())
    final_report = json.loads((strict_work / "final_qc.json").read_text())
    assert any(item["code"] == "probe_failed" for item in final_report["findings"])
    exact = [json.loads(line) for line in strict_calls.read_text().splitlines()]
    assert exact.count([
        "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
        str(strict_target),
    ]) == 1

    default_output = tmp_path / "default-output"
    default_target = default_output / "recap_source.mp4"
    default_work = tmp_path / "default-work"
    advisory = _run_source_cli(
        source, default_work, default_output,
        failing_env(default_target, tmp_path / "default-ffprobe.jsonl"), strict=False,
    )
    assert advisory.returncode == 0, advisory.stdout + advisory.stderr
    assert "✅ 完成" in advisory.stdout
    assert "仅报告，不阻断" in advisory.stdout
    assert json.loads((default_work / "final_qc.json").read_text())["ok"] is False
