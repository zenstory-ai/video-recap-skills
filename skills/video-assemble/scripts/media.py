"""Media probing and source-clip provenance for video-assemble."""

import json
import os
from pathlib import Path

from artifacts import _explicit_source_video
from lib import log, run_cmd

def _load_cut_timeline_plan(work_dir):
    """The cut plan, preferring clip_plan_validated.json unless the raw plan is newer; None in full mode."""
    raw_plan_path = Path(work_dir) / "clip_plan.json"
    validated_plan_path = Path(work_dir) / "clip_plan_validated.json"
    if not validated_plan_path.exists():
        return json.loads(raw_plan_path.read_text(encoding="utf-8")) if raw_plan_path.exists() else None
    if not raw_plan_path.exists():
        return json.loads(validated_plan_path.read_text(encoding="utf-8"))
    if validated_plan_path.stat().st_mtime_ns >= raw_plan_path.stat().st_mtime_ns:
        return json.loads(validated_plan_path.read_text(encoding="utf-8"))
    return json.loads(raw_plan_path.read_text(encoding="utf-8"))


def _plan_clip_spans(work_dir):
    """Cut-mode clip spans [{source_start, source_end, output_start, output_end, entry}], or None.

    clip_plan.json is either a bare list or {"clips": [...]}; a clip names its source range as
    source_start/source_end or start/end. Clips without explicit output_start/output_end are laid
    out back to back on the output timeline.
    """
    plan = _load_cut_timeline_plan(work_dir)
    if plan is None:
        return None
    entries = plan["clips"] if isinstance(plan, dict) else plan
    spans, cursor = [], 0.0
    for entry in entries:
        ss = float(entry.get("source_start", entry.get("start")))
        se = float(entry.get("source_end", entry.get("end")))
        if "output_start" in entry:
            out_s, out_e = float(entry["output_start"]), float(entry["output_end"])
            cursor = max(cursor, out_e)
        else:
            out_s, out_e = cursor, cursor + (se - ss)
            cursor = out_e
        spans.append({
            "source_start": ss, "source_end": se,
            "output_start": out_s, "output_end": out_e,
            "entry": entry,
        })
    return spans


def _ratio_to_float(value, default=1.0):
    """Parse an ffprobe ratio ("4:3", "16/9" or a bare number); unknown ratios yield `default`."""
    value = value.strip()
    if value in {"", "0:1", "0/1", "N/A"}:
        return default
    if ":" in value:
        num, den = value.split(":", 1)
    elif "/" in value:
        num, den = value.split("/", 1)
    else:
        return float(value)
    return float(num) / float(den) if float(den) else default


def _fps_from_rate(value, default=30.0):
    """Parse an ffprobe frame rate ("30000/1001" or a bare number); a 0/0 rate yields `default`."""
    if "/" in value:
        num, den = value.split("/", 1)
        return round(float(num) / float(den), 3) if float(den) else default
    return round(float(value), 3)


def _stream_rotation(stream):
    """Extract rotation from tags or side_data_list in ffprobe JSON."""
    for source in (stream.get("tags", {}).get("rotate"), stream.get("rotation")):
        if source not in (None, ""):
            return int(round(float(source))) % 360
    for item in stream.get("side_data_list", []):
        if item.get("rotation") not in (None, ""):
            return int(round(float(item["rotation"]))) % 360
    return 0


def _canvas_from_stream(stream):
    storage_w = stream["width"]
    storage_h = stream["height"]
    fps = _fps_from_rate(stream["r_frame_rate"])
    sar_text = stream.get("sample_aspect_ratio", "1:1")
    dar_text = stream.get("display_aspect_ratio", "")
    sar = _ratio_to_float(sar_text, 1.0)
    rotation = _stream_rotation(stream)

    display_w = max(1, int(round(storage_w * sar)))
    display_h = max(1, storage_h)
    if dar_text and dar_text not in {"0:1", "N/A"}:
        dar = _ratio_to_float(dar_text, 0.0)
        # ffprobe sources are not consistent: some report DAR before rotation
        # (landscape value > 1 for a 90° stream), while some containers report the
        # already-rotated portrait DAR (< 1). Only apply DAR before swapping when it
        # describes the stored orientation.
        if dar > 0 and not (rotation in {90, 270} and dar < 1.0):
            # Preserve height and adjust width. This keeps legacy square-pixel landscape
            # byte-identical while honoring non-square pixel DAR metadata.
            display_w = max(1, int(round(display_h * dar)))
    if rotation in {90, 270}:
        display_w, display_h = display_h, display_w

    return {
        "width": display_w,
        "height": display_h,
        "fps": fps,
        "storage_width": storage_w,
        "storage_height": storage_h,
        "rotation": rotation,
        "sample_aspect_ratio": sar_text,
        "display_aspect_ratio": dar_text or f"{display_w}:{display_h}",
    }


def _probe_canvas(video_path, *, command_runner=run_cmd):
    """Return rotation/SAR/DAR-aware canvas facts for a video.

    ``width``/``height`` are the display canvas used by subtitle/overlay geometry.
    For legacy square-pixel landscape sources, these remain the raw storage dimensions.
    """
    res = command_runner([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,avg_frame_rate,sample_aspect_ratio,display_aspect_ratio:stream_tags=rotate:stream_side_data=rotation",
        "-of", "json", str(video_path),
    ])
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe 无法读取视频流 {video_path}: {res.stderr}")
    streams = json.loads(res.stdout)["streams"]
    if not streams:
        raise RuntimeError(f"{video_path} 没有视频流")
    return _canvas_from_stream(streams[0])


def _has_audio_stream(video_path, *, command_runner=run_cmd):
    """Return True when the input has an audio stream usable as [0:a]."""
    result = command_runner([
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=index", "-of", "csv=p=0", str(video_path),
    ])
    return result.returncode == 0 and bool(result.stdout.strip())


def _build_video_clips(
    input_video,
    work_dir,
    duration_s,
    *,
    logger=log,
    source_video_getter=_explicit_source_video,
):
    """Video-track clips for the timeline.

    In cut mode each plan entry becomes a clip referencing the ORIGINAL source
    range. Multi-source validated plans carry per-clip source_path and do not
    require an explicit ambient --source-video. Without any declared source (full
    mode, or cut mode rendered without --source-video) the rendered input is one clip.
    """
    explicit_source_video = source_video_getter()
    spans = _plan_clip_spans(work_dir)
    multi_source = spans is not None and any(span["entry"].get("source_path") for span in spans)
    if spans is None or not (explicit_source_video or multi_source):
        return [{"source_path": str(input_video), "source_start": 0.0,
                 "source_end": float(duration_s), "timeline_start": 0.0,
                 "timeline_end": float(duration_s)}]
    clips = []
    for span in spans:
        entry = span["entry"]
        source_path = entry.get("source_path") or explicit_source_video
        timeline_start, timeline_end = span["output_start"], span["output_end"]
        if not source_path or not os.path.exists(source_path):
            # Degrade ONLY this clip — point it at the rendered cut for its own output
            # window — and keep real provenance for every present source, instead of
            # collapsing the whole multi-source timeline.
            logger(f"  时间线: source_path 不存在，该片段降级为剪后成片片段: {source_path or '(unset)'}")
            clips.append({"source_id": entry.get("source_id"),
                          "source_path": str(input_video),
                          "source_start": timeline_start,
                          "source_end": timeline_end,
                          "timeline_start": timeline_start,
                          "timeline_end": timeline_end,
                          "provenance_degraded": True,
                          "provenance_reason": f"missing_source_path:{source_path or 'unset'}"})
            continue
        clips.append({"source_id": entry.get("source_id"),
                      "source_path": source_path,
                      "source_start": span["source_start"],
                      "source_end": span["source_end"],
                      "timeline_start": timeline_start,
                      "timeline_end": timeline_end})
    return clips
