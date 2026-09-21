# Explicit adopted full-sound mix

Use this path only when the picture, a completed `prepared_bed_receipt`, an exact
narration adoption, narration placements/gains, and one fixed master gain have already
been independently selected. It is still `audio_mode=narration`, but it bypasses the
legacy narration timing, ducking, ambient BGM, loudness-normalization, and limiter path.

```bash
python3 scripts/assemble.py picture.mp4 --work-dir NEW_WORK \
  --tts-meta /local/tts_meta.json \
  --narration-adoption /local/narration_adoption.json \
  --audio-mix-adoption /local/audio_mix_adoption.json
```

All strict output paths, including the CLI delivery alias, must be new. The CLI copies
to a hidden delivery stage and creates the alias with an exclusive atomic link, so a
concurrent or existing file is never overwritten.

## Adoption schema v1

```json
{
  "artifact": "audio_mix_adoption",
  "schema_version": 1,
  "prepared_receipt": {"path": "/local/prepared_bed_receipt.json"},
  "format": {"sample_rate": 48000, "channels": 2, "total_samples": 1856000},
  "segments": [
    {"index": 0, "output_start_sample": 366000, "gain": 0.4251421093940735}
  ],
  "master_gain_db": 0.75
}
```

Unknown or missing fields fail; legacy `*_sha256` keys are ignored. The format must
equal the actual zero-origin picture frame clock at 48 kHz and the prepared receipt.
The receipt, all three float PCM beds (format, sample count, finite PCM), the picture,
the narration adoption file and the ordered segment indices are checked before
narration snapshots are written.

## Exact audio operations

Each narration snapshot is decoded directly and completely to `pcm_f32le`, 48 kHz
stereo. Version 1 uses one fixed channel matrix, recorded per segment as
`mono_equal_power` or `stereo_identity`:

- mono is panned to left and right at `1/sqrt(2)` per channel;
- stereo preserves independent left and right channels at unit gain;
- inputs with more than two channels are rejected.

There is no intermediate 44.1 kHz mono placement, speed change, fade, trim, tail pad,
normalization, or automatic fit. Every complete converted WAV must fit its adopted
integer `output_start_sample`; placements may not overlap. Adopted per-segment gains
form a float `voice_bus.wav`. The producer's `prepared_bed.wav` and voice bus form a
float premaster, then the sole adopted `master_gain_db` forms the float master consumed
by the final AAC encode. The picture's old audio is never a mixer input.

## Records and publication

`narration_input_binding.json` records the actual direct 48 kHz placed files and voice
bus, marking the bus `CONSUMED_BY_EXPLICIT_MIX`. `audio_mix_binding.json` records the
mix adoption path, picture path and clock, prepared receipt path and stems, converted
placements (path, channel matrix, PCM facts), voice bus, premaster, master, the
narration binding path, the final decoded PCM facts, the final AAC decoder/packet
count/payload bytes, and the actual output picture decoder/frame clock. The output
picture must preserve fps, frame count, zero start, and duration. A video-copy path
reports `packet_identity: EXACT` when decoder parameters and packet sizes/timestamps
are unchanged; an allowed visual re-encode reports `REENCODED_CLOCK_MATCH`. Timeline,
settings, QC, and manifest identify the explicit path rather than reporting ambient
ducking/BGM/loudnorm operations.

The candidate render is written to a hidden file. Both bindings are written, QC must
pass, the media is published, and a second QC runs against the published path. Any
render, binding, or QC failure removes the candidate, final-named media and the
bindings written by this run instead of overwriting an older success.
Diagnostic/staging audio may remain in the unique work directory.
