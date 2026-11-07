# Recap audio routing

The routes that combine `--edit-mode` and `--audio-mode` are in `SKILL.md` §1.

`full` with `source-mix` goes directly to assemble and does not implicitly run
understanding. `cut` still requires the existing `clip_plan`: the first run analyzes
and pauses for that plan, then later runs the cut renderer. It does not pause for
`narration.json`.

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
- The delivery routes removed after v0.6.2, and any work directory they left behind,
  fail before any stage with an error naming that tag; use a new `--work-dir`.

## Packaging and audio

Source modes never regenerate `visual_overlays.json`. A work directory already
bound to narration is rejected, preventing narration-derived overlays from
silently becoming source-mode author input. Otherwise existing overlays are
treated as caller-authored packaging and preserved.

No route leaves the audio untouched: exact audio needs v0.6.2. The closest settings
are `--audio-mode source-mix` with `IDLE_ORIG_VOLUME=1.0` and `FINAL_LOUDNORM=0`,
which keep the gain at unity but still run the limiter and another AAC generation.
