import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "skills" / "video-script" / "scripts")
)
import validate as narration_validate
from lib import CONFIG, file_identity


def _run_validate(monkeypatch, work_dir, mode="full", *extra, preserve=True):
    argv = ["validate.py", "--work-dir", str(work_dir), "--mode", mode, *extra]
    if preserve:
        argv.append("--preserve-approved-text")
    monkeypatch.setattr(sys, "argv", argv)
    narration_validate.main()


def _write_narration(work_dir, segments, **dumps_kwargs):
    """Write narration.json and return (path, raw bytes) so tests can prove byte-identity."""
    raw = json.dumps(segments, ensure_ascii=False, **dumps_kwargs)
    path = work_dir / "narration.json"
    path.write_text(raw, encoding="utf-8")
    return path, raw


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _approved_fields(segment):
    """Everything the author wrote; `overlaps_speech` is the only field validate may derive."""
    return {k: v for k, v in segment.items() if k != "overlaps_speech"}


def _assert_fields_and_types_preserved(actual, original):
    assert _approved_fields(actual) == _approved_fields(original)
    for key, value in _approved_fields(original).items():
        assert type(actual[key]) is type(value)


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
                "clip_plan_identity": file_identity(
                    work_dir / "clip_plan_validated.json"
                ),
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
    path, _ = _write_narration(tmp_path, approved)

    _run_validate(monkeypatch, tmp_path)

    persisted = _read_json(path)
    assert len(persisted) == len(approved)
    for actual, original in zip(persisted, approved):
        _assert_fields_and_types_preserved(actual, original)


@pytest.mark.parametrize("preserve", [True, False], ids=["preserved", "legacy_rewrite"])
def test_full_over_budget_text_is_kept_only_with_preserve_flag(
    monkeypatch, tmp_path, preserve
):
    monkeypatch.setitem(CONFIG, "speech_rate", 3.5)
    approved = [
        {
            "start": 0,
            "end": 3,
            "narration": "少年停在了门外。他终于明白同伴为什么坚持等候，也决定先把受伤的人送回家再去寻找失踪的同伴。",
        }
    ]
    path, _ = _write_narration(tmp_path, approved)

    _run_validate(monkeypatch, tmp_path, preserve=preserve)

    assert (_read_json(path)[0]["narration"] == approved[0]["narration"]) is preserve
    if preserve:
        lint = _read_json(tmp_path / "narration_lint.json")
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
    path, raw = _write_narration(tmp_path, approved, separators=(",", ":"))
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
    path, _ = _write_narration(tmp_path, approved)

    _run_validate(monkeypatch, tmp_path, "cut_output", "--output-duration", "10")

    persisted = _read_json(path)
    assert _approved_fields(persisted[0]) == approved[0]
    assert persisted[0]["overlaps_speech"] is False


_BAD_SHAPES = [
    ({"start": 0, "end": 1, "narration": 7}, "invalid_narration"),
    ({"start": 0, "end": 1, "narration": "   "}, "empty_narration"),
    ({"start": True, "end": 1, "narration": "文本。"}, "invalid_time"),
    ({"start": 0, "end": "1", "narration": "文本。"}, "invalid_time"),
    ({"start": 0, "end": float("nan"), "narration": "文本。"}, "invalid_time"),
    ({"start": 0, "end": 1, "narration": "文本。", "pause_after_ms": True}, "invalid_pause"),
    ({"start": 0, "end": 1, "narration": "文本。", "pause_after_ms": 1.5}, "invalid_pause"),
    ({"start": 0, "end": 1, "narration": "文本。", "pause_after_ms": -1}, "invalid_pause"),
]


@pytest.mark.parametrize(
    "segments, code",
    [pytest.param([segment], code, id=f"bad_shape_{code}") for segment, code in _BAD_SHAPES]
    + [
        pytest.param(
            [
                {"start": 5, "end": 6, "narration": "第二段。"},
                {"start": 0, "end": 1, "narration": "第一段。"},
            ],
            "out_of_order",
            id="out_of_order",
        ),
        pytest.param(
            [{"start": 5, "end": 5, "narration": "零长段。"}],
            "invalid_time_range",
            id="zero_length_segment",
        ),
    ],
)
def test_strict_input_failures_leave_narration_byte_identical(
    monkeypatch, tmp_path, segments, code
):
    path, raw = _write_narration(tmp_path, segments, indent=1)

    with pytest.raises(ValueError, match=code):
        _run_validate(monkeypatch, tmp_path)

    assert path.read_text(encoding="utf-8") == raw
    lint = _read_json(tmp_path / "narration_lint.json")
    assert lint["ok"] is False
    assert code in {item["code"] for item in lint["errors"]}


def test_strict_shape_cli_replaces_stale_pass_lint_with_current_failure(tmp_path):
    invalid = [{"start": 0, "end": 1, "narration": 7}]
    narration_path, raw = _write_narration(tmp_path, invalid, separators=(",", ":"))
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
    current = _read_json(tmp_path / "narration_lint.json")
    assert set(current) == set(stale)
    assert current["ok"] is False
    assert current["error_count"] == len(current["errors"]) >= 1
    assert current["errors"][0]["code"] == "invalid_narration"
    assert current["metrics"] == {}


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
    path, _ = _write_narration(tmp_path, approved)

    _run_validate(monkeypatch, tmp_path)

    persisted = _read_json(path)
    assert len(persisted) == 1
    assert persisted[0]["overlaps_speech"] is False
    _assert_fields_and_types_preserved(persisted[0], approved[0])


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
    path, _ = _write_narration(tmp_path, approved)

    _run_validate(monkeypatch, tmp_path)

    assert _approved_fields(_read_json(path)[0]) == approved[0]
    lint = _read_json(tmp_path / "narration_lint.json")
    assert any(item["code"] == "slot_too_short" for item in lint["warnings"])


def test_source_boundary_failure_keeps_approved_file_bytes(monkeypatch, tmp_path):
    (tmp_path / "asr_result.json").write_text(
        json.dumps([{"start": 0, "end": 5, "text": "原声正在说话"}]),
        encoding="utf-8",
    )
    path, raw = _write_narration(
        tmp_path,
        [{"start": 2, "end": 3, "narration": "不能打断原声。"}],
        separators=(",", ":"),
    )

    with pytest.raises(ValueError, match="source_sentence_anchors_unavailable"):
        _run_validate(monkeypatch, tmp_path)

    assert path.read_text(encoding="utf-8") == raw


def test_cut_output_bounds_failure_keeps_approved_file_bytes(monkeypatch, tmp_path):
    _write_output_evidence(tmp_path)
    path, raw = _write_narration(
        tmp_path, [{"start": 0, "end": 11, "narration": "越过输出边界。"}], indent=3
    )

    with pytest.raises(SystemExit, match="exceeds rendered output timeline"):
        _run_validate(monkeypatch, tmp_path, "cut_output", "--output-duration", "10")

    assert path.read_text(encoding="utf-8") == raw


@pytest.mark.parametrize("preserve", [False, True])
@pytest.mark.parametrize("duration", [None, "10", "0", "-1", "nan", "inf"])
def test_cut_output_cli_duration_failure_updates_lint(tmp_path, preserve, duration):
    _write_output_evidence(tmp_path)
    path, raw = _write_narration(
        tmp_path, [{"start": 0, "end": 11, "narration": "等待同伴。" * 30}], indent=3
    )
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
    current = _read_json(report_path)
    assert "stale" not in current
    assert current["ok"] is False
    assert current["error_count"] == len(current["errors"]) == 1
    assert current["errors"][0]["code"] == "invalid_output_timeline"
    assert current["errors"][0]["message"] in result.stderr
    assert current["warning_count"] == len(current["warnings"])
    assert any(warning["code"] == "over_budget" for warning in current["warnings"])
    assert current["deslop_qc"] == _read_json(tmp_path / "deslop_qc.json")


def test_cut_output_retry_clears_failure_and_keeps_duration_tolerance(monkeypatch, tmp_path):
    _write_output_evidence(tmp_path)
    narration = [{"start": 0, "end": 10.04, "narration": "等待同伴。"}]
    path, _ = _write_narration(tmp_path, narration)

    with pytest.raises(SystemExit, match="exceeds rendered output timeline"):
        _run_validate(monkeypatch, tmp_path, "cut_output", "--output-duration", "9")
    assert _read_json(tmp_path / "narration_lint.json")["ok"] is False

    _run_validate(monkeypatch, tmp_path, "cut_output", "--output-duration", "10")

    current = _read_json(tmp_path / "narration_lint.json")
    assert current["ok"] is True
    assert current["errors"] == [] and current["error_count"] == 0
    assert _approved_fields(_read_json(path)[0]) == narration[0]


def test_chronological_order_is_only_required_by_the_approved_text_policy(tmp_path):
    """Lint sorts segments for its timing checks; only --preserve-approved-text rejects
    input that is not already in order (the approved timeline must stay byte-identical)."""
    from narration_lint import lint_narration

    unsorted = [
        {"start": 5.0, "end": 6.0, "narration": "第二段。"},
        {"start": 0.0, "end": 1.0, "narration": "第一段。"},
    ]
    relaxed = lint_narration(unsorted, [], mode="full", work_dir=tmp_path)
    assert "out_of_order" not in {item["code"] for item in relaxed["errors"]}
    strict = lint_narration(
        unsorted, [], mode="full", work_dir=tmp_path, require_chronological=True
    )
    assert "out_of_order" in {item["code"] for item in strict["errors"]}
