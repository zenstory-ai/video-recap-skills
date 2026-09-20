# Caller-rendered foreground composition

`compose_foreground.py` performs one narrow operation: it overlays a caller-rendered,
full-canvas RGBA PNG sequence on an already locked H264/CFR/AAC base, optionally
replaces a declared tail with an explicit endcard asset, and copies `a:0` without
decoding or rewriting it. The PNG pixels are the source schema. This is not a CSS,
font, text, subtitle, logo, animation, or plugin renderer.

## Command

```bash
python3 scripts/compose_foreground.py foreground_plan.json \
  --output-dir a-new-directory [--plan-only]
```

The output directory must not exist. A normal run publishes `foreground.mp4` only
after staged media, all current input identities, frame clock, canvas/color metadata,
AAC decoder/packets/PTS/side data, and a full audio/video decode pass verification.
A failure retains `foreground_run.json` and available logs but removes staged/final
media. `--plan-only` validates the complete plan and inputs and writes only
`foreground_run.json`; it does not render media or update any current pointer.

## Strict plan schema v1

```json
{
  "artifact": "foreground_compose_plan",
  "schema_version": 1,
  "base": {"path": "/local/base.mp4", "sha256": "..."},
  "video": {"fps": "30/1", "width": 1280, "height": 720, "total_frames": 300},
  "foreground": {
    "directory": "/local/foreground_sequence",
    "pattern": "frame_%06d.png",
    "start_frame": 0,
    "end_frame": 270,
    "ordered_sha256": "..."
  },
  "endcard": {
    "kind": "sequence",
    "directory": "/local/endcard_sequence",
    "pattern": "frame_%06d.png",
    "start_frame": 270,
    "end_frame": 300,
    "ordered_sha256": "..."
  },
  "producer_receipt": {"path": "/local/producer_receipt.json", "sha256": "..."}
}
```

`endcard` is an exact tagged union. The alternative static form is:

```json
{
  "kind": "still",
  "path": "/local/endcard.png",
  "sha256": "...",
  "start_frame": 270,
  "end_frame": 300
}
```

When the foreground itself covers the entire declared frame clock, the only accepted
no-tail form is exactly:

```json
{"kind": "none"}
```

It accepts no additional fields. It is invalid when the foreground reserves any tail;
there is no implicit filler, repeated last frame, or generated endcard.

Use `still` only when a genuinely static full-canvas endcard is intended. A static
PNG does not reconstruct a delivered fade or other animated endcard; supply the
caller-rendered `sequence` form for that case.

Both sequences use **local zero-based filenames** regardless of their output range.
Thus the endcard example contains `frame_000000.png` through `frame_000029.png`,
mapped to output frames `[270,300)`. The foreground must start at output frame zero.
Its end must equal the endcard start, and the endcard end must equal the independently
probed base frame count. With `kind: "none"`, the foreground end must instead equal
that full base frame count. Bounds are half-open.

The only accepted filename pattern is the literal `frame_%06d.png`. The directory
must contain exactly the expected contiguous entries—no missing or extra files. Every
actual image must probe as PNG, `rgba`, and the exact declared full canvas. Repeated
files and symlinks are allowed so transparent holds need not duplicate storage.

Generate an ordered digest with the same-skill helper:

```python
from compose_foreground import ordered_sequence_digest

digest = ordered_sequence_digest(directory, "frame_%06d.png", 0, frame_count)
```

The digest is SHA256 over canonical JSON entries containing each local frame number
and the resolved file-content SHA256 in order. It therefore binds repeated/symlinked
content deterministically without binding symlink target path spelling.

## Base and output invariants

The first version accepts one selected `v:0` H264, zero-origin CFR, YUV420P,
BT.709/TV-range base and adopted AAC `a:0`. Declared fps, canvas, and frame count are
checked independently against actual decoded frame PTS. Picture and audio intervals
must satisfy the narrow `pair_media` timing contract.

Composition explicitly overlays in RGB, converts to BT.709 limited-range YUV420P,
limits the final video filter to the declared half-open frame clock, and re-encodes
only video with fixed `libx264 -preset fast -crf 18` settings. A no-tail run uses only
the base and foreground inputs; it does not synthesize or pad a tail. The filter EOF
prevents still-image input loops without imposing an output-level video frame cap that
could stop accepted trailing AAC packets from being copied. The run
report records that encoder, preset, CRF, pixel format, color contract, and audio-copy
mode explicitly. H.264 CRF encoding is lossy: preserved transparent regions are
visually checked against tolerances, not claimed pixel-identical to decoded base
pixels. The compositor does not use `-r`, `-shortest`, or `-t`. Audio is mapped from
base `a:0` with `-c:a copy`; output verification requires exact decoder identity,
packet payloads, count, PTS/DTS/duration/size, and side data.

## Provenance and review boundary

The producer receipt is a current hash-bound caller artifact. Its declarations may
describe roles or content decisions such as title/note/brand, `dialogues=[]`, or
hidden markers. Core records those declarations as `DECLARED_NOT_CHECKED`; it does
not infer semantic truth from pixels or claim that dialogue/subtitle duplication is
absent. A separate producer/reviewer must establish that evidence.

Every run reports `direct_listening` and `normal_speed_review` as `NOT_CHECKED` and
`release_approved` as false. Pixel validation and deterministic rendering are not
commercial-quality or release approval.
