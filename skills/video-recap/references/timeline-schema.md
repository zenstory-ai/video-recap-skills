# `timeline.json` v3 与 `check.json` v1 契约

`timeline.json` 为剪辑、配音、包装、体检与剪映导出的唯一契约；除 `schema_version`、`canvas`、`sources`、`video` 外均可选。

```json
{
  "schema_version": 3,
  "meta": {"title": "Demo", "voice": {"engine": "local", "voice_id": "default", "consent": "synthetic"}},
  "canvas": {"width": 1080, "height": 1920, "fps": "30000/1001", "backdrop": {"kind": "blur", "color": "#000000", "blur_sigma": 20}, "viewport": {"x": 0.0, "y": 0.18, "w": 1.0, "h": 0.56, "fit": "cover"}},
  "sources": {"s1": "raw/ep1.mp4", "bgm1": "assets/bgm.mp3", "sfx1": "assets/hit.wav", "p1": "work/overlay.mov"},
  "video": [{"id": "v1", "source": "s1", "in": 12.4, "out": 18.2, "crop": {"x": 420, "y": 0, "w": 608, "h": 1080}, "focus": [0.5, 0.35], "audio": "keep", "audio_gain": 1.0, "keep_windows": [[13.1, 15.0]]}],
  "audio": {
    "narration": [{"id": "n1", "at": 0.3, "file": "media/vo/n1.wav", "text": "2026年开篇", "spoken_text": "二零二六年开篇", "gain": 1.0, "duration": 2.75}],
    "bgm": [{"id": "b1", "source": "bgm1", "in": 0.0, "at": 0.0, "out": 42.0, "loop": true, "gain": 0.25, "fade_in": 0.3, "fade_out": 1.5}],
    "sfx": [{"id": "x1", "source": "sfx1", "at": 5.8, "gain": 0.7}]
  },
  "overlays": [{"id": "o1", "source": "p1", "start": 0.0, "end": 4.0, "rect": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}, "fade_in": 0.0, "fade_out": 0.0, "motion": "none"}],
  "subtitles": [{"start": 0.3, "end": 3.05, "text": "2026年开篇", "style": "narration"}],
  "subtitle_style": {"font_name": "PingFang SC", "font_file": null, "font_size": 46, "primary_color": "#FFFFFF", "outline_color": "#000000", "outline": 2.5, "shadow": 1.0, "glow": 0.0, "max_chars": 16, "safe_y": [0.74, 0.88], "strip_trailing_punct": true},
  "mix": {"target_lufs": -14.0, "true_peak_dbtp": -1.5, "duck": {"under_narration": 0.18, "bgm_under_narration": 0.12, "fade_s": 0.25, "bridge_s": 0.5}}
}
```

## 约定与 `check.json`

- `in/out`、`keep_windows` 为源秒数，其余为成片秒数；`spoken_text` 送 TTS，字幕用 `text`；`strip_trailing_punct` 默认 `true`；`font_file` 所在目录交给 libass 找字体，`font_name` 须与该文件的字体族名一致；`overlays[].motion`：`none|slide_up`。`crop` 源像素，`viewport`/`rect`/`focus`/`safe_y` 归一化 `[0,1]`；`fit`：`contain|cover`；`overlays[]` 支持 PNG 与 `.mov`/`.webm` 透明视频。
- `check.json` v1 含 7 键（`container_valid`/`decode_integrity`/`geometry_and_cfr`/`av_sync`/`audio_health`/`visual_health`/`subtitle_and_timing`，`status`：`pass|warn|fail|skip`）与 `review_hints: []`、`telemetry`、`limitations`：仅 100% 确定性损坏判 `fail`（`ok: false`）；黑屏/定格/死寂/切边音频/重叠记 `warn` + `review_hints`。
