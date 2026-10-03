# Multi-track timeline (`timeline.json`) + optional 剪映 export

`video-assemble` always emits `timeline.json`: a backend-neutral description of
the finished recap. ffmpeg remains the canonical renderer; the optional 剪映
exporter consumes the same timeline to make an editable sidecar draft. Timeline
times are seconds and volumes are gains, so this file has no 剪映-only units.

## `timeline.json` schema v2

```jsonc
{
  "schema_version": 2,
  "canvas": {"width": 1280, "height": 720, "fps": 25},
  "duration": 315.0,
  "tracks": [
    {"kind": "video", "name": "video", "clips": [
      {"source_path": "/orig.mp4",
       "source_start": 243.0, "source_end": 268.0,
       "timeline_start": 0.0, "timeline_end": 25.0,
       "audio": {"role": "original", "base_gain": 0.85,
                 "volume_keyframes": [{"t": 0.0, "gain": 0.85},
                                       {"t": 2.19, "gain": 0.2}]}}
    ]},
    {"kind": "audio", "name": "narration", "role": "narration", "segments": [
      {"source_path": "/_placed_0000.wav", "timeline_start": 2.19,
       "timeline_end": 5.9, "gain": 1.0, "text": "…",
       "overlaps_speech": true,
       "source_duck_end": 6.1, "source_restore_at": 6.35,
       "source_handoff_status": "sentence_boundary"}
    ]},
    {"kind": "audio", "name": "bgm", "role": "bgm", "loop": true,
     "segments": [{"source_path": "/bgm.mp3", "timeline_start": 0.0,
                    "timeline_end": 315.0, "gain": 0.18,
                    "volume_keyframes": []}]},
    {"kind": "text", "name": "subtitle", "segments": [
      {"text": "…", "timeline_start": 2.19, "timeline_end": 5.9}
    ]},
    {"kind": "image", "name": "image", "segments": [
      {"source_path": "/card.png", "timeline_start": 3.0,
       "timeline_end": 7.0,
       "scale": {"x": 0.5, "y": 0.5},
       "position": {"x": 0.25, "y": -0.2}}
    ]}
  ]
}
```

- **video** — source clips plus editable original-audio volume automation. In
  cut mode, explicit `--source-video` keeps references on the real source ranges.
- **narration** — placed TTS segments. `source_path` points to the exact complete
  per-segment PCM used by the canonical mix (`_placed_*.wav`), never a longer
  pre-fit file that an editor would trim at `timeline_end`.
- **bgm** — optional looped music with its own volume automation.
- **subtitle** — display-ready narration subtitles.
- **image** — optional local photo overlays. The optional `scale` / `position`
  transform uses normalized canvas-center coordinates with positive Y upward. `build_timeline(...,
  image_segments=[...])` is the programmatic entrypoint. Static packaging layers
  declared in `work_dir/packaging_layers.json` (for example from a bound
  `packaging` template) become full-length image segments whose position and
  scale match the ffmpeg render; recap does not invent any other image overlays.
- `volume_keyframes` are timeline-absolute `{t, gain}` points with linear ramps.
  Original-audio handoff keyframes stay low through the final source phoneme
  (`source_duck_end`) and release only inside the measured sentence pause, reaching
  idle at `source_restore_at`; ffmpeg and the editable timeline share this shape.

The exporter reads the fields above. It validates a copy of the timeline and
rejects two kinds of input outright instead of guessing:

- `schema_version` must be `2`. A 0.3-era v1 timeline is rejected; v2 only added
  the image track, so setting `schema_version` to `2` is the whole migration.
- the former hand-authoring extensions (`speed`, `reverse`, `opacity`,
  `rotation_degrees`, `flip`, `transition`, `mask`, `lut`, `chroma`, `compound`,
  `green_background`, `style`, `style_id`, `words`, top-level `style_presets` /
  `resource_packages`) and the `sound` / `sticker` / `text_template` /
  `video_effect` / `face_effect` resource tracks fail with an explicit error.
  No pipeline stage produced them, so the exporter no longer maps them.

## Optional 剪映 / JianYing export

`--export-jianying` / `EXPORT_JIANYING=1` maps the timeline into a folder with
`draft_content.json`, `draft_info.json`, and `draft_meta_info.json`. The current
adapter aligns its local-draft contract with
[`duoec/duo-video`](https://github.com/duoec/duo-video) commit
`ef4eb46c823910553f901649f2f13fd7575e748f`:

- root schema profile `version: 360000`, `new_version: 111.0.0`, and template
  `app_version: 5.9.5-beta1`, including `common_mask` in the material skeleton;
- all segment times converted to integer microseconds at one boundary;
- narration interval ends are rounded outward before that conversion, and export
  probes bundled PCM duration; this prevents timestamp rounding from shaving even
  a few samples from the final word;
- narration and BGM as audio tracks, video/photo as video tracks, subtitles as
  text tracks; regular segments use the schema `render_index` /
  `track_render_index` value `2`;
- subtitle materials use `type: subtitle`, with a zero-based full
  `source_timerange` and timeline placement in `target_timerange`;
- native `KFTypeVolume` keyframes and split BGM loops remain editable;
- true half-open interval allocation: adjacent segments reuse a track, while
  overlapping image/video/text material is moved to a numbered sibling track;
- same-type first tracks use `flag: 0`; additional tracks use `flag: 2`.

The upstream README's “verified with 剪映 v10.1.0” is an application smoke-test
claim. The embedded `5.9.5-beta1` value is the upstream JSON template profile;
it is **not** a claim that this project installs or emulates 剪映 5.9.5. We keep
those two facts separate and do not promise compatibility with every desktop
release without a real-app smoke test.

### Portable media bundle

Bundling is on by default because a clone or moved project must open without the
original machine's absolute paths. The writer copies and de-duplicates media to:

```text
Resources/local/video/
Resources/local/audio/
Resources/local/image/
```

Material paths in `draft_content.json` use 剪映's
`##_draftpath_placeholder_…_##/Resources/local/...` form. Type-0 entries in
`draft_meta_info.json` index each copied file with `file_Path`, `metetype`
(`video` / `music` / `photo`), ID, MD5, duration, dimensions, import timestamps,
and rough-cut/sub ranges. Filename collisions are resolved within each resource
kind. Missing media is reported and left as an honest external reference rather
than being recorded as successfully bundled.

`--jianying-no-bundle-media` is only for environments where 剪映 can reach the
original paths. It trades portability for avoiding a copy and is not the
recommended clone-ready workflow. Non-empty draft folders are never overwritten;
a numbered sibling such as `recap_demo_2` is created. The entire folder is staged
and atomically renamed, so a copy/write exception does not publish a half-draft.

### Duo-video protocol alignment

The adapter is pinned to `duo-video@ef4eb46`; it deep-copies the upstream MIT
JSON templates before replacing authored values. It maps exactly the tracks the
recap pipeline writes:

| Timeline | Output |
| --- | --- |
| `video` clips with original-audio volume automation | `materials.videos` plus video segments with `KFTypeVolume` keyframes |
| `audio` tracks (narration, BGM, prepared bed) | `materials.audios`; looped BGM is split into windowed pieces |
| `text` subtitle track | `type: subtitle` text materials with one default UTF-16 rich-text style |
| `image` overlays with `scale` / `position` | `type: photo` video materials with an editable segment `clip` transform |

## Isolation and failure behavior

- Export modules are lazy-imported only after `--export-jianying`; the ffmpeg
  render path does not import or depend on them.
- The adapter is Python stdlib + ffprobe only; no vendored package is required.
- Export failure is logged and never invalidates an already-rendered ffmpeg MP4.
- The source clip is un-burned, so hardcoded source subtitles remain visible in
  the editable draft. ffmpeg remains the authoritative final mix.

## Verification boundary and manual smoke checklist

Automated golden tests compare root, video, audio, text, rich-text, and
base-segment templates against the pinned upstream revision. Structural tests
cover meta output and exercise every row in the table. On macOS,
JianYing Pro `10.8.7-beta1` detected and registered a generated bundled smoke
draft (`copy_draft_external`, `errno: 0`). Manual verification then opened it,
confirmed online video/narration/BGM/subtitle/photo tracks and a valid preview,
saved it, closed it, and reopened it with the same editable timeline.

AutoJY is a separate desktop-automation/export pipeline in the duo-video
ecosystem, not part of the draft JSON protocol. This repository does not ship or
claim AutoJY UI automation, rendered-file upload, task-status callbacks, or an
automated final-video export smoke test.

With a desktop 剪映 install, generate a bundled draft and verify:

1. it appears in the draft list;
2. video, narration, BGM, subtitles, and photo overlays are online/editable;
3. subtitle semantics, track order/flags, and overlap lanes survive reopening;
4. volume automation is visible/audible;
5. no absolute-path/offline-media warning appears.

## Acknowledgements

The draft schema also follows
[pyJianYingDraft](https://github.com/GuanYixuan/pyJianYingDraft) and
[capcut-mate](https://github.com/Hommy-master/capcut-mate) (Apache-2.0). The
duo-video alignment independently implements the builders and vendors only the
pinned JSON protocol templates under duo-video's MIT license; no upstream
executable code, resource package, adapter binary, or credential is vendored.
