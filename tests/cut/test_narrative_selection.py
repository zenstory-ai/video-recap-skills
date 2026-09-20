"""Required-evidence contracts are judged on retained source spans, not on prose."""
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "video-cut" / "scripts"))

from narrative_selection import check_required_evidence


def _node(source_path, **overrides):
    node = {"id": "premise", "track": "audio", "content": "请求与拒绝",
            "source": str(source_path), "start": 10.0, "end": 12.0}
    node.update(overrides)
    return node


def _clip(source_start, source_end, output_start, *, source_path=None, source_id=None):
    clip = {"source_start": source_start, "source_end": source_end, "output_start": output_start,
            "output_end": output_start + source_end - source_start}
    if source_path is not None:
        clip["source_path"] = str(source_path)
    if source_id is not None:
        clip["source_id"] = source_id
    return clip


def _check(contract, clips, source, *, source_audio=True):
    return check_required_evidence(contract, {"clips": clips}, input_video=source,
                                   source_audio={str(source.resolve()): source_audio})


def _ordered_contract(source, premise, result):
    return {"nodes": [_node(source, id="premise", start=premise[0], end=premise[1]),
                      _node(source, id="result", track="video", content="回应",
                            start=result[0], end=result[1])],
            "before": [["premise", "result"]]}


def test_accepts_complete_contiguous_node_split_across_clips(tmp_path):
    source = tmp_path / "episode.mp4"
    report = _check({"nodes": [_node(source)], "before": []},
                    [_clip(8.0, 11.0, 0.0), _clip(11.0, 14.0, 3.0)], source)
    assert report["selection_status"] == "PASS"
    assert report["semantic_status"] == "NOT_CHECKED"
    assert report["findings"] == []
    assert report["nodes"] == [{**_node(source), "source": str(source.resolve()),
                                "occurrences": [{"start": 2.0, "end": 4.0}]}]


@pytest.mark.parametrize("clips", [
    pytest.param([dict(_clip(11.0, 12.0, 0.0), reason="完整保留请求与拒绝", title="请求与拒绝")],
                 id="partial_span_with_reason_and_title"),
    pytest.param([_clip(10.0, 11.0, 0.0), _clip(11.0, 12.0, 1.001)], id="output_hole"),
    pytest.param([_clip(11.0, 12.0, 0.0), _clip(10.0, 11.0, 1.0)], id="reordered"),
    pytest.param([_clip(10.0 + 1 / 30, 12.0, 0.0)], id="one_frame_short"),
])
def test_incomplete_source_span_is_missing_evidence(tmp_path, clips):
    source = tmp_path / "episode.mp4"
    report = _check({"nodes": [_node(source)], "before": []}, clips, source)
    assert report["selection_status"] == "BLOCK"
    assert report["nodes"][0]["occurrences"] == []
    assert {f["code"] for f in report["findings"]} == {"REQUIRED_EVIDENCE_MISSING"}


def test_audio_requirement_blocks_when_source_has_no_audio(tmp_path):
    source = tmp_path / "silent.mp4"
    report = _check({"nodes": [_node(source)], "before": []}, [_clip(10.0, 12.0, 0.0)],
                    source, source_audio=False)
    assert report["selection_status"] == "BLOCK"
    assert report["nodes"][0]["occurrences"] == []
    assert report["findings"][0]["code"] == "REQUIRED_EVIDENCE_AUDIO_UNAVAILABLE"


def test_video_node_does_not_require_source_audio(tmp_path):
    source = tmp_path / "silent.mp4"
    report = _check({"nodes": [_node(source, track="video", content="关键反应")], "before": []},
                    [_clip(10.0, 12.0, 0.0)], source, source_audio=False)
    assert report["selection_status"] == "PASS"


@pytest.mark.parametrize("premise,result,clips,expected_status,result_occurrences", [
    pytest.param((1.0, 2.0), (5.0, 6.0), [(5.0, 6.0, 0.0), (1.0, 2.0, 1.0), (5.0, 6.0, 2.0)],
                 "BLOCK", [{"start": 0.0, "end": 1.0}, {"start": 2.0, "end": 3.0}],
                 id="repeated_result_before_premise"),
    pytest.param((1.0, 2.0), (5.0, 6.0), [(1.0, 2.0, 0.0), (5.0, 6.0, 1.0), (5.0, 6.0, 2.0)],
                 "PASS", [{"start": 1.0, "end": 2.0}, {"start": 2.0, "end": 3.0}],
                 id="one_premise_precedes_every_repeat"),
    pytest.param((5.0, 7.0), (10.0, 12.0), [(11.0, 12.0, 0.0), (5.0, 7.0, 1.0), (10.0, 12.0, 3.0)],
                 "BLOCK", [{"start": 3.0, "end": 5.0}], id="early_partial_result"),
    pytest.param((5.0, 7.0), (10.0, 12.0), [(5.0, 7.0, 0.0), (11.0, 12.0, 2.0), (10.0, 12.0, 3.0)],
                 "PASS", [{"start": 3.0, "end": 5.0}], id="partial_and_complete_after_premise"),
])
def test_before_needs_a_complete_premise_ahead_of_every_result_fragment(
    tmp_path, premise, result, clips, expected_status, result_occurrences
):
    source = tmp_path / "episode.mp4"
    report = _check(_ordered_contract(source, premise, result), [_clip(*c) for c in clips], source)
    assert report["selection_status"] == expected_status
    assert report["nodes"][1]["occurrences"] == result_occurrences
    codes = {f["code"] for f in report["findings"]}
    assert codes == ({"REQUIRED_EVIDENCE_ORDER"} if expected_status == "BLOCK" else set())


def test_source_id_disambiguates_same_real_path(tmp_path):
    source = tmp_path / "shared.mp4"
    report = check_required_evidence(
        {"nodes": [_node(source, track="video", source_id="take-a")], "before": []},
        {"clips": [_clip(10.0, 12.0, 0.0, source_path=source, source_id="take-b")]},
        input_video=source, source_audio={str(source.resolve()): True})
    assert report["selection_status"] == "BLOCK"


def test_node_without_source_id_cannot_stitch_different_source_identities(tmp_path):
    source = tmp_path / "shared.mp4"
    report = check_required_evidence(
        {"nodes": [_node(source, track="video")], "before": []},
        {"clips": [_clip(10.0, 11.0, 0.0, source_path=source, source_id="take-a"),
                   _clip(11.0, 12.0, 1.0, source_path=source, source_id="take-b")]},
        input_video=source, source_audio={})
    assert report["selection_status"] == "BLOCK"
    assert report["nodes"][0]["occurrences"] == []


def test_realpath_matching_accepts_an_absolute_symlink(tmp_path):
    source = tmp_path / "episode.mp4"
    source.touch()
    alias = tmp_path / "alias.mp4"
    alias.symlink_to(source)
    report = check_required_evidence(
        {"nodes": [_node(alias, track="video")], "before": []},
        {"clips": [_clip(10.0, 12.0, 0.0)]},
        input_video=source, source_audio={str(source.resolve()): False})
    assert report["selection_status"] == "PASS"


@pytest.mark.parametrize("make_contract", [
    pytest.param(lambda s: None, id="null"),
    pytest.param(lambda s: {}, id="empty"),
    pytest.param(lambda s: {"nodes": [], "before": []}, id="no_nodes"),
    pytest.param(lambda s: {"nodes": [_node(s, id="")], "before": []}, id="empty_id"),
    pytest.param(lambda s: {"nodes": [_node(s), _node(s)], "before": []}, id="duplicate_id"),
    pytest.param(lambda s: {"nodes": [_node(s, source="relative.mp4")], "before": []}, id="relative_source"),
    pytest.param(lambda s: {"nodes": [_node(s, start=True)], "before": []}, id="bool_start"),
    pytest.param(lambda s: {"nodes": [_node(s, end=math.inf)], "before": []}, id="infinite_end"),
    pytest.param(lambda s: {"nodes": [_node(s, content="")], "before": []}, id="empty_content"),
    pytest.param(lambda s: {"nodes": [_node(s, track="subtitle")], "before": []}, id="unknown_track"),
    pytest.param(lambda s: {"nodes": [_node(s)], "before": [["unknown", "premise"]]}, id="unknown_edge_id"),
    pytest.param(lambda s: {"nodes": [_node(s)], "before": ["premise", "premise"]}, id="edge_not_pair"),
])
def test_invalid_contracts_block_instead_of_raising(tmp_path, make_contract):
    source = tmp_path / "episode.mp4"
    report = _check(make_contract(source), [_clip(10.0, 12.0, 0.0)], source)
    assert report["selection_status"] == "BLOCK"
    assert report["semantic_status"] == "NOT_CHECKED"
    assert report["findings"][0]["code"] == "REQUIRED_EVIDENCE_INVALID"
