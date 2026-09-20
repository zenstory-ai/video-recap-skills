import os
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

import recap_runner as recap
import recap_stage_qc
from _helpers import (
    manifest_args,
    multi_cut_clip,
    seed_full_work,
    seed_multi_work,
    write_assemble_output,
    write_cut_output,
)


def _mimo_result(work, stage, status="completed"):
    return {
        "path": str(Path(work) / "mimo_qc.json"),
        "report": {
            "metadata": {"status": status},
            "finding_count": 0,
            "findings": [],
        },
    }


def test_mimo_qc_off_clears_stale_report_without_request(monkeypatch, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    stale = work / "mimo_qc.json"
    stale.write_text("{}", encoding="utf-8")
    args = Namespace(mimo_qc="off", mimo_qc_refresh=False)
    calls = []
    monkeypatch.setattr(
        recap_stage_qc.mimo_qc, "run", lambda *a, **k: calls.append((a, k))
    )

    recap_stage_qc._prepare_mimo_qc(work, args)
    result = recap_stage_qc._run_mimo_qc_stage(work, args, "pre_assemble")

    assert result is None
    assert calls == []
    assert not stale.exists()


def _stage_order_stubs(monkeypatch, events, final_output):
    """Stub every child so only the mimo/assemble/final-qc ordering is observable."""

    def fake_mimo(work_dir, *, stage, **kwargs):
        events.append(f"mimo:{stage}")
        if stage == "post_render":
            assert kwargs["final_output"] == final_output
        return _mimo_result(work_dir, stage)

    monkeypatch.setattr(recap, "_preflight_burn_subtitles", lambda _args: None)
    monkeypatch.setattr(recap, "_read_video_duration_or_raise", lambda _path: 1.0)
    monkeypatch.setattr(recap, "run_narration_review", lambda *_a, **_k: False)
    monkeypatch.setattr(recap_stage_qc.mimo_qc, "run", fake_mimo)
    monkeypatch.setattr(
        recap,
        "_write_final_qc_reports",
        lambda *_a, **_k: (
            events.append("final_qc") or {"final_qc": {}, "golden_eval": {}}
        ),
    )


def _stage_order_run(work, final_output, events, cut=None):
    def fake_run(_skill, script, *cli_args):
        if script == "cut.py":
            cut()
        elif script == "voiceover.py":
            (work / "tts_meta.json").write_text('{"segments": []}', encoding="utf-8")
        elif script == "assemble.py":
            events.append("assemble")
            write_assemble_output(work, final_output)

    return fake_run


def test_full_pipeline_runs_pre_before_assemble_and_post_before_final_qc(
    monkeypatch, tmp_path
):
    video, work = seed_full_work(tmp_path, [{"start": 0, "end": 1, "narration": "line"}])
    events = []
    final_output = tmp_path / "recap_video.mp4"
    _stage_order_stubs(monkeypatch, events, final_output)
    monkeypatch.setattr(recap, "_run", _stage_order_run(work, final_output, events))
    monkeypatch.setattr(
        sys, "argv", ["recap.py", str(video), "--work-dir", str(work), "--mimo-qc", "both"]
    )

    recap.main()

    assert events == ["mimo:pre_assemble", "assemble", "mimo:post_render", "final_qc"]


def test_multi_pipeline_uses_the_same_mimo_stage_order(monkeypatch, tmp_path):
    videos, work, records = seed_multi_work(
        tmp_path,
        manifest_args(edit_mode="cut"),
        narration=[{"start": 0, "end": 1, "narration": "line"}],
    )
    events = []
    final_output = tmp_path / "multi.mp4"
    _stage_order_stubs(monkeypatch, events, final_output)
    monkeypatch.setattr(
        recap,
        "_run",
        _stage_order_run(
            work,
            final_output,
            events,
            cut=lambda: write_cut_output(
                work, [multi_cut_clip(records, videos)], total_duration=1
            ),
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "recap.py",
            *map(str, videos),
            "--work-dir",
            str(work),
            "--edit-mode",
            "cut",
            "--mimo-qc",
            "both",
        ],
    )

    recap.main()

    assert events == ["mimo:pre_assemble", "assemble", "mimo:post_render", "final_qc"]


def test_mimo_qc_stage_exception_is_visible_but_fail_open(
    monkeypatch, tmp_path, capsys
):
    args = Namespace(mimo_qc="pre-assemble", mimo_qc_refresh=False)
    monkeypatch.setattr(
        recap_stage_qc.mimo_qc,
        "run",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert recap_stage_qc._run_mimo_qc_stage(tmp_path, args, "pre_assemble") is None
    output = capsys.readouterr().out
    assert "MiMo QC" in output
    assert "继续流水线" in output


def test_mimo_qc_loads_its_own_client_after_another_skill_lib(tmp_path):
    root = Path(__file__).resolve().parents[2]
    script_lib = root / "skills" / "video-script" / "scripts"
    recap_lib = root / "skills" / "video-recap" / "scripts"
    code = (
        "import sys; "
        f"sys.path.insert(0, {str(script_lib)!r}); import lib; "
        f"sys.path.insert(0, {str(recap_lib)!r}); import mimo_qc; "
        "assert callable(mimo_qc.mimo_qc_api_call)"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=dict(os.environ),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_invalid_mimo_qc_environment_mode_is_rejected(monkeypatch, capsys):
    monkeypatch.setenv("MIMO_QC", "sometimes")
    monkeypatch.setattr(sys, "argv", ["recap.py"])

    with pytest.raises(SystemExit):
        recap.main()

    assert "MIMO_QC/--mimo-qc must be one of" in capsys.readouterr().err
