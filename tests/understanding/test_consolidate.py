import itertools
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'skills' / 'video-understanding' / 'scripts'))

import consolidate  # noqa: E402
from lib import file_identity  # noqa: E402


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    """Passes that call the model need a key; the offline tests below clear it."""
    monkeypatch.setitem(consolidate.CONFIG, "api_key", "test-key")


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
        "skipped_no_key": [],
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


# ── cut-off / unparseable replies (a real 5-min recap hit finish_reason=length) ──

_TRUNCATED_INDEX = '```json\n{"characters":[{"name":"范闲","description":"主角"},{"name":"五'
_FULL_INDEX = '{"characters":[{"name":"范闲"}],"relationships":[],"plot_points":[],"entities":[]}'


def _scripted_api(monkeypatch, replies):
    """Patch consolidate.api_call to return (content, finish_reason) replies in order."""
    seen = []

    def fake(payload):
        seen.append(payload["max_tokens"])
        content, finish = replies[len(seen) - 1]
        return {"choices": [{"message": {"content": content}, "finish_reason": finish}]}

    monkeypatch.setattr("consolidate.api_call", fake)
    return seen


def _one_scene(tmp_path):
    _write_json(tmp_path, "vlm_analysis.json", [{"scene_id": 0, "start": 0, "end": 5, "description": "d"}])


def test_index_retries_once_with_larger_budget_when_cut_off(monkeypatch, tmp_path):
    _one_scene(tmp_path)
    seen = _scripted_api(monkeypatch, [(_TRUNCATED_INDEX, "length"), (_FULL_INDEX, "stop")])

    idx = consolidate.consolidate_index(tmp_path)

    assert seen == [consolidate._INDEX_MAX_TOKENS, consolidate._INDEX_MAX_TOKENS * 2]
    assert idx["characters"][0]["name"] == "范闲"
    assert (tmp_path / "understanding_index.json").exists()


def test_index_cut_off_twice_raises_and_writes_nothing(monkeypatch, tmp_path):
    _one_scene(tmp_path)
    seen = _scripted_api(monkeypatch, [(_TRUNCATED_INDEX, "length"), (_TRUNCATED_INDEX, "length")])

    with pytest.raises(consolidate.ConsolidateIncomplete, match="finish_reason=length"):
        consolidate.consolidate_index(tmp_path)

    assert len(seen) == 2
    for name in ("understanding_index.json", "understanding_index.json.meta.json", "understanding_index.md"):
        assert not (tmp_path / name).exists()


def test_index_unparseable_reply_raises_without_retry(monkeypatch, tmp_path):
    _one_scene(tmp_path)
    seen = _scripted_api(monkeypatch, [("抱歉，我无法完成。", "stop")])

    with pytest.raises(consolidate.ConsolidateIncomplete, match="不是可解析的 JSON"):
        consolidate.consolidate_index(tmp_path)

    assert len(seen) == 1
    assert not (tmp_path / "understanding_index.json").exists()


def test_asr_clean_cut_off_twice_raises_and_writes_nothing(monkeypatch, tmp_path):
    _write_json(tmp_path, "asr_result.json", [{"start": 0.0, "end": 5.0, "text": "你给我站住"}])
    truncated = '{"segments":[{"i":0,"text":"你给'
    seen = _scripted_api(monkeypatch, [(truncated, "length"), (truncated, "length")])

    with pytest.raises(consolidate.ConsolidateIncomplete, match="consolidate\\(asr\\)"):
        consolidate.consolidate_transcript(tmp_path)

    assert seen == [consolidate._CLEAN_MAX_TOKENS, consolidate._CLEAN_MAX_TOKENS * 2]
    assert not (tmp_path / "asr_clean.json").exists()


# ── deterministic index repairs (index_normalize) ─────────────────────────────

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("00:95", "01:35"),  # scene seconds poured into the ss field
        ("1:75", "02:15"),
        ("02:05", "02:05"),
        ("0:5", "00:05"),
        ("1:02:03", "1:02:03"),
        (125, "02:05"),
        ("12.5s", "00:12"),
        ("12.5秒", "00:12"),
        ("约01:20", "01:20"),
        ("大约 1:20", "01:20"),
        ("01:20-01:45", "01:20"),
        ("01:20 ~ 01:45", "01:20"),
        ("80秒-95秒", "01:20"),
        ("01:20至01:45", "01:20"),
        ("00：95", "01:35"),  # full-width colon
        ("3分20秒", "03:20"),
        ("3分20", "03:20"),
        ("3分钟", "03:00"),
        ("约3分20.5秒", "03:20"),
        ("1小时2分3秒", "1:02:03"),
        ("3分20秒-3分45秒", "03:20"),
        ("01:20左右", "01:20"),
        ("约1分20秒左右", "01:20"),
        ("3分钟前后", "03:00"),
        ("80秒许", "01:20"),
        ("01:20左右-01:45左右", "01:20"),
        ("1.5分", "01:30"),
        ("2.5分钟", "02:30"),
        ("第3分钟", "03:00"),
        ("第 95 秒", "01:35"),
    ],
)
def test_plot_time_is_canonical_mm_ss(raw, expected):
    from index_normalize import normalize_plot_times

    points, dropped = normalize_plot_times([{"time": raw, "text": "x"}], duration=4000)
    assert points == [{"time": expected, "text": "x"}] and dropped == 0


@pytest.mark.parametrize("raw", ["开头", "", None, "-0:05", "05:00", "左右", "第一分钟"])
def test_unreadable_or_out_of_range_plot_time_is_dropped(raw):
    from index_normalize import normalize_plot_times

    points, dropped = normalize_plot_times(
        [{"time": raw, "text": "x"}, "bare string"], duration=120.0
    )
    assert points == [{"text": "x"}, "bare string"] and dropped == 1


def test_characters_sharing_a_name_or_alias_merge_deterministically():
    from index_normalize import normalize_index

    index = {
        "characters": [
            {"name": "范闲", "description": "", "aliases": ["小范大人"], "evidence_ids": ["visual:0"],
             "confidence": "medium"},
            {"name": "王启年", "aliases": [], "evidence_ids": ["visual:1"]},
            {"name": "小范大人", "description": "监察院提司", "aliases": ["范提司"],
             "evidence_ids": ["asr:2", "visual:0"], "confidence": "high"},
        ],
        "relationships": [
            {"a": "小范大人", "b": "王启年", "relation": "上下级", "evidence_ids": ["asr:2"]},
            {"a": "范闲", "b": "王启年", "relation": "上下级", "evidence_ids": ["visual:1"]},
            {"a": "范闲", "b": "小范大人", "relation": "同一人", "evidence_ids": []},
        ],
        "plot_points": [],
    }

    out, report = normalize_index(index)

    assert [c["name"] for c in out["characters"]] == ["范闲", "王启年"]
    fan = out["characters"][0]
    assert fan["aliases"] == ["小范大人", "范提司"]
    assert fan["evidence_ids"] == ["visual:0", "asr:2"]
    assert fan["description"] == "监察院提司" and fan["confidence"] == "high"
    assert out["relationships"] == [
        {"a": "范闲", "b": "王启年", "relation": "上下级", "evidence_ids": ["asr:2", "visual:1"]}
    ]
    assert report == {"merged_characters": 1, "dropped_plot_times": 0}
    assert normalize_index(out)[0] == out  # idempotent


def test_a_later_entry_linking_two_groups_is_kept_on_its_own():
    """An entry whose aliases name two different characters is ambiguous: no bridge merge."""
    from index_normalize import merge_characters

    chars, renames = merge_characters([
        {"name": "甲", "aliases": ["阿甲"]},
        {"name": "乙", "aliases": ["阿乙"]},
        {"name": "丙", "aliases": ["甲", "乙"]},
    ])
    assert [c["name"] for c in chars] == ["甲", "乙", "丙"]
    assert renames == {}


def test_entries_with_the_same_name_are_one_character():
    """Same-named entries pool their aliases before any link is judged, so one of them
    naming another character as an alias links the whole character, not a duplicate."""
    from index_normalize import merge_characters

    chars, renames = merge_characters([
        {"name": "甲", "aliases": ["阿甲"]},
        {"name": "乙", "aliases": ["阿乙"]},
        {"name": "乙", "aliases": ["阿二"], "description": "乙的第二条"},
    ])
    assert [c["name"] for c in chars] == ["甲", "乙"]
    assert chars[1]["aliases"] == ["阿乙", "阿二"]
    assert chars[1]["description"] == "乙的第二条"

    chars, renames = merge_characters([
        {"name": "甲", "aliases": ["阿甲"]},
        {"name": "乙", "aliases": ["阿乙"]},
        {"name": "乙", "aliases": ["甲"]},
    ])
    assert [c["name"] for c in chars] == ["甲"]
    assert chars[0]["aliases"] == ["阿甲", "乙", "阿乙"]
    assert renames == {"乙": "甲"}


_LEADS_AND_EXTRA = (
    {"name": "王大锤", "aliases": ["男子"], "evidence_ids": ["A"]},
    {"name": "李警官", "aliases": ["男子"], "evidence_ids": ["B"]},
    {"name": "男子", "aliases": ["路人"], "evidence_ids": ["C"]},
)
_TWO_NAMES_IN_ONE = (
    {"name": "丙", "aliases": ["甲", "乙"], "evidence_ids": ["丙"]},
    {"name": "甲", "evidence_ids": ["甲"]},
    {"name": "乙", "evidence_ids": ["乙"]},
)


@pytest.mark.parametrize(
    "characters",
    [
        pytest.param(list(order), id=f"{label}-{'-'.join(c['evidence_ids'][0] for c in order)}")
        for label, fixture in (("leads", _LEADS_AND_EXTRA), ("bridge", _TWO_NAMES_IN_ONE))
        for order in itertools.permutations(fixture)
    ],
)
def test_character_merging_does_not_depend_on_input_order(characters):
    """A bridging entry (an extra named by an alias two leads share, or one entry naming two
    people) stays on its own in every order, and never folds the others together."""
    from index_normalize import normalize_index

    relationships = [{"a": "王大锤", "b": "李警官", "relation": "对峙"}]
    index = {"characters": characters, "relationships": relationships, "plot_points": []}

    out, report = normalize_index(index)

    # Every entry is its own character: the same partition in every order.
    partition = sorted(sorted(c["evidence_ids"]) for c in out["characters"])
    assert partition == sorted(c["evidence_ids"] for c in characters)
    assert out["characters"] == characters
    assert out["relationships"] == relationships
    assert report["merged_characters"] == 0
    assert normalize_index(out)[0] == out


def test_a_shared_alias_alone_does_not_merge_two_characters():
    """A generic alias the model gives two people must not fold them into one."""
    from index_normalize import normalize_index

    index = {
        "characters": [
            {"name": "王大锤", "aliases": ["男子", "老板"]},
            {"name": "李警官", "aliases": ["男子"]},
        ],
        "relationships": [{"a": "王大锤", "b": "李警官", "relation": "对峙"}],
        "plot_points": [],
    }
    out, report = normalize_index(index)
    assert [c["name"] for c in out["characters"]] == ["王大锤", "李警官"]
    assert out["relationships"] == index["relationships"]
    assert report["merged_characters"] == 0


def test_an_extra_named_by_a_shared_alias_does_not_bridge_two_characters():
    """A later "男子" entry links to both people carrying the alias 男子: keep all three."""
    from index_normalize import normalize_index

    index = {
        "characters": [
            {"name": "王大锤", "aliases": ["男子", "老板"], "description": "老板"},
            {"name": "李警官", "aliases": ["男子"], "description": "警察"},
            {"name": "男子", "description": "路人"},
        ],
        "relationships": [{"a": "王大锤", "b": "李警官", "relation": "对峙"}],
        "plot_points": [],
    }
    out, report = normalize_index(index)
    assert out["characters"] == index["characters"]
    assert out["relationships"] == index["relationships"]
    assert report["merged_characters"] == 0
    assert normalize_index(out)[0] == out


def test_a_string_alias_is_one_alias_not_its_characters():
    from index_normalize import normalize_index

    index = {
        "characters": [
            {"name": "大", "aliases": []},
            {"name": "王二", "aliases": "王大锤"},
            {"name": "王大锤", "aliases": "老王"},
        ],
        "relationships": [],
        "plot_points": [],
    }
    out, report = normalize_index(index)
    # "大" is not split out of "王大锤"; the second and third entries are one person.
    assert [c["name"] for c in out["characters"]] == ["大", "王二"]
    assert out["characters"][1]["aliases"] == ["王大锤", "老王"]
    assert report["merged_characters"] == 1


def test_parse_index_response_reads_a_string_alias_as_a_list():
    out = consolidate.parse_index_response(
        '{"characters":[{"name":"王大锤","aliases":"老王"}],"relationships":[],'
        '"plot_points":[],"entities":[]}'
    )
    assert out["characters"][0]["aliases"] == ["老王"]


def test_consolidate_index_repairs_a_cached_index_without_a_model_call(monkeypatch, tmp_path):
    _write_json(tmp_path, "vlm_analysis.json", [{"scene_id": 0, "start": 0, "end": 120, "description": "对峙"}])
    calls = _counting_api(monkeypatch, lambda n: (
        '{"characters":[{"name":"范闲"},{"name":"范提司","aliases":["范闲"]}],'
        '"relationships":[],"plot_points":[{"time":"00:95","text":"转折"}],"entities":[]}'
    ))
    first = consolidate.consolidate_index(tmp_path)
    assert [c["name"] for c in first["characters"]] == ["范闲"]
    assert first["plot_points"] == [{"time": "01:35", "text": "转折"}]

    # An index cached by an older build (split entry, raw time) is repaired on the cache hit.
    stale = dict(first, plot_points=[{"time": "00:95", "text": "转折"}])
    (tmp_path / "understanding_index.json").write_text(json.dumps(stale), encoding="utf-8")
    again = consolidate.consolidate_index(tmp_path)

    assert calls["n"] == 1
    assert again["plot_points"] == [{"time": "01:35", "text": "转折"}]
    on_disk = json.loads((tmp_path / "understanding_index.json").read_text(encoding="utf-8"))
    assert on_disk["plot_points"] == [{"time": "01:35", "text": "转折"}]
