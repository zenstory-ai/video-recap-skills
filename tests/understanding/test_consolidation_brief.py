import hashlib
import json
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

from lib import CONFIG  # noqa: E402
from agent_brief import build_agent_brief  # noqa: E402
from agent_text import _chunk_asr_for_writing  # noqa: E402
from brief_context import (  # noqa: E402
    _clean_asr_prompt_fingerprint,
    _format_consolidation,
    _index_prompt_fingerprint,
    _load_consolidation,
)
from brief_inputs import (  # noqa: E402
    _load_clean_asr,
    _load_mimo_overview_for_brief,
    _load_optional_stage_status,
)

SCENES = [{"scene_id": 0, "start": 0.0, "end": 6.0, "description": "门口对峙"}]
ASR = [{"start": 1.0, "end": 5.0, "text": "第一句对白。第二句反击。"}]
SILENCE = [{"start": 0.0, "end": 1.0, "duration": 1.0, "has_speech": False}]
INDEX_HEADING = "Understanding index (from consolidate.py)"


def _md5(path):
    return hashlib.md5(Path(path).read_bytes()).hexdigest()


def _write_index_with_meta(work_dir, index, scenes=SCENES, **meta_overrides):
    (work_dir / "vlm_analysis.json").write_text(json.dumps(scenes), encoding="utf-8")
    (work_dir / "understanding_index.json").write_text(
        json.dumps(index), encoding="utf-8"
    )
    meta = {
        "schema_version": 1,
        "source_md5": _md5(work_dir / "vlm_analysis.json"),
        "scene_count": len(scenes),
        "model": CONFIG.get("vlm_model", ""),
        "prompt_md5": _index_prompt_fingerprint(),
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
        "source_md5": _md5(work_dir / "asr_result.json"),
        "model": CONFIG.get("vlm_model", ""),
        "prompt_md5": _clean_asr_prompt_fingerprint(),
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
        lambda mp, tmp: _write_index_with_meta(tmp, STALE_INDEX, source_md5="deadbeef"),
        lambda mp, tmp: (
            _write_index_with_meta(tmp, STALE_INDEX),
            mp.setitem(CONFIG, "vlm_model", "different-model"),
        ),
    ],
    ids=["vlm_source_md5", "model"],
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
        {"source_md5": "deadbeef"},
        {"model": "old-model"},
        {"prompt_md5": "deadbeef"},
        {"segments": [{"start": 99.0, "end": 100.0, "text": "x"}]},
    ],
    ids=["source_md5", "model", "prompt_md5", "mistimed_span"],
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


def test_index_prompt_fingerprint_tracks_consolidate_source_of_truth():
    """The provenance fingerprint must hash the exact prompt consolidate stamps into
    understanding_index.json.meta.json, or every index is silently rejected (PR #58)."""
    import consolidate

    assert _index_prompt_fingerprint() == consolidate._prompt_fingerprint(
        consolidate.INDEX_PROMPT
    )
