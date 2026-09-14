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
  "picture_sha256": "...",
  "prepared_receipt": {"path": "/local/prepared_bed_receipt.json", "sha256": "..."},
  "narration_adoption_sha256": "...",
  "format": {"sample_rate": 48000, "channels": 2, "total_samples": 1856000},
  "segments": [
    {"index": 0, "processed_wav_sha256": "...", "output_start_sample": 366000,
     "gain": 0.4251421093940735}
  ],
  "master_gain_db": 0.75
}
```

Unknown or missing fields fail. The format must equal the actual zero-origin picture
frame clock at 48 kHz and the prepared receipt. The receipt bytes, all three float PCM
beds, picture bytes, narration adoption bytes, ordered segment indices, and processed
WAV hashes are checked before narration snapshots are written.

## Exact audio operations

Each immutable V4 narration snapshot is decoded directly and completely to
`pcm_f32le`, 48 kHz stereo. Version 1 uses the fixed
`mono_equal_power_stereo_identity` policy:

- mono is panned to left and right at `1/sqrt(2)` per channel;
- stereo preserves independent left and right channels at unit gain;
- inputs with more than two channels are rejected.

There is no intermediate 44.1 kHz mono placement, speed change, fade, trim, tail pad,
normalization, or automatic fit. Every complete converted WAV must fit its adopted
integer `output_start_sample`; placements may not overlap. Adopted per-segment gains
form a float `voice_bus.wav`. The producer's `prepared_bed.wav` and voice bus form a
float premaster, then the sole adopted `master_gain_db` forms the float master consumed
by the final AAC encode. The picture's old audio is never a mixer input.

## Identity and publication

`narration_input_binding.json` records the actual direct 48 kHz placed files and voice
bus, marking the bus `CONSUMED_BY_EXPLICIT_MIX`. `audio_mix_binding.json` binds the mix
adoption, picture clock/hash, prepared receipt and stems, converted placements, voice
bus, premaster, master, finalized narration-binding hash, final decoded PCM evidence,
final AAC packet/decoder identity, and the actual output picture decoder/frame clock.
The output picture must preserve fps, frame count, zero start, and duration. A video-copy
path additionally reports exact packet identity; an allowed visual re-encode reports
`REENCODED_CLOCK_MATCH` rather than claiming packet equality. Timeline, settings, QC, and manifest identify the
explicit path rather than reporting ambient ducking/BGM/loudnorm operations.

Media and both bindings are staged together. Initial QC must pass before publication;
the published artifacts are then re-read and a second QC must also pass. Any render,
identity, binding, or QC failure removes final-named media and bindings instead of
overwriting an older success. Diagnostic/staging audio may remain in the unique work
directory.

These identities prove which bytes and numeric operations were consumed. Direct
listening, normal-speed review, acoustic voice authentication, subjective balance, and
commercial release approval remain `NOT_CHECKED` or false.
