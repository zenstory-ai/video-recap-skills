import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "skills" / "video-script" / "scripts")
)
import json
import os
import pytest

import evidence_bundle
import review_grounding
import review_response
import review_runner

OK_RESPONSE = '{"verdict":"OK","summary":"ok","findings":[]}'


def _write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _seed_work_dir(work_dir, narration, vlm=(), asr=()):
    _write_json(work_dir / "narration.json", narration)
    _write_json(work_dir / "vlm_analysis.json", list(vlm))
    _write_json(work_dir / "asr_result.json", list(asr))


def _capture_api(monkeypatch, content=OK_RESPONSE):
    """Stub the reviewer LLM; `content` is a JSON string or a callable of the call index."""
    payloads = []

    def fake_api(payload):
        payloads.append(payload)
        text = content(len(payloads) - 1) if callable(content) else content
        return {"choices": [{"message": {"content": text}}]}

    monkeypatch.setattr("review_runner.api_call", fake_api)
    return payloads


def _parse(payload):
    return review_response.parse_review_response(json.dumps(payload, ensure_ascii=False))


def test_parse_review_handles_fenced_raw_and_garbage():
    fenced = (
        '```json\n{"verdict":"REVISE","summary":"s","findings":'
        '[{"segment":0,"severity":"error","category":"hallucination","issue":"i","fix":"f"}]}\n```'
    )
    r = review_response.parse_review_response(fenced)
    assert r["verdict"] == "REVISE"
    assert r["findings"][0]["category"] == "hallucination"
    # A stray "OK" from the judge is approval, not a revision request.
    assert review_response.parse_review_response(OK_RESPONSE)["verdict"] == "PASS"
    junk = review_response.parse_review_response("no json here")
    assert junk["verdict"] == "REVISE" and junk.get("parse_error")


def test_parse_review_normalizes_bad_severity_and_category_and_verdict():
    r = review_response.parse_review_response(
        '{"verdict":"weird","findings":[{"severity":"BOGUS","category":"nope","issue":"i"}]}'
    )
    assert r["verdict"] == "REVISE"
    assert r["findings"][0]["severity"] == "warning"
    assert r["findings"][0]["category"] == "other"


@pytest.mark.parametrize(
    "frame_facts, expected_facts",
    [
        pytest.param([{"fact": "男子握紧拳头"}], ["男子握紧拳头"], id="list_of_fact_dicts"),
        pytest.param(
            {"2.0": ["男子握紧拳头"], "4.0": ["女子后退一步"]},
            ["男子握紧拳头", "女子后退一步"],
            id="dict_by_timestamp",
        ),
        pytest.param(
            {"intro": ["非数字锚点"], "1.0": ["数字锚点"]},
            ["非数字锚点", "数字锚点"],
            id="non_numeric_keys",
        ),
    ],
)
def test_build_review_messages_grounds_draft_asr_and_every_frame_fact_shape(
    frame_facts, expected_facts
):
    """frame_facts may be a list of {fact} dicts or vlm.py's {ts: [actions]} dict (regression
    guard for the list-as-dict silent drop); every action must reach the reviewer."""
    narration = [
        {"start": 1.0, "end": 4.0, "narration": "他下定决心。", "overlaps_speech": True}
    ]
    vlm = [
        {
            "scene_id": 0,
            "start": 0,
            "end": 5,
            "description": "门口对峙",
            "frame_facts": frame_facts,
        }
    ]
    asr = [{"start": 1, "end": 4, "text": "你给我站住"}]
    content = review_response.build_review_messages(narration, vlm, asr)[0]["content"]
    assert "他下定决心" in content and "门口对峙" in content
    assert "你给我站住" in content
    for fact in expected_facts:
        assert fact in content


def test_build_review_messages_includes_bounded_research_context(tmp_path):
    _write_json(
        tmp_path / "background_research.json",
        {
            "synopsis": "范闲卷入监察院暗线。",
            "episode_context": "本集他第一次公开试探对手。",
            "worldbuilding": "庆国朝堂暗流涌动。",
            "characters": {f"角色{i}": f"简介{i}" for i in range(20)},
            "character_details": {
                "范闲": {
                    "role": "主角",
                    "aliases": ["小范大人"],
                    "relationships": ["与五竹互相信任"],
                },
            },
            "plot_arcs": [
                {"name": f"线索{i}", "description": f"描述{i}", "status": "进行中"}
                for i in range(12)
            ],
            "cultural_notes": [{"item": "夜宴", "explanation": "权力试探"}],
            "noise": "x" * 5000,
        },
    )

    content = review_response.build_review_messages(
        [{"start": 1.0, "end": 4.0, "narration": "他开始反击。"}],
        [],
        [],
        work_dir=tmp_path,
    )[0]["content"]

    assert "背景资料（context-only/advisory" in content
    assert "范闲卷入监察院暗线" in content
    assert "角色0：简介0" in content
    assert "角色12" not in content
    assert "线索7：描述7 [进行中]" in content
    assert "线索8" not in content
    assert "noise" not in content


def test_review_narration_passes_background_research_to_reviewer(monkeypatch, tmp_path):
    _seed_work_dir(tmp_path, [{"start": 1, "end": 4, "narration": "测试。"}])
    _write_json(tmp_path / "background_research.json", {"synopsis": "主角秘密查案"})
    payloads = _capture_api(monkeypatch)

    review_runner.review_narration(tmp_path)

    assert "主角秘密查案" in payloads[0]["messages"][0]["content"]


def test_review_narration_writes_artifacts(monkeypatch, tmp_path):
    _seed_work_dir(tmp_path, [{"start": 1, "end": 4, "narration": "测试。"}])
    _capture_api(
        monkeypatch,
        '{"verdict":"REVISE","summary":"需加钩子","findings":'
        '[{"segment":0,"severity":"warning","category":"weak_hook","issue":"开头平淡","fix":"加悬念"}]}',
    )
    r = review_runner.review_narration(tmp_path)
    assert r["verdict"] == "REVISE"
    assert (tmp_path / "narration_review.json").exists()
    md = (tmp_path / "narration_review.md").read_text(encoding="utf-8")
    assert "weak_hook" in md and "需加钩子" in md


def test_review_cli_without_api_key_sends_nothing(monkeypatch, tmp_path):
    """No MIMO_API_KEY: stop before building a request, so no narration text leaves the machine."""
    _seed_work_dir(tmp_path, [{"start": 0, "end": 3, "narration": "测试"}])
    payloads = _capture_api(monkeypatch)
    monkeypatch.setitem(review_runner.CONFIG, "api_key", "")
    monkeypatch.setattr(sys, "argv", ["review.py", "--work-dir", str(tmp_path)])
    with pytest.raises(SystemExit, match="MIMO_API_KEY"):
        review_runner.main()
    assert payloads == []
    assert not (tmp_path / "narration_review.json").exists()


def test_auto_timeline_detects_validated_cut(tmp_path):
    """A bare work_dir reviews on the source timeline; a validated cut (clip_plan_validated.json
    + edited_source.mp4) auto-selects cut_output so manual review matches the orchestrator."""
    assert review_runner._auto_timeline(tmp_path) == "source"
    (tmp_path / "clip_plan_validated.json").write_text("{}", encoding="utf-8")
    assert (
        review_runner._auto_timeline(tmp_path) == "source"
    )  # plan alone is not enough
    (tmp_path / "edited_source.mp4").write_bytes(b"")
    assert review_runner._auto_timeline(tmp_path) == "cut_output"


def _write_manifest(work_dir, edit_mode):
    _write_json(
        work_dir / "recap_run_manifest.json", {"settings": {"edit_mode": edit_mode}}
    )


def test_auto_timeline_trusts_manifest_edit_mode(tmp_path):
    """recap_run_manifest.json is authoritative: full mode stays source even with stale cut
    artifacts in a reused work_dir, and cut mode selects cut_output."""
    (tmp_path / "clip_plan_validated.json").write_text("{}", encoding="utf-8")
    (tmp_path / "edited_source.mp4").write_bytes(b"")
    _write_manifest(tmp_path, "full")
    assert (
        review_runner._auto_timeline(tmp_path) == "source"
    )  # stale cut artifacts must not flip it
    _write_manifest(tmp_path, "cut")
    assert review_runner._auto_timeline(tmp_path) == "cut_output"


def test_cut_output_review_remaps_grounding_to_output_timeline():
    spans = [
        {
            "source_start": 10.0,
            "source_end": 20.0,
            "output_start": 0.0,
            "output_end": 10.0,
        }
    ]
    vlm, asr = review_grounding.remap_grounding_to_output_timeline(
        [
            {
                "scene_id": 1,
                "start": 12.0,
                "end": 16.0,
                "description": "保留片段",
                "frame_facts": {"14.0": ["关键动作"], "21.0": ["剪掉动作"]},
            }
        ],
        [
            {"start": 13.0, "end": 15.0, "text": "这句在成片三到五秒"},
            {"start": 25.0, "end": 26.0, "text": "被剪掉"},
        ],
        spans,
    )

    assert vlm[0]["start"] == 2.0
    assert vlm[0]["end"] == 6.0
    assert vlm[0]["frame_facts"] == {"4.000": ["关键动作"]}
    assert (
        asr[0]["start"] == 3.0
        and asr[0]["end"] == 5.0
        and asr[0]["text"] == "这句在成片三到五秒"
    )
    assert asr[0]["source_start"] == 13.0 and asr[0]["source_end"] == 15.0


def test_review_narration_cut_output_requires_fresh_validated_clip_spans(
    monkeypatch, tmp_path
):
    """Advisory cut_output review fails open with warnings recorded in narration_review.json;
    strict evidence blocks on a missing or stale clip_plan_validated.json. No grounding files
    at all: missing vlm/asr artifacts must degrade to empty evidence, not crash."""
    _write_json(tmp_path / "narration.json", [{"start": 1, "end": 2, "narration": "测试。"}])
    _capture_api(monkeypatch, '{"verdict":"PASS","summary":"ok","findings":[]}')

    out = review_runner.review_narration(tmp_path, timeline="cut_output")
    assert out["warnings"]
    written = json.loads((tmp_path / "narration_review.json").read_text(encoding="utf-8"))
    assert written["warnings"] == written["evidence_contract"]["warnings"] == out["warnings"]
    assert not (tmp_path / "grounding_qc.json").exists()
    with pytest.raises(SystemExit, match="clip_plan_validated"):
        review_runner.review_narration(tmp_path, timeline="cut_output", strict_evidence=True)

    raw = [{"start": 10, "end": 20}]
    _write_json(tmp_path / "clip_plan.json", raw)
    out = review_runner.review_narration(tmp_path, timeline="cut_output")
    assert out.get("warnings")

    stale = {
        "clips": [
            {"source_start": 10, "source_end": 20, "output_start": 0, "output_end": 10}
        ],
    }
    _write_json(tmp_path / "clip_plan_validated.json", stale)
    os.utime(tmp_path / "clip_plan_validated.json", ns=(1_000_000_000, 1_000_000_000))
    os.utime(tmp_path / "clip_plan.json", ns=(2_000_000_000, 2_000_000_000))
    with pytest.raises(SystemExit, match="clip_plan_validated"):
        review_runner.review_narration(tmp_path, timeline="cut_output", strict_evidence=True)


def test_review_narration_cut_output_uses_remapped_grounding(monkeypatch, tmp_path):
    _seed_work_dir(
        tmp_path,
        [{"start": 3, "end": 5, "narration": "测试。"}],
        vlm=[
            {
                "scene_id": 1,
                "start": 12,
                "end": 16,
                "description": "保留片段",
                "frame_facts": {},
            }
        ],
        asr=[{"start": 13, "end": 15, "text": "输出三到五秒对白"}],
    )
    raw_plan = [{"start": 10, "end": 20}]
    _write_json(tmp_path / "clip_plan.json", raw_plan)
    _write_json(
        tmp_path / "clip_plan_validated.json",
        {
            "clips": [
                {
                    "clip_id": 0,
                    "source_start": 10,
                    "source_end": 20,
                    "output_start": 0,
                    "output_end": 10,
                }
            ],
        },
    )
    payloads = _capture_api(monkeypatch)

    review_runner.review_narration(tmp_path, timeline="cut_output")

    content = payloads[0]["messages"][0]["content"]
    assert "[OUTPUT 3.0-5.0s 对白" in content and "输出三到五秒对白" in content
    assert "[OUTPUT 2.0-6.0s 画面" in content and "保留片段" in content
    assert "SOURCE 13.0-15.0s" in content


def test_multi_source_cut_output_review_loads_each_source_grounding(
    monkeypatch, tmp_path
):
    _write_json(
        tmp_path / "narration.json",
        [{"start": 0, "end": 10, "narration": "两个来源都要有证据。"}],
    )
    sources = []
    for source_id, label in (("src_a", "选秀夜"), ("src_b", "活塞包夹")):
        relative = f"sources/{source_id}"
        source_dir = tmp_path / relative
        source_dir.mkdir(parents=True)
        _write_json(
            source_dir / "vlm_analysis.json",
            [
                {
                    "scene_id": 0,
                    "start": 0,
                    "end": 5,
                    "description": label,
                    "frame_facts": {},
                }
            ],
        )
        _write_json(
            source_dir / "asr_clean.json",
            {"segments": [{"start": 0, "end": 5, "text": label + "对白"}]},
        )
        sources.append({"source_id": source_id, "source_work_dir": relative})
    _write_json(
        tmp_path / "multi_source_manifest.json",
        {"schema_version": 1, "sources": sources},
    )
    raw_plan = {
        "clips": [
            {"source_id": "src_a", "start": 0, "end": 5},
            {"source_id": "src_b", "start": 0, "end": 5},
        ]
    }
    _write_json(tmp_path / "clip_plan.json", raw_plan)
    _write_json(
        tmp_path / "clip_plan_validated.json",
        {
            "clips": [
                {
                    "clip_id": 0,
                    "source_id": "src_a",
                    "source_start": 0,
                    "source_end": 5,
                    "output_start": 0,
                    "output_end": 5,
                },
                {
                    "clip_id": 1,
                    "source_id": "src_b",
                    "source_start": 0,
                    "source_end": 5,
                    "output_start": 5,
                    "output_end": 10,
                },
            ],
        },
    )
    payloads = _capture_api(monkeypatch, '{"verdict":"PASS","summary":"ok","findings":[]}')

    review_runner.review_narration(tmp_path, timeline="cut_output", strict_evidence=True)

    content = payloads[0]["messages"][0]["content"]
    assert (
        content.count("选秀夜") == 2
    )  # one visual fact + one ASR line, not cross-mapped to src_b
    assert content.count("活塞包夹") == 2
    assert "[OUTPUT 0.0-5.0s 画面" in content
    assert "[OUTPUT 5.0-10.0s 画面" in content


def test_parse_review_keeps_only_verdict_summary_and_findings():
    """Advisory extras a judge may still emit (old scorecard prompt) are not carried over."""
    r = _parse(
        {
            "verdict": "PASS",
            "summary": "ok",
            "scorecard": {"hook_3s": 1},
            "hook_candidates_review": [{"candidate": "首句", "score": 5}],
            "grounding_assertions": [{"segment": 0, "source": "research"}],
            "findings": [],
        }
    )
    assert set(r) == {"verdict", "summary", "findings"}
    md = review_response.format_review_md(r)
    assert "Scorecard" not in md and "## Findings" in md


def test_review_markdown_numbers_blocks_from_one_while_json_keeps_draft_index():
    r = _parse(
        {
            "verdict": "REVISE",
            "summary": "s",
            "findings": [
                {"segment": 0, "severity": "warning", "category": "weak_hook", "issue": "开头平淡"},
                {"segment": "2", "severity": "warning", "category": "cliche", "issue": "套话"},
                {"segment": None, "severity": "suggestion", "category": "other", "issue": "整体"},
            ],
        }
    )
    assert [f["segment"] for f in r["findings"]] == [0, "2", None]
    md = review_response.format_review_md(r)
    assert "] 段 1** — 开头平淡" in md
    assert "] 段 3** — 套话" in md
    assert "] 整体** — 整体" in md
    assert "段 0" not in md


def test_review_markdown_never_prints_a_raw_zero_based_segment():
    r = _parse(
        {
            "verdict": "REVISE",
            "summary": "s",
            "findings": [
                {"segment": 0.0, "severity": "warning", "category": "weak_hook", "issue": "浮点段号"},
                {"segment": [0, 1], "severity": "warning", "category": "cliche", "issue": "列表段号"},
                {"segment": -1, "severity": "warning", "category": "other", "issue": "负段号"},
            ],
        }
    )
    md = review_response.format_review_md(r)
    assert "] 段 1** — 浮点段号" in md
    assert "] 段 ?（模型返回 [0, 1]）** — 列表段号" in md
    assert "] 段 ?（模型返回 -1）** — 负段号" in md
    assert "段 0" not in md and "段 [" not in md


def test_build_review_messages_includes_optional_planning_and_style_artifacts(tmp_path):
    _write_json(tmp_path / "packaging_plan.json", {"viewer_promise": "看到反转"})
    _write_json(
        tmp_path / "recap_story_plan.json",
        {
            "director_intent": {"pov": "女主", "dramatic_question": "他如何翻盘"},
            "beats": [{"beat_id": "b01", "change": "knowledge: doubt→proof"}],
        },
    )
    _write_json(
        tmp_path / "visual_audio_board.json",
        {
            "items": [
                {
                    "beat_id": "b01",
                    "audio_owner": "silence",
                    "narration_job": "none",
                }
            ],
        },
    )
    _write_json(tmp_path / "style_card.json", {"tone": "冷静克制", "avoid": ["空泛拔高"]})
    content = review_response.build_review_messages(
        [{"start": 0, "end": 3, "narration": "测试。"}], [], [], work_dir=tmp_path
    )[0]["content"]

    assert "packaging_plan.json" in content and "看到反转" in content
    assert (
        "女主" in content
        and "他如何翻盘" in content
        and "knowledge: doubt→proof" in content
    )
    assert (
        "visual_audio_board.json" in content and '"audio_owner": "silence"' in content
    )
    assert '"narration_job": "none"' in content
    assert "7:3 不是配额" in content
    assert (
        "style_card.json" in content and "冷静克制" in content and "空泛拔高" in content
    )
    assert "可能为空" in content
    assert "scorecard" not in content.lower() and "deslop_qc" not in content


def test_coverage_policy_v1_covers_long_video_tail_and_bme():
    scenes = [
        {
            "scene_id": i,
            "start": i * 10.0,
            "end": i * 10.0 + 8.0,
            "description": f"scene{i}",
        }
        for i in range(100)
    ]
    asr = [
        {"start": i * 5.0, "end": i * 5.0 + 2.0, "text": f"line{i}"} for i in range(200)
    ]
    narration = [{"start": 850.0, "end": 860.0, "narration": "后段关键反转。"}]
    cov = evidence_bundle.coverage_policy_v1(scenes, asr, narration)
    ranges = cov["selected_ranges"]
    assert any(r["start"] <= 0 <= r["end"] for r in ranges)
    assert any(r["start"] <= 500 <= r["end"] for r in ranges)
    assert any(r["start"] <= 990 <= r["end"] for r in ranges)
    assert any(
        r["start"] <= 855 <= r["end"] and "narration_window" in r["selection_reason"]
        for r in ranges
    )
    assert (
        evidence_bundle.coverage_policy_v1(scenes, asr, narration)["selected_ranges"] == ranges
    )


def test_evidence_bundle_labels_source_output_and_context_only():
    narration = [{"start": 3, "end": 5, "narration": "测试。"}]
    vlm = [
        {
            "scene_id": 1,
            "start": 2,
            "end": 6,
            "description": "保留",
            "source_start": 12,
            "source_end": 16,
            "output_segment_index": 0,
        }
    ]
    asr = [
        {
            "start": 3,
            "end": 5,
            "text": "对白",
            "source_start": 13,
            "source_end": 15,
            "output_segment_index": 0,
        }
    ]
    research = {
        "characters": {"叶轻眉": "主角之母"},
        "character_details": {"叶轻眉": {"aliases": ["叶青眉"], "role": "背景人物"}},
    }
    bundle = evidence_bundle.build_evidence_bundle(
        vlm, asr, narration, timeline="cut_output", research=research
    )
    assert {item["clock"] for item in bundle["items"]} == {"output"}
    assert all(
        "source_start" in item and "source_end" in item for item in bundle["items"]
    )
    assert bundle["context_items"] and all(
        item["clock"] is None and item["support"] == "context_only"
        for item in bundle["context_items"]
    )
    rendered = evidence_bundle.render_evidence_bundle(bundle)
    assert "clock=OUTPUT" in rendered and "SOURCE 12.0-16.0s" in rendered
    assert "clock=null" in rendered


def test_merge_review_findings_dedup_keeps_highest_severity():
    merged = review_response.merge_review_findings(
        [
            {
                "findings": [
                    {
                        "segment": 1,
                        "category": "hallucination",
                        "severity": "suggestion",
                        "issue": "X",
                        "fix": "a",
                    }
                ]
            },
            {
                "findings": [
                    {
                        "segment": 1,
                        "category": "hallucination",
                        "severity": "error",
                        "issue": "X",
                        "fix": "b",
                    }
                ]
            },
        ]
    )
    assert (
        len(merged) == 1
        and merged[0]["severity"] == "error"
        and merged[0]["fix"] == "b"
    )


def test_grounding_helpers_are_api_free():
    narration = [{"start": 1, "end": 3, "narration": "测试。"}]
    vlm = [
        {
            "scene_id": 1,
            "start": 0,
            "end": 4,
            "description": "门口对峙",
            "frame_facts": {"1.0": ["男子握拳"]},
        }
    ]
    asr = [{"start": 1, "end": 2, "text": "站住"}]

    bundle = evidence_bundle.build_evidence_bundle(
        vlm, asr, narration, research={"characters": {"甲": "背景"}}
    )
    ranges = bundle["coverage"]["selected_ranges"]
    filtered = evidence_bundle.filter_evidence_by_ranges(vlm, asr, ranges)
    assert [item["source"] for item in filtered["items"]] == ["visual", "asr"]

    assert bundle["metadata"]["reviewed_scene_count"] == 1
    assert "门口对峙" in evidence_bundle.render_evidence_bundle(bundle)


def test_review_narration_chunks_large_evidence_and_merges(monkeypatch, tmp_path):
    vlm = [
        {
            "scene_id": i,
            "start": i * 10.0,
            "end": i * 10.0 + 8.0,
            "description": f"scene{i}",
        }
        for i in range(130)
    ]
    _seed_work_dir(
        tmp_path, [{"start": 0, "end": 980, "narration": "全片复盘。"}], vlm=vlm
    )

    def chunk_response(idx):
        return json.dumps(
            {
                "verdict": "REVISE",
                "summary": "chunk",
                "findings": [
                    {
                        "segment": idx,
                        "severity": "warning",
                        "category": "grounding_risk",
                        "issue": f"issue{idx}",
                        "fix": "fix",
                    }
                ],
            },
            ensure_ascii=False,
        )

    payloads = _capture_api(monkeypatch, chunk_response)
    out = review_runner.review_narration(tmp_path)
    assert len(payloads) > 1
    assert out["chunked_review"]["chunk_count"] == len(payloads)
    assert len(out["findings"]) == len(payloads)
    assert out["evidence_contract"]["chunk_count"] == len(payloads)
    assert out["evidence_contract"]["selected_ranges"]


def test_duplicate_source_clip_backrefs_remain_distinguishable():
    vlm = [{"scene_id": 1, "start": 10, "end": 20, "description": "同一源片段"}]
    asr = [{"start": 12, "end": 14, "text": "同一句对白"}]
    spans = [
        {
            "source_start": 10,
            "source_end": 20,
            "output_start": 0,
            "output_end": 10,
            "source_id": "0",
            "source_clip_id": "clip-a",
            "output_segment_index": 0,
        },
        {
            "source_start": 10,
            "source_end": 20,
            "output_start": 30,
            "output_end": 40,
            "source_id": "0",
            "source_clip_id": "clip-b",
            "output_segment_index": 1,
        },
    ]
    rv, ra = review_grounding.remap_grounding_to_output_timeline(vlm, asr, spans)
    assert len(rv) == 2 and len(ra) == 2
    assert {x["source_clip_id"] for x in rv} == {"clip-a", "clip-b"}
    assert {x["output_segment_index"] for x in ra} == {0, 1}
    bundle = evidence_bundle.build_evidence_bundle(
        rv, ra, [{"start": 0, "end": 40, "narration": "测试"}], timeline="cut_output"
    )
    rendered = evidence_bundle.render_evidence_bundle(bundle)
    assert "clip#0" in rendered and "clip#1" in rendered


def test_build_review_messages_bad_style_json_keeps_context_title(tmp_path):
    (tmp_path / "style_card.json").write_text("{bad json", encoding="utf-8")

    content = review_response.build_review_messages(
        [{"start": 0, "end": 1, "narration": "测试。"}], [], [], work_dir=tmp_path
    )[0]["content"]

    assert "style_card.json（表达意图/语气/节奏/禁忌，不负责包装，可能为空）\n(无)" in content


def test_parse_review_clamps_craft_categories_to_warning_but_keeps_factual_errors():
    craft_categories = [
        "disjoint_handoff",
        "ai_flavor",
        "weak_payoff",
        "style_mismatch",
        "packaging_mismatch",
        "example_entity_leak",
    ]
    parsed = _parse(
        {
            "verdict": "FAIL",
            "summary": "s",
            "findings": [
                {
                    "segment": 0,
                    "severity": "error",
                    "category": category,
                    "issue": category,
                    "fix": "fix",
                }
                for category in craft_categories
            ]
            + [
                {
                    "segment": 1,
                    "severity": "error",
                    "category": "hallucination",
                    "issue": "fact",
                    "fix": "fix",
                },
                {
                    "segment": 2,
                    "severity": "error",
                    "category": "incomplete",
                    "issue": "cut",
                    "fix": "fix",
                },
            ],
        }
    )
    severities = {item["category"]: item["severity"] for item in parsed["findings"]}

    assert all(severities[category] == "warning" for category in craft_categories)
    assert severities["hallucination"] == "error"
    assert severities["incomplete"] == "error"


def test_single_source_review_grounds_on_asr_clean_like_multi_source(tmp_path):
    _write_json(tmp_path / "asr_result.json", [{"start": 0, "end": 5, "text": "原始转写"}])
    _write_json(
        tmp_path / "asr_clean.json",
        {"segments": [{"start": 0, "end": 5, "text": "清洗后的台词"}]},
    )
    _, asr = review_grounding._load_review_grounding(tmp_path)
    assert [row["text"] for row in asr] == ["清洗后的台词"]


def _original_audio_plan(tmp_path):
    # Padding pushes the original-dialogue beat past the clipped board JSON, which is how a
    # real review lost it and called the beat "skipped".
    filler = [
        {
            "beat_id": f"n{i:02d}",
            "source_start": i * 2.0,
            "source_end": i * 2.0 + 2.0,
            "audio_owner": "narration",
            "narration_job": "context",
            "handoff": "旁白先补关系，原声/动作发生时完全让位；下一拍承接人物反应。" * 3,
        }
        for i in range(12)
    ]
    _write_json(
        tmp_path / "visual_audio_board.json",
        {
            "items": filler
            + [
                {
                    "beat_id": "b05",
                    "source_start": 40.0,
                    "source_end": 46.0,
                    "output_start": 10.0,
                    "output_end": 16.0,
                    "audio_owner": "original_dialogue",
                    "original_audio_anchor": "原声“大嫂”",
                    "narration_job": "none",
                },
                {
                    "beat_id": "b06",
                    "source_start": 46.0,
                    "source_end": 50.0,
                    "audio_owner": "narration",
                    "narration_job": "context",
                },
            ]
        },
    )
    _write_json(
        tmp_path / "recap_story_plan.json",
        {
            "beats": [
                # The board says narration owns b06: the board wins over a stale plan.
                {"beat_id": "b06", "source_start": 46.0, "source_end": 50.0,
                 "audio_owner": "silence"},
                # A plan-only beat that names its own owner still counts.
                {"beat_id": "b07", "source_start": 50.0, "source_end": 53.0,
                 "audio_owner": "action_sound", "must_keep_moment": "枪声"},
                {"beat_id": "b08", "source_start": 53.0, "source_end": 55.0},
            ]
        },
    )
    _write_json(
        tmp_path / "original_subtitles.json",
        [{"start": 11.0, "end": 12.5, "text": "大嫂。"}],
    )


def test_review_prompt_lists_every_beat_left_to_original_audio(tmp_path):
    _original_audio_plan(tmp_path)
    content = review_response.build_review_messages(
        [{"start": 0, "end": 3, "narration": "测试。"}], [], [], work_dir=tmp_path
    )[0]["content"]

    board = content.split("## visual_audio_board.json", 1)[1].split("\n## ", 1)[0]
    assert "b05" not in board  # clipped out of the raw JSON context
    holds = content.split("## 计划内留给原声的区间（不是漏写）", 1)[1].split("\n## ", 1)[0]
    assert "[SOURCE 40.0-46.0s] beat b05 audio_owner=original_dialogue narration_job=none" in holds
    assert "原声“大嫂”" in holds
    assert "beat b07 audio_owner=action_sound" in holds and "枪声" in holds
    assert "b06" not in holds and "b08" not in holds and "n00" not in holds
    assert "[SOURCE 11.0-12.5s] 原声字幕块「大嫂。」" in holds
    assert "不要报为跳过、缺失或漏写" in holds
    assert "某拍没有旁白不算 incomplete" in content


def test_cut_output_review_lists_original_audio_beats_on_the_output_clock(tmp_path):
    _original_audio_plan(tmp_path)
    bundle = evidence_bundle.build_evidence_bundle(
        [], [], [{"start": 0, "end": 3, "narration": "测试。"}], timeline="cut_output"
    )
    content = review_response.build_review_messages(
        [{"start": 0, "end": 3, "narration": "测试。"}],
        [],
        [],
        work_dir=tmp_path,
        evidence_bundle=bundle,
    )[0]["content"]

    assert "[OUTPUT 10.0-16.0s] beat b05" in content
    # A plan beat with only source times keeps its own clock label instead of posing as output.
    assert "[SOURCE 50.0-53.0s] beat b07" in content
    assert "[OUTPUT 11.0-12.5s] 原声字幕块「大嫂。」" in content


def test_review_prompt_omits_original_audio_section_without_such_beats(tmp_path):
    content = review_response.build_review_messages(
        [{"start": 0, "end": 3, "narration": "测试。"}], [], [], work_dir=tmp_path
    )[0]["content"]
    assert "计划内留给原声的区间" not in content
