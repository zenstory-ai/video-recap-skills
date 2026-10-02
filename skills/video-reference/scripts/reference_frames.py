"""Contact sheets for looking at a finished video: suppressed cut candidates, longest shots, or a span.

The measurement can miss cuts (dissolves, dark scenes, rapid montage) or keep a few false ones,
and when no understanding run exists the agent has no pictures at all. Each sheet row is one
ffmpeg seek tiled to CELLS frames; rows are stacked into pages under U/reference_frames/ and the
printed legend gives every cell's time, so no drawtext/font support is needed.
"""
import math
import tempfile
from pathlib import Path

from lib import ffprobe, run_cmd

FRAMES_DIR = "reference_frames"
CELLS = 12
CELL_WIDTH = 160
ROWS_PER_PAGE = 8
LONGEST_EDGE_S = 0.1


def _row(label, start, end, cells):
    """Evenly spaced cell times over [start, end]; a single time when the range is a point."""
    cells = max(1, min(CELLS, cells))
    step = (end - start) / (cells - 1) if cells > 1 else 0.0
    return {"label": label, "times": [round(start + i * step, 2) for i in range(cells)], "step": step}


def review_rows(measurements, fps):
    """Every frame of each suppressed-candidate window, CELLS frames per row."""
    frame_s = 1.0 / fps if fps else 0.04
    rows = []
    for start, end in measurements["shots"].get("review_windows") or []:
        for row in span_rows(start, end, frame_s):
            rows.append({**row, "label": f"待复核 {start}–{end}s"})
    return rows


def longest_rows(measurements, count):
    """One row per longest measured shot: a long 'shot' is where a missed cut hides."""
    duration = measurements["source"]["duration_s"]
    bounds = [0.0, *measurements["shots"]["cuts"], duration]
    shots = sorted(zip(bounds, bounds[1:]), key=lambda s: s[1] - s[0], reverse=True)[:count]
    return [_row(f"镜头 {a:.2f}–{b:.2f}s（{b - a:.1f}s）", a + LONGEST_EDGE_S, b - LONGEST_EDGE_S, CELLS)
            for a, b in sorted(shots) if b - a > 2 * LONGEST_EDGE_S]


def span_rows(start, end, step):
    """Cells every `step` seconds over [start, end], CELLS per row."""
    if step <= 0 or end <= start:
        raise ValueError("--span 需要 A<B，--step 必须为正数")
    times = [round(start + i * step, 3) for i in range(int(math.floor((end - start) / step + 1e-9)) + 1)]
    return [{"label": f"{chunk[0]:g}–{chunk[-1]:g}s，每 {step:g}s", "times": chunk, "step": step}
            for chunk in (times[i:i + CELLS] for i in range(0, len(times), CELLS))]


def _render_row(video, row, out_path):
    times, step = row["times"], row["step"]
    # Cell i is the first frame at or after times[0] + i * step (the unrounded step). The fps filter
    # would instead pick the frame nearest each slot, which can be a frame of the *next* shot and hide
    # where a cut is. Only len(times) frames are picked: a short row leaves its other cells blank.
    pick = f"select='gte(t\\,selected_n*{step:.6f}-0.001)*lt(selected_n\\,{len(times)})'"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{times[0]:.3f}",
           "-t", f"{step * (len(times) - 1) + 0.2:.3f}", "-i", video,
           "-vf", f"{pick},scale={CELL_WIDTH}:-2,tile={CELLS}x1", "-frames:v", "1", out_path]
    result = run_cmd(cmd)
    if result.returncode != 0 or not Path(out_path).is_file():
        raise RuntimeError(f"ffmpeg 取帧失败（{row['label']}）: {result.stderr.strip()[-400:]}")


def render(video, work_dir, rows, name):
    """Write pages <name>_<n>.jpg under work_dir/reference_frames; return [(page, rows)]."""
    out_dir = Path(work_dir) / FRAMES_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    pages = []
    with tempfile.TemporaryDirectory(prefix="reference-frames-") as scratch:
        for page_index in range(0, len(rows), ROWS_PER_PAGE):
            page_rows = rows[page_index:page_index + ROWS_PER_PAGE]
            parts = []
            for i, row in enumerate(page_rows):
                part = Path(scratch) / f"row_{page_index + i}.jpg"
                _render_row(video, row, part)
                parts.append(str(part))
            page = out_dir / f"{name}_{page_index // ROWS_PER_PAGE + 1}.jpg"
            if len(parts) == 1:
                cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", parts[0], str(page)]
            else:
                cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
                for part in parts:
                    cmd += ["-i", part]
                cmd += ["-filter_complex", f"vstack=inputs={len(parts)}", str(page)]
            result = run_cmd(cmd)
            if result.returncode != 0:
                raise RuntimeError(f"ffmpeg 拼页失败: {result.stderr.strip()[-400:]}")
            pages.append((page, page_rows))
    return pages


def legend(pages):
    """Printable mapping from page/row/cell to seconds."""
    lines = []
    for page, rows in pages:
        lines.append(str(page))
        for i, row in enumerate(rows, 1):
            lines.append(f"  第 {i} 行 {row['label']}：" + " ".join(f"{t:g}" for t in row["times"]))
    return "\n".join(lines)


def frames(video, work_dir, measurements, *, mode, longest=5, span=None, step=2.0):
    """Build the rows for `mode` (review | longest | span) and render them."""
    if mode == "span":
        start, end = span
        rows = span_rows(start, end, step)
    else:
        if measurements is None:
            raise ValueError("先运行 measure：review / longest 需要 reference_measurements.json")
        if mode == "review":
            rows = review_rows(measurements, measurements["source"].get("fps") or ffprobe(video)["fps"])
        else:
            rows = longest_rows(measurements, longest)
    if not rows:
        return []
    return render(str(video), work_dir, rows, mode)
