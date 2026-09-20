# Narration adoption and input identity

Narration assembly accepts legacy `tts_meta.json`, but a current file by itself is
not an adoption decision. Use an explicit adoption document when the selected spoken
text, processed WAV bytes, requested provider/voice, and tempo policy must be bound
to the actual final mix.

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
  "tts_meta_sha256": "...",
  "segments": [
    {
      "index": 0,
      "spoken_text": "exact selected words",
      "processed_wav_sha256": "...",
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

The adoption must match the exact current `tts_meta.json` bytes. Its ordered segment
indices, spoken text, and processed hashes must also match both the file's segment
list and the in-memory list passed to `assemble_video`. The consumer never creates an
adoption from metadata that it is about to consume.

## Validation and immutable consumption

Before probing/rendering media or writing assembly artifacts, every supplied
`processed_wav_sha256` is checked against the actual `audio_path`. When present, a
provider receipt's processed hash, provider, or requested voice must not contradict
the adoption. A missing receipt is recorded as request evidence `UNKNOWN`; exact PCM
adoption is not acoustic voice authentication.

Processed-hash coverage is all-or-none. A list that hashes only some segments is
rejected rather than promoting the whole list to a misleading hash-bound status.

Identity-constrained inputs are copied to per-run snapshots only after all inputs
validate. Original/adoption/tts-meta/snapshot hashes are checked after snapshotting.
Every conversion, placed WAV, and the completed narration bus is then sealed with
hash and PCM facts before final FFmpeg. Original/adoption/tts-meta/snapshot and sealed
derived hashes are checked immediately before final FFmpeg and again after it. The
original files are never modified. A post-preflight change to either original or
derived render input fails the run instead of being mislabeled as bound.

On the legacy narration-mix path, Python's standard WAV reader cannot open every valid post-processed WAV encoding.
Noncanonical input, including `pcm_f32le`, 48 kHz, or stereo WAV, is explicitly
decoded by the existing FFmpeg executable to 44.1 kHz mono PCM16 before placement.
The conversion path and actual PCM facts are recorded; the consumer does not claim
that converted bytes equal the original bytes.

The explicit full-sound path described in `explicit-audio-mix.md` deliberately does
not use that 44.1 kHz mono conversion. It consumes the immutable snapshots directly as
complete 48 kHz float placements, preserving native stereo channel identity.

Identity-constrained video and binding report both use staging paths. QC runs against
the still-unpublished final path. Only a nonblocking QC plus a complete, valid binding
publishes both artifacts. Failure does not publish or overwrite final media/binding,
and records the final output as absent rather than claiming a missing file was
published. Non-final work or diagnostic artifacts may remain for investigation; this
contract does not destructively erase them.

## Binding report and evidence strength

After successful assembly, `work/narration_input_binding.json` binds:

- original input path/hash and immutable snapshot path/hash;
- any explicit conversion path/hash/PCM parameters;
- each complete placed WAV path/hash/PCM parameters;
- `narration.wav` path/hash/PCM parameters;
- the renamed final output path/hash and actual encoded audio-stream identity;
- the adoption and exact tempo policy when supplied.

The report uses one of three identity statuses:

- `LEGACY_UNVERIFIED`: no processed hashes were supplied;
- `DECLARED_HASH_BOUND_UNADOPTED`: supplied hashes matched actual bytes, but no
  explicit current adoption selected provider/voice/text;
- `BOUND_TO_ADOPTION`: exact metadata, input bytes, selection, and tempo policy were
  bound through the final mix.

`BOUND_TO_ADOPTION` proves consumption identity, not provider truth, acoustic speaker
identity, direct listening, naturalness, or release approval. The report keeps voice
authentication and direct listening `NOT_CHECKED`. QC, manifest, and settings records
reference only a binding whose final output path/hash is still current; source-mix
and adopted-packet-copy modes do not reuse narration binding evidence.
