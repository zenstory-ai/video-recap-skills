#!/usr/bin/env python3
"""Re-time the guohuo-60s Remotion overlay to a new run (stdlib only; ffprobe for --master).

The overlay reads two data files instead of hard-coded constants:

  src/captions.json  subtitle cues [{start, end, text}] on the locked master's clock
  src/overlay.json   canvas, durationInFrames, title text + windows, flower-text cues

This helper rebuilds captions.json from the subtitles.srt that the assemble stage wrote
for the run (its cues already follow the placed narration audio), sets durationInFrames
from the master's length, and reports title/flower windows that now fall outside the
master. It never moves the title or flower cues: those are creative decisions to re-place
by watching the new master, then edit in overlay.json. It exits 1 (after writing) when a
caption, the title or a flower cue ends past the master.

    python3 sync_overlay.py --srt <work_dir>/subtitles.srt --master <recap_master.mp4>
    python3 sync_overlay.py --srt <work_dir>/subtitles.srt --duration 58.96 --out ../captions.json
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_TIME = r"(\d+):(\d{2}):(\d{2})[,.](\d{3})"
_CUE_TIME = re.compile(rf"^{_TIME}\s*-->\s*{_TIME}")


def _seconds(hours, minutes, seconds, millis):
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000


def parse_srt(text):
    """SRT text -> [{start, end, text}] in file order; multi-line cue text joins with no space
    (assemble writes one-line CJK cues). Empty cues are dropped."""
    cues = []
    for block in re.split(r"\r?\n\s*\r?\n", text.lstrip("﻿").strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing = next((i for i, line in enumerate(lines) if _CUE_TIME.match(line)), None)
        if timing is None:
            continue
        groups = _CUE_TIME.match(lines[timing]).groups()
        body = "".join(lines[timing + 1:])
        if not body:
            continue
        start, end = _seconds(*groups[:4]), _seconds(*groups[4:])
        if end <= start:
            raise ValueError(f"cue ends before it starts: {lines[timing]!r}")
        cues.append({"start": round(start, 3), "end": round(end, 3), "text": body})
    if not cues:
        raise ValueError("no subtitle cues found")
    return cues


def probe_duration(master):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
         str(master)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"ffprobe could not read {master}: {result.stderr.strip()}")
    return float(result.stdout.strip())


def out_of_range(overlay, duration, cues):
    """Human-readable notes for every timed item that no longer fits the master."""
    notes = [
        f"caption {cue['start']:.3f}-{cue['end']:.3f}s ends after the master ({duration:.3f}s)"
        for cue in cues
        if cue["end"] > duration + 1e-6
    ]
    for window in overlay["title"]["windows"]:
        if window["end"] > duration + 1e-6:
            notes.append(
                f"title window {window['start']}-{window['end']}s ends after the master"
            )
    for cue in overlay["flowerCues"]:
        if cue["end"] > duration + 1e-6:
            notes.append(f"flower cue {cue['text']!r} {cue['start']}-{cue['end']}s ends after the master")
    return notes


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--srt", required=True, help="the run's work_dir/subtitles.srt")
    length = ap.add_mutually_exclusive_group(required=True)
    length.add_argument("--master", help="locked master video; its length sets durationInFrames")
    length.add_argument("--duration", type=float, help="master length in seconds")
    ap.add_argument("--overlay", default=str(HERE / "src" / "overlay.json"))
    ap.add_argument(
        "--out", action="append", default=None,
        help="captions.json to write (repeatable; default src/captions.json)",
    )
    args = ap.parse_args(argv)

    cues = parse_srt(Path(args.srt).read_text(encoding="utf-8"))
    duration = probe_duration(args.master) if args.master else args.duration
    if not duration or duration <= 0:
        ap.error("master duration must be positive")
    overlay_path = Path(args.overlay)
    overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
    overlay["durationInFrames"] = round(duration * overlay["fps"])

    for out in args.out or [str(HERE / "src" / "captions.json")]:
        Path(out).write_text(json.dumps(cues, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"captions: {len(cues)} cues -> {out}")
    overlay_path.write_text(json.dumps(overlay, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"overlay: durationInFrames={overlay['durationInFrames']} "
          f"({duration:.3f}s @ {overlay['fps']}fps) -> {overlay_path}")
    notes = out_of_range(overlay, duration, cues)
    for note in notes:
        print(f"WARNING: {note}", file=sys.stderr)
    print("Re-place title windows and flower cues in overlay.json by watching the new master.")
    return 1 if notes else 0


if __name__ == "__main__":
    sys.exit(main())
