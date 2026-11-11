"""Deterministic floor-quality gates in narration_lint and depth-first briefing."""

from argparse import Namespace
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "video-recap" / "scripts"))

from briefing.builder import (  # noqa: E402
    build_agent_brief,
    write_multi_source_clip_brief,
    write_multi_source_output_brief,
)
from narration_lint import lint_narration  # noqa: E402


def _seed_timing_evidence(work_dir, *, anchors=(), asr=(), silences=()):
    (work_dir / "speech_boundary_anchors.json").write_text(
        json.dumps({"sentence_anchors": list(anchors)}, ensure_ascii=False),
        encoding="utf-8",
    )
    (work_dir / "asr_result.json").write_text(
        json.dumps(list(asr), ensure_ascii=False),
        encoding="utf-8",
    )
    (work_dir / "silence_periods.json").write_text(
        json.dumps(list(silences), ensure_ascii=False),
        encoding="utf-8",
    )


def test_incomplete_sentence_is_blocking_error_while_quoted_endings_pass(tmp_path):
    bad = lint_narration(
        [{"start": 0.2, "end": 5.5, "narration": "林砚推门进去，却发现账本不见了，"}],
        [{"start": 0.0, "end": 6.0}],
        work_dir=tmp_path,
    )
    assert bad["ok"] is False
    assert [e["code"] for e in bad["errors"]] == ["incomplete_sentence"]

    good = lint_narration(
        [{"start": 0.2, "end": 6.0, "narration": "林砚冷笑一声：「这本账，你们谁也抹不掉。」"}],
        [{"start": 0.0, "end": 6.5}],
        work_dir=tmp_path,
    )
    assert good["ok"] is True


def test_unsafe_source_release_blocks_mid_sentence_unmute_and_passes_near_anchor(tmp_path):
    _seed_timing_evidence(
        tmp_path,
        anchors=[
            {"time": 0.2, "pause_start": 0.0, "confidence": "high"},
            {"time": 9.2, "pause_start": 9.0, "confidence": "high"},
        ],
        asr=[{"start": 2.5, "end": 9.2, "text": "你以为把证据烧了就能全身而退吗"}],
        silences=[{"start": 0.0, "end": 2.5, "has_speech": False}],
    )
    # Block ends at 4.0s while dialogue continues until 9.2s (5.2s > 3.0s hold bound)
    blocked = lint_narration(
        [{"start": 0.2, "end": 4.0, "narration": "林砚当场亮出底牌。"}],
        [{"start": 0.0, "end": 10.0}],
        work_dir=tmp_path,
    )
    assert "unsafe_source_release" in [e["code"] for e in blocked["errors"]]

    # Ending at 6.8s leaves 2.4s (<= 3.0s hold bound) to the 9.2s sentence anchor -> clean handoff
    clean = lint_narration(
        [{"start": 0.2, "end": 6.8, "narration": "林砚当场亮出底牌，逼对方当众松口。"}],
        [{"start": 0.0, "end": 10.0}],
        work_dir=tmp_path,
    )
    assert "unsafe_source_release" not in [e["code"] for e in clean["errors"]]


def test_weak_cold_open_silence_blocks_late_hook_unless_dialogue_opens(tmp_path):
    _seed_timing_evidence(
        tmp_path,
        silences=[{"start": 0.0, "end": 10.0, "has_speech": False}],
    )
    late = lint_narration(
        [{"start": 3.2, "end": 8.5, "narration": "三年前那场大火，根本不是意外。"}],
        [{"start": 0.0, "end": 10.0}],
        work_dir=tmp_path,
    )
    assert "weak_cold_open_silence" in [e["code"] for e in late["errors"]]

    # When high-tension original dialogue opens at 0.3s, narration entering at 3.2s is valid!
    _seed_timing_evidence(
        tmp_path,
        anchors=[{"time": 3.0, "pause_start": 2.8, "confidence": "high"}],
        asr=[{"start": 0.3, "end": 2.8, "text": "把门锁死，今天谁也别想走！"}],
        silences=[{"start": 2.8, "end": 10.0, "has_speech": False}],
    )
    cold_open_with_dialogue = lint_narration(
        [{"start": 3.1, "end": 8.5, "narration": "一句话，把潜伏三年的内鬼彻底逼了出来。"}],
        [{"start": 0.0, "end": 10.0}],
        work_dir=tmp_path,
    )
    assert cold_open_with_dialogue["ok"] is True


def test_excessive_dead_air_blocks_long_unnarrated_non_dialogue_gaps(tmp_path):
    _seed_timing_evidence(
        tmp_path,
        silences=[{"start": 0.0, "end": 16.0, "has_speech": False}],
    )
    # 7.5s gap between [0.2, 3.0] and [10.5, 14.0] with no dialogue -> excessive_dead_air
    report = lint_narration(
        [
            {"start": 0.2, "end": 3.0, "narration": "林砚在深夜悄悄潜入档案室。"},
            {"start": 10.5, "end": 14.0, "narration": "抽屉最底层藏着真正的密信。"},
        ],
        [{"start": 0.0, "end": 16.0}],
        work_dir=tmp_path,
    )
    assert "excessive_dead_air" in [e["code"] for e in report["errors"]]


def test_generic_character_label_blocks_vlm_appearance_terms_when_research_exists(tmp_path):
    (tmp_path / "background_research.json").write_text(
        json.dumps(
            {
                "title": "暗夜追光",
                "characters": {"林砚": "刑侦队长", "周牧": "卧底线人"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    bad = lint_narration(
        [{"start": 0.2, "end": 6.5, "narration": "黑衣男子突然转身，把密信塞进年轻女子手里。"}],
        [{"start": 0.0, "end": 7.0}],
        work_dir=tmp_path,
    )
    assert [e["code"] for e in bad["errors"]] == ["generic_character_label"]

    good = lint_narration(
        [{"start": 0.2, "end": 6.5, "narration": "周牧突然转身，把染血的密信塞进林砚手里。"}],
        [{"start": 0.0, "end": 7.0}],
        work_dir=tmp_path,
    )
    assert good["ok"] is True


def test_depth_first_brief_orders_depth_and_asr_before_visual_ref_and_warns_missing_research(
    tmp_path,
):
    scenes = [
        {
            "scene_id": 1,
            "start": 0.0,
            "end": 6.0,
            "duration": 6.0,
            "description": "男人在桌前翻看文件",
            "depth_analysis": "林砚发现三年前的结案报告被人伪造了签名",
        }
    ]
    asr = [{"start": 1.0, "end": 3.0, "text": "这份签名根本不是老队长的笔迹。"}]
    brief_path = build_agent_brief(scenes, asr, [], 6.0, tmp_path)
    text = brief_path.read_text(encoding="utf-8")

    assert "⚠️ Missing Background Research" in text
    assert "max budget if fully narrated" not in text
    assert '"start": 0.2' in text
    depth_idx = text.index("- Deeper analysis:")
    asr_idx = text.index("- ASR overlap:")
    vis_idx = text.index("- Visual ref (cut-point check only):")
    assert depth_idx < asr_idx < vis_idx

    # Multi-source clip & output briefs share the unified depth-first guidance
    src_dir = tmp_path / "sources" / "ep1"
    src_dir.mkdir(parents=True)
    (src_dir / "scenes.json").write_text(json.dumps(scenes, ensure_ascii=False), encoding="utf-8")
    (src_dir / "asr_result.json").write_text(json.dumps(asr, ensure_ascii=False), encoding="utf-8")
    records = [
        {
            "source_id": "ep1",
            "source_name": "ep1.mp4",
            "source_path": str(tmp_path / "ep1.mp4"),
            "source_work_dir": "sources/ep1",
            "duration": 6.0,
        }
    ]
    clip_brief = write_multi_source_clip_brief(
        tmp_path, records, Namespace(context="", target_duration="30s")
    ).read_text(encoding="utf-8")
    assert "⚠️ Missing Background Research" in clip_brief
    assert "底线质量与审美硬门禁" not in clip_brief or "0.20s" in clip_brief

    plan_path = tmp_path / "clip_plan_validated.json"
    plan = {
        "clips": [
            {
                "source_id": "ep1",
                "source_start": 0.0,
                "source_end": 6.0,
                "output_start": 0.0,
                "output_end": 6.0,
                "duration": 6.0,
                "reason": "b01 | hook",
            }
        ]
    }
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    out_brief = write_multi_source_output_brief(tmp_path, records, plan_path).read_text(
        encoding="utf-8"
    )
    assert "⚠️ Missing Background Research" in out_brief
    assert "底线质量与审美硬门禁" in out_brief
    assert "max budget if fully narrated" not in out_brief


def test_no_audio_stream_video_allows_narration_while_enforcing_dead_air_and_cold_open(tmp_path):
    from speech_ownership import measure_narration_speech_ownership

    (tmp_path / "asr_timing_evidence.json").write_text(
        json.dumps({"status": "NO_AUDIO_STREAM", "segments": []}), encoding="utf-8"
    )
    (tmp_path / "speech_boundary_anchors.json").write_text(
        json.dumps({"status": "NO_AUDIO_STREAM", "sentence_anchors": [], "quiet_windows": []}),
        encoding="utf-8",
    )
    (tmp_path / "asr_result.json").write_text("[]", encoding="utf-8")
    (tmp_path / "silence_periods.json").write_text("[]", encoding="utf-8")
    scenes = [{"start": 0.0, "end": 12.0}]

    items = [{"start": 0.2, "end": 11.4, "narration": "长街宫灯摇曳，密使深夜叩响相府侧门递上东宫急函，逼得众人连夜决断。", "overlaps_speech": False}]
    measured = measure_narration_speech_ownership(items, tmp_path, mode="full")
    assert measured[0]["overlaps_speech"] is False
    clean = lint_narration(measured, scenes, work_dir=tmp_path)
    assert clean["ok"] is True
    assert clean["errors"] == []

    late_items = measure_narration_speech_ownership(
        [{"start": 3.5, "end": 11.4, "narration": "长街宫灯摇曳，密使深夜叩响相府侧门递上东宫急函。", "overlaps_speech": False}],
        tmp_path,
        mode="full",
    )
    late_open = lint_narration(late_items, scenes, work_dir=tmp_path)
    assert "weak_cold_open_silence" in [e["code"] for e in late_open["errors"]]

    gap_items = measure_narration_speech_ownership(
        [
            {"start": 0.2, "end": 2.2, "narration": "密使深夜叩门。", "overlaps_speech": False},
            {"start": 8.5, "end": 11.4, "narration": "东宫急函逼众决断。", "overlaps_speech": False},
        ],
        tmp_path,
        mode="full",
    )
    dead_air = lint_narration(gap_items, scenes, work_dir=tmp_path)
    assert "excessive_dead_air" in [e["code"] for e in dead_air["errors"]]


def test_all_skill_scripts_avoid_unguarded_python310_typing_imports():
    import ast

    skills_root = Path(__file__).resolve().parents[2] / "skills"
    for py_file in sorted(skills_root.rglob("*.py")):
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_file))
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module == "typing":
                names = {alias.name for alias in node.names}
                assert "TypeGuard" not in names, (
                    f"{py_file} imports Python 3.10+ typing.TypeGuard at module top level"
                )
