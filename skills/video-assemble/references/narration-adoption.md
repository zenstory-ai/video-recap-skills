# Narration adoption and the consumed-input record

Narration assembly accepts legacy `tts_meta.json`, but a current file by itself is
not an adoption decision. Use an explicit adoption document when the selected spoken
text, requested provider/voice, and tempo policy must be bound to the actual final mix.

```bash
python3 scripts/assemble.py input.mp4 --work-dir work \
  --tts-meta /local/tts_meta.json \
  --narration-adoption /local/narration_adoption.json
```

`--narration-adoption` is narration-only and requires an explicitly supplied
`--tts-meta`; it never adopts an ambient work-directory file automatically.

## Strict v1 schema

```json
{
  "artifact": "narration_adoption",
  "schema_version": 1,
  "segments": [
    {
      "index": 0,
      "spoken_text": "exact selected words",
      "requested_provider": "caller-selected-provider",
      "requested_voice": "caller-selected-voice"
    }
  ],
  "tempo_policy": {
    "global_atempo": 1.0,
    "bounded_segment_fit": false,
    "segment_tempo_max": 1.0,
    "cumulative_tempo_max": 1.0,
    "cumulative_tempo_hard_max": 1.0
  }
}
```

That policy is the conservative default. An adoption may declare its own values and
they are honoured: `global_atempo` must be positive and no greater than
`cumulative_tempo_hard_max`, `segment_tempo_max` and `cumulative_tempo_max` must be at
least `1.0`, `cumulative_tempo_hard_max` must not be below `cumulative_tempo_max`, and
`bounded_segment_fit` must be a boolean. Malformed or out-of-bounds policies fail;
ambient defaults such as a global 1.15 speed never override an adoption. With
`bounded_segment_fit` false, an adopted segment that does not fit its authored window
blocks without time trimming or bounded fit.

The adoption's ordered segment indices and spoken text must match both the segment
list in the supplied `tts_meta.json` and the in-memory list passed to
`assemble_video`; those two lists must be equal to each other. Unknown fields fail;
legacy `tts_meta_sha256` / `processed_wav_sha256` keys are ignored. The consumer never
creates an adoption from metadata that it is about to consume.

## Validation and snapshots

Every adopted `audio_path` must exist before any media is probed or rendered. The
adopted inputs are then copied to per-run snapshots under
`work/.narration_input_snapshots/`; the original files are never modified, and the
render reads only the snapshots. Every conversion, placed WAV, and the completed
narration bus is recorded with its path and probed PCM facts before the final FFmpeg
command runs.

On the legacy narration-mix path, Python's standard WAV reader cannot open every valid post-processed WAV encoding.
Noncanonical input, including `pcm_f32le`, 48 kHz, or stereo WAV, is explicitly
decoded by the existing FFmpeg executable to 44.1 kHz mono PCM16 before placement.
The conversion path and actual PCM facts are recorded; the consumer does not claim
that converted samples equal the original samples.

The explicit full-sound path described in `explicit-audio-mix.md` deliberately does
not use that 44.1 kHz mono conversion. It consumes the snapshots directly as complete
48 kHz float placements, preserving native stereo channels.

An adopted render writes to a hidden candidate path. The binding is written, QC runs
against the candidate, the candidate is renamed to the final path, and QC runs once
more against the published file. A blocking QC removes the candidate and the binding
instead of publishing; the run records the final output as absent rather than
claiming a missing file was published. Non-final work or diagnostic artifacts may
remain for investigation.

## Binding report

After successful assembly, `work/narration_input_binding.json` records:

- original input path and snapshot path;
- any explicit conversion path and PCM parameters;
- each complete placed WAV path and PCM parameters;
- `narration.wav` path and PCM parameters;
- the final output path and its encoded audio-stream facts (decoder parameters,
  packet count, payload bytes, start time, duration);
- the adoption path, its `tts_meta` path, and the exact tempo policy when supplied.

The report uses one of two identity statuses:

- `UNADOPTED`: no adoption was supplied; the legacy inputs were consumed as given;
- `BOUND_TO_ADOPTION`: the adoption's selection and tempo policy were carried
  through the final mix.

`BOUND_TO_ADOPTION` records what was consumed, not provider truth, acoustic speaker
identity, direct listening, naturalness, or release approval; voice authentication
and direct listening stay `NOT_CHECKED`. QC, manifest, and settings records reference
a binding only while its recorded final output path still exists; source-mix and
adopted-packet-copy modes do not reuse narration binding evidence.
