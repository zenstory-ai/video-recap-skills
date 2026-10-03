#!/usr/bin/env python3
"""Storyboard (contact-sheet) generation for video-understanding.

Two ADVISORY artifacts that help the writing agent orient on the timeline by SCANNING
one image instead of opening dozens of frames:

  - source_storyboard.{jpg,json}  — scene-anchored tiles over the SOURCE timeline.
  - edited_storyboard.{jpg,json}  — one row per kept clip over the cut OUTPUT timeline,
                                    each tile dual-labelled (output time / source time).

Both reuse the frames already extracted by understand.py (frames/frame_*.jpg at CONFIG["fps"];
a multi-source edited sheet reads each source's own work_dir frames at the fps its frames
manifest records). Nothing here re-extracts video. Every function returns dict|None and degrades to
None + log(...) on ANY failure (no frames, ffmpeg missing/non-zero, font probe raises),
so a storyboard quirk can NEVER block the pipeline (Principle 1: advisory, never blocking).

drawtext "mm:ss" labels are attempted when a usable font is found (labels_burned:true);
when no font is available (or drawtext errors) the sheet is still produced UNLABELLED
(labels_burned:false) and the JSON sidecar stays authoritative for all timestamps.
"""
import json
import math
import re
import shutil
import subprocess
from pathlib import Path

from extract import frame_number_for_time, parse_frame_number
from lib import CONFIG, run_cmd, log, get_video_duration


# Candidate font files probed (in order) for burning mm:ss labels. The first that exists
# AND that drawtext can actually load wins. A probe that RAISES must never abort the sheet.
_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
)


def _fmt_mmss(seconds):
    """Format a timestamp as mm:ss (clamped to >= 0)."""
    total = int(round(max(0.0, float(seconds))))
    return f"{total // 60:02d}:{total % 60:02d}"


def _ffmpeg_available():
    return shutil.which("ffmpeg") is not None


def _probe_font():
    """Return a usable font file path, or None. Any exception → None (never raises out).

    Probe order: explicit candidate files first, then `fc-match` if available. The path
    is only returned when the file exists on disk; we do NOT shell out to drawtext here —
    a render-time drawtext error is caught separately and also degrades to unlabelled.
    """
    try:
        for candidate in _FONT_CANDIDATES:
            if Path(candidate).is_file():
                return candidate
        fc_match = shutil.which("fc-match")
        if fc_match:
            result = subprocess.run(
                [fc_match, "-f", "%{file}", "sans"],
                capture_output=True, text=True, timeout=10,
            )
            path = (result.stdout or "").strip()
            if result.returncode == 0 and path and Path(path).is_file():
                return path
    except Exception as exc:  # noqa: BLE001 - a font probe must NEVER abort the sheet
        log(f"storyboard 字体探测异常（降级为不烧时间戳）: {exc}")
        return None
    return None


def _frame_index(work_dir):
    """Return (sorted_frame_paths, sorted_numbers) for frames/frame_*.jpg, or ([], [])."""
    frames_dir = Path(work_dir) / "frames"
    if not frames_dir.is_dir():
        return [], []
    pairs = []
    for path in frames_dir.glob("frame_*.jpg"):
        number = parse_frame_number(path)
        if number is None:
            continue
        pairs.append((number, path))
    pairs.sort(key=lambda item: item[0])
    numbers = [num for num, _ in pairs]
    paths = [path for _, path in pairs]
    return paths, numbers


def _nearest_existing_frame(timestamp, fps, paths, numbers):
    """Map a SOURCE timestamp → the nearest EXISTING frame file, clamped to [first,last].

    Frames are named frame_{n:05d}.jpg; the number↔time mapping is owned by extract.py
    (frame_00001 is t=0, so n = t*fps + 1). Rounding a timestamp blindly can yield a frame
    number that was never written (fps boundary / last frame gap), so we resolve to the
    closest number that actually exists on disk.
    """
    if not numbers:
        return None
    fps = float(fps)
    if fps <= 0:
        return None
    target = frame_number_for_time(timestamp, fps)
    # Clamp into the real extracted range so out-of-range timestamps pin to first/last frame.
    if target <= numbers[0]:
        return paths[0]
    if target >= numbers[-1]:
        return paths[-1]
    # numbers is sorted; find the closest by absolute distance (ties → earlier frame).
    best_idx = 0
    best_dist = None
    for idx, num in enumerate(numbers):
        dist = abs(num - target)
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_idx = idx
        elif num - target > best_dist:
            break  # sorted: distance only grows from here
    return paths[best_idx]


def _scene_anchor_timestamps(scenes, max_tiles):
    """Scene-anchored sample timestamps: each scene midpoint; long scenes also +1/3 & +2/3.

    Returns a list of (scene_id, timestamp). A scene is "long" when adding the thirds gives
    materially distinct sample points; we treat scenes longer than `long_scene_seconds` as
    long. Total is capped at max_tiles by evenly subsampling the ordered anchor list, so the
    sheet stays legible (D2: one bounded contact sheet, not dozens of frame reads).
    """
    long_scene_seconds = float(CONFIG["storyboard_long_scene_seconds"])
    anchors = []
    for scene_id, scene in enumerate(scenes or []):
        start = float(scene["start"])
        end = float(scene["end"])
        if end <= start:
            continue
        mid = (start + end) / 2.0
        if (end - start) >= long_scene_seconds:
            third = start + (end - start) / 3.0
            two_third = start + 2.0 * (end - start) / 3.0
            points = [third, mid, two_third]
        else:
            points = [mid]
        for ts in points:
            anchors.append((scene_id, round(ts, 3)))
    if max_tiles and len(anchors) > max_tiles:
        # Evenly subsample to the cap, preserving timeline order and scene spread.
        step = len(anchors) / float(max_tiles)
        anchors = [anchors[int(i * step)] for i in range(max_tiles)]
    return anchors


def _frame_size(frame_path):
    """(width, height) of one extracted frame via ffprobe, or None on any failure."""
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(frame_path),
    ]
    try:
        result = run_cmd(cmd)
        width, height = (int(v) for v in result.stdout.strip().split("x"))
    except Exception:  # noqa: BLE001 - an unreadable size only means "normalise this tile"
        return None
    if result.returncode != 0 or width <= 0 or height <= 0:
        return None
    return width, height


def _prepared_frame(frame_path, label, font_path, fit, scratch_dir, out_name):
    """Copy frame_path through ffmpeg, letterboxed into `fit` and/or labelled; path or None.

    `fit` = (w, h) letterboxes a frame into one tile size and pixel format. A sheet mixing
    sources needs both: the tile filter's input is re-initialised whenever frame size or
    pixel format changes mid-sequence (a 4:4:4 source next to a 4:2:0 one), which silently
    drops the tiles already placed, and mixed sizes would otherwise be squashed to the first.
    `font_path=None` skips the label. None signals the caller to degrade; a failure here is
    never fatal to the pipeline.
    """
    filters = []
    if fit is not None:
        width, height = fit
        filters.append(
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,format=yuvj420p"
        )
    if font_path:
        safe_label = label.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’")
        filters.append(
            f"drawtext=fontfile='{font_path}':text='{safe_label}':"
            "x=8:y=8:fontsize=28:fontcolor=white:"
            "box=1:boxcolor=black@0.55:boxborderw=6"
        )
    out_path = scratch_dir / out_name
    cmd = [
        "ffmpeg", "-y", "-i", str(frame_path), "-vf", ",".join(filters),
        "-frames:v", "1", str(out_path),
    ]
    try:
        result = run_cmd(cmd)
    except Exception as exc:  # noqa: BLE001 - a tile render must never abort the sheet
        log(f"storyboard 帧处理异常（降级）: {exc}")
        return None
    if result.returncode != 0 or not out_path.exists():
        return None
    return out_path


def _prepare_frames(tiles, font_path, scratch_dir, out_stem):
    """Frames to tile, labelled when font_path is set; None as soon as one render fails.

    A tile with neither a label nor a `fit` uses its extracted frame as-is (no ffmpeg).
    """
    frames = []
    for idx, tile in enumerate(tiles):
        fit = tile.get("fit")
        if not font_path and fit is None:
            frames.append(Path(tile["frame_file"]))
            continue
        prepared = _prepared_frame(
            Path(tile["frame_file"]), tile["label"], font_path, fit, scratch_dir,
            f"lbl_{out_stem}_{idx:05d}.jpg",
        )
        if prepared is None:
            return None
        frames.append(prepared)
    return frames


def _tile_pages(frame_paths, columns, out_dir, out_stem, scratch_dir):
    """Tile frame_paths into one or more contact-sheet pages; return the list of page paths.

    ffmpeg's tile filter lays one grid per page. We page so each sheet holds at most
    columns*rows tiles where rows is chosen to keep the grid roughly square but capped, then
    spill into _001.jpg, _002.jpg… Returns [] on any ffmpeg failure (caller degrades to None).
    """
    columns = max(1, int(columns))
    rows_per_page = int(CONFIG["storyboard_rows_per_page"])
    per_page = columns * rows_per_page
    pages = []
    total = len(frame_paths)
    page_count = max(1, math.ceil(total / per_page))
    for page_idx in range(page_count):
        chunk = frame_paths[page_idx * per_page:(page_idx + 1) * per_page]
        if not chunk:
            continue
        cols = min(columns, len(chunk))
        rows = max(1, math.ceil(len(chunk) / cols))
        if page_count == 1:
            page_path = out_dir / f"{out_stem}.jpg"
        else:
            page_path = out_dir / f"{out_stem}_{page_idx + 1:03d}.jpg"
        # Stage the chunk as a contiguous numbered sequence so ffmpeg's image2 demuxer +
        # tile filter consume EXACTLY these frames (the source frame numbers are sparse).
        seq_dir = scratch_dir / f"seq_{out_stem}_{page_idx:03d}"
        seq_dir.mkdir(parents=True, exist_ok=True)
        for seq_idx, src in enumerate(chunk):
            shutil.copyfile(src, seq_dir / f"f_{seq_idx:05d}.jpg")
        cmd = [
            "ffmpeg", "-y",
            "-framerate", "1",
            "-i", str(seq_dir / "f_%05d.jpg"),
            "-frames:v", "1",
            "-vf", f"tile={cols}x{rows}",
            str(page_path),
        ]
        result = run_cmd(cmd)
        if result.returncode != 0 or not page_path.exists():
            log(f"storyboard tile 失败: {result.stderr[-300:]}")
            return []
        pages.append(page_path)
    return pages


_PATH_SEPARATORS = re.compile(r"[\\/]")


def work_dir_relative_pages(pages):
    """Page images as work_dir-relative POSIX paths ("storyboard/<name>").

    Pages always live in <work_dir>/storyboard/, so only the file name is kept: an absolute
    path would keep naming the original work_dir after the directory is copied or moved."""
    # Split on both separators: a sidecar written on Windows may be read on POSIX and back.
    return ["storyboard/" + _PATH_SEPARATORS.split(str(page))[-1] for page in pages or []]


def _render_storyboard(work_dir, tiles, out_stem):
    """Shared render path: optionally burn labels, tile to pages, return (page_paths, labels_burned).

    `tiles` is a list of dicts that ALREADY carry a resolved `frame_file` (absolute path), a
    `label` string and optionally a `fit` tile size. Returns (None, _) on hard failure so
    callers degrade to None.
    """
    if not _ffmpeg_available():
        log("storyboard 跳过：未找到 ffmpeg")
        return None, False
    storyboard_dir = Path(work_dir) / "storyboard"
    storyboard_dir.mkdir(parents=True, exist_ok=True)
    scratch_dir = storyboard_dir / f".scratch_{out_stem}"
    if scratch_dir.exists():
        shutil.rmtree(scratch_dir, ignore_errors=True)
    scratch_dir.mkdir(parents=True, exist_ok=True)

    font_path = _probe_font()
    try:
        render_frames = (
            _prepare_frames(tiles, font_path, scratch_dir, out_stem) if font_path else None
        )
        labels_burned = render_frames is not None
        if render_frames is None:
            # First label failure → abandon labelling for the WHOLE sheet (no half-labelled
            # pages). The JSON sidecar still carries every timestamp.
            render_frames = _prepare_frames(tiles, None, scratch_dir, out_stem)
        pages = (
            _tile_pages(
                render_frames, CONFIG["storyboard_columns"],
                storyboard_dir, out_stem, scratch_dir,
            )
            if render_frames is not None
            else []
        )
    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)
    if not pages:
        return None, labels_burned
    return pages, labels_burned


def build_source_storyboard(work_dir, video_path, scenes, fps):
    """Scene-anchored SOURCE-timeline contact sheet. Returns dict|None.

    Samples each scene midpoint (long scenes also +1/3,+2/3), maps every sample to the
    nearest EXISTING extracted frame (clamped), tiles them, and writes
    storyboard/source_storyboard.json. Returns None + log on any failure.
    """
    try:
        work_dir = Path(work_dir)
        paths, numbers = _frame_index(work_dir)
        if not paths:
            log("storyboard 跳过 source：frames/ 为空或缺失")
            return None
        max_tiles = int(CONFIG["storyboard_max_tiles"])
        columns = int(CONFIG["storyboard_columns"])
        anchors = _scene_anchor_timestamps(scenes, max_tiles)
        if not anchors:
            log("storyboard 跳过 source：无可用场景锚点")
            return None

        tiles = []
        for tile_id, (scene_id, ts) in enumerate(anchors):
            frame = _nearest_existing_frame(ts, fps, paths, numbers)
            if frame is None:
                continue
            tiles.append({
                "tile_id": tile_id,
                "timestamp": round(float(ts), 3),
                "label": _fmt_mmss(ts),
                "scene_id": scene_id,
                "frame_file": str(frame),
            })
        if not tiles:
            log("storyboard 跳过 source：未解析到任何帧")
            return None

        pages, labels_burned = _render_storyboard(work_dir, tiles, "source_storyboard")
        if not pages:
            log("storyboard 跳过 source：拼贴失败")
            return None

        for tile in tiles:
            tile["frame_file"] = Path(tile["frame_file"]).name
        payload = {
            "schema_version": 1,
            "timeline": "source",
            "video_path": str(video_path),
            "fps": float(fps) if fps else None,
            "labels_burned": labels_burned,
            "page_images": work_dir_relative_pages(pages),
            "sample_policy": {
                "max_tiles": max_tiles,
                "columns": columns,
                "anchors": "scene_midpoint+long_scene_thirds",
            },
            "tiles": tiles,
        }
        try:
            payload["duration"] = round(get_video_duration(video_path), 3)
        except RuntimeError as exc:
            log(f"storyboard source：无法读取视频时长，sidecar 省略 duration（忽略）: {exc}")
        json_path = work_dir / "storyboard" / "source_storyboard.json"
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"storyboard source: {len(tiles)} tiles → {len(pages)} page(s), labels_burned={labels_burned}")
        return payload
    except Exception as exc:  # noqa: BLE001 - advisory: never propagate
        log(f"storyboard source 失败（忽略）: {exc}")
        return None


def _source_to_output(source_time, clip):
    """Forward affine source→output map for ONE clip (reimplements cut.py:319 locally).

    output = clip.output_start + (src − clip.source_start), clamped to [output_start, output_end].
    Read the authoritative numbers from clip_plan_validated.json; do NOT import cut.py.
    """
    src = float(source_time)
    out_start = float(clip["output_start"])
    out_end = float(clip["output_end"])
    mapped = out_start + (src - float(clip["source_start"]))
    return round(max(out_start, min(mapped, out_end)), 3)


def _multi_source_tile_size(frame_sets):
    """One tile size for a multi-source sheet: the first source's frame size rounded down to
    even numbers (640x360 when no frame can be probed). Every multi-source tile is re-encoded
    into it — see _prepared_frame. Odd sizes are real (frames keep storage resolution, e.g.
    853x480) and ffmpeg's scale rounds a 4:2:0 frame up to even, so an odd pad target would
    be smaller than its input and every tile would fail."""
    for entry in frame_sets.values():
        size = _frame_size(entry["paths"][0])
        if size:
            width, height = size
            return max(2, width - width % 2), max(2, height - height % 2)
    return 640, 360


def build_edited_storyboard(
    work_dir, source_video_path, clip_plan_validated, fps, *, source_frames=None
):
    """OUTPUT-timeline contact sheet: one row per kept clip over the cut. Returns dict|None.

    For each kept clip, samples source start / mid / (end − 0.5s), maps each to the nearest
    EXISTING SOURCE frame (SOURCE fps — frames are reused, NO re-extraction), and FRAME-IDENTITY
    dedupes so a ≤1s clip yields 1-2 tiles (not 3 identical). Each tile is dual-labelled with
    both `output_timestamp` and `source_timestamp` (+ `source_clip_id`). Writes
    storyboard/edited_storyboard.json. Returns None + log on any failure.

    Single-source plans read work_dir/frames at `fps`. A multi-source plan passes
    `source_frames` = {source_id: {"paths", "numbers", "fps"[, "source_path"]}} built from each
    source's own work_dir; its tiles also carry `source_id` and a short `S<n>` label listed
    under `sources`, and `frame_file` keeps the full path (frame names repeat across sources).
    """
    try:
        work_dir = Path(work_dir)
        if source_frames is None:
            paths, numbers = _frame_index(work_dir)
            if not paths:
                log("storyboard 跳过 edited：frames/ 为空或缺失")
                return None
            frame_sets = {None: {"paths": paths, "numbers": numbers, "fps": fps}}
            tile_size = None
        else:
            # Only sources a clip uses: an unused one must not take an S<n> label, be listed
            # under `sources`, or set the tile size.
            used = {
                clip.get("source_id") for clip in clip_plan_validated["clips"]
                if isinstance(clip, dict)
            }
            frame_sets = {
                sid: entry for sid, entry in source_frames.items()
                if entry["paths"] and sid in used
            }
            if not frame_sets:
                log("storyboard 跳过 edited：剪辑用到的来源都没有 frames/")
                return None
            tile_size = _multi_source_tile_size(frame_sets)
        short_labels = {sid: f"S{n}" for n, sid in enumerate(frame_sets, start=1)}
        clips = clip_plan_validated["clips"]
        if not clips:
            log("storyboard 跳过 edited：clip_plan_validated 无 clips")
            return None
        columns = int(CONFIG["storyboard_columns"])
        max_tiles = int(CONFIG["storyboard_max_tiles"])

        tiles = []
        seen_frames = set()  # frame-identity dedupe (NOT luma de-dupe; that stays deferred)
        tile_id = 0
        for clip in clips:
            try:
                source_start = float(clip["source_start"])
                source_end = float(clip["source_end"])
                clip_id = clip.get("clip_id")
            except (KeyError, TypeError, ValueError):
                continue
            if source_end <= source_start:
                continue
            source_id = clip.get("source_id") if source_frames is not None else None
            frame_set = frame_sets.get(source_id)
            if frame_set is None:
                continue  # this source has no extracted frames (e.g. a material restore)
            mid = (source_start + source_end) / 2.0
            end_sample = max(source_start, source_end - 0.5)
            for src_ts in (source_start, mid, end_sample):
                frame = _nearest_existing_frame(
                    src_ts, frame_set["fps"], frame_set["paths"], frame_set["numbers"]
                )
                if frame is None:
                    continue
                key = (clip_id, str(frame))
                if key in seen_frames:
                    continue  # same clip resolving to the same frame → drop the duplicate tile
                seen_frames.add(key)
                out_ts = _source_to_output(src_ts, clip)
                src_label = "src" if source_id is None else short_labels[source_id]
                tile = {
                    "tile_id": tile_id,
                    "output_timestamp": out_ts,
                    "source_timestamp": round(float(src_ts), 3),
                    "source_clip_id": clip_id,
                    "label": f"out {_fmt_mmss(out_ts)} / {src_label} {_fmt_mmss(src_ts)}",
                    "frame_file": str(frame),
                }
                if source_id is not None:
                    tile["source_id"] = source_id
                if tile_size is not None:
                    tile["fit"] = tile_size
                tiles.append(tile)
                tile_id += 1
        if not tiles:
            log("storyboard 跳过 edited：未解析到任何帧")
            return None
        if len(tiles) > max_tiles:
            tiles = tiles[:max_tiles]

        pages, labels_burned = _render_storyboard(work_dir, tiles, "edited_storyboard")
        if not pages:
            log("storyboard 跳过 edited：拼贴失败")
            return None

        for tile in tiles:
            tile["frame_file"] = (
                Path(tile["frame_file"]).name
                if source_frames is None
                else str(tile["frame_file"])
            )
            tile.pop("fit", None)
        edited_source = Path(work_dir) / "edited_source.mp4"
        payload = {
            "schema_version": 1,
            "timeline": "output",
            "source_video_path": str(source_video_path) if source_frames is None else None,
            "edited_video_path": edited_source.name if edited_source.exists() else None,
            "labels_burned": labels_burned,
            "page_images": work_dir_relative_pages(pages),
            "sample_policy": {
                "max_tiles": max_tiles,
                "columns": columns,
                "per_clip": "source_start+mid+(end-0.5s), frame-identity deduped",
            },
            "tiles": tiles,
        }
        if source_frames is not None:
            on_sheet = {tile.get("source_id") for tile in tiles}
            payload["sources"] = [
                {
                    "label": short_labels[sid],
                    "source_id": sid,
                    "source_path": frame_sets[sid].get("source_path"),
                }
                for sid in frame_sets
                if sid in on_sheet
            ]
        json_path = work_dir / "storyboard" / "edited_storyboard.json"
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"storyboard edited: {len(tiles)} tiles → {len(pages)} page(s), labels_burned={labels_burned}")
        return payload
    except Exception as exc:  # noqa: BLE001 - advisory: never propagate
        log(f"storyboard edited 失败（忽略）: {exc}")
        return None
