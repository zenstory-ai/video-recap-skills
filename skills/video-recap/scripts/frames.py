#!/usr/bin/env python3
"""Atomic frame extractor and timestamped contact-sheet generator for agent visual inspection."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

from _common import has_tool, parse_fps, probe_media, run_cmd, save_json

__all__ = ["build_contact_sheet", "build_shot_sheet", "extract_frames", "main", "shot_pieces"]

_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
)


def _probe_font() -> str | None:
    for candidate in _FONT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    fc_match = has_tool("fc-match")
    if fc_match:
        try:
            res = run_cmd([fc_match, "-f", "%{file}", "sans"], timeout=10)
            path = (res.stdout or "").strip()
            if res.returncode == 0 and path and Path(path).is_file():
                return path
        except Exception:
            return None
    return None


def _fmt_mmss(seconds: float) -> str:
    sec = max(0.0, float(seconds))
    total = int(sec)
    tenth = int(round((sec - total) * 10))
    if tenth == 10:
        total += 1
        tenth = 0
    return f"{total // 60:02d}:{total % 60:02d}.{tenth}"


def _escape_drawtext(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _parse_window(raw: str | None, duration: float) -> tuple[float, float]:
    if not raw:
        return 0.0, duration
    left, sep, right = raw.partition(":")
    if not sep:
        raise ValueError(f"Invalid --window '{raw}'; expected START:END")
    start = max(0.0, float(left)) if left.strip() else 0.0
    end = min(duration, float(right)) if right.strip() else duration
    if end <= start:
        raise ValueError(f"Empty --window [{start}, {end}]")
    return start, end


def _parse_crop(raw: str | None) -> tuple[int, int, int, int] | None:
    if not raw:
        return None
    parts = [int(p.strip()) for p in raw.split(",")]
    if len(parts) != 4 or parts[2] <= 0 or parts[3] <= 0:
        raise ValueError(f"Invalid --crop '{raw}'; expected x,y,w,h")
    return parts[0], parts[1], parts[2], parts[3]


def _extract_single_frame(
    video: Path,
    t_s: float,
    out_img: Path,
    *,
    width: int | None = None,
    crop: tuple[int, int, int, int] | None = None,
    label: str | None = None,
    font_path: str | None = None,
) -> bool:
    """Extract one frame via fast input seek; falls back to unlabelled if drawtext fails."""
    filters: list[str] = []
    if crop is not None:
        x, y, w, h = crop
        filters.append(f"crop={w}:{h}:{x}:{y}")
    if width and width > 0:
        filters.append(f"scale={int(width)}:-2")

    if label and font_path:
        esc_font = _escape_drawtext(font_path)
        esc_txt = _escape_drawtext(label)
        dt = (
            f"drawtext=fontfile='{esc_font}':text='{esc_txt}':x=6:y=6:"
            "fontsize=16:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=4"
        )
        vf = ",".join([*filters, dt])
        res = run_cmd([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{max(0.0, t_s):.4f}", "-i", str(video),
            "-frames:v", "1", "-vf", vf, "-q:v", "3", str(out_img),
        ])
        if res.returncode == 0 and out_img.is_file() and out_img.stat().st_size > 0:
            return True

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(0.0, t_s):.4f}", "-i", str(video), "-frames:v", "1",
    ]
    if filters:
        cmd.extend(["-vf", ",".join(filters)])
    cmd.extend(["-q:v", "3", str(out_img)])
    run_cmd(cmd, check=True)
    return False


def extract_frames(
    video_path: str | Path,
    timestamps: Sequence[float],
    out_target: str | Path,
    *,
    width: int | None = None,
    crop: tuple[int, int, int, int] | None = None,
    labels: bool = True,
) -> dict[str, Any]:
    """Extract frames at explicit timestamps to a file (if 1 timestamp) or directory."""
    if not has_tool("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH")
    video = Path(video_path)
    info = probe_media(video)
    fps = float(parse_fps(info.get("fps_rational") or info.get("fps", 30.0)))
    font = _probe_font() if labels else None

    target = Path(out_target)
    single_file = len(timestamps) == 1 and target.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    if single_file:
        target.parent.mkdir(parents=True, exist_ok=True)
    else:
        target.mkdir(parents=True, exist_ok=True)

    tiles: list[dict[str, Any]] = []
    any_labeled = False
    for idx, t_s in enumerate(timestamps):
        out_img = target if single_file else target / f"frame_{idx:04d}_{int(round(t_s * 1000)):07d}ms.jpg"
        lbl = _fmt_mmss(t_s) if labels else None
        was_labeled = _extract_single_frame(
            video, t_s, out_img, width=width, crop=crop, label=lbl, font_path=font
        )
        any_labeled = any_labeled or was_labeled
        tiles.append({
            "index": idx,
            "t_s": round(float(t_s), 4),
            "frame": int(round(float(t_s) * fps)),
            "path": str(out_img),
        })
    return {
        "video": str(video),
        "crop": list(crop) if crop else None,
        "labels_burned": any_labeled,
        "tiles": tiles,
    }


def build_contact_sheet(
    video_path: str | Path,
    cols: int,
    rows: int,
    out_image: str | Path,
    *,
    window: tuple[float, float] | None = None,
    width: int = 320,
    crop: tuple[int, int, int, int] | None = None,
    labels: bool = True,
) -> dict[str, Any]:
    """Build a `cols x rows` annotated contact sheet over `window` via fast input-seek sampling."""
    if not has_tool("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH")
    if cols <= 0 or rows <= 0:
        raise ValueError("Grid dimensions must be positive integers")
    video = Path(video_path)
    info = probe_media(video)
    duration = float(info.get("duration_s") or 0.0)
    if duration <= 0:
        raise RuntimeError(f"Video has non-positive duration: {video}")
    fps = float(parse_fps(info.get("fps_rational") or info.get("fps", 30.0)))

    win_start, win_end = window if window is not None else (0.0, duration)
    span = max(0.01, win_end - win_start)
    count = cols * rows
    # Sample cell centers so the very last frame never seeks past EOF.
    timestamps = [
        min(max(0.0, win_start + span * (i + 0.5) / count), max(0.0, duration - 0.05))
        for i in range(count)
    ]

    font = _probe_font() if labels else None
    out = Path(out_image)
    out.parent.mkdir(parents=True, exist_ok=True)
    tiles: list[dict[str, Any]] = []
    any_labeled = False

    with tempfile.TemporaryDirectory(prefix="frames_sheet_") as tmp_dir:
        tmp = Path(tmp_dir)
        for idx, t_s in enumerate(timestamps):
            tile_img = tmp / f"tile_{idx:04d}.jpg"
            lbl = _fmt_mmss(t_s) if labels else None
            was_labeled = _extract_single_frame(
                video, t_s, tile_img, width=width, crop=crop, label=lbl, font_path=font
            )
            any_labeled = any_labeled or was_labeled
            tiles.append({
                "index": idx,
                "t_s": round(t_s, 4),
                "frame": int(round(t_s * fps)),
            })
        run_cmd([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-framerate", "1", "-i", str(tmp / "tile_%04d.jpg"),
            "-vf", f"tile={cols}x{rows}:padding=2:margin=2:color=black",
            "-frames:v", "1", "-q:v", "3", str(out),
        ], check=True)

    return {
        "video": str(video),
        "sheet": str(out),
        "grid": f"{cols}x{rows}",
        "window": [round(win_start, 4), round(win_end, 4)],
        "crop": list(crop) if crop else None,
        "labels_burned": any_labeled,
        "tiles": tiles,
    }


def shot_pieces(cuts: Sequence[float], duration: float, window: tuple[float, float], split_s: float = 3.0) -> list[dict[str, Any]]:
    """Shots between probe cuts that overlap `window`, inset 0.08s; shots longer than 2*split_s are split into equal pieces."""
    if split_s <= 0:
        raise ValueError("--split must be positive")
    edges = [0.0, *[float(c) for c in cuts if 0 < float(c) < duration], duration]
    pieces: list[dict[str, Any]] = []
    for a, b in zip(edges, edges[1:]):
        x, y = a + 0.08, b - 0.08
        if y < window[0] or x > window[1] or y - x < 0.6:
            continue
        k = max(1, int((y - x) // split_s)) if y - x > 2 * split_s else 1
        for i in range(k):
            px, py = x + (y - x) * i / k, x + (y - x) * (i + 1) / k
            pieces.append({"n": len(pieces), "in": round(px, 3), "out": round(py, 3), "len": round(py - px, 2), "shot": [round(x, 3), round(y, 3)]})
    return pieces


def build_shot_sheet(video_path: str | Path, cuts: Sequence[float], out_image: str | Path, *, window: tuple[float, float],
                     width: int = 240, crop: tuple[int, int, int, int] | None = None, split_s: float = 3.0,
                     labels: bool = True) -> dict[str, Any]:
    """One head+tail tile pair per shot piece inside `window`, labelled `#n len` / source time, 3 pieces per row."""
    video = Path(video_path)
    duration = float(probe_media(video).get("duration_s") or 0.0)
    pieces = shot_pieces(cuts, duration, window, split_s)
    if not pieces:
        raise RuntimeError(f"No shot of at least 0.6s inside window {window}")
    font, out = (_probe_font() if labels else None), Path(out_image)
    out.parent.mkdir(parents=True, exist_ok=True)
    any_labeled = False
    with tempfile.TemporaryDirectory(prefix="frames_shots_") as tmp_dir:
        tmp, idx = Path(tmp_dir), 0
        for pc in pieces:
            for t_s, lbl in ((pc["in"] + 0.1, f"#{pc['n']} {pc['len']:.1f}s"), (pc["out"] - 0.1, _fmt_mmss(pc["in"]))):
                was_labeled = _extract_single_frame(
                    video, t_s, tmp / f"tile_{idx:04d}.jpg", width=width, crop=crop,
                    label=(lbl if labels else None), font_path=font,
                )
                any_labeled = any_labeled or was_labeled
                idx += 1
        run_cmd(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-framerate", "1", "-i", str(tmp / "tile_%04d.jpg"),
                 "-vf", f"tile=6x{(idx + 5) // 6}:padding=2:margin=2:color=black", "-frames:v", "1", "-q:v", "3", str(out)], check=True)
    return {
        "video": str(video), "sheet": str(out), "window": [round(window[0], 3), round(window[1], 3)],
        "labels_burned": any_labeled, "pieces": pieces,
    }


def _probe_cuts(probe_json: str, video: str) -> list[float]:
    probe_path = Path(probe_json).resolve()
    rows = json.loads(probe_path.read_text(encoding="utf-8")).get("sources") or []
    want = Path(video).resolve()
    row = None
    for root in (probe_path.parent, probe_path.parent.parent, Path.cwd()):
        for r in rows:
            raw = str(r.get("path") or "")
            if not raw:
                continue
            p = Path(raw)
            if (p.resolve() if p.is_absolute() else (root / p).resolve()) == want:
                row = r
                break
        if row is not None:
            break
    if row is None:
        by_name = [r for r in rows if r.get("path") and Path(str(r["path"])).name == want.name]
        row = by_name[0] if len(by_name) == 1 else None
    if row is None or "cuts" not in row or not isinstance(row["cuts"], list):
        raise ValueError(f"{probe_json} has no cuts for {video}; run probe.py --scenes on it")
    return [float(c) for c in row["cuts"]]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract timestamped frames or tiled contact sheets.")
    parser.add_argument("video", help="Source video path.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--grid", help="Contact sheet layout CxR, e.g. 4x4.")
    mode.add_argument("--at", help="Comma-separated timestamps in seconds, e.g. 1.5,4.2,9.0.")
    mode.add_argument("--interval", type=float, help="Extract one frame every N seconds.")
    mode.add_argument("--shots", help="probe.json with --scenes cuts: one head/tail pair per shot inside --window (candidate sheet).")
    parser.add_argument("--split", type=float, default=3.0, help="With --shots, split shots longer than 2x this many seconds.")
    parser.add_argument("--window", help="Optional time window START:END in seconds.")
    parser.add_argument("--crop", help="Optional source pixel crop x,y,w,h.")
    parser.add_argument("--width", type=int, default=None, help="Target frame/tile width in px (default 240 for --shots, 320 otherwise).")
    parser.add_argument("--no-labels", action="store_true", help="Do not burn timestamp labels.")
    parser.add_argument("--out", required=True, help="Output image path (grid/shots/single) or directory.")
    parser.add_argument("--out-json", help="Optional JSON metadata path.")
    args = parser.parse_args(argv)

    info = probe_media(args.video)
    duration = float(info.get("duration_s") or 0.0)
    win = _parse_window(args.window, duration) if args.window else None
    crop = _parse_crop(args.crop)
    labels = not args.no_labels
    width = args.width if args.width is not None else (240 if args.shots else 320)

    if args.shots:
        if not win:
            raise ValueError("--shots needs --window START:END")
        if args.split <= 0:
            raise ValueError("--split must be positive")
        meta = build_shot_sheet(args.video, _probe_cuts(args.shots, args.video), args.out, window=win,
                                width=width, crop=crop, split_s=args.split, labels=labels)
    elif args.grid:
        cols_s, sep, rows_s = args.grid.lower().partition("x")
        if not sep:
            raise ValueError(f"Invalid --grid '{args.grid}'; expected CxR")
        meta = build_contact_sheet(
            args.video, int(cols_s), int(rows_s), args.out,
            window=win, width=width, crop=crop, labels=labels,
        )
    else:
        if args.at:
            ts = [float(x.strip()) for x in args.at.split(",") if x.strip()]
        else:
            step = float(args.interval)
            if step <= 0:
                raise ValueError("--interval must be positive")
            s0, s1 = win if win else (0.0, duration)
            ts = []
            cur = s0
            while cur < s1:
                ts.append(round(cur, 4))
                cur += step
        meta = extract_frames(args.video, ts, args.out, width=width, crop=crop, labels=labels)

    if args.out_json:
        save_json(args.out_json, meta)
    else:
        sys.stdout.write(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
