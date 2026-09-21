# Source and score bed producer

`source_score.py` is a standalone producer for explicitly authored source-audio
intervals and one continuous score playhead. It does not mix narration, inspect old
masters, infer dialogue, choose music, retime sound, normalize loudness, limit peaks,
or publish a final video.

```bash
python3 scripts/source_score.py source_score_plan.json --output-dir a-new-directory
```

The output directory must not exist. Success creates:

- `source_bed.wav` — reordered/pre-gained source intervals plus explicit silence;
- `score_bed.wav` — one continuous raw-score window or an adopted frozen stem;
- `prepared_bed.wav` — source plus score, without master gain/limiting;
- `prepared_bed_receipt.json` — input paths, clocks, sample ranges, PCM facts and QC facts.

All three beds are `pcm_f32le`, 48 kHz, stereo. Float output avoids repeated integer
quantization and preserves values outside `[-1,1]`; the receipt records actual peak,
finite-sample status, and `FLOAT_PRESERVED_NO_MASTER`. A later explicitly adopted
consumer owns final master gain and delivery encoding.

## Strict plan schema v1

```json
{
  "artifact": "source_score_plan",
  "schema_version": 1,
  "output": {"sample_rate": 48000, "channels": 2, "total_samples": 1920000},
  "source_segments": [
    {
      "id": "protected-dialogue-1",
      "path": "/local/source.mov",
      "audio_stream": 0,
      "source_fps": "24/1",
      "source_start_frame": 120,
      "source_end_frame": 180,
      "output_start_sample": 240000,
      "gain": 1.0,
      "fade_in_samples": 0,
      "fade_out_samples": 96,
      "fade_shape": "linear",
      "role": "protected_original"
    }
  ],
  "source_silence": [
    {"output_start_sample": 0, "output_end_sample": 240000, "role": "silence"},
    {"output_start_sample": 360000, "output_end_sample": 1920000, "role": "silence"}
  ],
  "score": {
    "kind": "raw",
    "path": "/local/score.wav",
    "audio_stream": 0,
    "source_offset_sample": 384000,
    "gain": 0.18,
    "fade_in_samples": 33600,
    "fade_out_samples": 120000,
    "fade_shape": "half_cosine"
  }
}
```

Every source range is half-open in CFR picture frames. Version 1 accepts H.264 and
HEVC picture sources whose complete packet PTS/duration grid proves one zero-origin
packet per same-speed frame. Frame boundaries are projected onto the 48 kHz clock by
rounding the exact position half-up once, so `48000/fps` need not be an integer and a
broadcast rate such as 30000/1001 is accepted; every derived bound comes from those
rounded positions. Other codecs, incomplete packet timing, VFR, retime fields,
nominal time seeks, and unknown fields are rejected rather than called verified
(legacy `sha256` keys are ignored).
Each unique selected source stream is decoded from its actual PTS clock to canonical
48 kHz stereo exactly once. All authored intervals are then cut from that canonical
sample array—never independently decoded or `-ss`-seeked per edit.

The source segment duration is `(source_end_frame-source_start_frame)*48000/fps`.
Its output end is derived, not declared. Source segments and explicit silence ranges
must form an exact, nonoverlapping partition of `[0,total_samples)`. There are no
implicit gaps. Supported roles are `protected_original` and
`mixed_original_under_narration`; their gains and labels are retained unchanged.

Source fades are explicitly `linear`. A fade of `N>=2` samples uses inclusive
endpoints: fade-in gain is `i/(N-1)`, and fade-out gain is `(N-1-i)/(N-1)`. Thus the
first/last samples are exactly zero and the opposite endpoints exactly one. Zero
means no fade; a one-sample fade is rejected as ambiguous. Fade windows may not
overlap within a segment.

## Score modes

`raw` decodes the selected score stream once, advances one playhead from
`source_offset_sample`, and takes exactly `total_samples`. The input must be long
enough; there is no implicit loop. Gain and one pair of whole-score fades are applied
once, so score playback never resets at picture/source cuts. `linear` and
`half_cosine` fades use inclusive endpoints.

When the adopted source bed is already the complete soundtrack and no additional
music is selected, use the exact no-score form:

```json
{"kind":"none"}
```

It accepts no other fields. `score_bed.wav` is real all-zero float PCM at the exact
output length, while `prepared_bed.wav` is an exact canonical PCM copy of
`source_bed.wav`; the receipt records `kind:none` rather than inventing a music asset.

An adopted score that already contains its chosen offset/gain/fades uses the exact
alternative schema:

```json
{"kind":"frozen","path":"/local/frozen.wav","audio_stream":0}
```

Frozen input must be PCM16, PCM24, or PCM float, 48 kHz stereo, and exactly
`total_samples`. PCM16/24 values convert exactly to float canonical representation.
No offset, gain, fade, loop, normalization or other processing fields are accepted.
The receipt records the frozen file path and its probed PCM facts.

## Receipt and failure boundary

`prepared_bed_receipt` schema version 1 records the plan path, each input path,
selected stream, actual CFR/audio clocks, canonical decoded PCM facts, resolved
input/output sample ranges, gains, fades, roles, and each output's path, byte size,
format, sample count, finite status and peak.

Every declared input must exist and probe as declared before work starts. A selected
source range must fit the actual canonical decoded samples; silence padding can never
conceal a short source. WAVs remain hidden staging artifacts until every output
validates. FFmpeg failure, invalid ranges, non-finite PCM, or an existing target
directory cannot publish final-named beds or a receipt.
Diagnostic command/log/intermediate files may remain in the unique failed directory.

To consume a completed receipt with explicitly adopted narration, use the strict
`--audio-mix-adoption` path in `explicit-audio-mix.md`. To deliver an already complete
prepared bed without narration, a dedicated prepared-audio renderer will follow in a
later release. Do not feed `prepared_bed.wav` through legacy source ducking or
ambient BGM/loudness settings.

## 与旧入口的关系（中文摘要）

原片完整解码一次再切样本，分别执行保留对白、低位原声和明确静音；渐变必须显式给定。
已处理的音乐轨走 `frozen`，不能再次偏移、调增益或加渐变。若采用的原声底轨本身已包含
完整音乐决定且不再叠加配乐，使用严格的 `score:{"kind":"none"}`；它生成真实全零 score，
并保持 prepared 与 source 逐字节相同，不伪造静音音乐资产。

这一步仅输出声音底轨和来源回执，不是最终视频。旧入口保留兼容行为；调用方须区分
“底轨已验证”“配音已验证”和“完整混音已验证”三种状态，不能相互冒充。
