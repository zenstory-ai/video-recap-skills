"""Frame-grid facts and edge snapping so edited_source.mp4 is constant frame rate.

A clip edge between two source frames leaves the concat filter a segment whose audio is a
fraction of a frame longer than its video, so the next segment starts off the frame grid and
the encoded file loses one frame slot per join (an 80 ms hold at 25 fps). Every clip therefore
starts on its source's frame grid and lasts a whole number of output frames.
"""

import math
from fractions import Fraction

# The NTSC rates ffprobe reports as N/1001; canvas fps buckets store them rounded.
_NTSC_RATES = {
    23.976: Fraction(24000, 1001),
    29.97: Fraction(30000, 1001),
    47.952: Fraction(48000, 1001),
    59.94: Fraction(60000, 1001),
}
# An edge "inside" a pause window is judged without the gate's 50 ms tolerance: a half-frame
# move must not pull an edge that sits on a pause boundary back over the spoken word.
_INSIDE_EPSILON = 0.001


def parse_frame_rate(text):
    """An ffprobe 'N/D' rate as a Fraction; None when unknown or implausible.

    Rates above 120 fps are container timebases of variable-rate sources, not a frame grid.
    """
    num, _, den = str(text or "").partition("/")
    try:
        rate = Fraction(num) / Fraction(den or 1)
    except (ValueError, ZeroDivisionError):
        return None
    return rate if 0 < rate <= 120 else None


def canvas_frame_rate(fps):
    """Exact rate for a canvas fps bucket (29.97 is 30000/1001, not 2997/100)."""
    return _NTSC_RATES.get(round(float(fps), 3)) or Fraction(float(fps)).limit_denominator(1001)


def output_frame_rate(rows, canvas_fps):
    """The frame rate edited_source.mp4 is rendered at.

    One source keeps its own exact rate; several sources are resampled to the canvas bucket.
    """
    if len(rows) == 1:
        rate = parse_frame_rate(rows[0].get("frame_rate"))
        if rate:
            return rate
    return canvas_frame_rate(canvas_fps)


def frame_count(duration, rate):
    """Whole output frames a clip of `duration` seconds renders to (at least one)."""
    return max(1, int(round(duration * rate)))


def source_frame_grids(geometry_qc):
    """{source_path: grid} from the selected output geometry, for snap_edges_to_frames."""
    out_rate = Fraction(geometry_qc["frame_rate"])
    return {
        row["path"]: {
            "source_rate": parse_frame_rate(row.get("frame_rate")),
            "origin": float(row.get("video_start_offset", 0.0)),
            "output_rate": out_rate,
        }
        for row in geometry_qc["sources"]
    }


# Gate statuses from sentence_boundaries' edge classifier, safest first.
_GATE_ORDER = {"safe": 0, "unchecked": 1, "blocking": 2}


def _rank(edge, ts, windows, classify):
    """(gate status, 0 strictly inside a sentence/quiet window else 1).

    `classify(edge, ts)` is the sentence-boundary gate itself, so a sub-frame move never
    turns an edge the gate passes into one it blocks.
    """
    strict = any(w["start"] - _INSIDE_EPSILON <= ts <= w["end"] + _INSIDE_EPSILON
                 for w in windows)
    return _GATE_ORDER[classify(edge, ts)[0]], 0 if strict else 1


def _drops_kept_edge(edge, original, ts, keep_ranges):
    """Whether moving a clip edge from `original` to `ts` cuts into a required range.

    Required-evidence coverage is exact, so a start that moves past a node's start (or an
    end that moves before a node's end) the clip used to cover would lose that node.
    """
    if edge == "start":
        return any(original <= start < ts for start, _ in keep_ranges)
    return any(ts < end <= original for _, end in keep_ranges)


# How far a detected shot change may sit from a frame boundary and still count as on it
# (ffmpeg pts times and clip edges are both rounded to the millisecond).
_CUT_EPSILON = 0.002


def _straddles_cut(edge, ts, candidates, scene_cuts):
    """Whether choosing `ts` keeps a sliver of the other shot across a source hard cut.

    A start before a cut that lies up to the later candidate opens on the old shot (a flash
    frame before the cut); an end after a cut that lies from the earlier candidate on closes on
    the next shot. The candidate at or past the cut (start) or before it (end) does not.
    """
    if edge == "start":
        last = max(candidates)
        return any(ts + _CUT_EPSILON < cut <= last + _CUT_EPSILON for cut in scene_cuts)
    first = min(candidates)
    return any(first - _CUT_EPSILON <= cut < ts - _CUT_EPSILON for cut in scene_cuts)


def _pick(edge, original, candidates, windows, classify, keep_ranges, scene_cuts=()):
    """The candidate the gate likes best (then one strictly inside a pause window), then one
    that keeps required ranges, then one that does not straddle a detected source hard cut
    (no flash frame), then the nearest, then the earlier (a deterministic tie-break)."""
    return min(
        candidates,
        key=lambda t: (
            *_rank(edge, t, windows, classify),
            _drops_kept_edge(edge, original, t, keep_ranges),
            _straddles_cut(edge, t, candidates, scene_cuts),
            abs(t - original),
            t,
        ),
    )


def _overlaps(clips, idx, start, end):
    return any(
        start < other["source_end"] and end > other["source_start"]
        for j, other in enumerate(clips)
        if j != idx
    )


def snap_edges_to_frames(clips, grid, windows, classify, source_duration, *,
                         allow_overlap, joined_to_previous):
    """Snap every clip of ONE source onto the frame grid; returns (clips, events).

    The start moves to the nearest source frame boundary and the end to the nearest whole
    number of output frames after it, each choosing between the two neighbouring candidates
    the one the sentence-boundary gate `classify(edge, ts) -> (status, reason)` rates safest
    (safe, then unchecked, then blocking), preferring one strictly inside a pause window.
    A start that continues the previous clip's source range exactly follows that clip's
    snapped end, so a lossless join stays lossless. Between equally safe candidates the one
    that still covers every `grid["keep_ranges"]` (required-evidence nodes) edge it covered
    wins, then the one that does not straddle a `grid["scene_cuts"]` source hard cut (a start
    at or after the cut, an end at or before it, so no frame of the other shot flashes).
    Without a usable source rate the start is left alone, but the length is still a whole
    number of output frames.
    """
    clips = [dict(c) for c in clips]
    rate, origin, out_rate = grid["source_rate"], grid["origin"], grid["output_rate"]
    keep_ranges = grid.get("keep_ranges", ())
    scene_cuts = grid.get("scene_cuts", ())
    events = []
    for idx, clip in enumerate(clips):
        start, end = clip["source_start"], clip["source_end"]
        if joined_to_previous[idx] and idx > 0:
            new_start, start_reason = clips[idx - 1]["source_end"], "continuous_source_join"
        elif rate is None:
            new_start, start_reason = start, "source_frame_rate_unknown"
        else:
            pos = (start - origin) * rate
            ks = {max(0, math.floor(pos)), max(0, math.ceil(pos))}
            options = [round(origin + float(k / rate), 3) for k in sorted(ks)]
            safe = [t for t in options if t >= start or allow_overlap
                    or not _overlaps(clips, idx, t, end)]
            new_start = _pick("start", start, safe or [max(options)], windows, classify,
                              keep_ranges, scene_cuts)
            start_reason = "frame_grid"
        span = (end - new_start) * out_rate
        counts = sorted({max(1, math.floor(span)), max(1, math.ceil(span))})
        options = [
            (n, round(new_start + float(n / out_rate), 3)) for n in counts
            if new_start + float(n / out_rate) <= source_duration + _INSIDE_EPSILON
        ] or [(counts[0], round(new_start + float(counts[0] / out_rate), 3))]
        safe = [(n, t) for n, t in options if t <= end or allow_overlap
                or not _overlaps(clips, idx, new_start, t)] or options[:1]
        new_end = _pick("end", end, [t for _, t in safe], windows, classify, keep_ranges,
                        scene_cuts)
        frames = next(n for n, t in safe if t == new_end)
        clip["source_start"], clip["source_end"] = new_start, new_end
        events.append({
            "clip_id": clip["clip_id"],
            "source_id": clip.get("source_id"),
            "original_start": round(start, 3),
            "original_end": round(end, 3),
            "new_start": new_start,
            "new_end": new_end,
            "start_reason": start_reason,
            "frame_count": frames,
        })
    return clips, events


def edge_frame_snapper(grid, windows, classify, source_duration):
    """frame_snap(edge, clip, ts): where snap_edges_to_frames puts `clip`'s edge moved to ts.

    The gate uses it to check a suggested edge time after frame alignment, not before.
    """
    def frame_snap(edge, clip, when):
        key = "source_start" if edge == "start" else "source_end"
        moved = {**clip, key: when}
        if moved["source_end"] <= moved["source_start"]:
            return when
        snapped, _ = snap_edges_to_frames([moved], grid, windows, classify, source_duration,
                                          allow_overlap=True, joined_to_previous=[False])
        return snapped[0][key]

    return frame_snap


def record_frame_grid(plan, geometry_qc):
    """Stamp each clip's frame_count and qc.frame_grid; the render draws exactly these frames.

    The output timeline (output_start/output_end, total_duration) is rewritten from the
    cumulative frame count, so it matches the render instead of drifting by the per-clip
    millisecond rounding (a 29.97 fps frame is 33.367 ms).
    """
    out_rate = Fraction(geometry_qc["frame_rate"])
    total = 0
    for clip in plan["clips"]:
        clip["frame_count"] = frame_count(clip["duration"], out_rate)
        clip["output_start"] = round(float(total / out_rate), 3)
        total += clip["frame_count"]
        clip["output_end"] = round(float(total / out_rate), 3)
    plan["total_duration"] = round(float(total / out_rate), 3)
    plan.setdefault("qc", {})["frame_grid"] = {
        "output_frame_rate": geometry_qc["frame_rate"],
        "frame_count": total,
        "duration": round(float(total / out_rate), 3),
        "sources": [
            {
                "source_id": row["source_id"],
                "path": row["path"],
                "frame_rate": row.get("frame_rate"),
                "video_start_offset": row.get("video_start_offset", 0.0),
                "start_snapped": parse_frame_rate(row.get("frame_rate")) is not None,
            }
            for row in geometry_qc["sources"]
        ],
    }
    return plan
