# Independent subtitle track contract (schema v1)

`scripts/subtitles/track.py` loads a subtitle track whose cue times already use
the final **output clock**. It validates the track against picture, edit, audio,
and duration facts independently supplied by the caller. It does not align
speech, remap source time, split text, repair cue boundaries, or prove that a
subtitle is perceptually synchronized.

## Loader API

```python
from fractions import Fraction
from subtitles.track import load_subtitle_track

loaded = load_subtitle_track(
    "subtitle_track.json",
    expected_picture_identity={
        "path": "/project/paired.mp4",
        "edit_plan": "/project/edit_plan.json",
    },
    expected_audio_identity={
        "selected_stream": 1,
        "sample_rate": 48000,
        "packet_count": 4700,
    },
    expected_duration_seconds=Fraction(duration_ts) * stream_time_base,
    reject_legacy_estimate=True,
)
metadata = loaded["metadata"]
entries = loaded["entries"]
```

The input may also be an in-memory mapping. `entries` is a list of dictionaries
ready for the existing seconds-based subtitle renderer:

```json
{
  "start": 1.0,
  "end": 3.0,
  "text": "example",
  "source": "narration",
  "source_ref": "narration:7",
  "timing_evidence": {
    "kind": "asr_boundary_calibrated",
    "evidence_refs": ["alignment-run:example"],
    "calibration": "asr_energy",
    "word_alignment": "none"
  }
}
```

The loader preserves cue text and boundaries. The only conversion is exact
integer ticks to renderer-facing seconds. `metadata.timing_evidence_kinds`
reports the labels present; it is not an aggregate precision verdict.

## Schema v1

```json
{
  "schema_version": 1,
  "clock": {
    "kind": "output",
    "timebase": {"numerator": 1, "denominator": 30},
    "duration_ticks": 300
  },
  "overlap_policy": "forbid",
  "bindings": {
    "picture": {
      "path": "/project/paired.mp4",
      "edit_plan": "/project/edit_plan.json"
    },
    "audio": {
      "selected_stream": 1,
      "sample_rate": 48000,
      "packet_count": 4700
    }
  },
  "cues": [
    {
      "start_tick": 30,
      "end_tick": 90,
      "text": "example",
      "attribution": {"kind": "narration", "ref": "narration:7"},
      "timing_evidence": {
        "kind": "asr_boundary_calibrated",
        "evidence_refs": ["alignment-run:example"],
        "calibration": "asr_energy",
        "word_alignment": "none"
      }
    }
  ]
}
```

- `timebase` is rational seconds per tick. Cue intervals are half-open
  `[start_tick, end_tick)`.
- `duration_ticks`, cue bounds, and stream indexes are nonnegative integers
  (booleans are not integers for this contract).
- Cues must be ordered, non-overlapping, nonempty, and contained by the output
  duration. Adjacent half-open cues may touch.
- Distinct tick bounds must remain a positive, finite interval after conversion
  to the renderer's float seconds. Tracks beyond that projection precision fail
  closed rather than becoming a zero-length rendered cue.
- `overlap_policy` must be `forbid`. Schema v1 has no permissive overlap mode.
- `attribution.kind` is `source` or `narration`; `ref` identifies the source
  utterance or narration item without changing its text.
- Unknown fields and schema versions other than integer `1` are rejected, so a
  newer producer cannot be silently interpreted as v1. Legacy `sha256` /
  `edit_sha256` binding keys are the one exception: they are ignored.

## Independent binding checks

The loader compares track declarations with the caller's current facts; it never
treats a track's own binding as evidence that the track is fresh.

- `picture.path` is mandatory and must resolve to the caller's current picture
  path. `edit_plan` is optional for inputs without a separately materialized
  edit plan; if the track contains it, the caller must supply the same path.
- `audio.selected_stream`, `sample_rate` and `packet_count` describe the
  **actually adopted** audio stream, not merely a source filename or an intended
  mix manifest. Pass the facts probed from the current media; this module
  deliberately does not run ffprobe itself.
- `expected_duration_seconds` is mandatory and checked against
  `duration_ticks * timebase`. Prefer `Fraction(duration_ts) * time_base` from
  the actual output stream to avoid decimal/container rounding. `int`, finite
  `float`, and finite `Decimal` are also accepted.

## Timing evidence labels

Evidence labels describe how a cue boundary was obtained. They do not change
the exact burn time represented by its ticks.

| `kind` | Required calibration | Required word alignment | Evidence refs |
|---|---|---|---|
| `legacy_estimate` | `none` | `none` | optional |
| `asr_boundary_calibrated` | `asr_energy` | `none` | required |
| `word_timestamps` | `none` or `asr_energy` | `asr_words` | required |
| `human_verified` | `human_boundary` | `none` or `human_words` | required |

`asr_boundary_calibrated` is the label for a coarse-ASR boundary adjusted with
ASR context and/or energy evidence. Energy onset is not proof of a phoneme, so
this label cannot claim word alignment or human verification. Missing evidence
references cannot claim any non-legacy kind. A strict caller may reject
`legacy_estimate`; accepting another label still does not automatically call it
strong alignment.

Because cue intervals are half-open, a cue is not visible at any tick before its
`start_tick`; the tick immediately preceding it belongs to whatever came before.

## Deliberate limits

- Schema v1 has no source-to-cut/edit map and cannot map source-clock cues.
- The module does not inspect media, select audio streams, or tolerate a
  self-declared binding without current caller facts.
- Validation proves schema consistency and the requested bindings only. Actual
  rendered first/last subtitle frames and perceptual speech alignment require
  separate render/media review.

## Current assembly integration (bounded, not automatic alignment)

Place `subtitle_track.json` in `work_dir` and select
`assemble.py --audio-mode adopted-packet-copy`. A present track is a **complete
replacement** of all generated narration/original-dialogue subtitles, not a
partial patch and not merged with legacy subtitles. Carry every cue that should
remain; an empty `cues` array intentionally removes all generated subtitles.
Unknown patch/merge modes are rejected by schema v1. This does not detect words
missing from the authored full track: acoustic/coverage review remains required.

The assembly integration independently probes the chosen input stream for its
sample rate and packet count, resolves the input path for the picture binding,
and records the `{size, mtime_ns}` of the track, video and optional edit plan in
`subtitle_track_validation.json`. It does not use the track's own declarations
as current facts.

Actual decoded frame PTS are read before projection. Integer cue ticks and the
rational timebase are retained until each boundary is resolved to the first
frame at or after it. `subtitle_track_validation.json` records original ticks,
resolved frame indexes/PTS, quantization deltas, ASS thresholds, validation
schema and projector version. SRT and timeline consume resolved frame seconds;
ASS thresholds are chosen to switch on those same frames despite ASS's 10ms
clock. A cue with no visible frame, or a boundary that ASS cannot distinguish,
is rejected rather than silently dropped. The original author file is not
rewritten. Legacy subtitles keep their previous rendering behavior.

Each consumption re-checks the track/media/optional edit plan `{size, mtime_ns}`
and the consumer duration; a rewritten input is stale and must be prepared
again. Deleting the explicit track clears its previous validation record.
An invalid/stale track never falls back to character-proportional timing.

Current limits:
- Integration is for output media starting at zero with an adopted AAC track,
  not raw-source-to-edited-output mapping or a newly mixed narration track.
- The low-level preparation API accepts `edit_plan_path`, but the assembly CLI
  does not yet expose it. CLI tracks must omit `edit_plan`; that path binds the
  input media, not edit-plan ancestry.
- The low-level policy `reject_legacy_estimate=True` is available. There is not
  yet a wired commercial-profile CLI gate. The current CLI preserves timing
  evidence labels and does not call every accepted cue precisely aligned.
- The actual FFmpeg regression demonstrates frame timing with a synthetic cue,
  not phoneme alignment. `direct_listening` and `acoustic_alignment` remain
  `NOT_CHECKED`; evidence labels are declarations, not automatically verified
  human approval or proof that an external evidence reference is true.
