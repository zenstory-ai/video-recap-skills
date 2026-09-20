"""Strict loader for versioned, output-clock subtitle tracks.

This module intentionally uses only the Python standard library so it can be
loaded by the independently distributed ``video-assemble`` skill.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from decimal import Decimal
from fractions import Fraction
from pathlib import Path


SCHEMA_VERSION = 1

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_EVIDENCE_KINDS = frozenset(
    {
        "human_verified",
        "word_timestamps",
        "asr_boundary_calibrated",
        "legacy_estimate",
    }
)
_CALIBRATION_KINDS = frozenset({"none", "asr_energy", "human_boundary"})
_WORD_ALIGNMENT_KINDS = frozenset({"none", "asr_words", "human_words"})


class SubtitleTrackError(ValueError):
    """The subtitle track is malformed, stale, or incompatible with its caller."""


def _fail(path, message):
    raise SubtitleTrackError(f"{path}: {message}")


def _mapping(value, path):
    if not isinstance(value, Mapping):
        _fail(path, "must be an object")
    return value


def _strict_fields(value, *, required, optional=(), path):
    value = _mapping(value, path)
    keys = set(value)
    missing = set(required) - keys
    unknown = keys - set(required) - set(optional)
    if missing:
        _fail(path, f"missing field(s): {', '.join(sorted(missing))}")
    if unknown:
        _fail(path, f"unknown field(s): {', '.join(sorted(unknown))}")
    return value


def _integer(value, path, *, minimum=None):
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(path, "must be an integer")
    if minimum is not None and value < minimum:
        _fail(path, f"must be >= {minimum}")
    return value


def _nonempty_string(value, path):
    if not isinstance(value, str) or not value.strip():
        _fail(path, "must be a nonempty string")
    return value


def _sha256(value, path):
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        _fail(path, "must be a lowercase 64-hex sha256 digest")
    return value


def _seconds_fraction(value, path):
    if isinstance(value, bool):
        _fail(path, "duration must be finite and numeric")
    if isinstance(value, Fraction):
        result = value
    elif isinstance(value, Decimal):
        if not value.is_finite():
            _fail(path, "duration must be finite")
        result = Fraction(value)
    elif isinstance(value, int):
        result = Fraction(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            _fail(path, "duration must be finite")
        result = Fraction(str(value))
    else:
        _fail(path, "duration must be an int, float, Decimal, or Fraction")
    if result < 0:
        _fail(path, "duration must be nonnegative")
    return result


def _load_document(path_or_mapping):
    if isinstance(path_or_mapping, Mapping):
        return path_or_mapping
    try:
        path = Path(path_or_mapping)
    except TypeError as exc:
        raise SubtitleTrackError("track must be a mapping or filesystem path") from exc
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SubtitleTrackError(f"cannot read subtitle track {path}: {exc}") from exc
    return _mapping(value, "track")


def _validate_picture_binding(binding, expected):
    binding = _strict_fields(
        binding, required={"sha256"}, optional={"edit_sha256"}, path="bindings.picture"
    )
    expected = _mapping(expected, "expected_picture_identity")
    picture_sha = _sha256(binding["sha256"], "bindings.picture.sha256")
    expected_sha = _sha256(expected.get("sha256"), "expected_picture_identity.sha256")
    if picture_sha != expected_sha:
        _fail("bindings.picture.sha256", "picture identity does not match current picture")

    edit_sha = binding.get("edit_sha256")
    if edit_sha is not None:
        edit_sha = _sha256(edit_sha, "bindings.picture.edit_sha256")
        if "edit_sha256" not in expected:
            _fail("expected_picture_identity.edit_sha256", "edit identity is required by track")
        expected_edit = _sha256(
            expected["edit_sha256"], "expected_picture_identity.edit_sha256"
        )
        if edit_sha != expected_edit:
            _fail("bindings.picture.edit_sha256", "edit identity does not match current edit")
    return {"sha256": picture_sha, **({"edit_sha256": edit_sha} if edit_sha else {})}


def _validate_audio_binding(binding, expected):
    binding = _strict_fields(
        binding,
        required={"sha256", "selected_stream"},
        path="bindings.audio",
    )
    expected = _mapping(expected, "expected_audio_identity")
    audio_sha = _sha256(binding["sha256"], "bindings.audio.sha256")
    expected_sha = _sha256(expected.get("sha256"), "expected_audio_identity.sha256")
    if audio_sha != expected_sha:
        _fail("bindings.audio.sha256", "audio identity does not match adopted audio")
    stream = _integer(binding["selected_stream"], "bindings.audio.selected_stream", minimum=0)
    expected_stream = _integer(
        expected.get("selected_stream"),
        "expected_audio_identity.selected_stream",
        minimum=0,
    )
    if stream != expected_stream:
        _fail("bindings.audio.selected_stream", "selected_stream does not match adopted audio")
    return {"sha256": audio_sha, "selected_stream": stream}


def _validate_evidence(value, cue_path):
    path = f"{cue_path}.timing_evidence"
    value = _strict_fields(
        value,
        required={"kind", "evidence_refs", "calibration", "word_alignment"},
        path=path,
    )
    kind = _nonempty_string(value["kind"], f"{path}.kind")
    calibration = _nonempty_string(value["calibration"], f"{path}.calibration")
    word_alignment = _nonempty_string(
        value["word_alignment"], f"{path}.word_alignment"
    )
    if kind not in _EVIDENCE_KINDS:
        _fail(path, f"timing_evidence kind is not supported: {kind!r}")
    if calibration not in _CALIBRATION_KINDS:
        _fail(path, f"timing_evidence calibration is not supported: {calibration!r}")
    if word_alignment not in _WORD_ALIGNMENT_KINDS:
        _fail(path, f"timing_evidence word_alignment is not supported: {word_alignment!r}")
    refs = value["evidence_refs"]
    if not isinstance(refs, list):
        _fail(path, "timing_evidence evidence_refs must be a list")
    refs = [_nonempty_string(ref, f"{path}.evidence_refs[{index}]") for index, ref in enumerate(refs)]

    valid_shape = {
        "legacy_estimate": calibration == "none" and word_alignment == "none",
        "asr_boundary_calibrated": calibration == "asr_energy" and word_alignment == "none",
        "word_timestamps": calibration in {"none", "asr_energy"} and word_alignment == "asr_words",
        "human_verified": calibration == "human_boundary" and word_alignment in {"none", "human_words"},
    }[kind]
    if not valid_shape:
        _fail(path, f"timing_evidence fields are inconsistent with kind {kind!r}")
    if kind != "legacy_estimate" and not refs:
        _fail(path, f"timing_evidence kind {kind!r} requires evidence_refs")
    return {
        "kind": kind,
        "evidence_refs": refs,
        "calibration": calibration,
        "word_alignment": word_alignment,
    }


def load_subtitle_track(
    path_or_mapping,
    *,
    expected_picture_identity,
    expected_audio_identity,
    expected_duration_seconds,
    reject_legacy_estimate=False,
):
    """Validate schema v1 and return metadata plus second-based render entries.

    Identity arguments are current facts supplied independently by the caller;
    declarations inside the track are never accepted as proof of their own
    freshness. Cue boundaries and text are validated, not split or corrected.
    """

    if not isinstance(reject_legacy_estimate, bool):
        _fail("reject_legacy_estimate", "must be a boolean")
    track = _strict_fields(
        _load_document(path_or_mapping),
        required={"schema_version", "clock", "overlap_policy", "bindings", "cues"},
        path="track",
    )
    if type(track["schema_version"]) is not int or track["schema_version"] != SCHEMA_VERSION:
        _fail(
            "schema_version",
            f"only schema_version {SCHEMA_VERSION} is supported; got {track['schema_version']!r}",
        )
    if track["overlap_policy"] != "forbid":
        _fail("overlap_policy", "schema v1 requires fail-closed value 'forbid'")

    clock = _strict_fields(
        track["clock"],
        required={"kind", "timebase", "duration_ticks"},
        path="clock",
    )
    if clock["kind"] != "output":
        _fail("clock.kind", "schema v1 supports output clock only")
    timebase = _strict_fields(
        clock["timebase"], required={"numerator", "denominator"}, path="clock.timebase"
    )
    numerator = _integer(timebase["numerator"], "clock.timebase.numerator", minimum=1)
    denominator = _integer(timebase["denominator"], "clock.timebase.denominator", minimum=1)
    duration_ticks = _integer(clock["duration_ticks"], "clock.duration_ticks", minimum=0)
    tick_seconds = Fraction(numerator, denominator)
    duration = duration_ticks * tick_seconds
    expected_duration = _seconds_fraction(expected_duration_seconds, "expected_duration_seconds")
    if duration != expected_duration:
        _fail(
            "clock.duration_ticks",
            f"duration {duration} does not match independently supplied duration {expected_duration}",
        )
    try:
        duration_seconds = float(duration)
    except OverflowError:
        _fail("clock.duration_ticks", "duration must be representable as finite renderer seconds")
    if not math.isfinite(duration_seconds):
        _fail("clock.duration_ticks", "duration must be representable as finite renderer seconds")

    bindings = _strict_fields(
        track["bindings"], required={"picture", "audio"}, path="bindings"
    )
    picture = _validate_picture_binding(bindings["picture"], expected_picture_identity)
    audio = _validate_audio_binding(bindings["audio"], expected_audio_identity)

    cues = track["cues"]
    if not isinstance(cues, list):
        _fail("cues", "must be a list")
    entries = []
    kinds = set()
    previous_end = 0
    for index, raw_cue in enumerate(cues):
        cue_path = f"cues[{index}]"
        cue = _strict_fields(
            raw_cue,
            required={"start_tick", "end_tick", "text", "attribution", "timing_evidence"},
            path=cue_path,
        )
        start_tick = _integer(cue["start_tick"], f"{cue_path}.start_tick", minimum=0)
        end_tick = _integer(cue["end_tick"], f"{cue_path}.end_tick", minimum=0)
        if end_tick <= start_tick:
            _fail(cue_path, "half-open cue requires end_tick > start_tick")
        if end_tick > duration_ticks:
            _fail(cue_path, "cue end_tick exceeds output duration")
        if start_tick < previous_end:
            _fail(cue_path, "cue is out of order or overlaps the previous cue")
        previous_end = end_tick
        text = _nonempty_string(cue["text"], f"{cue_path}.text")

        attribution = _strict_fields(
            cue["attribution"], required={"kind", "ref"}, path=f"{cue_path}.attribution"
        )
        source = _nonempty_string(attribution["kind"], f"{cue_path}.attribution.kind")
        if source not in {"source", "narration"}:
            _fail(f"{cue_path}.attribution.kind", "must be 'source' or 'narration'")
        source_ref = _nonempty_string(attribution["ref"], f"{cue_path}.attribution.ref")
        evidence = _validate_evidence(cue["timing_evidence"], cue_path)
        if reject_legacy_estimate and evidence["kind"] == "legacy_estimate":
            _fail(cue_path, "legacy_estimate is rejected by strict caller policy")
        start_seconds = float(start_tick * tick_seconds)
        end_seconds = float(end_tick * tick_seconds)
        if not (math.isfinite(start_seconds) and math.isfinite(end_seconds)):
            _fail(cue_path, "cue float projection must be finite")
        if end_seconds <= start_seconds:
            _fail(cue_path, "distinct cue ticks collapse in renderer float projection")
        kinds.add(evidence["kind"])
        entries.append(
            {
                "start": start_seconds,
                "end": end_seconds,
                "text": text,
                "source": source,
                "source_ref": source_ref,
                "timing_evidence": evidence,
            }
        )

    metadata = {
        "schema_version": SCHEMA_VERSION,
        "clock": {
            "kind": "output",
            "timebase": {"numerator": numerator, "denominator": denominator},
            "duration_ticks": duration_ticks,
            "duration_seconds": duration_seconds,
        },
        "overlap_policy": "forbid",
        "bindings": {"picture": picture, "audio": audio},
        "timing_evidence_kinds": sorted(kinds),
    }
    return {"metadata": metadata, "entries": entries}
