# Prepared source-only audio delivery

`render_prepared_audio.py` pairs an immutable zero-origin H264/MP4 picture with an
explicitly adopted completed `prepared_bed_receipt`. It is for a soundtrack that is
already complete: it does not synthesize narration, choose or fabricate music, add
silence, duck, normalize, limit, retime, trim, pad, or request a network service.

```bash
python3 scripts/render_prepared_audio.py prepared_audio_adoption.json \
  --output-dir a-new-directory
```

The adoption is strict and has no implicit master gain:

```json
{
  "artifact": "prepared_audio_adoption",
  "schema_version": 1,
  "picture": {"path": "/local/locked-picture.mp4", "sha256": "..."},
  "prepared_receipt": {"path": "/local/prepared_bed_receipt.json", "sha256": "..."},
  "master_gain_db": 0
}
```

`master_gain_db` must be finite and within `[-24,24]`. The complete declared float
PCM sample array is retained. After gain, every sample must be finite with
`abs(sample)<=1`; `+1` and `-1` are valid. There is no automatic attenuation. At
zero dB the master canonical PCM payload must equal the adopted prepared payload.

The picture must satisfy the existing pair-media zero-origin CFR H264/MP4 contract.
Its frame clock is projected onto the 48 kHz output clock by rounding the exact
frame-count position half-up once — so broadcast rates such as 30000/1001 are
supported and integral rates are unchanged — and that sample count must exactly equal
the receipt and all three current float PCM beds. AAC is encoded without `make_zero`,
`-shortest`, `-t`, trimming, padding, or time offsets. Its presentation starts at
zero while the negative priming packet and `skip_samples` remain consistent. Header,
packet, PCM, and picture endpoints must agree within one 48 kHz sample before the
existing pair operation may consume it.

AAC is lossy and decoder padding is expected. Reports claim exact master PCM identity
before encoding and exact compressed AAC packet identity through pairing; they do not
claim AAC-decoded PCM identity, true-peak measurement, listening approval, alignment
of arbitrary sources, or release approval.

The outer operation owns publication. Master PCM, AAC, the pair plan, and nested pair
output remain in hidden staging. Adoption, receipt, all beds, picture, master, and AAC
are rechecked after pairing; only then is `prepared_audio.mp4` published. Failure
removes every generated candidate and retains only the outer FAILED report and any
outer diagnostic logs. Input assets are never modified.
