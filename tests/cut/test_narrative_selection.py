import math
import sys
from pathlib import Path


sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "skills" / "video-cut" / "scripts")
)

from narrative_selection import check_required_evidence


def _node(source_path, **overrides):
    node = {
        "id": "premise",
        "track": "audio",
        "content": "请求与拒绝",
        "source": str(source_path),
        "start": 10.0,
        "end": 12.0,
    }
    node.update(overrides)
    return node


def _clip(source_start, source_end, output_start, *, source_path=None, source_id=None):
    clip = {
        "source_start": source_start,
        "source_end": source_end,
        "output_start": output_start,
        "output_end": output_start + source_end - source_start,
    }
    if source_path is not None:
        clip["source_path"] = str(source_path)
    if source_id is not None:
        clip["source_id"] = source_id
    return clip


def _check(contract, clips, source, *, source_audio=True):
    return check_required_evidence(
        contract,
        {"clips": clips},
        input_video=source,
        source_audio={str(source.resolve()): source_audio},
    )


def test_accepts_complete_contiguous_node_split_across_clips(tmp_path):
    source = tmp_path / "episode.mp4"
    contract = {"nodes": [_node(source)], "before": []}
    report = _check(
        contract,
        [_clip(8.0, 11.0, 0.0), _clip(11.0, 14.0, 3.0)],
        source,
    )

    assert report["selection_status"] == "PASS"
    assert report["semantic_status"] == "NOT_CHECKED"
    assert report["findings"] == []
    assert report["nodes"] == [
        {
            **_node(source),
            "source": str(source.resolve()),
            "occurrences": [{"start": 2.0, "end": 4.0}],
        }
    ]


def test_reason_or_title_cannot_replace_missing_source_span(tmp_path):
    source = tmp_path / "episode.mp4"
    clip = _clip(11.0, 12.0, 0.0)
    clip.update(reason="完整保留请求与拒绝", title="请求与拒绝")

    report = _check({"nodes": [_node(source)], "before": []}, [clip], source)

    assert report["selection_status"] == "BLOCK"
    assert report["nodes"][0]["occurrences"] == []
    assert {finding["code"] for finding in report["findings"]} == {
        "REQUIRED_EVIDENCE_MISSING"
    }


def test_rejects_source_continuity_with_output_hole_or_reordering(tmp_path):
    source = tmp_path / "episode.mp4"
    contract = {"nodes": [_node(source)], "before": []}

    hole = _check(
        contract,
        [_clip(10.0, 11.0, 0.0), _clip(11.0, 12.0, 1.001)],
        source,
    )
    reordered = _check(
        contract,
        [_clip(11.0, 12.0, 0.0), _clip(10.0, 11.0, 1.0)],
        source,
    )

    assert hole["selection_status"] == "BLOCK"
    assert reordered["selection_status"] == "BLOCK"


def test_does_not_tolerate_a_missing_frame_sized_boundary(tmp_path):
    source = tmp_path / "episode.mp4"
    report = _check(
        {"nodes": [_node(source)], "before": []},
        [_clip(10.0 + 1 / 30, 12.0, 0.0)],
        source,
    )

    assert report["selection_status"] == "BLOCK"


def test_audio_requirement_blocks_when_source_has_no_audio(tmp_path):
    source = tmp_path / "silent.mp4"
    report = _check(
        {"nodes": [_node(source)], "before": []},
        [_clip(10.0, 12.0, 0.0)],
        source,
        source_audio=False,
    )

    assert report["selection_status"] == "BLOCK"
    assert report["nodes"][0]["occurrences"] == []
    assert report["findings"][0]["code"] == "REQUIRED_EVIDENCE_AUDIO_UNAVAILABLE"


def test_video_node_does_not_require_source_audio(tmp_path):
    source = tmp_path / "silent.mp4"
    node = _node(source, track="video", content="关键反应")

    report = _check(
        {"nodes": [node], "before": []},
        [_clip(10.0, 12.0, 0.0)],
        source,
        source_audio=False,
    )

    assert report["selection_status"] == "PASS"


def test_every_repeated_result_needs_an_earlier_complete_premise(tmp_path):
    source = tmp_path / "episode.mp4"
    premise = _node(source, id="premise", start=1.0, end=2.0)
    result = _node(
        source,
        id="result",
        track="video",
        content="宣言",
        start=5.0,
        end=6.0,
    )
    contract = {"nodes": [premise, result], "before": [["premise", "result"]]}
    clips = [
        _clip(5.0, 6.0, 0.0),
        _clip(1.0, 2.0, 1.0),
        _clip(5.0, 6.0, 2.0),
    ]

    report = _check(contract, clips, source)

    assert report["selection_status"] == "BLOCK"
    assert report["nodes"][1]["occurrences"] == [
        {"start": 0.0, "end": 1.0},
        {"start": 2.0, "end": 3.0},
    ]
    assert any(f["code"] == "REQUIRED_EVIDENCE_ORDER" for f in report["findings"])


def test_one_complete_premise_can_precede_every_repeated_result(tmp_path):
    source = tmp_path / "episode.mp4"
    premise = _node(source, id="premise", start=1.0, end=2.0)
    result = _node(
        source,
        id="result",
        track="video",
        content="宣言",
        start=5.0,
        end=6.0,
    )
    report = _check(
        {"nodes": [premise, result], "before": [["premise", "result"]]},
        [
            _clip(1.0, 2.0, 0.0),
            _clip(5.0, 6.0, 1.0),
            _clip(5.0, 6.0, 2.0),
        ],
        source,
    )

    assert report["selection_status"] == "PASS"
    assert report["findings"] == []


def test_before_rejects_an_early_partial_result_even_when_a_later_one_is_complete(
    tmp_path,
):
    source = tmp_path / "episode.mp4"
    premise = _node(source, id="premise", start=5.0, end=7.0)
    result = _node(
        source,
        id="result",
        track="video",
        content="回应",
        start=10.0,
        end=12.0,
    )
    report = _check(
        {"nodes": [premise, result], "before": [["premise", "result"]]},
        [
            _clip(11.0, 12.0, 0.0),
            _clip(5.0, 7.0, 1.0),
            _clip(10.0, 12.0, 3.0),
        ],
        source,
    )

    assert report["nodes"][1]["occurrences"] == [{"start": 3.0, "end": 5.0}]
    assert report["selection_status"] == "BLOCK"
    assert any(f["code"] == "REQUIRED_EVIDENCE_ORDER" for f in report["findings"])


def test_before_allows_partial_and_complete_result_fragments_after_the_premise(
    tmp_path,
):
    source = tmp_path / "episode.mp4"
    premise = _node(source, id="premise", start=5.0, end=7.0)
    result = _node(
        source,
        id="result",
        track="video",
        content="回应",
        start=10.0,
        end=12.0,
    )
    report = _check(
        {"nodes": [premise, result], "before": [["premise", "result"]]},
        [
            _clip(5.0, 7.0, 0.0),
            _clip(11.0, 12.0, 2.0),
            _clip(10.0, 12.0, 3.0),
        ],
        source,
    )

    assert report["selection_status"] == "PASS"
    assert report["findings"] == []


def test_node_without_before_is_a_valid_cold_open(tmp_path):
    source = tmp_path / "episode.mp4"
    result = _node(source, id="result", track="video", content="冷开宣言")

    report = _check(
        {"nodes": [result], "before": []},
        [_clip(10.0, 12.0, 0.0)],
        source,
        source_audio=False,
    )

    assert report["selection_status"] == "PASS"


def test_source_id_disambiguates_same_real_path(tmp_path):
    source = tmp_path / "shared.mp4"
    node = _node(source, track="video", source_id="take-a")
    report = check_required_evidence(
        {"nodes": [node], "before": []},
        {
            "clips": [
                _clip(
                    10.0,
                    12.0,
                    0.0,
                    source_path=source,
                    source_id="take-b",
                )
            ]
        },
        input_video=source,
        source_audio={str(source.resolve()): True},
    )

    assert report["selection_status"] == "BLOCK"


def test_node_without_source_id_cannot_stitch_different_source_identities(tmp_path):
    source = tmp_path / "shared.mp4"
    report = check_required_evidence(
        {"nodes": [_node(source, track="video")], "before": []},
        {
            "clips": [
                _clip(
                    10.0,
                    11.0,
                    0.0,
                    source_path=source,
                    source_id="take-a",
                ),
                _clip(
                    11.0,
                    12.0,
                    1.0,
                    source_path=source,
                    source_id="take-b",
                ),
            ]
        },
        input_video=source,
        source_audio={},
    )

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
        input_video=source,
        source_audio={str(source.resolve()): False},
    )

    assert report["selection_status"] == "PASS"


def test_invalid_contracts_block_instead_of_raising(tmp_path):
    source = tmp_path / "episode.mp4"
    invalid_contracts = [
        None,
        {},
        {"nodes": [], "before": []},
        {"nodes": [_node(source, id="")], "before": []},
        {"nodes": [_node(source), _node(source)], "before": []},
        {"nodes": [_node(source, source="relative.mp4")], "before": []},
        {"nodes": [_node(source, start=True)], "before": []},
        {"nodes": [_node(source, end=math.inf)], "before": []},
        {"nodes": [_node(source, content="")], "before": []},
        {"nodes": [_node(source, track="subtitle")], "before": []},
        {"nodes": [_node(source)], "before": [["unknown", "premise"]]},
        {"nodes": [_node(source)], "before": ["premise", "premise"]},
    ]

    for contract in invalid_contracts:
        report = _check(contract, [_clip(10.0, 12.0, 0.0)], source)
        assert report["selection_status"] == "BLOCK", contract
        assert report["semantic_status"] == "NOT_CHECKED"
        assert report["findings"][0]["code"] == "REQUIRED_EVIDENCE_INVALID"
