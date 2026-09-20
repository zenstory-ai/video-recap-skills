import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "skills" / "video-script" / "scripts")
)
import validate as narration_validate
from lib import CONFIG, stable_hash


def _run_validate(monkeypatch, work_dir, mode="full", *extra):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate.py",
            "--work-dir",
            str(work_dir),
            "--mode",
            mode,
            "--preserve-approved-text",
            *extra,
        ],
    )
    narration_validate.main()


def _write_output_evidence(work_dir):
    plan = {"clips": []}
    (work_dir / "clip_plan_validated.json").write_text(
        json.dumps(plan), encoding="utf-8"
    )
    (work_dir / "speech_boundary_anchors_output.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "timeline": "cut_output",
                "clip_plan_fingerprint": stable_hash(plan),
                "sentence_anchors": [],
                "speech_spans": [],
                "quiet_windows": [{"start": 0, "end": 10}],
            }
        ),
        encoding="utf-8",
    )


def test_full_preserves_refrain_punctuation_pause_and_unknown_metadata(
    monkeypatch, tmp_path
):
    approved = [
        {
            "start": 0,
            "end": 5.0,
            "narration": "每次遇到危险，他都会先转身寻找同伴，",
            "pause_after_ms": 0,
            "overlaps_speech": False,
            "author_note": {"locked": True, "rank": 1},
        },
        {
            "start": 5,
            "end": 10.0,
            "narration": "每次遇到危险，她也会先转身寻找同伴。",
            "pause_after_ms": 200,
            "overlaps_speech": False,
            "custom_list": [1, "two", None],
        },
    ]
    path = tmp_path / "narration.json"
    path.write_text(json.dumps(approved, ensure_ascii=False), encoding="utf-8")

    _run_validate(monkeypatch, tmp_path)

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert len(persisted) == len(approved)
    for actual, original in zip(persisted, approved):
        assert {k: v for k, v in actual.items() if k != "overlaps_speech"} == {
            k: v for k, v in original.items() if k != "overlaps_speech"
        }
        for key, value in original.items():
            if key != "overlaps_speech":
                assert type(actual[key]) is type(value)


def test_full_keeps_over_budget_approved_text_and_reports_warning(monkeypatch, tmp_path):
    monkeypatch.setitem(CONFIG, "speech_rate", 3.5)
    approved = [
        {
            "start": 0,
            "end": 3,
            "narration": "少年停在了门外。他终于明白同伴为什么坚持等候，也决定先把受伤的人送回家再去寻找失踪的同伴。",
        }
    ]
    path = tmp_path / "narration.json"
    path.write_text(json.dumps(approved, ensure_ascii=False), encoding="utf-8")

    _run_validate(monkeypatch, tmp_path)

    assert json.loads(path.read_text(encoding="utf-8"))[0]["narration"] == approved[0][
        "narration"
    ]
    lint = json.loads((tmp_path / "narration_lint.json").read_text(encoding="utf-8"))
    assert any(item["code"] == "over_budget" for item in lint["warnings"])


def test_cut_preserve_mode_validates_without_writing(monkeypatch, tmp_path):
    approved = [
        {
            "start": 1,
            "end": 4,
            "narration": "这段批准稿保持原样。",
            "pause_after_ms": 123,
            "overlaps_speech": False,
            "extra": {"owner": "author"},
        }
    ]
    raw = json.dumps(approved, ensure_ascii=False, separators=(",", ":"))
    path = tmp_path / "narration.json"
    path.write_text(raw, encoding="utf-8")
    (tmp_path / "clip_plan.json").write_text(
        json.dumps({"clips": [{"start": 0, "end": 5}]}), encoding="utf-8"
    )

    _run_validate(monkeypatch, tmp_path, "cut")

    assert path.read_text(encoding="utf-8") == raw


def test_cut_output_preserves_fields_and_derives_only_ownership(monkeypatch, tmp_path):
    _write_output_evidence(tmp_path)
    approved = [
        {
            "start": 0,
            "end": 8.0,
            "narration": "批准的输出时间线文本不会因预算被截短，也不会补写标点，",
            "pause_after_ms": 321,
            "unknown": ["keep", 2],
        }
    ]
    path = tmp_path / "narration.json"
    path.write_text(json.dumps(approved, ensure_ascii=False), encoding="utf-8")

    _run_validate(monkeypatch, tmp_path, "cut_output", "--output-duration", "10")

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert {k: v for k, v in persisted[0].items() if k != "overlaps_speech"} == approved[0]
    assert persisted[0]["overlaps_speech"] is False


@pytest.mark.parametrize(
    "segment",
    [
        {"start": 0, "end": 1, "narration": 7},
        {"start": 0, "end": 1, "narration": "   "},
        {"start": True, "end": 1, "narration": "文本。"},
        {"start": 0, "end": "1", "narration": "文本。"},
        {"start": 0, "end": float("nan"), "narration": "文本。"},
        {"start": 0, "end": 1, "narration": "文本。", "pause_after_ms": True},
        {"start": 0, "end": 1, "narration": "文本。", "pause_after_ms": 1.5},
        {"start": 0, "end": 1, "narration": "文本。", "pause_after_ms": -1},
    ],
)
def test_strict_shape_failures_leave_narration_byte_identical(
    monkeypatch, tmp_path, segment
):
    raw = json.dumps([segment], ensure_ascii=False)
    path = tmp_path / "narration.json"
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(SystemExit, match="approved narration"):
        _run_validate(monkeypatch, tmp_path)

    assert path.read_text(encoding="utf-8") == raw


def test_strict_shape_cli_replaces_stale_pass_lint_with_current_failure(tmp_path):
    invalid = [{"start": 0, "end": 1, "narration": 7}]
    raw = json.dumps(invalid, ensure_ascii=False, separators=(",", ":"))
    narration_path = tmp_path / "narration.json"
    narration_path.write_text(raw, encoding="utf-8")
    stale = {
        "ok": True,
        "error_count": 0,
        "warning_count": 0,
        "metrics": {"stale": True},
        "deslop_qc": {"ok": True},
        "errors": [],
        "warnings": [],
    }
    (tmp_path / "narration_lint.json").write_text(
        json.dumps(stale), encoding="utf-8"
    )

    result = subprocess.run(
        [
            sys.executable,
            str(Path(narration_validate.__file__)),
            "--work-dir",
            str(tmp_path),
            "--mode",
            "full",
            "--preserve-approved-text",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert narration_path.read_text(encoding="utf-8") == raw
    current = json.loads(
        (tmp_path / "narration_lint.json").read_text(encoding="utf-8")
    )
    assert set(current) == set(stale)
    assert current["ok"] is False
    assert current["error_count"] == 1
    assert current["errors"][0]["code"] == "invalid_approved_shape"
    assert current["metrics"] == {"input_fingerprint": stable_hash(invalid)}


def test_out_of_order_is_rejected_before_derivation_and_keeps_bytes(
    monkeypatch, tmp_path
):
    approved = [
        {"start": 5, "end": 6, "narration": "第二段。"},
        {"start": 0, "end": 1, "narration": "第一段。"},
    ]
    raw = json.dumps(approved, ensure_ascii=False, indent=1)
    path = tmp_path / "narration.json"
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(SystemExit, match="chronological order"):
        _run_validate(monkeypatch, tmp_path)

    assert path.read_text(encoding="utf-8") == raw


def test_non_increasing_segment_bounds_are_rejected_and_keep_bytes(monkeypatch, tmp_path):
    approved = [{"start": 5, "end": 5, "narration": "零长段。"}]
    raw = json.dumps(approved, ensure_ascii=False)
    path = tmp_path / "narration.json"
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(SystemExit, match="end must be greater than start"):
        _run_validate(monkeypatch, tmp_path)

    assert path.read_text(encoding="utf-8") == raw


def test_full_derives_quiet_ownership_without_changing_other_approved_fields(
    monkeypatch, tmp_path
):
    approved = [
        {
            "start": 0,
            "end": 4.0,
            "narration": "安静窗口里的批准旁白。",
            "pause_after_ms": 88,
            "overlaps_speech": True,
            "unknown": {"count": 2, "locked": True},
        }
    ]
    (tmp_path / "silence_periods.json").write_text(
        json.dumps([{"start": 0, "end": 4, "has_speech": False}]),
        encoding="utf-8",
    )
    path = tmp_path / "narration.json"
    path.write_text(json.dumps(approved, ensure_ascii=False), encoding="utf-8")

    _run_validate(monkeypatch, tmp_path)

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert len(persisted) == 1
    assert persisted[0]["overlaps_speech"] is False
    assert {k: v for k, v in persisted[0].items() if k != "overlaps_speech"} == {
        k: v for k, v in approved[0].items() if k != "overlaps_speech"
    }
    for key, value in approved[0].items():
        if key != "overlaps_speech":
            assert type(persisted[0][key]) is type(value)


def test_strict_very_short_slot_warns_without_dropping_segment(monkeypatch, tmp_path):
    approved = [
        {
            "start": 0,
            "end": 0.1,
            "narration": "短。",
            "pause_after_ms": 0,
            "tag": "keep",
        }
    ]
    raw = json.dumps(approved, ensure_ascii=False)
    path = tmp_path / "narration.json"
    path.write_text(raw, encoding="utf-8")

    _run_validate(monkeypatch, tmp_path)

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert {k: v for k, v in persisted[0].items() if k != "overlaps_speech"} == approved[0]
    lint = json.loads((tmp_path / "narration_lint.json").read_text(encoding="utf-8"))
    assert any(item["code"] == "slot_too_short" for item in lint["warnings"])


def test_source_boundary_failure_keeps_approved_file_bytes(monkeypatch, tmp_path):
    (tmp_path / "asr_result.json").write_text(
        json.dumps([{"start": 0, "end": 5, "text": "原声正在说话"}]),
        encoding="utf-8",
    )
    approved = [{"start": 2, "end": 3, "narration": "不能打断原声。"}]
    raw = json.dumps(approved, ensure_ascii=False, separators=(",", ":"))
    path = tmp_path / "narration.json"
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(ValueError, match="source_sentence_anchors_unavailable"):
        _run_validate(monkeypatch, tmp_path)

    assert path.read_text(encoding="utf-8") == raw


def test_cut_output_bounds_failure_keeps_approved_file_bytes(monkeypatch, tmp_path):
    _write_output_evidence(tmp_path)
    approved = [{"start": 0, "end": 11, "narration": "越过输出边界。"}]
    raw = json.dumps(approved, ensure_ascii=False, indent=3)
    path = tmp_path / "narration.json"
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(SystemExit, match="exceeds rendered output timeline"):
        _run_validate(monkeypatch, tmp_path, "cut_output", "--output-duration", "10")

    assert path.read_text(encoding="utf-8") == raw


@pytest.mark.parametrize("preserve", [False, True])
@pytest.mark.parametrize("duration", [None, "10", "0", "-1", "nan", "inf"])
def test_cut_output_cli_duration_failure_updates_lint(tmp_path, preserve, duration):
    _write_output_evidence(tmp_path)
    narration = [{"start": 0, "end": 11, "narration": "等待同伴。" * 30}]
    raw = json.dumps(narration, ensure_ascii=False, indent=3)
    path = tmp_path / "narration.json"
    path.write_text(raw, encoding="utf-8")
    report_path = tmp_path / "narration_lint.json"
    report_path.write_text(json.dumps({"ok": True, "stale": True}), encoding="utf-8")
    command = [sys.executable, str(Path(narration_validate.__file__)),
               "--work-dir", str(tmp_path), "--mode", "cut_output"]
    if preserve:
        command.append("--preserve-approved-text")
    if duration is not None:
        command.extend(["--output-duration", duration])

    result = subprocess.run(command, capture_output=True, text=True, check=False)

    assert result.returncode != 0
    assert '"status": "validated"' not in result.stdout
    assert path.read_text(encoding="utf-8") == raw
    current = json.loads(report_path.read_text(encoding="utf-8"))
    assert "stale" not in current
    assert current["ok"] is False
    assert current["error_count"] == len(current["errors"]) == 1
    assert current["errors"][0]["code"] == "invalid_output_timeline"
    assert current["errors"][0]["message"] in result.stderr
    assert current["warning_count"] == len(current["warnings"])
    assert any(warning["code"] == "over_budget" for warning in current["warnings"])
    assert current["deslop_qc"] == json.loads(
        (tmp_path / "deslop_qc.json").read_text(encoding="utf-8")
    )


def test_cut_output_retry_clears_failure_and_keeps_duration_tolerance(monkeypatch, tmp_path):
    _write_output_evidence(tmp_path)
    path = tmp_path / "narration.json"
    narration = [{"start": 0, "end": 10.04, "narration": "等待同伴。"}]
    path.write_text(json.dumps(narration, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(SystemExit, match="exceeds rendered output timeline"):
        _run_validate(monkeypatch, tmp_path, "cut_output", "--output-duration", "9")
    failed = json.loads((tmp_path / "narration_lint.json").read_text(encoding="utf-8"))
    assert failed["ok"] is False

    _run_validate(monkeypatch, tmp_path, "cut_output", "--output-duration", "10")

    current = json.loads((tmp_path / "narration_lint.json").read_text(encoding="utf-8"))
    assert current["ok"] is True
    assert current["errors"] == [] and current["error_count"] == 0
    actual = json.loads(path.read_text(encoding="utf-8"))[0]
    assert {key: value for key, value in actual.items() if key != "overlaps_speech"} == narration[0]


def test_unflagged_full_mode_retains_legacy_budget_rewrite(monkeypatch, tmp_path):
    monkeypatch.setitem(CONFIG, "speech_rate", 3.5)
    approved = [
        {
            "start": 0,
            "end": 3,
            "narration": "少年停在了门外。他终于明白同伴为什么坚持等候，也决定先把受伤的人送回家再去寻找失踪的同伴。",
        }
    ]
    path = tmp_path / "narration.json"
    path.write_text(json.dumps(approved, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        ["validate.py", "--work-dir", str(tmp_path), "--mode", "full"],
    )

    narration_validate.main()

    assert json.loads(path.read_text(encoding="utf-8"))[0]["narration"] != approved[0][
        "narration"
    ]
