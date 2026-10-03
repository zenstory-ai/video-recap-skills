import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "skills" / "video-script" / "scripts")
)
import json
import os
import validate as narration_validate
import pytest
from lib import CONFIG, env_float, file_identity
from agent_text import _text_char_count
from narration_lint import lint_narration


def _write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_source_anchors(work_dir, anchors):
    _write_json(
        work_dir / "speech_boundary_anchors.json",
        {"schema_version": 1, "sentence_anchors": anchors},
    )


def _write_output_evidence(
    work_dir, plan, *, sentence_anchors, speech_spans, quiet_windows
):
    _write_json(work_dir / "clip_plan_validated.json", plan)
    _write_json(
        work_dir / "speech_boundary_anchors_output.json",
        {
            "schema_version": 2,
            "timeline": "cut_output",
            "clip_plan_identity": file_identity(work_dir / "clip_plan_validated.json"),
            "sentence_anchors": sentence_anchors,
            "speech_spans": speech_spans,
            "quiet_windows": quiet_windows,
        },
    )


def _run_validate(monkeypatch, work_dir, mode, *extra):
    monkeypatch.setattr(
        sys,
        "argv",
        ["validate.py", "--work-dir", str(work_dir), "--mode", mode, *extra],
    )
    narration_validate.main()


def _run_validate_cut_output(monkeypatch, work_dir):
    _run_validate(monkeypatch, work_dir, "cut_output", "--output-duration", "10")


def test_text_char_count():
    assert _text_char_count("hello") == 5
    assert _text_char_count("你好世界") == 4
    assert _text_char_count("") == 0
    assert _text_char_count("你好，世界。") == 4
    assert _text_char_count("「你好」 world!") == 7


@pytest.mark.parametrize("raw", ["nan", "inf", "-inf"])
def test_env_float_rejects_nonfinite_values(monkeypatch, raw):
    monkeypatch.setenv("NONFINITE_FLOAT", raw)

    with pytest.raises(ValueError, match="NONFINITE_FLOAT.*finite"):
        env_float("NONFINITE_FLOAT", 1.0, minimum=0)


def test_lint_narration_reports_warnings_and_errors(tmp_path, monkeypatch):
    monkeypatch.setitem(CONFIG, "speech_rate", 3.5)
    monkeypatch.setitem(CONFIG, "speech_safety_margin", 0.85)
    report = lint_narration(
        [
            {
                "start": 0.0,
                "end": 3.0,
                "narration": "这是一段明显超过时间预算的很长很长解说文本。",
            },
            {"start": 2.5, "end": 4.0, "narration": "第二段没有句号"},
            {"start": 4.0, "end": 4.5, "narration": "太短。"},
            {"start": 5.0, "end": 6.0, "narration": ""},
        ],
        [{"scene_id": 0, "start": 0.0, "end": 6.0}],
        work_dir=tmp_path,
    )

    assert report["ok"] is False
    codes = {issue["code"] for issue in report["errors"] + report["warnings"]}
    assert "over_budget" in codes
    assert "time_overlap" in codes
    assert "slot_too_short" in codes
    assert "empty_narration" in codes
    assert "incomplete_sentence" in codes
    assert (tmp_path / "narration_lint.json").exists()


def test_lint_narration_rejects_empty_file(tmp_path):
    report = lint_narration([], work_dir=tmp_path)

    assert report["ok"] is False
    assert any(issue["code"] == "empty_narration_file" for issue in report["errors"])
    assert (tmp_path / "narration_lint.json").exists()


def test_lint_narration_rejects_malformed_visual_overlays(tmp_path):
    """Lint is the last cheap place to catch an overlay recap will read after TTS."""
    segment = {"start": 0.0, "end": 5.0, "narration": "开场的一段解说文字内容。"}
    scenes = [{"scene_id": 0, "start": 0.0, "end": 5.0}]

    for overlays in (
        {"type": "top_title", "text": "标题"},          # not an array
        ["top_title"],                                  # entry is not an object
        [{"text": "标题"}],                             # missing type
        [{"type": "top_title"}],                        # missing text
        [{"type": "top_title", "text": "  "}],          # blank text
        [{"type": "top_title", "text": "标题", "start": "0"}],  # non-numeric span
    ):
        report = lint_narration(
            [{**segment, "visual_overlays": overlays}], scenes, work_dir=tmp_path
        )
        codes = {issue["code"] for issue in report["errors"]}
        assert codes & {"invalid_visual_overlay", "invalid_visual_overlays"}, overlays

    ok = lint_narration(
        [{**segment, "visual_overlays": [{"type": "top_title", "text": "标题"}]}],
        scenes,
        work_dir=tmp_path,
    )
    assert not any(
        issue["code"].startswith("invalid_visual_overlay") for issue in ok["errors"]
    )


@pytest.mark.parametrize(
    "anchors, start, end, suggested_start, text_tail",
    [
        pytest.param(
            [
                {"time": 5.81, "text_tail": "带你重走詹姆斯的二十一年。", "confidence": "high"},
                {"time": 14.34, "text_tail": "把自己的名字写进历史。", "confidence": "high"},
                {"time": 22.86, "text_tail": "开启了自己的全明星之路。", "confidence": "high"},
            ],
            20.41,
            29.3,
            22.86,
            "全明星之路",
            id="mid_sentence_suggests_next_anchor",
        ),
        pytest.param(
            [
                {"time": 5.81, "pause_start": 5.22, "text_tail": "第一句说完。", "confidence": "high"},
                {"time": 14.34, "pause_start": 13.74, "text_tail": "第二句说完。", "confidence": "high"},
            ],
            6.10,
            12.0,
            14.34,
            "第二句说完",
            id="shortly_after_pause_ended_suggests_next_anchor",
        ),
        pytest.param(
            [{"time": 5.81, "pause_start": 5.22, "text_tail": "已完成句子。", "confidence": "high"}],
            8.0,
            12.0,
            None,
            "",
            id="after_last_known_anchor_has_no_fake_suggestion",
        ),
    ],
)
def test_lint_blocks_narration_entry_that_interrupts_source_sentence(
    tmp_path, anchors, start, end, suggested_start, text_tail
):
    _write_source_anchors(tmp_path, anchors)

    report = lint_narration(
        [
            {
                "start": start,
                "end": end,
                "narration": "这里切入会打断原句。",
                "overlaps_speech": True,
            },
        ],
        work_dir=tmp_path,
    )

    issue = next(
        item
        for item in report["errors"]
        if item["code"] == "interrupts_source_sentence"
    )
    assert issue["entry_time"] == start
    assert issue["suggested_start"] == suggested_start
    assert text_tail in issue["source_text_tail"]


def test_lint_never_allows_intentional_source_interrupt_override(tmp_path):
    _write_source_anchors(
        tmp_path, [{"time": 5.81, "text_tail": "一句说完。", "confidence": "high"}]
    )

    report = lint_narration(
        [
            {
                "start": 3.0,
                "end": 7.0,
                "narration": "这是有意的抢断式开场。",
                "overlaps_speech": True,
                "source_entry_policy": "intentional_interrupt",
                "source_entry_reason": "用突发新闻式抢断制造转折",
            },
        ],
        work_dir=tmp_path,
    )

    assert any(
        item["code"] == "interrupts_source_sentence" for item in report["errors"]
    )


def _write_cut_output_speech_evidence(work_dir):
    plan = {
        "clips": [
            {
                "source_start": 100.0,
                "source_end": 110.0,
                "output_start": 0.0,
                "output_end": 10.0,
            }
        ]
    }
    _write_json(
        work_dir / "speech_boundary_anchors.json",
        {"sentence_anchors": [{"time": 104.0, "pause_start": 103.8, "confidence": "high"}]},
    )
    _write_output_evidence(
        work_dir,
        plan,
        sentence_anchors=[{"time": 4.0, "pause_start": 3.8, "confidence": "high"}],
        speech_spans=[{"start": 0.0, "end": 10.0}],
        quiet_windows=[{"start": 3.8, "end": 4.1}],
    )


def test_cut_lint_suggests_output_clock_anchor_from_brief_evidence(tmp_path):
    """Lint reads the output-clock anchors video-understanding writes for cut pass 2."""
    _write_output_evidence(
        tmp_path,
        {
            "clips": [
                {
                    "source_start": 100.0,
                    "source_end": 110.0,
                    "output_start": 0.0,
                    "output_end": 10.0,
                }
            ]
        },
        sentence_anchors=[
            {
                "time": 4.0,
                "pause_start": 3.88,
                "source_time": 104.0,
                "source_pause_start": 103.88,
                "text_tail": "输出第四秒句末。",
                "confidence": "high",
            }
        ],
        speech_spans=[{"start": 1.0, "end": 5.0, "text": "清洗后一到五秒对白。"}],
        quiet_windows=[{"start": 6.0, "end": 8.0}],
    )

    report = lint_narration(
        [
            {
                "start": 2.0,
                "end": 6.0,
                "narration": "剪后时间中途切入。",
                "overlaps_speech": True,
            },
        ],
        mode="cut",
        work_dir=tmp_path,
    )

    issue = next(
        item for item in report["errors"] if item["code"] == "interrupts_source_sentence"
    )
    assert issue["suggested_start"] == 4.0


def test_validate_cut_output_uses_output_clock_sentence_anchors(
    monkeypatch, tmp_path
):
    _write_cut_output_speech_evidence(tmp_path)
    _write_json(
        tmp_path / "narration.json",
        [
            {
                "start": 4.0,
                "end": 6.0,
                "narration": "从剪后句末安全进入。",
                "overlaps_speech": True,
            }
        ],
    )

    _run_validate_cut_output(monkeypatch, tmp_path)

    report = json.loads((tmp_path / "narration_lint.json").read_text(encoding="utf-8"))
    assert not any(
        item["code"] == "interrupts_source_sentence" for item in report["errors"]
    )


def test_validate_cut_output_rejects_false_speech_override(monkeypatch, tmp_path):
    _write_cut_output_speech_evidence(tmp_path)
    _write_json(
        tmp_path / "narration.json",
        [
            {
                "start": 2.0,
                "end": 3.0,
                "narration": "伪造静音标记不能绕过门禁。",
                "overlaps_speech": False,
            }
        ],
    )

    with pytest.raises(SystemExit, match="段 1 interrupts_source_sentence"):
        _run_validate_cut_output(monkeypatch, tmp_path)


def test_validate_cut_output_fails_closed_without_mapped_speech_evidence(
    monkeypatch, tmp_path
):
    _write_json(
        tmp_path / "narration.json",
        [
            {
                "start": 2.0,
                "end": 3.0,
                "narration": "缺少输出证据时不能信任静音标记。",
                "overlaps_speech": False,
            }
        ],
    )

    with pytest.raises(SystemExit, match="段 1 source_sentence_anchors_unavailable"):
        _run_validate_cut_output(monkeypatch, tmp_path)


def test_validate_cut_output_checks_entry_before_later_quiet(monkeypatch, tmp_path):
    _write_output_evidence(
        tmp_path,
        {"clips": []},
        sentence_anchors=[{"time": 1.0, "pause_start": 0.95, "confidence": "high"}],
        speech_spans=[{"start": 0.0, "end": 1.0}],
        quiet_windows=[{"start": 1.0, "end": 10.0}],
    )
    _write_json(
        tmp_path / "narration.json",
        [
            {
                "start": 0.8,
                "end": 10.0,
                "narration": "后面大段静音不能掩盖入口仍在原声句内。",
                "overlaps_speech": False,
            }
        ],
    )

    with pytest.raises(SystemExit, match="段 1 interrupts_source_sentence"):
        _run_validate_cut_output(monkeypatch, tmp_path)


def test_validate_cut_output_marks_later_speech_after_mostly_quiet_entry(
    monkeypatch, tmp_path
):
    _write_output_evidence(
        tmp_path,
        {"clips": []},
        sentence_anchors=[{"time": 10.0, "pause_start": 9.8, "confidence": "high"}],
        speech_spans=[{"start": 8.5, "end": 10.0}],
        quiet_windows=[{"start": 0.0, "end": 8.5}],
    )
    narration_path = tmp_path / "narration.json"
    _write_json(
        narration_path,
        [
            {
                "start": 1.0,
                "end": 10.0,
                "narration": "入口安静，但后段原声仍需混音避让。",
                "overlaps_speech": False,
            }
        ],
    )

    _run_validate_cut_output(monkeypatch, tmp_path)

    persisted = json.loads(narration_path.read_text(encoding="utf-8"))
    assert persisted[0]["overlaps_speech"] is True


def test_lint_does_not_treat_back_to_back_narration_as_new_source_entry(tmp_path):
    _write_source_anchors(
        tmp_path,
        [
            {"time": 5.81, "text_tail": "第一句原声。", "confidence": "high"},
            {"time": 22.86, "text_tail": "下一句原声。", "confidence": "high"},
        ],
    )

    report = lint_narration(
        [
            {
                "start": 5.81,
                "end": 18.34,
                "narration": "第一段旁白。",
                "overlaps_speech": True,
            },
            {
                "start": 18.34,
                "end": 30.0,
                "narration": "旁白连续交棒。",
                "overlaps_speech": True,
            },
        ],
        work_dir=tmp_path,
    )

    assert not any(
        item["code"] == "interrupts_source_sentence" for item in report["errors"]
    )


def test_lint_counts_leading_original_audio_block_and_interval_union(tmp_path):
    report = lint_narration(
        [
            {"start": 5.81, "end": 18.34, "narration": "第一段旁白讲述选秀夜的起点。"},
            {
                "start": 18.34,
                "end": 30.0,
                "narration": "第二段旁白继续讲述全明星和季后赛。",
            },
        ],
        [{"scene_id": 0, "start": 0.0, "end": 30.0}],
        work_dir=tmp_path,
    )

    codes = {item["code"] for item in report["warnings"]}
    assert "no_original_blocks" not in codes
    assert "no_original_breaks" not in codes
    assert report["metrics"]["original_block_count"] >= 1


def test_lint_narration_cut_mode_checks_clip_membership_and_boundaries():
    """A beat outside the plan is an error; one spilling past its clip is silently trimmed
    by the mapper, so it must at least warn."""
    plan = {"clips": [{"clip_id": 0, "source_start": 10.0, "source_end": 20.0}]}
    scenes = [{"scene_id": 0, "start": 0.0, "end": 30.0}]

    membership = lint_narration(
        [
            {"start": 11.0, "end": 15.0, "narration": "片段内解说。"},
            {"start": 22.0, "end": 24.0, "narration": "片段外解说。"},
        ],
        scenes,
        clip_plan=plan,
        mode="cut",
    )
    assert membership["ok"] is False
    assert any(issue["code"] == "outside_clip_plan" for issue in membership["errors"])
    assert "crosses_clip_boundary" not in {i["code"] for i in membership["warnings"]}

    crossing = lint_narration(
        [{"start": 12.0, "end": 25.0, "narration": "跨过片段边界的解说。"}],
        scenes,
        clip_plan=plan,
        mode="cut",
    )  # mid 18.5 in clip, end 25 > 20
    assert "crosses_clip_boundary" in {i["code"] for i in crossing["warnings"]}


def test_lint_narration_warns_when_segment_spans_too_many_visual_beats(monkeypatch):
    monkeypatch.setitem(CONFIG, "visual_beat_max_seconds", 10.0)
    monkeypatch.setitem(CONFIG, "visual_beat_max_facts", 2)
    report = lint_narration(
        [
            {
                "start": 0.0,
                "end": 20.0,
                "narration": "这段长解说跨过太多画面锚点，应该拆开。",
            },
        ],
        [
            {
                "scene_id": 0,
                "start": 0.0,
                "end": 25.0,
                "frame_facts": {
                    "1.0": ["人物走入房间"],
                    "6.0": ["人物坐下"],
                    "12.0": ["人物起身争执"],
                    "18.0": ["镜头切到门外"],
                },
            }
        ],
    )

    codes = {issue["code"] for issue in report["warnings"]}
    assert "visual_beat_too_broad" in codes


def test_lint_block_coverage_metrics_and_warnings(monkeypatch):
    monkeypatch.setitem(CONFIG, "speech_rate", 3.5)
    monkeypatch.setitem(CONFIG, "narration_speed", 1.3)

    # Under-narrated: two tiny blocks over a long span -> coverage far below the ~0.7 target.
    sparse = lint_narration(
        [
            {
                "start": 0.0,
                "end": 4.0,
                "narration": "第一句话。",
                "pause_after_ms": 250,
            },
            {
                "start": 60.0,
                "end": 64.0,
                "narration": "很久之后的第二句话。",
                "pause_after_ms": 250,
            },
        ],
        mode="full",
    )
    sparse_codes = {issue["code"] for issue in sparse["warnings"]}
    assert "under_narrated" in sparse_codes
    assert sparse["metrics"]["narration_coverage"] < 0.5
    assert sparse["metrics"]["segment_count"] == 2

    # Healthy block layout: a few big blocks covering most of the span, with deliberate
    # original-audio gaps between them -> no coverage/fragmentation warnings.
    block = "范闲表面是个闲散少爷背地里却握着监察院最深的暗线这一次他押上全部身家也要查清楚母亲当年究竟为何而死"
    healthy = []
    t = 0.0
    for _ in range(6):
        healthy.append(
            {
                "start": round(t, 2),
                "end": round(t + 12.0, 2),
                "narration": block,
                "pause_after_ms": 250,
            }
        )
        t += 16.0
    report = lint_narration(healthy, mode="full")
    codes = {issue["code"] for issue in report["warnings"]}
    assert "under_narrated" not in codes
    assert "no_original_blocks" not in codes
    assert "fragmented_beats" not in codes
    assert 0.45 <= report["metrics"]["narration_coverage"] <= 0.85
    assert report["metrics"]["original_block_count"] >= 2

    # cut mode -> coverage lint is skipped (measured on the mapped output timeline elsewhere)
    cut_report = lint_narration(sparse, mode="cut")
    assert cut_report["metrics"] == {}


def test_lint_narration_accepts_ascii_period_as_complete_sentence():
    report = lint_narration(
        [
            {"start": 0.0, "end": 3.0, "narration": "It ends."},
        ],
        mode="full",
    )

    assert "incomplete_sentence" not in {issue["code"] for issue in report["warnings"]}


def test_lint_flags_fragmented_beats(tmp_path, monkeypatch):
    # The forbidden pattern: many lone short sentences instead of blocks -> each synthesizes as a
    # separate choppy TTS utterance, so fragmented_beats must fire.
    monkeypatch.setitem(CONFIG, "speech_rate", 3.5)
    monkeypatch.setitem(CONFIG, "narration_speed", 1.3)
    segs = [
        {"start": i * 5.0, "end": i * 5.0 + 1.6, "narration": "一句短解说。"}
        for i in range(8)
    ]
    report = lint_narration(segs, mode="full", work_dir=tmp_path)
    codes = {w["code"] for w in report["warnings"]}
    assert "fragmented_beats" in codes
    assert report["metrics"]["avg_block_chars"] < 16


def test_lint_flags_wall_to_wall_narration_with_no_original_blocks(monkeypatch):
    # The user's complaint: narration nearly wall-to-wall, the original never gets to breathe.
    monkeypatch.setitem(CONFIG, "speech_rate", 3.5)
    monkeypatch.setitem(CONFIG, "narration_speed", 1.3)
    rate = 3.5 * 1.3
    block = "这是一段连续不断的解说词没有给原声留下任何空隙一路讲到底"
    spoken = len(block) / rate
    segs = []
    t = 0.0
    for _ in range(6):
        segs.append(
            {
                "start": round(t, 2),
                "end": round(t + spoken + 0.05, 2),
                "narration": block,
            }
        )
        t += spoken + 0.1  # next block starts right after -> no original gap
    report = lint_narration(segs, mode="full")
    codes = {w["code"] for w in report["warnings"]}
    assert "no_original_blocks" in codes
    assert report["metrics"]["original_block_count"] == 0
    assert report["metrics"]["narration_coverage"] > 0.85


# A beat that is 84% quiet but still covers 0.7s of source speech: the retired full-mode
# quiet-ratio rule called it quiet; speech ownership (speech minus quiet) does not.
_MOSTLY_QUIET_BEAT = {"start": 0.0, "end": 5.0, "narration": "门外安静了很久。"}
_QUIET_WINDOW = {"start": 0.0, "end": 4.2}
_LATE_SPEECH = {"start": 4.3, "end": 5.0, "text": "等等我"}


def _validated_ownership(monkeypatch, work_dir, mode):
    _write_json(work_dir / "narration.json", [dict(_MOSTLY_QUIET_BEAT)])
    if mode == "full":
        _run_validate(monkeypatch, work_dir, "full")
    else:
        _run_validate_cut_output(monkeypatch, work_dir)
    persisted = json.loads((work_dir / "narration.json").read_text(encoding="utf-8"))
    return persisted[0]["overlaps_speech"]


def test_full_and_cut_output_measure_speech_ownership_with_one_algorithm(
    monkeypatch, tmp_path
):
    full_dir, cut_dir = tmp_path / "full", tmp_path / "cut"
    full_dir.mkdir()
    cut_dir.mkdir()
    _write_json(full_dir / "asr_result.json", [_LATE_SPEECH])
    _write_json(
        full_dir / "silence_periods.json", [{**_QUIET_WINDOW, "has_speech": False}]
    )
    _write_output_evidence(
        cut_dir,
        {"clips": []},
        sentence_anchors=[],
        speech_spans=[_LATE_SPEECH],
        quiet_windows=[_QUIET_WINDOW],
    )

    full = _validated_ownership(monkeypatch, full_dir, "full")
    cut_output = _validated_ownership(monkeypatch, cut_dir, "cut_output")

    assert full is cut_output is True


def test_cut_validate_uses_validated_plan_only_when_newer_than_raw(tmp_path):
    raw_payload = {"clips": [{"start": 40.0, "end": 50.0}]}
    raw = tmp_path / "clip_plan.json"
    validated = tmp_path / "clip_plan_validated.json"
    _write_json(raw, raw_payload)
    _write_json(
        validated, {"clips": [{"clip_id": 0, "source_start": 40.0, "source_end": 50.0}]}
    )
    os.utime(raw, ns=(2_000_000_000, 2_000_000_000))
    os.utime(validated, ns=(1_000_000_000, 1_000_000_000))
    stale = narration_validate._load_cut_clip_plan(tmp_path)
    assert stale["clips"][0]["start"] == 40.0  # validated older than raw -> raw wins

    os.utime(validated, ns=(2_000_000_000, 2_000_000_000))
    fresh = narration_validate._load_cut_clip_plan(tmp_path)
    assert fresh["clips"][0]["source_start"] == 40.0


def test_full_validation_preserves_visual_overlays(tmp_path, monkeypatch):
    """Full mode persists measured ownership without losing render metadata."""
    overlays = [
        {"type": "top_title", "text": "二十一年", "start": 0.0, "end": 2.0},
        {"type": "inline_label_or_callout", "text": "2003", "start": 2.0, "end": 3.0},
    ]
    _write_json(
        tmp_path / "narration.json",
        [
            {
                "start": 0.0,
                "end": 10.0,
                "narration": "这是一段能够通过完整模式校验的解说。",
                "visual_overlays": overlays,
            }
        ],
    )
    _write_json(
        tmp_path / "vlm_analysis.json",
        [{"scene_id": 0, "start": 0.0, "end": 10.0, "description": "人物站在球场中央"}],
    )
    monkeypatch.setitem(CONFIG, "speech_rate", 6.0)

    _run_validate(monkeypatch, tmp_path, "full")

    rewritten = json.loads((tmp_path / "narration.json").read_text(encoding="utf-8"))
    assert rewritten[0]["visual_overlays"] == overlays


def test_cut_output_duration_bounds_reject_out_of_range_and_non_finite_input():
    validate_bounds = narration_validate._validate_output_timeline_bounds
    validate_bounds([{"start": 0.0, "end": 9.95, "narration": "有效。"}], 10.0)

    bad = [
        {"start": -0.1, "end": 1.0, "narration": "负时间。"},
        {"start": 9.0, "end": 10.2, "narration": "超出时长。"},
        {"start": 10.1, "end": 11.0, "narration": "完全在外。"},
    ]
    with pytest.raises(SystemExit) as exc:
        validate_bounds(bad, 10.0)
    msg = str(exc.value)
    assert "output_duration=10.000" in msg
    assert "段 1 start=" in msg and "段 2 end=" in msg and "段 3 [" in msg

    with pytest.raises(SystemExit, match="finite and positive"):
        validate_bounds([{"start": 0.0, "end": 1.0}], float("nan"))




def _full_mode_entry_lint(tmp_path, anchors, start):
    _write_json(
        tmp_path / "speech_boundary_anchors.json",
        {"schema_version": 2, "sentence_anchors": anchors},
    )
    _write_json(tmp_path / "asr_result.json", [{"start": 0.0, "end": 30.0, "text": "持续原声。"}])
    report = lint_narration(
        [{"start": start, "end": start + 3.0, "narration": "入口测试。", "overlaps_speech": True}],
        mode="full",
        work_dir=tmp_path,
    )
    return [item for item in report["errors"] if "source_sentence" in item["code"]]


_UNVERIFIED_ANCHOR = {
    "time": 12.0, "pause_start": 11.8, "confidence": "low",
    "boundary_use": "unverified", "timing_bound_seconds": 9.0,
}


def test_entry_at_unverified_anchor_passes_the_sentence_gate(tmp_path):
    assert _full_mode_entry_lint(tmp_path, [_UNVERIFIED_ANCHOR], 12.0) == []


def test_mid_speech_entry_with_unverified_anchors_names_the_boundary_use(tmp_path):
    errors = _full_mode_entry_lint(tmp_path, [_UNVERIFIED_ANCHOR], 8.0)
    assert [item["code"] for item in errors] == ["interrupts_source_sentence"]
    assert errors[0]["suggested_start"] == 12.0
    assert errors[0]["anchor_boundary_use"] == "unverified"


def test_only_unusable_anchors_still_report_anchors_unavailable(tmp_path):
    errors = _full_mode_entry_lint(
        tmp_path, [{**_UNVERIFIED_ANCHOR, "boundary_use": "none"}], 8.0
    )
    assert [item["code"] for item in errors] == ["source_sentence_anchors_unavailable"]


def test_schema1_anchor_is_reported_as_unverified(tmp_path):
    _write_source_anchors(tmp_path, [{"time": 12.0, "pause_start": 11.8, "confidence": "high"}])
    _write_json(tmp_path / "asr_result.json", [{"start": 0.0, "end": 30.0, "text": "持续原声。"}])
    report = lint_narration(
        [{"start": 8.0, "end": 11.0, "narration": "入口测试。", "overlaps_speech": True}],
        mode="full",
        work_dir=tmp_path,
    )
    errors = [item for item in report["errors"] if "source_sentence" in item["code"]]
    assert [item["code"] for item in errors] == ["interrupts_source_sentence"]
    assert errors[0]["anchor_boundary_use"] == "unverified"


@pytest.mark.parametrize(("start", "blocked"), [(16.0, False), (15.5, True)])
def test_entry_ignores_interjection_only_window_beyond_the_dialogue_guard(
    tmp_path, start, blocked
):
    # Same rule as the cut gate: "啊！" has no sentence to interrupt, except the 1s guard
    # on the edge it shares with real dialogue.
    _write_json(
        tmp_path / "speech_boundary_anchors.json",
        {"schema_version": 2, "sentence_anchors": [_UNVERIFIED_ANCHOR]},
    )
    _write_json(
        tmp_path / "asr_result.json",
        [{"start": 0.0, "end": 15.0, "text": "真实对白。"}, {"start": 15.0, "end": 30.0, "text": "啊！"}],
    )
    report = lint_narration(
        [{"start": start, "end": start + 3.0, "narration": "入口测试。", "overlaps_speech": True}],
        mode="full",
        work_dir=tmp_path,
    )
    codes = [item["code"] for item in report["errors"] if "source_sentence" in item["code"]]
    assert codes == (["interrupts_source_sentence"] if blocked else [])
