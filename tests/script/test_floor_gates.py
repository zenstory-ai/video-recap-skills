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


def test_unsafe_source_release_warns_mid_sentence_unmute_and_clears_near_anchor(tmp_path):
    _seed_timing_evidence(
        tmp_path,
        anchors=[
            {"time": 0.2, "pause_start": 0.0, "confidence": "high"},
            {"time": 9.2, "pause_start": 9.0, "confidence": "high"},
        ],
        asr=[{"start": 2.5, "end": 9.2, "text": "你以为把证据烧了就能全身而退吗"}],
        silences=[{"start": 0.0, "end": 2.5, "has_speech": False}],
    )
    # Block ends at 4.0s while coarse/detected dialogue continues until 9.2s (5.2s > 3.0s hold bound):
    # emits non-blocking warning so pre-line setup ("垫半句、让半句") inside 15s ASR windows is not deadlocked
    warned = lint_narration(
        [{"start": 0.2, "end": 4.0, "narration": "林砚当场亮出底牌。"}],
        [{"start": 0.0, "end": 10.0}],
        work_dir=tmp_path,
    )
    assert warned["ok"] is True
    assert "unsafe_source_release" in [w["code"] for w in warned["warnings"]]

    # Ending at 6.8s leaves 2.4s (<= 3.0s hold bound) to the 9.2s sentence anchor -> clean handoff
    clean = lint_narration(
        [{"start": 0.2, "end": 6.8, "narration": "林砚当场亮出底牌，逼对方当众松口。"}],
        [{"start": 0.0, "end": 10.0}],
        work_dir=tmp_path,
    )
    assert clean["ok"] is True
    assert "unsafe_source_release" not in [w["code"] for w in clean["warnings"]]


def test_weak_cold_open_silence_warns_late_hook_unless_dialogue_opens(tmp_path):
    _seed_timing_evidence(
        tmp_path,
        silences=[{"start": 0.0, "end": 10.0, "has_speech": False}],
    )
    late = lint_narration(
        [{"start": 3.2, "end": 8.5, "narration": "三年前那场大火，根本不是意外。"}],
        [{"start": 0.0, "end": 10.0}],
        work_dir=tmp_path,
    )
    assert late["ok"] is True
    assert "weak_cold_open_silence" in [w["code"] for w in late["warnings"]]

    # When high-tension original dialogue opens at 0.3s, narration entering at 3.1s emits no cold-open warning
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
    assert "weak_cold_open_silence" not in [w["code"] for w in cold_open_with_dialogue["warnings"]]


def test_excessive_dead_air_warns_on_long_unnarrated_non_dialogue_gaps(tmp_path):
    _seed_timing_evidence(
        tmp_path,
        silences=[{"start": 0.0, "end": 16.0, "has_speech": False}],
    )
    # 6.8s gap between [0.2, 4.2] and [11.0, 15.5] with no dialogue -> advisory excessive_dead_air warning
    report = lint_narration(
        [
            {"start": 0.2, "end": 4.2, "narration": "林砚深夜潜入档案室。"},
            {"start": 11.0, "end": 15.5, "narration": "抽屉底下藏着密信。"},
        ],
        [{"start": 0.0, "end": 16.0}],
        work_dir=tmp_path,
    )
    assert report["ok"] is True
    assert "excessive_dead_air" in [w["code"] for w in report["warnings"]]


def test_generic_character_label_warns_vlm_appearance_terms_when_research_exists(tmp_path):
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
    flagged = lint_narration(
        [{"start": 0.2, "end": 6.5, "narration": "黑衣男子突然转身，把密信塞进年轻女子手里。"}],
        [{"start": 0.0, "end": 7.0}],
        work_dir=tmp_path,
    )
    assert flagged["ok"] is True
    assert [w["code"] for w in flagged["warnings"]] == ["generic_character_label"]

    good = lint_narration(
        [{"start": 0.2, "end": 6.5, "narration": "周牧突然转身，把染血的密信塞进林砚手里。"}],
        [{"start": 0.0, "end": 7.0}],
        work_dir=tmp_path,
    )
    assert good["ok"] is True
    assert good["warnings"] == []


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

    assert "⚠️ 缺少背景资料" in text
    assert "max budget if fully narrated" not in text
    assert '"start": 0.2' in text
    depth_idx = text.index("- 深层分析:")
    asr_idx = text.index("- ASR 对白重叠:")
    vis_idx = text.index("- 画面参考（仅供核对切点）:")
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
    assert "⚠️ 缺少背景资料" in clip_brief
    assert "0.20s" in clip_brief

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
                "reason": "b01 (hook)：亮出伪造签名",
            }
        ]
    }
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    out_brief = write_multi_source_output_brief(tmp_path, records, plan_path).read_text(
        encoding="utf-8"
    )
    assert "⚠️ 缺少背景资料" in out_brief
    assert "底线校验与质量提示" in out_brief
    assert "max budget if fully narrated" not in out_brief


def test_no_audio_stream_video_allows_narration_and_surfaces_advisory_pacing_warnings(tmp_path):
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
    assert late_open["ok"] is True
    assert "weak_cold_open_silence" in [w["code"] for w in late_open["warnings"]]

    gap_items = measure_narration_speech_ownership(
        [
            {"start": 0.2, "end": 2.6, "narration": "密使叩门。", "overlaps_speech": False},
            {"start": 8.5, "end": 11.4, "narration": "急函呈上。", "overlaps_speech": False},
        ],
        tmp_path,
        mode="full",
    )
    dead_air = lint_narration(gap_items, scenes, work_dir=tmp_path)
    assert dead_air["ok"] is True
    assert "excessive_dead_air" in [w["code"] for w in dead_air["warnings"]]


def test_editorial_commentary_tone_emits_advisory_warning_without_blocking(tmp_path):
    _seed_timing_evidence(
        tmp_path,
        silences=[{"start": 0.0, "end": 16.0, "has_speech": False}],
    )
    scenes = [{"start": 0.0, "end": 16.0}]
    report = lint_narration(
        [
            {"start": 0.2, "end": 5.5, "narration": "大家好，今天我们来聊聊林砚追查旧案的真相。"},
            {"start": 6.0, "end": 11.0, "narration": "权力博弈面前，好戏才刚刚开始。"},
            {"start": 11.5, "end": 15.8, "narration": "这说明那枚印章昨天已经被调包了。"},
        ],
        scenes,
        work_dir=tmp_path,
    )
    assert report["ok"] is True
    flagged_indices = [w["index"] for w in report["warnings"] if w["code"] == "editorial_commentary_tone"]
    assert flagged_indices == [0, 1]


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


def test_validate_cli_exits_zero_on_advisory_craft_warnings_and_blocks_stage_only_brackets(tmp_path):
    import subprocess

    _seed_timing_evidence(
        tmp_path,
        silences=[{"start": 0.0, "end": 24.0, "has_speech": False}],
    )
    (tmp_path / "scenes.json").write_text(
        json.dumps([{"scene_id": 0, "start": 0.0, "end": 24.0, "description": "走廊"}], ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "background_research.json").write_text(
        json.dumps({"characters": {"林砚": "刑侦队长"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "narration.json").write_text(
        json.dumps(
            [
                {"start": 3.2, "end": 8.0, "narration": "黑衣男子推门而入。", "overlaps_speech": False},
                {"start": 14.0, "end": 18.5, "narration": "旧档当场露馅。", "overlaps_speech": False},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    validate_py = Path(__file__).resolve().parents[2] / "skills" / "video-recap" / "scripts" / "validate.py"
    res = subprocess.run(
        [sys.executable, "-X", "utf8", str(validate_py), "--work-dir", str(tmp_path), "--mode", "full"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout.strip().splitlines()[-1])
    assert payload["status"] == "validated"
    lint = json.loads((tmp_path / "narration_lint.json").read_text(encoding="utf-8"))
    assert lint["ok"] is True
    assert lint["errors"] == []
    warning_codes = {w["code"] for w in lint["warnings"]}
    assert {"weak_cold_open_silence", "excessive_dead_air", "generic_character_label"} <= warning_codes

    stage_only = lint_narration(
        [{"start": 0.2, "end": 3.0, "narration": "[压低声音。]"}],
        [{"start": 0.0, "end": 4.0}],
    )
    assert stage_only["ok"] is False
    assert [e["code"] for e in stage_only["errors"]] == [
        "empty_narration",
        "empty_narration_file",
    ]
