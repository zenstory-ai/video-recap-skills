import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "skills" / "video-script" / "scripts")
)
import validate as narration_validate
from lib import CONFIG, file_identity


def _run_validate(monkeypatch, work_dir, mode="full", *extra):
    argv = ["validate.py", "--work-dir", str(work_dir), "--mode", mode, *extra]
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


def test_full_over_budget_text_fails_lint_with_actionable_budget(monkeypatch, tmp_path):
    """Text that does not fit goes back to the author; validate never shortens it."""
    monkeypatch.setitem(CONFIG, "speech_rate", 3.5)
    approved = [
        {"start": 0, "end": 10, "narration": "这一段长度合适。"},
        {
            "start": 10,
            "end": 13,
            "narration": "少年停在了门外。他终于明白同伴为什么坚持等候，也决定先把受伤的人送回家再去寻找失踪的同伴。",
        },
    ]
    path, raw = _write_narration(tmp_path, approved, indent=2)

    with pytest.raises(SystemExit) as exc_info:
        _run_validate(monkeypatch, tmp_path)

    # A documented lint failure reaches the author as a compact per-block summary
    # (1-based block numbers, the key numbers, the fix, the report path), not a traceback.
    summary = str(exc_info.value.code)
    assert summary.splitlines()[1] == (
        "- 段 2 over_budget：10.00s-13.00s 写了 42 字，窗口约 8 字（硬上限 10，超出 32 字）。"
        "改法：缩短文字，或放宽/挪动时间窗"
    )
    assert str(tmp_path / "narration_lint.json") in summary
    assert path.read_text(encoding="utf-8") == raw
    lint = _read_json(tmp_path / "narration_lint.json")
    [error] = [item for item in lint["errors"] if item["code"] == "over_budget"]
    # JSON keeps the 0-based index; 3 s minus the 0.45 s TTS edge silence and the 0.1 s
    # tail pad leaves 2.45 s at 3.5 * 0.85 * 1.15 chars/s.
    assert error["index"] == 1
    assert error["budget_chars"] == 8
    assert error["limit_chars"] == 10
    assert error["actual_chars"] == 42
    assert error["over_chars"] == 32
    assert error["tts_overhead_seconds"] == 0.45
    assert "段 2 [10.00-13.00s] has 42 chars" in error["message"]
    assert "32 over the limit" in error["message"]
    assert not any(item["code"] == "over_budget" for item in lint["warnings"])


@pytest.mark.parametrize("extra_chars, ok", [(0, True), (1, False)], ids=["at_limit", "over"])
def test_full_over_budget_error_starts_just_past_the_hard_limit(
    tmp_path, extra_chars, ok
):
    from agent_text import _lint_char_budget
    from narration_lint import OVER_BUDGET_ERROR_RATIO, lint_narration

    limit = int(_lint_char_budget(0, 10) * OVER_BUDGET_ERROR_RATIO)
    text = "门" * (limit + extra_chars) + "。"
    report = lint_narration(
        [{"start": 0, "end": 10, "narration": text}], [], mode="full", work_dir=tmp_path
    )

    assert ("over_budget" in {item["code"] for item in report["errors"]}) is not ok


def test_lint_budget_reserves_one_utterance_of_tts_edge_silence():
    from agent_text import (
        TTS_UTTERANCE_OVERHEAD_SECONDS,
        _lint_char_budget,
        _recommended_char_budget,
    )

    # The brief's per-window budget, minus one utterance's edge silence.
    assert _lint_char_budget(0, 2.5) == _recommended_char_budget(
        0, 2.5 - TTS_UTTERANCE_OVERHEAD_SECONDS
    )
    assert _lint_char_budget(0, 2.5) < _recommended_char_budget(0, 2.5)
    assert _lint_char_budget(0, 0.3) == 0


def test_short_window_that_fits_the_brief_fails_full_lint_before_tts(tmp_path):
    """A 2.5 s window: the brief's rate alone admits 9 chars, but a TTS utterance of 9
    chars plus its edge silence does not fit, so lint stops it instead of assemble."""
    from agent_text import _lint_char_budget, _recommended_char_budget
    from narration_lint import OVER_BUDGET_ERROR_RATIO, lint_narration

    brief_budget = _recommended_char_budget(0, 2.5)
    assert brief_budget > int(_lint_char_budget(0, 2.5) * OVER_BUDGET_ERROR_RATIO)
    segment = {"start": 0, "end": 2.5, "narration": "门" * brief_budget + "。"}

    full = lint_narration([segment], [], mode="full", work_dir=tmp_path)
    [error] = [e for e in full["errors"] if e["code"] == "over_budget"]
    assert error["budget_chars"] == _lint_char_budget(0, 2.5)



def test_cut_output_warning_counts_the_utterance_edge_silence():
    """cut_output keeps over_budget a warning, but its spoken-length estimate now includes
    the TTS edge silence: 10 chars read at the median rate fit 2.4 s, the utterance does not."""
    from agent_text import TTS_UTTERANCE_OVERHEAD_SECONDS
    from narration_lint import lint_narration

    segment = {"start": 0, "end": 2.5, "narration": "门" * 10 + "。"}
    speech_only = 10 / (CONFIG["speech_rate"] * CONFIG["narration_speed"])
    assert speech_only < 2.4 < speech_only + TTS_UTTERANCE_OVERHEAD_SECONDS

    report = lint_narration([segment], [], mode="cut_output", work_dir=None)

    assert not [e for e in report["errors"] if e["code"] == "over_budget"]
    [warning] = [w for w in report["warnings"] if w["code"] == "over_budget"]
    assert warning["estimated_tts_seconds"] == round(
        speech_only + TTS_UTTERANCE_OVERHEAD_SECONDS, 2
    )


def test_cut_output_over_budget_text_stays_a_warning(monkeypatch, tmp_path):
    _write_output_evidence(tmp_path)
    approved = [{"start": 0, "end": 3, "narration": "等待同伴。" * 10}]
    path, _ = _write_narration(tmp_path, approved)

    _run_validate(monkeypatch, tmp_path, "cut_output", "--output-duration", "10")

    assert _approved_fields(_read_json(path)[0]) == approved[0]
    lint = _read_json(tmp_path / "narration_lint.json")
    assert any(item["code"] == "over_budget" for item in lint["warnings"])


def test_cut_mode_validates_without_writing(monkeypatch, tmp_path):
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

    with pytest.raises(SystemExit, match=code):
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
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
    )

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "段 1 invalid_narration：narration must be a string" in result.stderr
    assert str(tmp_path / "narration_lint.json") in result.stderr
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


def test_short_slot_that_fits_warns_without_dropping_segment(monkeypatch, tmp_path):
    # 1.4 s leaves 0.85 s of speech after the TTS edge silence and tail pad: room for
    # three characters at the conservative rate.
    approved = [
        {
            "start": 0,
            "end": 1.4,
            "narration": "他转身。",
            "pause_after_ms": 0,
            "tag": "keep",
        }
    ]
    path, _ = _write_narration(tmp_path, approved)

    _run_validate(monkeypatch, tmp_path)

    assert _approved_fields(_read_json(path)[0]) == approved[0]
    lint = _read_json(tmp_path / "narration_lint.json")
    assert any(item["code"] == "slot_too_short" for item in lint["warnings"])


def test_slot_too_short_for_its_text_fails_instead_of_being_dropped(monkeypatch, tmp_path):
    path, raw = _write_narration(
        tmp_path, [{"start": 0, "end": 0.1, "narration": "短。"}], separators=(",", ":")
    )

    with pytest.raises(SystemExit, match="段 1 over_budget"):
        _run_validate(monkeypatch, tmp_path)

    assert path.read_text(encoding="utf-8") == raw


def test_unexpected_validate_errors_still_raise_with_a_traceback(monkeypatch, tmp_path):
    _write_narration(tmp_path, [{"start": 0, "end": 5, "narration": "正常的一段。"}])

    def broken(*_args, **_kwargs):
        raise RuntimeError("ownership measurement crashed")

    monkeypatch.setattr(narration_validate, "measure_narration_speech_ownership", broken)
    with pytest.raises(RuntimeError, match="ownership measurement crashed"):
        _run_validate(monkeypatch, tmp_path)


def test_lint_failure_summary_lists_a_bounded_number_of_blocks():
    from lint_summary import MAX_LISTED_ERRORS, format_lint_failure

    errors = [
        {"level": "error", "index": i, "code": "empty_narration", "message": "narration text must not be empty"}
        for i in range(MAX_LISTED_ERRORS + 3)
    ]
    errors.append({"level": "error", "index": None, "code": "empty_narration_file", "message": "m"})
    summary = format_lint_failure({"errors": errors}, "narration_lint.json")
    lines = summary.splitlines()

    assert lines[0].startswith(f"narration.json 预检失败：{len(errors)} 个 error")
    assert lines[1] == "- 段 1 empty_narration：narration text must not be empty"
    assert len(lines) == 1 + MAX_LISTED_ERRORS + 2
    assert lines[-2] == "- …另有 4 个 error 未列出"
    assert lines[-1].endswith("narration_lint.json")


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

    with pytest.raises(SystemExit, match="段 1 source_sentence_anchors_unavailable"):
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


@pytest.mark.parametrize("duration", [None, "10", "0", "-1", "nan", "inf"])
def test_cut_output_cli_duration_failure_updates_lint(tmp_path, duration):
    _write_output_evidence(tmp_path)
    path, raw = _write_narration(
        tmp_path, [{"start": 0, "end": 11, "narration": "等待同伴。" * 30}], indent=3
    )
    report_path = tmp_path / "narration_lint.json"
    report_path.write_text(json.dumps({"ok": True, "stale": True}), encoding="utf-8")
    command = [sys.executable, str(Path(narration_validate.__file__)),
               "--work-dir", str(tmp_path), "--mode", "cut_output"]
    if duration is not None:
        command.extend(["--output-duration", duration])

    result = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", check=False,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},  # messages carry "段 N"; Windows pipes default to cp1252
    )

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
    assert "blockers" in current["deslop_qc"]
    assert not (tmp_path / "deslop_qc.json").exists()


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


@pytest.mark.parametrize("mode", ["full", "cut_output"])
def test_lint_always_requires_chronological_order(tmp_path, mode):
    """Validation never reorders segments, so unordered input goes back to the author."""
    from narration_lint import lint_narration

    unsorted = [
        {"start": 5.0, "end": 6.0, "narration": "第二段。"},
        {"start": 0.0, "end": 1.0, "narration": "第一段。"},
    ]
    report = lint_narration(unsorted, [], mode=mode, work_dir=tmp_path)
    assert "out_of_order" in {item["code"] for item in report["errors"]}


def test_validate_no_longer_accepts_preserve_approved_text(monkeypatch, tmp_path):
    _write_narration(tmp_path, [{"start": 0, "end": 2, "narration": "批准稿。"}])

    with pytest.raises(SystemExit):
        _run_validate(monkeypatch, tmp_path, "full", "--preserve-approved-text")
