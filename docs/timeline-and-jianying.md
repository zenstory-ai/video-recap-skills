# Multi-track timeline (`timeline.json` v3) + optional 剪映 export

`timeline.json` (`schema_version: 3`) is the single backend-neutral machine contract shared by `render.py`, `check.py`, `packaging.py`, `dashboard.py`, `library.py`, and the standalone 剪映 / CapCut exporter `export_jianying.py`. `ffmpeg` (via `render.py`) remains the canonical renderer; `export_jianying.py` consumes the exact same `timeline.json` to emit an editable multi-track draft. All times are in seconds and volumes are linear gains, with zero editor-specific units.

## `timeline.json` schema v3

All top-level fields except `schema_version`, `canvas`, `sources`, and `video` are optional:

```json
{
  "schema_version": 3,
  "meta": {
    "title": "Demo Edit",
    "voice": {"engine": "fish", "voice_id": "voice-id", "consent": "licensed"},
    "assets": [{"source_id": "bgm1", "license": "CC-BY", "credit": "Artist"}]
  },
  "canvas": {
    "width": 1080, "height": 1440, "fps": "25",
    "backdrop": {"kind": "none", "color": "#09090C", "blur_sigma": 20},
    "viewport": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0, "fit": "cover"}
  },
  "sources": {
    "s1": "raw/ep01.mp4",
    "bgm1": "assets/bgm.mp3",
    "sfx1": "assets/whoosh.wav",
    "pkg": "media/plates/remotion_overlay.mov"
  },
  "video": [
    {
      "id": "v1", "source": "s1", "in": 12.4, "out": 18.2,
      "focus": [0.5, 0.35],
      "audio": "keep", "audio_gain": 1.0,
      "keep_windows": [[13.1, 15.0]]
    }
  ],
  "audio": {
    "narration": [
      {"id": "n1", "at": 0.3, "file": "media/vo/n1.wav", "text": "2026年开篇", "spoken_text": "二零二六年开篇", "gain": 1.0, "duration": 2.75}
    ],
    "bgm": [
      {"id": "b1", "source": "bgm1", "in": 0.0, "at": 0.0, "out": 42.0, "loop": true, "gain": 0.25, "fade_in": 0.3, "fade_out": 1.5}
    ],
    "sfx": [
      {"id": "x1", "source": "sfx1", "at": 5.8, "gain": 0.7}
    ]
  },
  "overlays": [
    {
      "id": "remotion_packaging", "source": "pkg", "start": 0.0, "end": 42.0,
      "rect": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0},
      "fade_in": 0.0, "fade_out": 0.0, "motion": "none"
    }
  ],
  "subtitles": [
    {"start": 0.3, "end": 3.05, "text": "2026年开篇", "style": "narration"}
  ],
  "subtitle_style": {
    "font_name": "PingFang SC", "font_file": null, "font_size": 46,
    "primary_color": "#FFFFFF", "outline_color": "#000000",
    "outline": 2.0, "shadow": 1.5, "glow": 1.2, "max_chars": 15,
    "safe_y": [0.72, 0.82], "strip_trailing_punct": true
  },
  "mix": {
    "target_lufs": -14.0, "true_peak_dbtp": -1.5, "voice_over_bgm_db": 4.0,
    "duck": {"under_narration": 0.18, "bgm_under_narration": 0.12, "fade_s": 0.25, "bridge_s": 0.5},
    "flash_guard": true
  }
}
```

- **`video[]`** — sequential source clips (`in` / `out` and `keep_windows` in source seconds). `video[].crop` uses source pixels; `canvas.viewport`, `overlays[].rect`, `video[].focus`, and `subtitle_style.safe_y` use normalized `[0.0, 1.0]` top-left canvas coordinates (`export_jianying.py` converts to JianYing's center-origin Y-up coordinates).
- **`audio.narration[]`** — synthesized TTS clips with measured `duration` (no speed-warping). Optional `spoken_text` feeds TTS while clean `text` feeds subtitles.
- **`audio.bgm[]` & `audio.sfx[]`** — optional looped BGM and real-audio SFX tracks. Both `render.py` and `export_jianying.py` compute original-audio and BGM ducking automation from the exact same helper (`_audio.py::ducking_keyframes`).
- **`overlays[]`** — static RGBA PNG plates (from `packaging.py apply`) or transparent animated video overlays (`.mov` ProRes 4444 / `.webm` VP9 from `packaging.py remotion-render`).
- **`subtitles[]` & `subtitle_style`** — display-ready narration and original-dialogue subtitles with semantic balanced wrapping and `strip_trailing_punct` (default `true`).

## Standalone 剪映 / JianYing export (`export_jianying.py`)

```bash
python3 skills/video-recap/scripts/export_jianying.py timeline.json --out-dir out/jianying_draft
# Optional: skip media copying only when paths stay fixed on the same machine
python3 skills/video-recap/scripts/export_jianying.py timeline.json --out-dir out/jianying_draft --no-bundle-media
```

`export_jianying.py` maps `timeline.json` into a folder with `draft_content.json`, `draft_info.json`, and `draft_meta_info.json`. The adapter aligns its local-draft contract with [`duoec/duo-video`](https://github.com/duoec/duo-video) commit `ef4eb46c823910553f901649f2f13fd7575e748f` (skeleton templates live under `skills/video-recap/references/jianying/`):

- root schema profile `version: 360000`, `new_version: 111.0.0`, and template `app_version: 5.9.5-beta1`, including `common_mask` in the material skeleton;
- all segment times converted to integer microseconds at one boundary;
- narration, BGM, and SFX exported as separate audio tracks; base video clips and image/animated-video overlays exported as video tracks; subtitles exported as `type: subtitle` text tracks with UTF-16 character ranges;
- native `KFTypeVolume` keyframes (from `_audio.py::ducking_keyframes`) and split BGM loops remain editable;
- half-open interval allocation: adjacent segments reuse a track, while overlapping video/overlay/text material moves to a numbered sibling track (`flag: 0` on first track of each type, `flag: 2` on additional tracks).

The upstream README's “verified with 剪映 v10.1.0” is an application smoke-test claim; embedded `5.9.5-beta1` is the upstream JSON template profile, not a claim that this project installs or emulates 剪映 5.9.5.

### Portable media bundle

Bundling is on by default so a moved or cloned draft opens cleanly without machine-specific absolute paths. Files are copied and MD5-deduplicated into:

```text
Resources/local/video/
Resources/local/audio/
Resources/local/image/
```

Material paths in `draft_content.json` use JianYing's `##_draftpath_placeholder_…_##/Resources/local/...` form, indexed in `draft_meta_info.json` with `file_Path`, `metetype` (`video` / `music` / `photo`), ID, MD5, duration, dimensions, and timestamps. Missing media is reported honestly as external references rather than falsely marked bundled. `--no-bundle-media` skips copying when JianYing can directly access local paths. Existing non-empty draft folders are never overwritten (a numbered sibling is created instead), and staging + atomic rename prevents publishing half-written drafts.

### Duo-video protocol mapping

The adapter deep-copies the pinned MIT templates (`duo-video@ef4eb46`) before populating authored values:

| `timeline.json` v3 | JianYing draft output |
| --- | --- |
| `video[]` clips + original-audio ducking | `materials.videos` + base video track segments with `KFTypeVolume` keyframes |
| `audio.narration[]`, `audio.bgm[]`, `audio.sfx[]` | Separate `materials.audios` tracks; looped BGM split into windowed pieces with ducking keyframes |
| `overlays[]` (PNG or `.mov`/`.webm` video) | `type: photo` (images) or `type: video` (animated overlays) bundled into `Resources/local/{image,video}/` on overlay track(s) with normalized center-origin transforms |
| `subtitles[]` + `subtitle_style` | `type: subtitle` text materials with UTF-16 rich-text styling |

## Isolation and failure behavior

- `export_jianying.py` is a strictly unidirectional leaf CLI: core primitives (`probe.py`, `frames.py`, `transcribe.py`, `tts.py`, `render.py`, `check.py`) never import it.
- Pure Python 3.10+ stdlib + `ffprobe` only; zero third-party packages required.
- Export failure never invalidates an already-rendered `render.py` MP4.
- Source clips remain un-burned in the editable draft; `render.py`'s MP4 remains the authoritative final render.

## Verification boundary and manual smoke checklist

Automated tests verify `timeline.json` v3 export structure, MD5 bundling, UTF-16 subtitle ranges, `audio.sfx` tracks, animated `.mov`/`.webm` overlay bundling, and shared `_audio.py` ducking keyframes. On macOS, JianYing Pro `10.8.7-beta1` detected and registered a generated bundled smoke draft (`copy_draft_external`, `errno: 0`) with online video/narration/BGM/subtitle/overlay tracks that survived save-close-reopen.

AutoJY is a separate desktop-automation pipeline in the duo-video ecosystem and is not shipped or claimed here. With a desktop 剪映 install, verify:

1. the draft appears in the draft list;
2. video, narration, BGM, SFX, subtitles, and image/video overlays are online and editable;
3. track order, overlap lanes, and UTF-16 subtitle styling survive reopening;
4. `KFTypeVolume` ducking automation is visible and audible;
5. no absolute-path or offline-media warning appears.

## Acknowledgements

The draft schema also references [pyJianYingDraft](https://github.com/GuanYixuan/pyJianYingDraft) and [capcut-mate](https://github.com/Hommy-master/capcut-mate) (Apache-2.0). Only the pinned MIT JSON protocol skeletons under `skills/video-recap/references/jianying/` are vendored; no upstream executable code, binaries, or credentials are included.
