# Semantic theme foreground producer

Use `render_theme_foreground.py` after the base picture and sound are locked. It renders four
finite roles—one title pair, captions from one validated bound subtitle track, timed notes, and
optional timed markers—into a full-canvas RGBA PNG sequence for `compose_foreground.py`. It does
not generate copy, split cues, estimate speech timing, render an endcard, or issue approval.

```bash
python3 scripts/render_theme_foreground.py theme_foreground_plan.json \
  --output-dir NEW_DIRECTORY \
  [--node-executable /local/node] [--browser-executable /local/chrome]
```

Without `--browser-executable`, the capture browser is resolved from
`THEME_FOREGROUND_BROWSER`, then from a Chrome/Chromium binary on `PATH`, then from the usual
installed application paths; a run fails with the list of candidates when none exists.

`--plan-only` validates identities, schema, alpha geometry, subtitle media binding, and local
runtime prerequisites. It writes a `PLANNED` report but renders no pixels and issues no receipt.

## Optional direct finished video

The same command can invoke the existing compositor after successful foreground production:

```bash
# Whole-video foreground, without replacing any tail: body_end_frame == total_frames.
python3 scripts/render_theme_foreground.py theme_foreground_plan.json \
  --output-dir NEW_DIRECTORY --compose

# Reserved tail: explicitly provide the existing still/sequence endcard descriptor.
python3 scripts/render_theme_foreground.py theme_foreground_plan.json \
  --output-dir NEW_DIRECTORY --compose --endcard selected_endcard.json
```

`selected_endcard.json` is exactly the `endcard` object documented in
[foreground-compose.md](foreground-compose.md), not another timeline. The tail's range and
local assets are validated before browser capture. Without it, only a full-length foreground
is accepted; no missing tail is filled or frozen automatically. `--endcard` requires `--compose`.
`--compose --plan-only` is rejected; producer-only planning remains unchanged.

The command derives `compose_plan.json` from the successful producer receipt and writes the
verified final file to `NEW_DIRECTORY/composed/foreground.mp4`. It does not run cut, TTS,
ASR, mix or registration. No independent caption text is copied into the composition plan.
The compositor retains its H264/CFR/BT.709/AAC input limits and exact audio-packet checks;
invalid composition cannot report a finished video, though successful foreground assets can
remain available for diagnosis. Old output directories and source media are not overwritten.

Use a **clean locked base**, not a video already carrying captions to be replaced. For newly
mixed narration, first finish that mix, then bind the subtitle track to that actual MP4 and
its selected audio. That finished sound can be frozen here; it is not necessary to suppress
captions merely because the base originated from narration. Existing burned-in text cannot
be erased by drawing a new caption on top. The new variant is not automatically adopted or
registered as the prior recap output.

## Plan version 1

The plan has exactly `artifact`, `schema_version`, `base`, `video`, `subtitle`, `profile`, and
`content`. `artifact` is `theme_foreground_plan`; `schema_version` is `1`.

- `base` is `{path,sha256}`.
- `video` is `{width,height,fps,total_frames,body_end_frame}`; `fps` is canonical `N/D`.
- `subtitle` is `{kind:"none"}` or
  `{kind:"bound",track:{path,sha256},validation:{path,sha256}}`.
- `profile` is `{path,sha256}`.
- `content` is `{title,notes,markers}`.

`content` deliberately has no caption field. Bound caption text and half-open frame intervals
come only from the validated subtitle projection. Validation is replayed in an isolated snapshot
and checked against the current base bytes, selected audio packet identity, duration, edit-plan
identity when present, and actual picture frame clock. A stale track fails rather than falling
back to handwritten dialogue.

`title` is `{primary,secondary}`. Notes are
`{id,start_frame,end_frame,lines,position:{left,top}}`. Markers are
`{id,start_frame,end_frame,text,visible}`; hidden markers remain in the schedule and state key.
Intervals are integer, half-open, non-empty, and inside `body_end_frame`.

For a copy-only revision, copy the current plan and edit only the adopted `content` items;
keep `base`, `video`, `subtitle`, and `profile` unchanged. Preserve unmentioned items, their IDs,
timing, positions, and marker visibility. An empty `notes` array in a complete plan removes all
notes: do not interpret an external proposal's “no additions” as a request to erase existing notes.
Resolve proposed times against the current picture before writing frame intervals. Validate the
candidate with `--plan-only` when rendering is deferred; the plan itself is not an updated video.

## Profile version 1

A `theme_foreground_profile` declares `canvas`, `picture`, an underlay, fonts, and exactly
`title`, `caption`, `note`, and `marker` roles. Local assets use full lowercase SHA-256. The
underlay must be an actual full-canvas RGBA PNG and every pixel in the picture rectangle must have
alpha zero. A completely transparent underlay is valid.

Fonts are `{id,weight,path,sha256,platform_families,postscript_names}`. Both allowlists are
non-empty and explicit. Role `font_weight` must match its font. An anchor is:

```json
{"horizontal":"left|center|right","vertical":"top|bottom","x":0,"y":0,"width":100}
```

Coordinates are absolute canvas coordinates. Horizontal anchoring places the width around `x`;
vertical anchoring makes `y` the box top or bottom. Mandatory `style.text_align` is independent.

Standard roles contain exactly `{anchor,style,fit}`; title adds `shared_fit:true`, `gap`, and
two `colors`. Styles accept only `font_id`, `font_weight`, `preferred_size`, `minimum_size`,
`line_height:{kind:"ratio|px",value}`, `letter_spacing`, `#RRGGBB[AA]` `color`, `skew_x`,
structured `shadow`/`stroke` or null, `wrap:"nowrap|pre_wrap_anywhere"`, `max_lines`, and
`text_align`. Raw CSS, HTML, JavaScript, remote URLs, and caller page code are not accepted.

Fit is finite and versioned:

```json
{"policy":"fixed|cjk_count_v1","version":1,"width":940,"count_subtract":1,"addend":0.7}
```

For `cjk_count_v1`, `count` is at least one and is the longest line's Unicode code-point count.
The candidate is exactly `(width - count_subtract * count) / (count + addend)`, clamped between
the declared minimum and preferred size. DOM geometry and `max_lines` guards reject overflow;
the producer never edits or truncates text. The title pair shares the smaller fit, has two
explicit colors, and uses `max(gap.minimum, gap.visible - size * gap.size_subtract)`.

## Browser and output boundary

The bundled trusted DOM sets caller text through `textContent`. It loads inlined, hashed assets
with `FontFace.load`, awaits `document.fonts.ready`, checks `document.fonts.check`, and settles
for two animation frames. For every rendered leaf, CDP `CSS.getPlatformFontsForNode` must report
positive glyph use from a custom font whose family and PostScript name both match that font's
allowlists; the receipt records those rows. This detects fallback but is not universal Unicode
coverage or legibility proof. The producer records element client geometry, computed font
evidence, and `Range.getClientRects()` text-fragment boxes only to count wrapped DOM lines. Those
fragment boxes are explicitly not classified as glyph ink bounds or legibility evidence.

The dependency-free Node helper launches the specified Chrome on `about:blank` with a new
temporary profile and DPR 1. CDP Fetch fails any non-`data:` page request. It never uses a real
profile, `--no-sandbox`, a network asset, React, npm, or caller code. Timeouts terminate only the
owned process group; renderer resource hashes are rechecked throughout the run.

Only unique active states are captured. Output exposes exact contiguous zero-based
`frames/frame_%06d.png` hardlinks through `body_end_frame`; state identity includes hidden marker
state. A successful directory also contains normalized input/caption/schedule snapshots,
per-state layout/font evidence, the ordered PNG digest, and `producer_receipt.json` for a
subsequent compose-plan identity reference. The receipt explicitly leaves listening, acoustic
alignment, normal-speed review, creative approval, and release approval unchecked.

CDP API references: [DOM domain](https://chromedevtools.github.io/devtools-protocol/tot/DOM/)
and [CSS domain](https://chromedevtools.github.io/devtools-protocol/tot/CSS/). Font family and
PostScript allowlists are caller data for the explicit hashed face, not an allowlist for system
fallback. Platform observations may differ from offline metadata (for example, a browser may
report a shorter PostScript name); callers must record the browser-observed custom-face identity
explicitly rather than weakening the gate.
