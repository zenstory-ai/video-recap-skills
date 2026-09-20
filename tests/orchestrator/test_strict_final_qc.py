"""Opt-in final-QC exit semantics at the real video-recap entry point."""

import ast
from argparse import Namespace
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills/video-recap/scripts"
sys.path.insert(0, str(SCRIPTS))
import recap_cli  # noqa: E402
import recap_runner  # noqa: E402
import recap_runtime  # noqa: E402
import recap_stage_qc  # noqa: E402
import recap_timeline  # noqa: E402


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


def test_both_runner_exits_use_completion_helper():
    tree = ast.parse(Path(recap_runner.__file__).read_text(encoding="utf-8"))
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "_finish_recap"
    ]
    assert len(calls) == 2


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
