import json
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parents[2]
        / "skills"
        / "video-recap"
        / "scripts"
    ),
)

from lib import CONFIG  # noqa: E402
import briefing.timeline as brief_timeline  # noqa: E402
from briefing.builder import _duration_label, build_agent_brief  # noqa: E402

SCENES = [{"scene_id": 0, "start": 0.0, "end": 6.0, "description": "门口对峙"}]
ASR = [{"start": 1.0, "end": 5.0, "text": "第一句对白。第二句反击。"}]
SILENCE = [{"start": 0.0, "end": 1.0, "duration": 1.0, "has_speech": False}]


def _brief_text(tmp_path, **kwargs):
    return build_agent_brief(SCENES, ASR, SILENCE, 6.0, tmp_path, **kwargs).read_text(
        encoding="utf-8"
    )


def test_brief_uses_raw_asr_without_leaking_hardcoded_entities_or_removed_sidecars(tmp_path):
    text = _brief_text(tmp_path, style="纪实复盘")
    for removed in ("deslop_qc_requirements.json", "asr_writing_chunks.json", "timeline_fusion.json"):
        assert not (tmp_path / removed).exists()
    for leaked in ("范闲", "监察院", "五竹", "京都"):
        assert leaked not in text
    assert ASR[0]["text"] in text


def _write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_source_anchors(work_dir, anchors):
    _write_json(
        work_dir / "speech_boundary_anchors.json",
        {"schema_version": 1, "sentence_anchors": anchors},
    )


def _set_brief_mode(monkeypatch, edit_mode, target_duration="", context_info=""):
    monkeypatch.setitem(CONFIG, "edit_mode", edit_mode)
    monkeypatch.setitem(CONFIG, "target_duration", target_duration)
    monkeypatch.setitem(CONFIG, "context_info", context_info)


def _agent_brief_text(scenes, asr, silence, duration, work_dir, **kwargs):
    return build_agent_brief(scenes, asr, silence, duration, work_dir, **kwargs).read_text(
        encoding="utf-8"
    )


def _json_examples(text):
    return [
        json.loads(raw) for raw in re.findall(r"```json\s*\n(.*?)\n```", text, re.DOTALL)
    ]


def test_build_agent_brief_cut_mode_sizes_to_output(monkeypatch, tmp_path):
    """Cut mode sizes the target to the OUTPUT length, not the source (2h->30min regression)."""
    _set_brief_mode(monkeypatch, "cut", target_duration="1m")
    scenes = [
        {
            "scene_id": i,
            "start": i * 60.0,
            "end": i * 60.0 + 60.0,
            "description": "画面",
        }
        for i in range(10)
    ]
    text = _agent_brief_text(scenes, [], [], 600.0, tmp_path)
    assert "CUT OUTPUT" in text
    assert "sized to the ~1min CUT OUTPUT, NOT the 10min source" in text
    assert "step 1 of 2" in text
    assert "Inspect dense scene-change candidates" in text
    assert "restore same-source motion" in text
    clip_plan = next(
        item for item in _json_examples(text) if isinstance(item, dict) and "clips" in item
    )
    reason_parts = [part.strip() for part in clip_plan["clips"][0]["reason"].split("|")]
    assert clip_plan["target_duration"] == "1m"
    assert len(reason_parts) == 7
    assert reason_parts[0].startswith("b") and "→" in reason_parts[2]
    assert reason_parts[3].startswith("POV=")
    assert reason_parts[-2].startswith("入点=") and reason_parts[-1].startswith("出点=")


def test_build_agent_brief_cut_pass2_narrates_output_timeline_sized_to_validated_cut(
    monkeypatch, tmp_path
):
    """Once edited_source.mp4 exists the cut brief becomes the PASS-2 variant: narrate in
    OUTPUT time, list the kept clips, and size to the validated cut, not --target-duration."""
    _set_brief_mode(monkeypatch, "cut", target_duration="1m")
    (tmp_path / "edited_source.mp4").write_bytes(b"edited")
    _write_json(
        tmp_path / "clip_plan_validated.json",
        {
            "clips": [
                {
                    "clip_id": 0,
                    "source_start": 10.0,
                    "source_end": 20.0,
                    "output_start": 0.0,
                    "output_end": 10.0,
                    "reason": "开端",
                },
                {
                    "clip_id": 1,
                    "source_start": 40.0,
                    "source_end": 50.0,
                    "output_start": 10.0,
                    "output_end": 20.0,
                    "reason": "转折",
                },
            ],
            "total_duration": 20.0,
        },
    )
    scenes = [{"scene_id": 0, "start": 10.0, "end": 50.0, "description": "保留片段"}]
    text = _agent_brief_text(scenes, [], [], 120.0, tmp_path)
    assert "step 2 of 2: write `narration.json` in OUTPUT time" in text
    assert "Kept clips on the OUTPUT timeline" in text
    assert "OUTPUT 0.0–10.0s ← SOURCE[0] 10.0–20.0s" in text
    assert "step 1 of 2" not in text
    assert "across the ~20s CUT OUTPUT" in text
    assert "edited_source.mp4` (~20s)" in text
    assert "~1min" not in text


@pytest.mark.parametrize(
    "seconds, label",
    [
        (20.0, "20s"),
        (59.6, "1min"),
        (60.0, "1min"),
        (90.0, "1m30s"),
        (101.5, "1m42s"),
        (600.0, "10min"),
        (3599.0, "59m59s"),
        (3599.6, "1h"),
        (3660.0, "1h01m"),
        (7265.0, "2h01m05s"),
        (36005.0, "10h00m05s"),
    ],
)
def test_duration_labels_keep_the_seconds(seconds, label):
    assert _duration_label(seconds) == label


def test_cut_brief_shows_a_90s_target_in_seconds(monkeypatch, tmp_path):
    _set_brief_mode(monkeypatch, "cut", target_duration="90s")
    scenes = [{"scene_id": 0, "start": 0.0, "end": 600.0, "description": "画面"}]
    text = _agent_brief_text(scenes, [], [], 600.0, tmp_path)
    assert "Goal: a ~1m30s recap cut from a 10min source (sized to the ~1m30s CUT OUTPUT, NOT the 10min source)." in text
    assert "~2min" not in text


def test_cut_pass2_scene_guide_lists_only_kept_footage_in_output_time(
    monkeypatch, tmp_path
):
    """Pass 2 narrates edited_source.mp4: the guide is on the OUTPUT clock and leaves out
    scenes the cut dropped (credits), instead of the full source-time guide."""
    _set_brief_mode(monkeypatch, "cut", target_duration="20s")
    (tmp_path / "edited_source.mp4").write_bytes(b"edited")
    _write_json(
        tmp_path / "clip_plan_validated.json",
        {
            "clips": [
                {"source_start": 10.0, "source_end": 20.0, "output_start": 0.0, "output_end": 10.0},
                {"source_start": 40.0, "source_end": 50.0, "output_start": 10.0, "output_end": 20.0},
            ],
        },
    )
    scenes = [
        {
            "scene_id": 0,
            "start": 10.0,
            "end": 50.0,
            "description": "对峙",
            "frame_facts": {"12.0": ["抬头"], "30.0": ["剪掉的动作"]},
        },
        {"scene_id": 1, "start": 50.0, "end": 60.0, "description": "片尾演职员表"},
    ]
    asr = [{"start": 41.0, "end": 43.0, "text": "保留的对白。"}]
    silence = [{"start": 44.0, "end": 47.0, "duration": 3.0, "has_speech": False}]

    text = _agent_brief_text(scenes, asr, silence, 60.0, tmp_path)

    guide = text.split("## Scene timing guide (OUTPUT time)")[1]
    assert "## Scene timing guide\n" not in text
    assert "### OUTPUT 0.0-10.0s (source scene 1 part 1)" in guide
    assert "### OUTPUT 10.0-20.0s (source scene 1 part 2)" in guide
    assert "2.000s: 抬头" in guide and "剪掉的动作" not in guide
    assert "[11.0-13.0] 保留的对白。" in guide
    assert "Quiet windows: 14.0-17.0s" in guide
    assert "片尾演职员表" not in guide
    assert "### Scene " not in text and "10.0-50.0s" not in text


def test_build_agent_brief_keeps_plan_linkage_in_the_board_not_narration_schema(
    monkeypatch, tmp_path
):
    _set_brief_mode(monkeypatch, "full")

    text = _agent_brief_text(
        [{"scene_id": 0, "start": 0.0, "end": 8.0, "description": "人物作出选择"}],
        [{"start": 1.0, "end": 3.0, "text": "我决定留下。"}],
        [],
        8.0,
        tmp_path,
    )
    narration = next(
        item
        for item in _json_examples(text)
        if isinstance(item, list) and item and "narration" in item[0]
    )

    assert set(narration[0]) == {
        "start",
        "end",
        "narration",
        "pause_after_ms",
        "overlaps_speech",
        "emotion",
        "source_entry_policy",
    }
    assert "beat 对应关系记录在 `visual_audio_board.json`" in text
    assert "`narration.json` 仍只承载时间、文本与朗读参数" in text


def test_build_agent_brief_surfaces_sentence_end_entry_anchors(tmp_path):
    _write_source_anchors(
        tmp_path,
        [
            {"time": 5.81, "text_tail": "带你重走詹姆斯的二十一年。", "confidence": "high"},
            {"time": 22.86, "text_tail": "开启了自己的全明星之路。", "confidence": "high"},
        ],
    )

    text = _agent_brief_text(
        [{"scene_id": 0, "start": 0.0, "end": 30.0, "description": "生涯回顾"}],
        [{"start": 0.0, "end": 30.0, "text": "原声持续讲述。"}],
        [],
        30.0,
        tmp_path,
    )

    assert "原声句末安全切入点" in text
    assert "5.81s" in text and "22.86s" in text
    assert "interrupts_source_sentence" in text
    assert "source_entry_policy" in text


def test_build_agent_brief_preserves_freeform_style_and_artifact_contract(tmp_path):
    style = "悬疑冷幽默，但每句都像朋友复盘：别端着，保留东北味儿"

    text = _agent_brief_text(
        [{"scene_id": 0, "start": 0.0, "end": 6.0, "description": "门口对峙"}],
        [{"start": 1.0, "end": 5.0, "text": "第一句对白。第二句反击。"}],
        [{"start": 0.0, "end": 1.0, "duration": 1.0, "has_speech": False}],
        6.0,
        tmp_path,
        style=style,
    )

    assert f"- Style (--style, freeform verbatim guidance): {style}" in text
    assert (
        "Do not translate `--style` into a preset, enum, switch, or fallback ladder"
        in text
    )
    assert "style_card.json" in text and "packaging_plan.json" in text
    assert "not a preset enum, fixed taxonomy" in text


def test_agent_brief_ignores_malformed_optional_artifacts(monkeypatch, tmp_path):
    _set_brief_mode(monkeypatch, "full")
    asr = [{"start": 0.0, "end": 1.0, "text": "原始对白"}]
    _write_json(tmp_path / "asr_result.json", asr)
    for name in (
        "mimo_video_overview.json",
        "mimo_video_overview.status.json",
    ):
        (tmp_path / name).write_text("not json", encoding="utf-8")

    text = _agent_brief_text(
        [{"scene_id": 0, "start": 0.0, "end": 1.0, "description": "画面"}],
        asr,
        [],
        1.0,
        tmp_path,
    )

    assert "原始对白" in text


def test_build_agent_brief_injects_background_research(monkeypatch, tmp_path):
    _set_brief_mode(monkeypatch, "full")
    _write_json(
        tmp_path / "background_research.json",
        {
            "synopsis": "少年范闲深夜查案。",
            "characters": {"范闲": "主角", "五竹": "范闲的护卫"},
        },
    )
    text = _agent_brief_text(
        [
            {
                "scene_id": 0,
                "start": 0.0,
                "end": 3.0,
                "description": "夜路",
                "frame_facts": {"1.0": ["走路"]},
            }
        ],
        [{"start": 0.0, "end": 3.0, "text": "你终于来了"}],
        [],
        3.0,
        tmp_path,
    )
    assert "Story context" in text
    assert "五竹" in text
    assert "范闲的护卫" in text


def test_brief_accepts_research_written_with_bare_strings(monkeypatch, tmp_path):
    """The VLM step accepts string plot arcs / notes; the brief must not crash on them afterwards."""
    _set_brief_mode(monkeypatch, "full")
    _write_json(
        tmp_path / "background_research.json",
        {
            "plot_arcs": ["范闲查母亲旧案"],
            "cultural_notes": ["监察院：庆国的监察机构"],
            "character_details": {"范闲": {"aliases": "小范大人", "role": "主角"}, "五竹": "护卫"},
        },
    )
    text = _agent_brief_text(
        [{"scene_id": 0, "start": 0.0, "end": 3.0, "description": "夜路"}],
        [{"start": 0.0, "end": 3.0, "text": "你终于来了"}],
        [],
        3.0,
        tmp_path,
    )
    assert "    - 范闲查母亲旧案" in text
    assert "    - 监察院：庆国的监察机构" in text
    assert "范闲: 别名 小范大人; 主角" in text


def test_parse_target_seconds_table():
    """Parse documented forms, leave unset values empty, and fail fast on typos."""
    from briefing.timeline import _parse_target_seconds

    assert _parse_target_seconds("1:30") == 90.0
    assert _parse_target_seconds("00:30:00") == 1800.0
    assert _parse_target_seconds("30m") == 1800.0
    assert _parse_target_seconds("1h5m") == 3900.0
    assert _parse_target_seconds("600") == 600.0
    assert _parse_target_seconds(90) == 90.0
    for unset in ("", None, "  "):
        assert _parse_target_seconds(unset) is None
    for bad in (
        "abc",
        "0",
        "-5",
        "10x",
        "1:-30",
        "01:75",
        "1:60:00",
        "1:2:3:4",
        "nan",
        "inf",
        float("nan"),
        float("inf"),
    ):
        with pytest.raises(ValueError):
            _parse_target_seconds(bad)


def test_cut_pass2_agent_brief_writes_output_time_evidence(monkeypatch, tmp_path):
    _set_brief_mode(monkeypatch, "cut", target_duration="10s")
    raw_plan = {"clips": [{"start": 100.0, "end": 110.0}]}
    _write_json(tmp_path / "clip_plan.json", raw_plan)
    _write_json(
        tmp_path / "clip_plan_validated.json",
        {
            "clips": [
                {
                    "source_start": 100.0,
                    "source_end": 110.0,
                    "output_start": 0.0,
                    "output_end": 10.0,
                }
            ],
        },
    )
    (tmp_path / "edited_source.mp4").write_bytes(b"edited")
    _write_source_anchors(
        tmp_path,
        [{"time": 104.0, "text_tail": "输出第四秒句末。", "confidence": "high"}],
    )
    asr_payload = [{"start": 101.0, "end": 105.0, "text": "输出一到五秒对白。"}]
    _write_json(tmp_path / "asr_result.json", asr_payload)

    text = _agent_brief_text(
        [
            {
                "scene_id": 7,
                "start": 100.0,
                "end": 110.0,
                "description": "保留片段",
                "frame_facts": {"102.0": ["抬头"]},
            }
        ],
        asr_payload,
        [{"start": 106.0, "end": 108.0, "duration": 2.0, "has_speech": False}],
        120.0,
        tmp_path,
    )

    assert "[1.0-5.0] 输出一到五秒对白。" in text
    assert "4.00s [unverified] (SOURCE 104.00s)" in text  # schema-1 anchor
    output_anchors = json.loads(
        (tmp_path / "speech_boundary_anchors_output.json").read_text(encoding="utf-8")
    )
    assert output_anchors["sentence_anchors"][0]["time"] == 4.0
    assert output_anchors["sentence_anchors"][0]["pause_start"] == 3.88
    assert output_anchors["sentence_anchors"][0]["source_pause_start"] == 103.88


def test_cut_output_anchors_map_to_every_repeated_source_range(tmp_path):
    plan = {
        "clips": [
            {
                "source_start": 0.0,
                "source_end": 10.0,
                "output_start": 0.0,
                "output_end": 10.0,
            },
            {
                "source_start": 0.0,
                "source_end": 10.0,
                "output_start": 10.0,
                "output_end": 20.0,
            },
        ]
    }
    _write_json(tmp_path / "clip_plan_validated.json", plan)
    (tmp_path / "edited_source.mp4").write_bytes(b"edited")
    _write_json(
        tmp_path / "speech_boundary_anchors.json",
        {"sentence_anchors": [{"time": 4.0, "pause_start": 3.8, "confidence": "high"}]},
    )

    anchors = brief_timeline._sentence_entry_anchors_for_brief(tmp_path, "cut")

    assert [row["time"] for row in anchors] == [4.0, 14.0]


def test_cut_output_anchors_outside_the_output_are_dropped(tmp_path):
    """The 0.05 s source slack must not place an anchor before 0 or past the output end."""
    _write_json(
        tmp_path / "clip_plan_validated.json",
        {"clips": [{"source_start": 100.0, "source_end": 181.40,
                    "output_start": 0.0, "output_end": 81.40}]},
    )
    (tmp_path / "edited_source.mp4").write_bytes(b"edited")
    _write_json(
        tmp_path / "speech_boundary_anchors.json",
        {"sentence_anchors": [
            {"time": 99.97, "confidence": "high"},
            {"time": 150.0, "confidence": "high"},
            {"time": 181.40, "confidence": "high"},
            {"time": 181.42, "confidence": "high"},
        ]},
    )

    anchors = brief_timeline._sentence_entry_anchors_for_brief(tmp_path, "cut")

    assert [row["time"] for row in anchors] == [50.0, 81.4]
    output = json.loads(
        (tmp_path / "speech_boundary_anchors_output.json").read_text(encoding="utf-8")
    )
    assert [row["source_time"] for row in output["sentence_anchors"]] == [150.0, 181.4]


def test_cut_output_anchor_past_a_snapped_clip_edge_stays_on_that_clip(tmp_path):
    """A pause end 20 ms past a frame-snapped clip end maps to that clip's output end, not into
    the next clip (a different source range). One within the edge tolerance of two clips maps
    to each at its edge; one a clip plays itself is not duplicated onto a neighbour's edge."""
    plan = {"clips": [
        {"source_start": 10.0, "source_end": 12.48, "output_start": 0.0, "output_end": 2.48},
        {"source_start": 40.0, "source_end": 43.0, "output_start": 2.48, "output_end": 5.48},
        {"source_start": 12.51, "source_end": 14.0, "output_start": 5.48, "output_end": 6.97},
    ]}
    _write_json(tmp_path / "clip_plan_validated.json", plan)
    (tmp_path / "edited_source.mp4").write_bytes(b"edited")
    _write_json(tmp_path / "speech_boundary_anchors.json", {"sentence_anchors": [
        {"time": 12.5, "pause_start": 12.3, "expected_time": 12.52, "confidence": "high"},
        {"time": 39.97, "confidence": "high"},
        {"time": 12.53, "pause_start": 12.52, "confidence": "high"},
    ]})

    anchors = brief_timeline._sentence_entry_anchors_for_brief(tmp_path, "cut")

    rows = [(a["source_time"], a["time"], a["pause_start"]) for a in anchors]
    assert rows == [(12.5, 2.48, 2.3), (39.97, 2.48, 2.48), (12.5, 5.48, 5.48), (12.53, 5.5, 5.49)]
    assert anchors[0]["expected_time"] == 2.48


def test_cut_output_brief_labels_unverified_anchors_and_maps_pause_end(tmp_path):
    _write_json(
        tmp_path / "clip_plan_validated.json",
        {"clips": [{"source_start": 100.0, "source_end": 110.0,
                    "output_start": 0.0, "output_end": 10.0}]},
    )
    (tmp_path / "edited_source.mp4").write_bytes(b"edited")
    _write_json(
        tmp_path / "speech_boundary_anchors.json",
        {"schema_version": 2, "sentence_anchors": [
            {"time": 104.0, "pause_start": 103.8, "pause_end": 104.0, "expected_time": 103.5,
             "confidence": "low", "boundary_use": "unverified",
             "timing_bound_seconds": 9.24, "text_tail": "估计句末。"},
            {"time": 106.0, "pause_start": 105.8, "pause_end": 106.0,
             "confidence": "low", "boundary_use": "none",
             "timing_bound_seconds": 9.0, "text_tail": "不可用。"},
        ]},
    )

    lines = brief_timeline._format_sentence_entry_anchors_for_brief(tmp_path, "cut")

    text = "\n".join(lines)
    assert "4.00s [unverified ±9.2s] (SOURCE 104.00s) 估计句末。" in text
    assert "不可用" not in text
    output = json.loads(
        (tmp_path / "speech_boundary_anchors_output.json").read_text(encoding="utf-8")
    )
    mapped = output["sentence_anchors"][0]
    assert mapped["pause_end"] == mapped["time"] == 4.0
    assert mapped["source_pause_end"] == 104.0
    assert (mapped["expected_time"], mapped["source_expected_time"]) == (3.5, 103.5)


def test_cut_output_speech_spans_keep_raw_text_for_a_partial_window(tmp_path):
    """The partial-window marker is brief prose. In speech_spans it would turn an
    interjection-only window into dialogue for the lint and the mix, which read this text."""
    _write_json(
        tmp_path / "clip_plan_validated.json",
        {"clips": [{"source_start": 100.0, "source_end": 110.0,
                    "output_start": 0.0, "output_end": 10.0}]},
    )
    (tmp_path / "edited_source.mp4").write_bytes(b"edited")
    _write_json(tmp_path / "speech_boundary_anchors.json", {"sentence_anchors": []})
    _write_json(tmp_path / "asr_result.json", [{"start": 108.0, "end": 112.0, "text": "嗯"}])

    brief_timeline._sentence_entry_anchors_for_brief(tmp_path, "cut")

    output = json.loads(
        (tmp_path / "speech_boundary_anchors_output.json").read_text(encoding="utf-8")
    )
    assert output["speech_spans"] == [{"start": 8.0, "end": 10.0, "text": "嗯"}]


@pytest.mark.parametrize(
    "validated_plan, match",
    [
        pytest.param(
            None,
            "cut pass2 brief requires fresh clip_plan_validated.json",
            id="missing_validated_plan",
        ),
        pytest.param(
            {
                "clips": [
                    {
                        "source_start": 100.0,
                        "source_end": float("nan"),
                        "output_start": 0.0,
                        "output_end": 10.0,
                    }
                ],
            },
            "non-finite clip span",
            id="non_finite_clip_span",
        ),
        pytest.param(
            {
                "clips": [
                    {
                        "source_start": 100.0,
                        "source_end": 110.0,
                        "output_start": 0.0,
                        "output_end": 10.0,
                    }
                ],
            },
            "stale clip_plan_validated.json",
            id="stale_validated_plan",
        ),
    ],
)
def test_cut_pass2_agent_brief_fails_closed_on_bad_output_spans(
    monkeypatch, tmp_path, validated_plan, match
):
    _set_brief_mode(monkeypatch, "cut", target_duration="10s")
    (tmp_path / "edited_source.mp4").write_bytes(b"edited")
    if validated_plan is not None:
        _write_json(tmp_path / "clip_plan_validated.json", validated_plan)
    if match.startswith("stale"):
        _write_json(tmp_path / "clip_plan.json", {"clips": []})
        os.utime(tmp_path / "clip_plan_validated.json", ns=(1, 1))

    with pytest.raises(SystemExit, match=match):
        build_agent_brief(
            [{"scene_id": 7, "start": 100.0, "end": 110.0, "description": "保留片段"}],
            [{"start": 101.0, "end": 105.0, "text": "源时间对白。"}],
            [],
            120.0,
            tmp_path,
        )


def test_brief_counts_moderation_refused_scenes(tmp_path):
    """A refused scene has a blank placeholder description; the brief must say why."""
    scenes = [
        {"scene_id": 0, "start": 0.0, "end": 3.0, "description": "门口对峙"},
        {
            "scene_id": 1,
            "start": 3.0,
            "end": 6.0,
            "description": "(VLM 无法识别此场景画面)",
            "depth_analysis": "",
            "analysis_status": "moderation_refused",
        },
    ]
    text = build_agent_brief(scenes, ASR, SILENCE, 6.0, tmp_path).read_text(
        encoding="utf-8"
    )
    count_lines = [line for line in text.splitlines() if "Moderation-refused scenes" in line]
    assert len(count_lines) == 1
    assert "1/2 (Scene 2)" in count_lines[0]
    assert "- Visual ref (cut-point check only): (VLM 无法识别此场景画面) [moderation_refused]" in text
    assert "[moderation_refused]" not in text.split("### Scene 1:")[1].split("### Scene 2:")[0]


def test_brief_has_no_moderation_line_without_refusals(tmp_path):
    assert "Moderation-refused" not in _brief_text(tmp_path)
