import importlib.util
import json
import math
from fractions import Fraction
from pathlib import Path

import pytest


MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "video-assemble"
    / "scripts"
    / "subtitles"
    / "track.py"
)
SPEC = importlib.util.spec_from_file_location("subtitle_track_standalone", MODULE_PATH)
subtitle_track = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(subtitle_track)


PICTURE_PATH = "/media/picture.mp4"
EDIT_PLAN = "/media/edit_plan.json"
AUDIO_FACTS = {"selected_stream": 1, "sample_rate": 48000, "packet_count": 470}


def _cue(**overrides):
    cue = {
        "start_tick": 24,
        "end_tick": 48,
        "text": "A precise subtitle",
        "attribution": {"kind": "narration", "ref": "narration:1"},
        "timing_evidence": {
            "kind": "word_timestamps",
            "evidence_refs": ["aligner:words:1"],
            "calibration": "none",
            "word_alignment": "asr_words",
        },
    }
    cue.update(overrides)
    return cue


def _track(*, cues=None):
    return {
        "schema_version": 1,
        "clock": {
            "kind": "output",
            "timebase": {"numerator": 1, "denominator": 24},
            "duration_ticks": 240,
        },
        "overlap_policy": "forbid",
        "bindings": {
            "picture": {"path": PICTURE_PATH, "edit_plan": EDIT_PLAN},
            "audio": dict(AUDIO_FACTS),
        },
        "cues": [_cue()] if cues is None else cues,
    }


def _load(track=None, **overrides):
    kwargs = {
        "expected_picture_identity": {"path": PICTURE_PATH, "edit_plan": EDIT_PLAN},
        "expected_audio_identity": dict(AUDIO_FACTS),
        "expected_duration_seconds": 10,
    }
    kwargs.update(overrides)
    return subtitle_track.load_subtitle_track(track or _track(), **kwargs)


def test_loads_path_and_returns_render_ready_seconds_without_mutating_cues(tmp_path):
    path = tmp_path / "subtitle_track.json"
    path.write_text(json.dumps(_track()), encoding="utf-8")

    loaded = _load(path)

    assert loaded["entries"] == [
        {
            "start": 1.0,
            "end": 2.0,
            "text": "A precise subtitle",
            "source": "narration",
            "source_ref": "narration:1",
            "timing_evidence": {
                "kind": "word_timestamps",
                "evidence_refs": ["aligner:words:1"],
                "calibration": "none",
                "word_alignment": "asr_words",
            },
        }
    ]
    assert loaded["metadata"]["clock"]["duration_seconds"] == 10.0
    assert loaded["metadata"]["timing_evidence_kinds"] == ["word_timestamps"]


def test_accepts_exact_fraction_duration_from_stream_timebase_probe():
    track = _track()
    track["clock"]["duration_ticks"] = 140

    loaded = _load(track, expected_duration_seconds=Fraction(140, 24))

    assert loaded["metadata"]["clock"]["duration_ticks"] == 140


def test_cue_is_not_visible_before_its_start_tick():
    track = _track(
        cues=[
            _cue(
                start_tick=30,
                end_tick=90,
                text="calibrated cue",
                timing_evidence={
                    "kind": "asr_boundary_calibrated",
                    "evidence_refs": ["alignment-run:example"],
                    "calibration": "asr_energy",
                    "word_alignment": "none",
                },
            )
        ]
    )
    track["clock"]["timebase"] = {"numerator": 1, "denominator": 30}
    track["clock"]["duration_ticks"] = 300

    entry = _load(track)["entries"][0]

    assert entry["start"] == pytest.approx(30 / 30)
    assert entry["end"] == pytest.approx(90 / 30)
    assert not (entry["start"] <= 25 / 30 < entry["end"])
    assert entry["timing_evidence"]["kind"] == "asr_boundary_calibrated"


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"expected_picture_identity": {"path": "/media/other.mp4", "edit_plan": EDIT_PLAN}}, "picture"),
        ({"expected_picture_identity": {"path": PICTURE_PATH, "edit_plan": "/media/other.json"}}, "edit"),
        ({"expected_audio_identity": {**AUDIO_FACTS, "packet_count": 471}}, "packet_count"),
        ({"expected_audio_identity": {**AUDIO_FACTS, "sample_rate": 44100}}, "sample_rate"),
        ({"expected_audio_identity": {**AUDIO_FACTS, "selected_stream": 2}}, "selected_stream"),
        ({"expected_duration_seconds": 9.5}, "duration"),
    ],
)
def test_rejects_stale_picture_edit_audio_stream_or_duration_binding(override, message):
    with pytest.raises(subtitle_track.SubtitleTrackError, match=message):
        _load(**override)


def test_track_edit_plan_requires_independent_caller_edit_plan():
    with pytest.raises(subtitle_track.SubtitleTrackError, match="edit"):
        _load(expected_picture_identity={"path": PICTURE_PATH})


def test_legacy_digest_keys_in_bindings_are_ignored():
    track = _track()
    track["bindings"]["picture"].update(sha256="1" * 64, edit_sha256="2" * 64)
    track["bindings"]["audio"]["sha256"] = "3" * 64

    loaded = _load(track)

    assert loaded["metadata"]["bindings"] == {
        "picture": {"path": str(Path(PICTURE_PATH).resolve()),
                    "edit_plan": str(Path(EDIT_PLAN).resolve())},
        "audio": dict(AUDIO_FACTS),
    }


@pytest.mark.parametrize("schema_version", [0, 2, "1", 1.0, True])
def test_rejects_unknown_or_malformed_schema_versions(schema_version):
    track = _track()
    track["schema_version"] = schema_version

    with pytest.raises(subtitle_track.SubtitleTrackError, match="schema_version"):
        _load(track)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t["clock"].update(kind="source"),
        lambda t: t["clock"]["timebase"].update(denominator=0),
        lambda t: t.update(overlap_policy="allow"),
        lambda t: t["cues"][0].update(start_tick=-1),
        lambda t: t["cues"][0].update(end_tick=24),
        lambda t: t["cues"][0].update(end_tick=241),
        lambda t: t["cues"][0].update(text=" \n "),
        lambda t: t["cues"][0].update(start_tick=1.5),
        lambda t: t["bindings"]["audio"].update(selected_stream=True),
        lambda t: t["cues"][0]["timing_evidence"].update(kind=[]),
    ],
)
def test_rejects_malformed_or_illegal_fields(mutate):
    track = _track()
    mutate(track)

    with pytest.raises(subtitle_track.SubtitleTrackError):
        _load(track)


def test_rejects_unknown_fields_in_strict_v1_schema():
    track = _track()
    track["cues"][0]["confidence"] = 0.99

    with pytest.raises(subtitle_track.SubtitleTrackError, match="unknown field"):
        _load(track)


def test_rejects_overlapping_or_out_of_order_cues_fail_closed():
    track = _track(cues=[_cue(start_tick=48, end_tick=72), _cue(start_tick=36, end_tick=60)])

    with pytest.raises(subtitle_track.SubtitleTrackError, match="overlap|order"):
        _load(track)


def test_half_open_touching_cues_are_valid():
    track = _track(cues=[_cue(start_tick=0, end_tick=24), _cue(start_tick=24, end_tick=48)])

    assert [(e["start"], e["end"]) for e in _load(track)["entries"]] == [(0.0, 1.0), (1.0, 2.0)]


def test_strict_caller_rejects_legacy_estimate_but_compatibility_keeps_label():
    estimate = _cue(
        timing_evidence={
            "kind": "legacy_estimate",
            "evidence_refs": [],
            "calibration": "none",
            "word_alignment": "none",
        }
    )
    track = _track(cues=[estimate])

    compatible = _load(track)
    assert compatible["entries"][0]["timing_evidence"]["kind"] == "legacy_estimate"
    assert compatible["metadata"]["timing_evidence_kinds"] == ["legacy_estimate"]
    with pytest.raises(subtitle_track.SubtitleTrackError, match="legacy_estimate"):
        _load(track, reject_legacy_estimate=True)


@pytest.mark.parametrize(
    "timing_evidence",
    [
        {
            "kind": "human_verified",
            "evidence_refs": [],
            "calibration": "human_boundary",
            "word_alignment": "none",
        },
        {
            "kind": "word_timestamps",
            "evidence_refs": ["asr:1"],
            "calibration": "none",
            "word_alignment": "none",
        },
        {
            "kind": "asr_boundary_calibrated",
            "evidence_refs": ["asr:1", "energy:1"],
            "calibration": "asr_energy",
            "word_alignment": "asr_words",
        },
        {
            "kind": "legacy_estimate",
            "evidence_refs": [],
            "calibration": "human_boundary",
            "word_alignment": "none",
        },
    ],
)
def test_evidence_kind_does_not_overclaim_calibration_or_word_alignment(timing_evidence):
    with pytest.raises(subtitle_track.SubtitleTrackError, match="timing_evidence"):
        _load(_track(cues=[_cue(timing_evidence=timing_evidence)]))


def test_expected_duration_must_be_finite():
    with pytest.raises(subtitle_track.SubtitleTrackError, match="duration"):
        _load(expected_duration_seconds=math.nan)


def test_declared_duration_must_be_representable_as_finite_renderer_seconds():
    track = _track(cues=[])
    track["clock"]["timebase"]["numerator"] = 10**400

    with pytest.raises(subtitle_track.SubtitleTrackError, match="finite renderer seconds"):
        _load(track, expected_duration_seconds=Fraction(240 * 10**400, 24))


def test_rejects_cue_whose_distinct_ticks_collapse_to_same_renderer_float():
    start_tick = 2**56
    track = _track(cues=[_cue(start_tick=start_tick, end_tick=start_tick + 1)])
    track["clock"]["duration_ticks"] = start_tick + 2

    with pytest.raises(subtitle_track.SubtitleTrackError, match="float projection"):
        _load(track, expected_duration_seconds=Fraction(start_tick + 2, 24))
