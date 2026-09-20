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

## Local adopted full-sound assembly

The strict local-adoption route accepts one prebuilt picture in `full` mode and
exactly this all-or-none bundle:

```text
--tts-meta PATH --narration-adoption PATH --audio-mix-adoption PATH
```

It requires narration mode, stream 0, a new explicit `--work-dir`, and a delivery path
that does not already exist. `--output-dir` is optional; when omitted, delivery uses the
new work directory's parent. The route calls only the video-assemble CLI; it does
not run understanding, script validation, narration review, voiceover, MiMo QC, cut,
continuation, editor export, or material-cache reuse. Ambient TTS provider and voice
configuration are inert. Explicit TTS/voice/review/MiMo/editor flags are rejected rather
than silently ignored.

`recap_run_manifest.json` records the resolved path and independent full SHA-256 for all
three local artifacts under `audio.local_adoption`. Analysis settings remain separate and
unchanged. Recap validates routing and freshness; video-assemble remains authoritative
for the adoption schemas, hashes, PCM identities, frame clock, mix, bindings, and final
media transaction. This path executes caller-adopted assets and does not claim that recap
authored or approved their story, voice, or mix.

## Keep adopted voice after a cut

The single input above is the **finished picture**, which may contain one or many source
episodes. Use two existing stages rather than sending an old mix binding into a new cut:

1. Finish the selected cut with `video-cut`: either run `recap.py ... --edit-mode cut`,
   or call that skill's own `scripts/cut.py` from its installed directory, with an
   existing clip plan and source manifest:

   ```bash
   python3 <video-cut>/scripts/cut.py ep1.mp4 --work-dir CUT_WORK \
     --sources-manifest SOURCES_JSON --no-narration-map
   ```

   A single-source cut omits `--sources-manifest`. For already locked frame decisions,
   use the picture-plan path instead. Read `clip_plan_validated.json` (or the locked path's
   `picture_map.json`) before placing sound.
2. Keep the selected WAVs, `tts_meta.json`, and `narration_adoption.json` when the words,
   WAV bytes, and selected voice are unchanged. Do not run voiceover just to move a line.
   In this explicit mix, new `output_start_sample` values own placement; old generation
   windows in `tts_meta.json` are not the revised output timeline.
3. Match source dialogue, ambience and music to the new picture. Reuse the prepared bed
   only if that sound and its complete sample clock remain appropriate; otherwise rebuild
   it with the existing `video-assemble` source/score producer. Write a **new** mix adoption
   with the actual picture hash, prepared receipt, total samples and complete WAV placements.
   Reordering shots can require moving dialogue too; changing a hash alone cannot fix that.
4. Run the local adopted `full` command in `SKILL.md` with `CUT_WORK/edited_source.mp4` and a new
   assembly work/delivery directory. Inspect the emitted narration placements and subtitles
   against that mixed output. Rebind any precise subtitle track to the new finished audio.

The same steps handle a later revision: keep unchanged picture/voice assets, rebuild only
the affected bed and placement decisions, then assemble into a new directory. The local
assembly command still does not resume an old work directory or decide new placements.

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
- Local adopted full-sound assembly consumes a prebuilt picture in `full` mode; use the
  two-stage workflow above for single- or multi-source cuts. That assembly invocation
  does not resume an old work directory or export an editor draft.

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
