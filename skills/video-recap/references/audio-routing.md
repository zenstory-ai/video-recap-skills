# Recap audio routing

The routes that combine `--edit-mode` and `--audio-mode` are in `SKILL.md` §1.

| Audio mode | Narration validation | TTS | Assemble input |
| --- | --- | --- | --- |
| `narration` (default) | Runs as before | Runs as before | source or rendered cut |
| `source-mix` | Not applicable | Not run | current source or rendered cut |

`full` with `source-mix` goes directly to assemble and does not implicitly run
understanding. `cut` still requires the existing `clip_plan`: the first run analyzes
and pauses for that plan, then later runs the single- or multi-source cut renderer.
It does not pause for `narration.json`.

## Identity and resume

`recap_run_manifest.json` binds `{mode, selected_stream_index}` separately from
understanding/material settings; `selected_stream_index` is always 0 now, and a
manifest recording another stream is a mismatch. Changing audio mode requires a new
work directory; it never reuses narrated TTS accidentally. A manifest without the
audio field is also a mismatch, so resuming such a run needs a new work directory.
Continuation commands retain the audio mode.

An unbound work directory containing `narration.json` is ambiguous and cannot
be adopted by a source mode. Source runs skip TTS and narration
validation; an old `tts_meta.json` is not read as evidence for the current run.

## Fail-closed combinations

- Explicit TTS options are rejected in source modes rather than
  ignored. Ambient TTS provider/voice configuration is inert because TTS does
  not run, and continuation commands do not promote it into explicit flags.

## Packaging and audio

Static image layers in `packaging_layers.json` apply identically across audio modes,
while work directories remain strictly bound to a single audio mode so narration-mode
artifacts never leak into `source-mix` runs.

No route leaves raw source audio packets untouched. The closest settings for unity-gain
source delivery are `--audio-mode source-mix` with `IDLE_ORIG_VOLUME=1.0`,
`ENDING_AUDIO_FADE_MS=0`, and `FINAL_LOUDNORM=0`, which keep gain at unity while still
running the limiter and AAC encoding.
