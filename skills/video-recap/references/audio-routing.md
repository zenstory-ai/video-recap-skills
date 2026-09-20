# Recap audio routing

`--edit-mode` selects the picture workflow; `--audio-mode` independently
selects the final audio workflow:

| Audio mode | Narration validation/review | TTS | Assemble input |
| --- | --- | --- | --- |
| `narration` (default) | Runs as before | Runs as before | source or rendered cut |
| `source-mix` | Not applicable | Not run | current source or rendered cut |
| `adopted-packet-copy` | Not applicable | Not run | current source or rendered cut |

`full` with either non-narration mode goes directly to assemble and does not
implicitly run understanding. `cut` still requires the existing `clip_plan`:
the first run analyzes and pauses for that plan, then later runs the established
single- or multi-source cut renderer. It does not pause for `narration.json`.

## Identity and resume

`recap_run_manifest.json` binds `{mode, selected_stream_index}` separately from
understanding/material settings. Changing audio mode or stream requires a new
work directory; it never reuses narrated TTS accidentally. Legacy manifests
without the audio field are interpreted as `narration`, stream 0, preserving
old in-flight narration runs. Continuation commands retain the audio selection.

An unbound work directory containing `narration.json` is ambiguous and cannot
be adopted by a source mode. Existing run-local QC stages are reset after the
work-directory policy is validated. Source runs record TTS, narration
validation, and narration review as `not_applicable`; old `tts_meta.json` and
narration review files are not read as evidence for the current run.

## Fail-closed combinations

- `dub` cannot be combined with a non-narration audio mode.
- Explicit TTS/text-policy options are rejected in source modes rather than
  ignored. Ambient TTS provider/voice configuration is inert because TTS does
  not run, and continuation commands do not promote it into explicit flags.
- Cut audio modes support selected stream 0 only. Full mode may pass
  another stream to assemble, subject to assemble/export support.
- Source modes do not support advisory MiMo QC; use `off`.

## Packaging and frozen-audio boundary

Source modes never regenerate `visual_overlays.json`. A work directory already
bound to narration is rejected, preventing narration-derived overlays from
silently becoming source-mode author input. Otherwise existing overlays are
treated as caller-authored packaging and preserved.

`adopted-packet-copy` freezes only the selected audio stream on the actual file
passed to assemble. In `full`, that is the input video. In `cut`, the cut stage
has already rendered `edited_source.mp4`; packet-copy verification therefore
applies to that file's audio, **not** to AAC packets from any original source.
This routing does not prove listening quality or publication approval.
