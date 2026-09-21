# Assembly audio modes

`assemble.py` keeps `narration` as its API and CLI default. Non-narration
behavior is opt-in with `--audio-mode`; `--audio-stream-index N` is the
zero-based audio ordinal used by FFmpeg's `0:a:N` selector.

## `narration`

- Requires non-empty `tts_meta.json` segments, as before.
- Builds/places narration WAV, performs source ducking and optional BGM mix,
  then applies the configured final loudness/limiter stage and AAC encoding.
- Missing source audio may use the existing synthetic-silence fallback.
- With the additional strict `--audio-mix-adoption`, narration instead consumes an
  adopted prepared bed plus complete direct-to-48-kHz narration placements. This is
  still narration mode, but it bypasses ambient BGM, ducking, speed/fit, loudnorm, and
  limiter operations. See `explicit-audio-mix.md`.

## `source-mix`

- Does not read or require `tts_meta.json` and never creates narration audio.
- Uses the selected input audio stream, applies `IDLE_ORIG_VOLUME`, mixes a
  declared `BGM_PATH` when present, and runs the configured final
  loudness/limiter stage before AAC encoding.
- A declared but missing `BGM_PATH` is an error in this new mode (the legacy
  narration-mode warn-and-skip behavior remains unchanged).
- This is a processed mix, not frozen audio. `assembly_qc.json` reports the
  operations that actually ran.

## `adopted-packet-copy`

- First implementation supports a selected AAC stream from `<video>` only.
- Rejects TTS segments, explicit `--tts-meta`, any configured `BGM_PATH`, a
  missing/wrong stream, unsupported codec, or audio/picture interval mismatch.
- Maps that stream with `-c:a copy`. It does not build narration, mix, duck,
  normalize, limit, resample, change tempo, or add silence.
- It deliberately omits `-t` and `-shortest`, preserving AAC priming and tail
  packets. After rendering, the output is probed and compared with the input:
  decoder parameters, packet count, total payload bytes, the packet-clock span,
  and every packet's size and PTS/DTS/duration converted to rational time must
  match. Packet side data also must match, including AAC skip-sample/
  discard-padding values and their reason fields.
- QC/manifest evidence records selected stream, absolute stream index, codec,
  time base, sample rate, channels/layout, packet count, payload bytes, packet
  details, and actual operation flags.

The adopted-copy settings payload excludes narration/mix/loudness defaults, so
irrelevant ambient settings do not invalidate a frozen-audio render. Video
filters and video re-encoding remain allowed; packet verification must still
pass afterward.

These modes do not create a commercial quality profile, automatic speech
alignment, listening approval, or release approval. An explicit subtitle track
is only a version/media-bound timing declaration; its evidence labels and
`NOT_CHECKED` acoustic/listening status remain authoritative.
Matching packets and decoder parameters show the encoded audio was copied; they
do not prove perceptual quality or that a person listened to the result.

`timeline.json` represents either non-narration mode as one complete clip from
the current input video and records the selected stream. Adopted copy uses gain
1 with no automation. Source-mix records processed/non-frozen delivery and is
not claimed to be reconstructable by an editor. The optional JianYing exporter
currently supports only selected stream 0; requesting export with another
stream fails instead of silently substituting the default stream.
