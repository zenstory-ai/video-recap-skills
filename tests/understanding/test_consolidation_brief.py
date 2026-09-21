import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parents[2]
        / "skills"
        / "video-understanding"
        / "scripts"
    ),
)

from lib import CONFIG, file_identity  # noqa: E402
import briefing.context as brief_context  # noqa: E402
import briefing.inputs as brief_inputs  # noqa: E402
import briefing.timeline as brief_timeline  # noqa: E402
from briefing.builder import build_agent_brief  # noqa: E402
from agent_text import _chunk_asr_for_writing  # noqa: E402
from briefing.context import (  # noqa: E402
    _format_consolidation,
    _load_consolidation,
    assess_understanding_substrate,
)
from briefing.inputs import (  # noqa: E402
    _load_clean_asr,
    _load_mimo_overview_for_brief,
    _load_optional_stage_status,
)
from narration_lint import lint_narration  # noqa: E402

SCENES = [{"scene_id": 0, "start": 0.0, "end": 6.0, "description": "门口对峙"}]
ASR = [{"start": 1.0, "end": 5.0, "text": "第一句对白。第二句反击。"}]
SILENCE = [{"start": 0.0, "end": 1.0, "duration": 1.0, "has_speech": False}]
INDEX_HEADING = "Understanding index (from consolidate.py)"


def _write_index_with_meta(work_dir, index, scenes=SCENES, **meta_overrides):
    (work_dir / "vlm_analysis.json").write_text(json.dumps(scenes), encoding="utf-8")
    (work_dir / "understanding_index.json").write_text(
        json.dumps(index), encoding="utf-8"
    )
    meta = {
        "schema_version": 1,
        "source": file_identity(work_dir / "vlm_analysis.json"),
        "scene_count": len(scenes),
        "model": CONFIG.get("vlm_model", ""),
        **meta_overrides,
    }
    (work_dir / "understanding_index.json.meta.json").write_text(
        json.dumps(meta), encoding="utf-8"
    )


def _brief_text(tmp_path, **kwargs):
    return build_agent_brief(SCENES, ASR, SILENCE, 6.0, tmp_path, **kwargs).read_text(
        encoding="utf-8"
    )


def _write_clean_asr(work_dir, **overrides):
    """asr_clean.json with fresh provenance for the ASR fixture; overrides break one field."""
    (work_dir / "asr_result.json").write_text(json.dumps(ASR), encoding="utf-8")
    payload = {
        "source": file_identity(work_dir / "asr_result.json"),
        "model": CONFIG.get("vlm_model", ""),
        "segments": [{"start": 1.0, "end": 5.0, "text": "第一句对白。第二句反击。CLEANED"}],
        **overrides,
    }
    (work_dir / "asr_clean.json").write_text(json.dumps(payload), encoding="utf-8")


def test_brief_without_consolidation_uses_raw_asr_and_writes_sidecars(monkeypatch, tmp_path):
    """GOLDEN: with no consolidation artifacts the brief gains no index section, asr chunking
    uses RAW asr, and the chunk/fusion sidecars are still written and surfaced."""
    monkeypatch.setitem(CONFIG, "asr_chunk_min_chars", 5)
    monkeypatch.setitem(CONFIG, "asr_chunk_max_chars", 12)  # == len(ASR text): max flush
    text = _brief_text(tmp_path)
    requirements = json.loads(
        (tmp_path / "deslop_qc_requirements.json").read_text(encoding="utf-8")
    )
    assert requirements == {
        "schema_version": 1,
        "style_card_required": False,
    }
    assert INDEX_HEADING not in text
    assert "ASR writing chunks" in text
    assert "Timeline fusion" in text
    written = json.loads(
        (tmp_path / "asr_writing_chunks.json").read_text(encoding="utf-8")
    )
    assert written and written == _chunk_asr_for_writing(ASR, SCENES)
    fusion = json.loads((tmp_path / "timeline_fusion.json").read_text(encoding="utf-8"))
    assert fusion[0]["dialogue_segments"][0]["text"] == ASR[0]["text"]


def test_chunk_asr_tolerates_mixed_int_str_scene_ids():
    """Regression (cut-mode pass2): a split scene gets a str id like '5.0' while unsplit scenes
    keep int ids; a chunk spanning both must not crash sorted() with 'int < str'."""
    scenes = [
        {"scene_id": 5, "start": 0.0, "end": 3.0, "description": "a"},
        {"scene_id": "5.0", "start": 3.0, "end": 6.0, "description": "b"},
    ]
    asr = [{"start": 1.0, "end": 5.0, "text": "一句横跨两个场景的较长原声对白内容。"}]
    chunks = _chunk_asr_for_writing(asr, scenes)  # must not raise TypeError
    ids = chunks[0]["scene_ids"]
    assert 5 in ids and "5.0" in ids


def test_asr_chunks_split_on_sentences_and_track_scene_ids(monkeypatch):
    monkeypatch.setitem(CONFIG, "asr_chunk_min_chars", 8)
    monkeypatch.setitem(CONFIG, "asr_chunk_max_chars", 16)
    chunks = _chunk_asr_for_writing(
        [
            {
                "start": 0.0,
                "end": 40.0,
                "text": "第一句很重要。第二句继续推进。第三句制造悬念。第四句收尾。",
            }
        ],
        [
            {"scene_id": 0, "start": 0.0, "end": 20.0},
            {"scene_id": 1, "start": 20.0, "end": 40.0},
        ],
    )

    assert len(chunks) >= 2
    assert chunks[0]["text"].endswith("。")
    assert all(chunk["char_count"] <= 16 for chunk in chunks)
    assert chunks[0]["scene_ids"] == [0]
    assert chunks[-1]["scene_ids"] == [1]


def _write_status(work_dir, name, **fields):
    (work_dir / name).write_text(json.dumps(fields), encoding="utf-8")


def test_optional_stage_warnings_surface_failed_overview_and_consolidation(tmp_path):
    _write_status(
        tmp_path,
        "mimo_video_overview.status.json",
        stage="mimo_video_overview",
        enabled=True,
        status="failed",
        message="quota timeout with stack trace that should not be repeated" * 5,
        artifact=None,
    )
    _write_status(
        tmp_path,
        "consolidation.status.json",
        stage="consolidation",
        enabled=True,
        do_asr=False,
        do_index=True,
        status="failed",
        message="index api failed",
        artifacts=[],
    )

    text = _brief_text(tmp_path, mimo_overview_enabled=True)

    assert "Optional stage warnings" in text
    assert "mimo_video_overview: failed" in text
    assert "consolidation: failed" in text
    assert "quota timeout" in text


def test_optional_stage_warnings_flag_missing_enabled_artifacts(tmp_path):
    _write_status(
        tmp_path,
        "mimo_video_overview.status.json",
        stage="mimo_video_overview",
        enabled=True,
        status="ok",
        message="ok",
        artifact="mimo_video_overview.json",
    )
    _write_status(
        tmp_path,
        "consolidation.status.json",
        stage="consolidation",
        enabled=True,
        do_asr=False,
        do_index=True,
        status="ok",
        message="ok",
        artifacts=["understanding_index.json"],
    )

    text = _brief_text(tmp_path, mimo_overview_enabled=True)

    assert "mimo_video_overview: missing_artifact" in text
    assert "consolidation: missing_index" in text


def test_optional_brief_loaders_fall_back_on_invalid_json_schema_and_io(
    monkeypatch, tmp_path
):
    monkeypatch.setitem(CONFIG, "mimo_video_overview", True)
    (tmp_path / "asr_result.json").write_text(json.dumps(ASR), encoding="utf-8")

    (tmp_path / "asr_clean.json").write_text("not json", encoding="utf-8")
    assert _load_clean_asr(tmp_path, ASR) is None
    (tmp_path / "asr_clean.json").write_text("[]", encoding="utf-8")
    assert _load_clean_asr(tmp_path, ASR) is None

    (tmp_path / "mimo_video_overview.json").write_text("[]", encoding="utf-8")
    assert _load_mimo_overview_for_brief(tmp_path, SCENES) is None
    (tmp_path / "mimo_video_overview.json").unlink()
    (tmp_path / "mimo_video_overview.json").mkdir()
    assert _load_mimo_overview_for_brief(tmp_path, SCENES) is None

    assert _load_optional_stage_status(tmp_path, "missing.status.json") is None
    (tmp_path / "bad.status.json").write_text("[1, 2, 3]", encoding="utf-8")
    assert _load_optional_stage_status(tmp_path, "bad.status.json") is None
    (tmp_path / "bad.status.json").unlink()
    (tmp_path / "bad.status.json").mkdir()
    assert _load_optional_stage_status(tmp_path, "bad.status.json") is None


def test_consolidation_cache_files_are_optional_when_malformed_or_unreadable(tmp_path):
    index = {
        "characters": [],
        "relationships": [],
        "plot_points": [],
        "entities": [],
    }
    _write_index_with_meta(tmp_path, index)
    (tmp_path / "understanding_index.json").write_text("[]", encoding="utf-8")
    assert _load_consolidation(tmp_path, SCENES) == {}

    _write_index_with_meta(tmp_path, index)
    (tmp_path / "understanding_index.json.meta.json").write_text(
        "not json", encoding="utf-8"
    )
    assert _load_consolidation(tmp_path, SCENES) == {}

    (tmp_path / "understanding_index.json.meta.json").unlink()
    (tmp_path / "understanding_index.json.meta.json").mkdir()
    assert _load_consolidation(tmp_path, SCENES) == {}


def test_consolidation_loaders_are_safe_when_absent():
    assert _load_consolidation("/nonexistent-dir", []) == {}
    assert _format_consolidation({}) == []


def test_brief_folds_in_index_when_present(tmp_path):
    _write_index_with_meta(
        tmp_path,
        {
            "characters": [{"name": "张三", "description": "主角"}],
            "relationships": [],
            "plot_points": ["开端"],
            "entities": ["匕首"],
        },
    )
    text = _brief_text(tmp_path)
    assert INDEX_HEADING in text
    assert "张三" in text and "匕首" in text


STALE_INDEX = {
    "characters": [{"name": "旧角色", "description": "旧素材"}],
    "relationships": [],
    "plot_points": [],
    "entities": [],
}


@pytest.mark.parametrize(
    "spoil",
    [
        lambda mp, tmp: _write_index_with_meta(
            tmp, STALE_INDEX, source={"size": 0, "mtime_ns": 0}
        ),
        lambda mp, tmp: (
            _write_index_with_meta(tmp, STALE_INDEX),
            mp.setitem(CONFIG, "vlm_model", "different-model"),
        ),
    ],
    ids=["vlm_source_identity", "model"],
)
def test_brief_rejects_index_with_stale_provenance(monkeypatch, tmp_path, spoil):
    spoil(monkeypatch, tmp_path)

    text = _brief_text(tmp_path)

    assert INDEX_HEADING not in text
    assert "旧角色" not in text


def test_clean_asr_accepted_when_fresh_provenance_timing_ok(tmp_path):
    _write_clean_asr(tmp_path)
    got = _load_clean_asr(tmp_path, ASR)
    assert got is not None and got[0]["text"].endswith("CLEANED")


@pytest.mark.parametrize(
    "overrides",
    [
        {"source": {"size": 0, "mtime_ns": 0}},
        {"model": "old-model"},
        {"segments": [{"start": 99.0, "end": 100.0, "text": "x"}]},
    ],
    ids=["source_identity", "model", "mistimed_span"],
)
def test_clean_asr_rejected_on_bad_provenance_or_mistiming(tmp_path, overrides):
    _write_clean_asr(tmp_path, **overrides)
    assert _load_clean_asr(tmp_path, ASR) is None


def test_clean_asr_absent_is_none(tmp_path):
    (tmp_path / "asr_result.json").write_text(json.dumps(ASR), encoding="utf-8")
    assert _load_clean_asr(tmp_path, ASR) is None


def test_brief_ignores_stale_mimo_overview_when_disabled_or_chunk_mismatch(
    monkeypatch, tmp_path
):
    monkeypatch.setitem(CONFIG, "mimo_video_overview", False)
    monkeypatch.setitem(CONFIG, "mimo_video_chunk_max_seconds", 2)
    monkeypatch.setitem(CONFIG, "mimo_video_chunk_min_seconds", 0.5)
    (tmp_path / "mimo_video_overview.json").write_text(
        json.dumps(
            {
                "input": "scene_chunks",
                "content": "STALE MIMO OVERVIEW",
                "chunks": [
                    {
                        "chunk_id": 0,
                        "scene_id": 99,
                        "start": 0.0,
                        "end": 2.0,
                        "content": "STALE",
                    }
                ],
                "settings": {
                    "model": CONFIG.get("mimo_video_model")
                    or CONFIG.get("mimo_model")
                    or CONFIG.get("vlm_model"),
                    "mimo_video_fps": CONFIG.get("mimo_video_fps", 2.0),
                    "mimo_media_resolution": CONFIG.get(
                        "mimo_media_resolution", "default"
                    ),
                    "mimo_video_chunk_max_seconds": 2,
                    "mimo_video_chunk_min_seconds": 0.5,
                    "mimo_video_base64_max_mb": CONFIG.get(
                        "mimo_video_base64_max_mb", 45.0
                    ),
                    "mimo_video_prompt": CONFIG.get("mimo_video_prompt", ""),
                    "mimo_disable_thinking": CONFIG.get("mimo_disable_thinking", True),
                },
            }
        ),
        encoding="utf-8",
    )

    assert "STALE MIMO OVERVIEW" not in _brief_text(tmp_path)

    monkeypatch.setitem(CONFIG, "mimo_video_overview", True)
    assert "STALE MIMO OVERVIEW" not in _brief_text(tmp_path)


def test_producer_only_meta_keys_never_reject_a_fresh_index(tmp_path):
    """Producer-private sidecar keys are ignored; the brief only checks source/model."""
    index = {"characters": [{"name": "甲"}], "relationships": [], "plot_points": [], "entities": []}
    _write_index_with_meta(tmp_path, index, producer_cache_key="opaque")
    assert _load_consolidation(tmp_path, SCENES) == index
    _write_clean_asr(tmp_path, producer_cache_key="opaque")
    assert _load_clean_asr(tmp_path, ASR) is not None


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
    """Cut mode sizes the beat target to the OUTPUT length, not the source (2h->30min regression)."""
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
    assert (
        "narration BLOCKS across the ~1min CUT OUTPUT" in text
    )  # sized to 1min output (~5 blocks)
    assert "47 narration BLOCKS" not in text  # NOT the source-sized (10min) count
    assert (
        "step 1 of 2" in text
    )  # A1: cut-first, write clip_plan only (no edited_source yet)
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
    assert "deterministic report-only tool QC" in text
    assert "not treat it as an AIGC detector" in text
    assert "do not auto-rewrite" in text
    assert "not a preset enum, fixed taxonomy" in text


def test_build_agent_brief_empty_substrate_warns_and_relaxes_density(
    monkeypatch, tmp_path
):
    """Empty substrate turns the density target into a ceiling, not a quota, so the agent
    is not forced to fill beats with 看图说话."""
    _set_brief_mode(monkeypatch, "full")
    scenes = [
        {"scene_id": i, "start": i * 6.0, "end": i * 6.0 + 6.0, "description": "画面"}
        for i in range(4)
    ]
    assert assess_understanding_substrate(scenes, [])["level"] == "empty"
    text = _agent_brief_text(scenes, [], [], 24.0, tmp_path)
    assert "SUBSTRATE IS EMPTY" in text
    assert "do NOT chase a beat count" in text
    assert "grounded blocks" in text  # thin -> fewer, grounded blocks (no quota)
    assert (
        "segments/min (minimum" not in text
    )  # the strict quota line is replaced when thin


def test_build_agent_brief_research_directive_when_context_without_research(
    monkeypatch, tmp_path
):
    """A title/context with no background_research.json triggers a research-first directive."""
    _set_brief_mode(monkeypatch, "full", context_info="这是《庆余年》第一集")
    scenes = [
        {
            "scene_id": 0,
            "start": 0.0,
            "end": 6.0,
            "description": "范闲登场与人对峙暗藏机锋",
        }
    ]
    asr = [{"start": 1.0, "end": 5.0, "text": "一句对白。"}]
    text = _agent_brief_text(scenes, asr, [], 6.0, tmp_path)
    assert "Research the story FIRST" in text
    assert "庆余年" in text  # the context is echoed into the directive

    (tmp_path / "background_research.json").write_text(
        '{"synopsis": "范闲查案"}', encoding="utf-8"
    )
    text2 = _agent_brief_text(scenes, asr, [], 6.0, tmp_path)
    assert (
        "Research the story FIRST" not in text2
    )  # already researched -> directive gone


def test_agent_brief_includes_mimo_video_overview(monkeypatch, tmp_path):
    monkeypatch.setitem(CONFIG, "mimo_video_overview", True)
    monkeypatch.setitem(CONFIG, "mimo_video_chunk_max_seconds", 20.0)
    monkeypatch.setitem(CONFIG, "mimo_video_chunk_min_seconds", 1.0)
    chunks = [
        {
            "chunk_id": 0,
            "scene_id": 0,
            "start": 0.0,
            "end": 3.0,
            "content": "这是 MiMo 对分片汇总的故事线概览。",
        }
    ]
    overview = {
        "input": "scene_chunks",
        "content": "这是 MiMo 对分片汇总的故事线概览。",
        "reasoning_content": "内部推理",
        "chunks": chunks,
        "settings": brief_inputs._mimo_video_settings(),
    }
    _write_json(tmp_path / "mimo_video_overview.json", overview)
    _set_brief_mode(monkeypatch, "full")

    text = _agent_brief_text(
        [{"scene_id": 0, "start": 0.0, "end": 3.0, "description": "场景"}],
        [],
        [],
        3.0,
        tmp_path,
    )
    assert "MiMo scene-chunk video overview" in text
    assert "这是 MiMo 对分片汇总的故事线概览。" in text
    assert "内部推理" not in text


def test_agent_brief_ignores_malformed_optional_artifacts(monkeypatch, tmp_path):
    monkeypatch.setitem(CONFIG, "mimo_video_overview", True)
    _set_brief_mode(monkeypatch, "full")
    asr = [{"start": 0.0, "end": 1.0, "text": "原始对白"}]
    _write_json(tmp_path / "asr_result.json", asr)
    for name in (
        "asr_clean.json",
        "mimo_video_overview.json",
        "mimo_video_overview.status.json",
        "consolidation.status.json",
        "understanding_index.json",
        "understanding_index.json.meta.json",
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


def test_assess_understanding_substrate_levels():
    empty = assess_understanding_substrate(
        [{"scene_id": 0, "start": 0.0, "end": 3.0, "description": "短"}], []
    )
    assert empty["level"] == "empty"

    facts_scenes = [
        {
            "scene_id": i,
            "start": float(i),
            "end": float(i + 1),
            "description": "画面描述" * 6,
            "frame_facts": {"1.0": ["动作"]},
        }
        for i in range(4)
    ]
    # Rich requires a story SPINE: substantial dialogue (ASR >= 200 chars) ...
    rich = assess_understanding_substrate(
        facts_scenes, [{"start": 0.0, "end": 3.0, "text": "对白" * 120}]
    )
    assert rich["level"] == "rich"
    # ... or researched/given story context lifts a frame-fact-rich clip to rich.
    storyful = assess_understanding_substrate(facts_scenes, [], has_story_context=True)
    assert storyful["level"] == "rich"
    # Frame-fact-rich but STORYLESS (no dialogue, no context) is thin, NOT rich, so the
    # cold-narration safeguards (sparse warning, research directive, density relief) fire.
    # This is the canonical anime case the old volume-only classifier mislabeled "rich".
    storyless = assess_understanding_substrate(facts_scenes, [])
    assert storyless["level"] == "thin"


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
        "1:2:3:4",
        "nan",
        "inf",
    ):
        with pytest.raises(ValueError):
            _parse_target_seconds(bad)


def test_build_agent_brief_storyless_rich_video_relaxes_and_prompts_research(
    monkeypatch, tmp_path
):
    """Anime case: frame-fact-rich but storyless (no dialogue, no research) is treated as
    thin, so the density relaxes and the research directive fires instead of shipping cold."""
    _set_brief_mode(monkeypatch, "full")
    scenes = [
        {
            "scene_id": i,
            "start": float(i * 6),
            "end": float(i * 6 + 6),
            "description": "人物在画面里走动" * 3,
            "frame_facts": {str(i * 6): ["走动"]},
        }
        for i in range(6)
    ]
    text = _agent_brief_text(scenes, [], [], 36.0, tmp_path)
    assert "do NOT chase a beat count" in text  # density relaxed (FIX D)
    assert "Research the story FIRST" in text  # research directive (FIX E)
    assert "segments/min (minimum" not in text  # strict quota line suppressed


def test_build_agent_brief_rich_substrate_frames_density_as_guide_without_research_nag(
    monkeypatch, tmp_path
):
    """RICH substrate: density stays a GUIDE, never a quota, and a title alone (dialogue-rich,
    no research file) must not trigger the research-first nag."""
    _set_brief_mode(monkeypatch, "full", context_info="这是《庆余年》第一集")
    scenes = [
        {
            "scene_id": i,
            "start": float(i * 6),
            "end": float(i * 6 + 6),
            "description": "范闲在书房翻看卷宗神色凝重",
            "frame_facts": {str(i * 6): ["翻书"]},
        }
        for i in range(6)
    ]
    asr = [
        {"start": 1.0, "end": 5.0, "text": "对" * 250}
    ]  # >= 200 chars -> a real story spine -> rich
    assert assess_understanding_substrate(scenes, asr)["level"] == "rich"
    text = _agent_brief_text(scenes, asr, [], 36.0, tmp_path)
    assert (
        "Content-led audio allocation" in text
    )  # story/sound decisions, not ratio, are the headline
    assert "not a quota or quality target" in text  # 7:3 remains only a rough fallback
    assert "never pad" in text  # timing fallback is still not a quota
    assert (
        "Narration density target:" not in text
    )  # the old hard-quota phrasing is gone
    assert "Research the story FIRST" not in text  # rich + titled -> no nag


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
    _write_json(
        tmp_path / "asr_clean.json",
        {
            "segments": [{"start": 101.0, "end": 105.0, "text": "清洗后一到五秒对白。"}],
            "source": file_identity(tmp_path / "asr_result.json"),
            "model": brief_context._consolidation_model(),
        },
    )

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

    chunks = json.loads(
        (tmp_path / "asr_writing_chunks.json").read_text(encoding="utf-8")
    )
    fusion = json.loads((tmp_path / "timeline_fusion.json").read_text(encoding="utf-8"))

    assert chunks[0]["start"] == pytest.approx(1.0)
    assert chunks[0]["end"] == pytest.approx(5.0)
    assert chunks[0]["text"] == "清洗后一到五秒对白。"
    assert fusion[0]["time_range"] == [0.0, 10.0]
    assert fusion[0]["dialogue_segments"][0]["start"] == pytest.approx(1.0)
    assert fusion[0]["dialogue_segments"][0]["end"] == pytest.approx(5.0)
    assert fusion[0]["narration_slots"][0]["start"] == pytest.approx(6.0)
    assert "ASR chunk 1: 1.0-5.0s" in text
    assert "ASR chunk 1: 101.0-105.0s" not in text
    assert "4.00s [high] (SOURCE 104.00s)" in text
    output_anchors = json.loads(
        (tmp_path / "speech_boundary_anchors_output.json").read_text(encoding="utf-8")
    )
    assert output_anchors["sentence_anchors"][0]["time"] == 4.0
    assert output_anchors["sentence_anchors"][0]["pause_start"] == 3.88
    assert output_anchors["sentence_anchors"][0]["source_pause_start"] == 103.88

    lint = lint_narration(
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
        item for item in lint["errors"] if item["code"] == "interrupts_source_sentence"
    )
    assert issue["suggested_start"] == 4.0


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
    ],
)
def test_cut_pass2_agent_brief_fails_closed_on_bad_output_spans(
    monkeypatch, tmp_path, validated_plan, match
):
    _set_brief_mode(monkeypatch, "cut", target_duration="10s")
    (tmp_path / "edited_source.mp4").write_bytes(b"edited")
    if validated_plan is not None:
        _write_json(tmp_path / "clip_plan_validated.json", validated_plan)

    with pytest.raises(SystemExit, match=match):
        build_agent_brief(
            [{"scene_id": 7, "start": 100.0, "end": 110.0, "description": "保留片段"}],
            [{"start": 101.0, "end": 105.0, "text": "源时间对白。"}],
            [],
            120.0,
            tmp_path,
        )


def test_script_narration_brief_does_not_leak_hardcoded_example_entities(tmp_path):
    scenes = [{"scene_id": 0, "start": 0.0, "end": 6.0, "description": "门口对峙"}]
    asr = [{"start": 1.0, "end": 5.0, "text": "第一句对白。第二句反击。"}]
    silence = [{"start": 0.0, "end": 1.0, "duration": 1.0, "has_speech": False}]

    text = build_agent_brief(scenes, asr, silence, 6.0, tmp_path, style="纪实复盘").read_text(encoding="utf-8")
    requirements = json.loads((tmp_path / "deslop_qc_requirements.json").read_text(encoding="utf-8"))

    assert requirements == {
        "schema_version": 1,
        "style_card_required": False,
    }
    for leaked in ["范闲", "监察院", "五竹", "京都"]:
        assert leaked not in text
