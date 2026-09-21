import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'skills' / 'video-understanding' / 'scripts'))

import consolidate  # noqa: E402
from lib import file_identity  # noqa: E402


def _fake(content):
    return {"choices": [{"message": {"content": content}}]}


EMPTY_INDEX = '{"characters":[],"relationships":[],"plot_points":[],"entities":[]}'


def _write_json(work_dir, name, payload):
    (work_dir / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _counting_api(monkeypatch, reply):
    """Patch consolidate.api_call with a call counter; reply(n) builds the n-th response."""
    calls = {"n": 0}

    def fake(payload):
        calls["n"] += 1
        return _fake(reply(calls["n"]))

    monkeypatch.setattr("consolidate.api_call", fake)
    return calls


# ── Pass A: parse_clean_response (timing preserved by construction) ────────────

def test_parse_clean_preserves_spans_and_applies_cleaned_text():
    asr = [{"start": 0.0, "end": 5.0, "text": "你给我站住"}, {"start": 5.0, "end": 9.0, "text": "我不会放手"}]
    resp = '```json\n{"segments":[{"i":0,"text":"你给我站住！","speaker":"男"},{"i":1,"text":"我不会放手。"}]}\n```'
    out = consolidate.parse_clean_response(resp, asr)
    assert [s["start"] for s in out] == [0.0, 5.0]      # spans untouched
    assert [s["end"] for s in out] == [5.0, 9.0]
    assert out[0]["text"] == "你给我站住！" and out[0]["speaker"] == "男"
    assert out[1]["text"] == "我不会放手。"


def test_parse_clean_rejects_count_mismatch_and_garbage():
    asr = [{"start": 0, "end": 5, "text": "a"}, {"start": 5, "end": 9, "text": "b"}]
    # garbage -> original unchanged
    assert consolidate.parse_clean_response("not json", asr) == asr
    # count mismatch (1 != 2) -> original unchanged
    assert consolidate.parse_clean_response('{"segments":[{"i":0,"text":"x"}]}', asr) == asr


# ── Pass B: index ─────────────────────────────────────────────────────────────

def test_parse_index_normalizes_v2_shape():
    """Garbage and partial JSON both normalize to the four list keys + schema_version 2;
    research_glossary entries are forced to support=context_only."""
    r = consolidate.parse_index_response('garbage no json')
    assert {k: r[k] for k in ("characters", "relationships", "plot_points", "entities")} == {"characters": [], "relationships": [], "plot_points": [], "entities": []}
    assert r["schema_version"] == 2 and r["research_glossary"] == []

    r2 = consolidate.parse_index_response(
        '{"characters":[{"name":"A"}],"plot_points":["p1"],'
        '"research_glossary":[{"name":"叶轻眉","support":"direct"}]}'
    )
    assert r2["schema_version"] == 2
    assert r2["characters"][0]["name"] == "A" and r2["plot_points"] == ["p1"]
    assert r2["relationships"] == [] and r2["entities"] == []
    assert r2["research_glossary"][0]["support"] == "context_only"


def test_build_index_messages_includes_frame_facts_asr_and_research_glossary():
    # frame_facts is a DICT {ts: [actions]} (guards against the review.py list-shape bug)
    vlm = [{"scene_id": 0, "start": 0, "end": 5, "description": "门口对峙",
            "frame_facts": {"2.0": ["男子握紧拳头"], "4.0": ["女子后退一步"]}}]
    vlm_only = consolidate.build_index_messages(vlm)[0]["content"]
    assert "门口对峙" in vlm_only and "男子握紧拳头" in vlm_only and "女子后退一步" in vlm_only

    content = consolidate.build_index_messages(
        vlm,
        asr_result=[{"start": 1, "end": 2, "text": "叶青眉留下了线索"}],
        background_research={"character_details": {"叶轻眉": {"aliases": ["叶青眉"], "role": "主角之母"}}},
    )[0]["content"]
    assert "[asr:0" in content and "叶青眉留下了线索" in content
    assert "support=context_only" in content and "叶轻眉" in content and "aliases=叶青眉" in content


def test_build_clean_messages_includes_transcript():
    asr = [{"start": 0, "end": 5, "text": "第一句对白"}]
    content = consolidate.build_clean_messages(asr)[0]["content"]
    assert "第一句对白" in content
    assert "相邻两段" in content
    assert "分段音频窗口互不重叠" in content
    assert "真的很难。难得让人想放弃" in content


# ── drivers (mocked api) ──────────────────────────────────────────────────────

def test_consolidate_index_writes_artifacts(monkeypatch, tmp_path):
    _write_json(tmp_path, "vlm_analysis.json",
                [{"scene_id": 0, "start": 0, "end": 5, "description": "d", "frame_facts": {"1.0": ["act"]}}])
    monkeypatch.setattr("consolidate.api_call", lambda payload: _fake(
        '{"characters":[{"name":"张三","description":"主角"}],"relationships":[],"plot_points":["开端"],"entities":["匕首"]}'))
    idx = consolidate.consolidate_index(tmp_path)
    assert idx["characters"][0]["name"] == "张三"
    assert (tmp_path / "understanding_index.json").exists()
    meta = json.loads((tmp_path / "understanding_index.json.meta.json").read_text(encoding="utf-8"))
    assert meta["source"] == file_identity(tmp_path / "vlm_analysis.json")
    assert meta["scene_count"] == 1
    assert meta["model"] == consolidate.CONFIG.get("vlm_model", "")
    assert meta["prompt"] == consolidate.INDEX_PROMPT
    md = (tmp_path / "understanding_index.md").read_text(encoding="utf-8")
    assert "张三" in md and "匕首" in md


def test_consolidate_transcript_writes_provenance_and_preserves_spans(monkeypatch, tmp_path):
    asr = [{"start": 0.0, "end": 5.0, "text": "你给我站住"}]
    _write_json(tmp_path, "asr_result.json", asr)
    monkeypatch.setattr("consolidate.api_call", lambda payload: _fake('{"segments":[{"i":0,"text":"你给我站住！"}]}'))
    out = consolidate.consolidate_transcript(tmp_path)
    assert out["source"] == file_identity(tmp_path / "asr_result.json")
    assert out["model"] == consolidate.CONFIG.get("vlm_model", "")
    assert out["prompt"] == consolidate.CLEAN_PROMPT
    assert out["postprocess_version"] == consolidate.ASR_CLEAN_POSTPROCESS_VERSION
    assert out["segments"][0]["start"] == 0.0 and out["segments"][0]["end"] == 5.0
    assert out["segments"][0]["text"] == "你给我站住！"


def test_consolidate_transcript_preserves_legitimate_boundary_repetition(
    monkeypatch, tmp_path
):
    asr = [
        {"start": 0.0, "end": 15.0, "text": "这件事真的很难。"},
        {"start": 15.0, "end": 30.0, "text": "难得让人想放弃。"},
    ]
    _write_json(tmp_path, "asr_result.json", asr)
    monkeypatch.setattr(
        "consolidate.api_call",
        lambda _payload: _fake(
            '{"segments":['
            '{"i":0,"text":"这件事真的很难。"},'
            '{"i":1,"text":"难得让人想放弃。"}'
            "]}"
        ),
    )

    out = consolidate.consolidate_transcript(tmp_path)

    assert [row["text"] for row in out["segments"]] == [
        "这件事真的很难。",
        "难得让人想放弃。",
    ]


def _unlink_meta(tmp_path):
    (tmp_path / "understanding_index.json.meta.json").unlink()


def _drop_prompt(tmp_path):
    meta_path = tmp_path / "understanding_index.json.meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.pop("prompt")
    meta_path.write_text(json.dumps(meta), encoding="utf-8")


def _change_vlm(tmp_path):
    _write_json(tmp_path, "vlm_analysis.json", [{"scene_id": 0, "start": 0, "end": 5, "description": "changed"}])


def _change_research(tmp_path):
    _write_json(tmp_path, "background_research.json", {"characters": {"甲": "第二版"}})


def _change_asr(tmp_path):
    _write_json(tmp_path, "asr_result.json", [{"start": 0, "end": 1, "text": "第二版"}])


def _change_asr_clean(tmp_path):
    _write_json(tmp_path, "asr_clean.json", {"segments": [{"start": 0, "end": 1, "text": "第二版"}]})


@pytest.mark.parametrize(
    "invalidate",
    [_unlink_meta, _drop_prompt, _change_vlm, _change_research, _change_asr, _change_asr_clean],
    ids=lambda fn: fn.__name__.strip("_"),
)
def test_consolidate_index_is_idempotent_until_provenance_or_inputs_change(
    monkeypatch, tmp_path, invalidate
):
    """A fresh index skips the API; losing provenance or rewriting any input recomputes."""
    _write_json(tmp_path, "vlm_analysis.json", [{"scene_id": 0, "start": 0, "end": 5, "description": "first"}])
    _write_json(tmp_path, "asr_result.json", [{"start": 0, "end": 1, "text": "第一版"}])
    _write_json(tmp_path, "asr_clean.json", {"segments": [{"start": 0, "end": 1, "text": "第一版"}]})
    _write_json(tmp_path, "background_research.json", {"characters": {"甲": "第一版"}})
    calls = _counting_api(
        monkeypatch,
        lambda n: f'{{"characters":[{{"name":"第{n}次"}}],"relationships":[],"plot_points":[],"entities":[]}}',
    )

    first = consolidate.consolidate_index(tmp_path)
    consolidate.consolidate_index(tmp_path)  # fresh artifact -> skip, no 2nd api call
    assert calls["n"] == 1
    assert first["characters"][0]["name"] == "第1次"
    meta = json.loads((tmp_path / "understanding_index.json.meta.json").read_text(encoding="utf-8"))
    assert meta["schema_version"] == 2
    assert meta["inputs"]["asr_result"] == file_identity(tmp_path / "asr_result.json")
    assert meta["inputs"]["background_research"] == file_identity(tmp_path / "background_research.json")

    invalidate(tmp_path)
    second = consolidate.consolidate_index(tmp_path)

    assert calls["n"] == 2
    assert second["characters"][0]["name"] == "第2次"


def test_consolidate_graceful_when_inputs_absent(tmp_path):
    # no vlm_analysis.json / asr_result.json -> no crash, returns empty-ish
    res = consolidate.consolidate(tmp_path, do_asr=True, do_index=True)
    assert res.get("index") is None and res.get("asr_clean") is None


def test_consolidate_runs_asr_cleanup_before_index_when_both_requested(monkeypatch, tmp_path):
    order = []

    def clean(_work_dir):
        order.append("asr")
        return {"segments": [{"text": "clean"}]}

    def index(_work_dir):
        order.append("index")
        return {"characters": []}

    monkeypatch.setattr(consolidate, "consolidate_transcript", clean)
    monkeypatch.setattr(consolidate, "consolidate_index", index)

    result = consolidate.consolidate(tmp_path, do_asr=True, do_index=True)

    assert order == ["asr", "index"]
    assert result == {
        "asr_clean": {"segments": [{"text": "clean"}]},
        "index": {"characters": []},
    }


def test_consolidate_recomputes_asr_clean_when_model_or_prompt_meta_missing(monkeypatch, tmp_path):
    _write_json(tmp_path, "asr_result.json", [{"start": 0.0, "end": 5.0, "text": "你给我站住"}])
    calls = _counting_api(monkeypatch, lambda n: f'{{"segments":[{{"i":0,"text":"第{n}次"}}]}}')
    first = consolidate.consolidate_transcript(tmp_path)
    assert first["segments"][0]["text"] == "第1次"

    payload = json.loads((tmp_path / "asr_clean.json").read_text(encoding="utf-8"))
    payload.pop("model")
    (tmp_path / "asr_clean.json").write_text(json.dumps(payload), encoding="utf-8")
    second = consolidate.consolidate_transcript(tmp_path)

    assert calls["n"] == 2
    assert second["segments"][0]["text"] == "第2次"


def test_index_v2_deterministic_asr_mentions_when_llm_omits(monkeypatch, tmp_path):
    _write_json(tmp_path, "vlm_analysis.json", [{"scene_id": 0, "start": 0, "end": 5, "description": "无人名画面"}])
    _write_json(tmp_path, "asr_result.json", [{"start": 1, "end": 2, "text": "叶青眉留下线索"}])
    _write_json(tmp_path, "background_research.json", {"character_details": {"叶轻眉": {"aliases": ["叶青眉"], "role": "关键人物"}}})
    monkeypatch.setattr("consolidate.api_call", lambda payload: _fake('{"characters":[],"relationships":[],"plot_points":[],"entities":[],"research_glossary":[]}'))
    idx = consolidate.consolidate_index(tmp_path)
    char = idx["characters"][0]
    assert char["name"] == "叶轻眉"
    assert char["asr_mentions"][0]["evidence_id"] == "asr:0"
    assert "asr:0" in char["evidence_ids"]
    assert idx["research_glossary"][0]["support"] == "context_only"
