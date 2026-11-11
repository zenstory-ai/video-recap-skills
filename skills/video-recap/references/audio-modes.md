# Assembly audio modes

`assemble.py` keeps `narration` as its API and CLI default. Non-narration
behavior is opt-in with `--audio-mode`. Source audio is always the input's first
audio stream (FFmpeg `0:a:0`); remux a multi-track source first if the wanted
track is not the first one.

## `narration`

- Requires non-empty `tts_meta.json` segments.
- Builds/places narration WAV, performs source ducking (`SPEECH_DUCKING_VOLUME=0.12` / `ZONE_DUCKING_VOLUME=0.12`, returning to `IDLE_ORIG_VOLUME=1.0` in intentional original-dialogue gaps) and optional BGM mix, then applies the configured linear final loudness/limiter stage and AAC encoding.
- **BGM purity discipline**: When supplying `BGM_PATH` for narrated recaps, always choose a **pure instrumental** track (zero vocals/lyrics) matched to the scene's emotional register. If the source footage already carries a loud vocal OST song that cannot be cleanly avoided, leave `BGM_PATH` unset so narrator speech, actor lines, and vocal lyrics never collide.
- Missing source audio may use the existing synthetic-silence fallback.

## `source-mix`

- Does not read or require `tts_meta.json` and never creates narration audio.
- Uses the input's first audio stream, applies `IDLE_ORIG_VOLUME`, mixes a
  declared `BGM_PATH` when present, and runs the configured final
  loudness/limiter stage before AAC encoding.
- A declared but missing `BGM_PATH` is an error in source-mix mode. Narration
  mode warns and skips the missing music.
- This is a processed mix: the source audio always passes through `volume`, the
  final loudness/limiter stage and AAC encoding. `assembly_qc.json` reports the
  operations that actually ran.

## Processed audio delivery

Both modes re-encode audio to AAC 192 kbps at 48 kHz, apply a smooth tail fade-out
(`ENDING_AUDIO_FADE_MS`, default `350` ms) on original audio and BGM so the ending never
cuts abruptly, and trim to the video duration with `-t`. No mode leaves the raw source
audio packets untouched.

The closest settings for a picture or packaging re-render are
`--audio-mode source-mix` with `IDLE_ORIG_VOLUME=1.0` and `FINAL_LOUDNORM=0`. The
limiter and the AAC generation still apply, so packet identity, the original sample
rate and timescale, and AAC priming and tail packets are not preserved.

These modes do not create a commercial quality profile, automatic speech
alignment, listening approval, or release approval.

`timeline.json` represents source-mix as one complete clip from the current input
video, with `IDLE_ORIG_VOLUME` as the clip's `base_gain`. It records processed
delivery and is not claimed to be reconstructable by an editor.
