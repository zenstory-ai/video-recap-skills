# Pair rebuilt picture with a retained adopted soundtrack

When the picture is rebuilt/reframed but the adopted mix must not change, use
`pair_media.py` before `assemble.py --audio-mode adopted-packet-copy`. This is a
real two-input stream copy, not re-encoding an old finished video's picture.
It does not generate narration, align speech, design packaging, or create an
end card. It does not decide which inputs the editor intended to adopt.

```json
{
  "artifact": "media_pair",
  "schema_version": 1,
  "picture": {"path": "/project/picture.mp4"},
  "audio": {"path": "/project/adopted.m4a", "selected_stream": 0}
}
```

Both paths must be explicit local files. `selected_stream` is the audio ordinal
(`a:N`), not the absolute stream index. The audio donor may be an old MP4 with
unrelated video: only the selected audio is used. The picture's audio is ignored.
Both files must exist when the plan is read. The plan is strict: unknown fields
do not silently request unsupported retime, gain, trimming or offset operations
(legacy `sha256` keys are ignored).

```bash
python3 scripts/pair_media.py pair.json --output-dir new-pair-directory --plan-only
# A PLANNED directory is not reusable as a rendered candidate: choose a NEW one.
python3 scripts/pair_media.py pair.json --output-dir new-render-directory
```

The output directory must not already exist. No existing input, final asset,
current pointer, or user-approved version is overwritten. `--plan-only` checks
actual inputs and timing but creates no video and invokes no mux. A rendered run
provides:

- `paired.mp4`: selected picture and audio, both compressed-stream copied.
- `pair_run.json`: `PLANNED`, `PAIR_RENDERED`, or `FAILED`, explicit input and
  plan/output paths, frame count, timing tolerance and output stream numbering.
- `picture_identity.json`: decoder, geometry/color, complete actual frame clock
  and compressed packet size/timing/side-data facts.
- `adopted_audio_identity.json`: donor and output AAC packet/decoder facts.
- mux command/log for reconstruction and diagnosis.

The first implementation deliberately accepts H264 or HEVC in an MP4-family container,
complete zero-origin CFR picture (1–120 fps), and contiguous AAC packets with
known nominal sample duration. No VFR, retime, offset, format conversion, padding,
trimming, `-shortest`, normalization or gain is inferred. Packet priming and skip
metadata are retained. The packet clock must also agree with the declared audio
stream interval (at most one nominal priming packet before the start; the packet
end matches the stream end within one sample). This is checked again on the
actual muxed output. Picture/audio starts and ends must differ by no more than
the larger of one picture frame or one nominal AAC packet. The interval check is
compatibility, **not perceptual synchronization or acoustic alignment**. A tiny
container-tail tolerance does not authorize cutting a word.

Muxing goes to a staging file. The output's video decoder/packet sizes and
timestamps/full frame clock/geometry/color and adopted AAC packets must match
the inputs, the output must contain only `v:0,a:0`, and full decode must pass
before final publication. Failure leaves a FAILED record and logs, not a
final `paired.mp4`. Rebuilding source geometry is certified by its own upstream
render/map evidence, not by the existence of a successfully paired container.

## Subtitle integration: bind AFTER pairing

The output audio ordinal is always **0**, even when the donor used `a:1`.
Create the complete output-clock `subtitle_track.json` against
`subtitle_track_binding.current_bindings(paired_video, 0)`, then run the existing
adopted assembly path. Do not reuse a binding computed against the picture-only
file or the donor's old stream ordinal. A present stale subtitle track fails rather than falling
back to estimated timings. See [subtitle-track.md](subtitle-track.md).

Pairing preserves the input picture, including any explicit black tail; it does
not turn that black tail into a branded end card. Listening, normal-speed review,
editorial intent and release approval remain separate from this mechanical proof.
