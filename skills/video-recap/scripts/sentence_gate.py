"""The sentence-boundary gate: may a clip edge cut the source audio here?

Every audible clip edge must rest on the source start/end, a sentence or quiet pause, a
lossless same-source join, or outside detected speech. A blocked edge names the nearest
safe edge times on either side.
"""


def _unverified_window(row):
    return row.get("boundary_use") == "unverified"


def _same_source(left, right):
    # Single-source normalized plans omit source identity; both sides then read as None.
    return left.get("source_id") == right.get("source_id") and left.get(
        "source_path"
    ) == right.get("source_path")


def _continuous_source_join(left, right, tolerance=0.05):
    """`right` plays straight on from `left` with no media removed between them.

    Plan neighbours only: a per-source sub-plan (multi-source snapping) packs clips that
    another source's clip separates in the real plan, so consecutive clip_ids are required.
    """
    return (
        right.get("clip_id", 1) - left.get("clip_id", 0) == 1
        and _same_source(left, right)
        and abs(left["source_end"] - right["source_start"]) <= tolerance
        and abs(left["output_end"] - right["output_start"]) <= tolerance
    )


def _edge_joined(clips, idx, edge, tolerance=0.05):
    """Whether clips[idx]'s `edge` is a continuous same-source join with its plan neighbour."""
    if edge == "start":
        return idx > 0 and _continuous_source_join(clips[idx - 1], clips[idx], tolerance)
    return idx + 1 < len(clips) and _continuous_source_join(clips[idx], clips[idx + 1], tolerance)


# How far either side of a blocked edge the gate looks for a safe edge to suggest.
_SAFE_EDGE_SEARCH_SECONDS = 5.0
# How close to a pause window or speech span an edge counts as on it.
_GATE_TOLERANCE = 0.05
# The longest first-video-frame delay still waived as the source start. Typical mp4 offsets
# are a few ms; a capture whose picture begins later than this has real audio before it.
_VIDEO_START_WAIVER_SECONDS = 0.2


def _edge_classifier(boundary_windows, speech_spans, video_duration, tolerance,
                     video_start=0.0):
    """classify(edge, ts, contiguous) -> (status, reason) under the sentence-boundary gate.

    `video_start` is the source's first video frame (seconds after the file start): a clip
    cannot start any earlier on the picture, so a start up to it is the source start. Only
    up to `_VIDEO_START_WAIVER_SECONDS`: a later first frame (a TS capture whose picture
    starts 1.5 s into the audio) gets the normal speech rules, so a start moved onto it
    that lands mid-sentence still blocks.
    """
    verified_windows = [row for row in boundary_windows if not _unverified_window(row)]
    unverified_windows = [row for row in boundary_windows if _unverified_window(row)]
    source_start = video_start if video_start <= _VIDEO_START_WAIVER_SECONDS else 0.0

    def inside(rows, ts):
        return any(row["start"] - tolerance <= ts <= row["end"] + tolerance for row in rows)

    def classify(edge, ts, contiguous=False):
        if edge == "start" and ts <= source_start + tolerance:
            return "safe", "source_start"
        if edge == "end" and ts >= video_duration - tolerance:
            return "safe", "source_end"
        if contiguous:
            return "safe", "continuous_source_join"
        if inside(verified_windows, ts):
            return "safe", "sentence_or_quiet_boundary"
        if inside(unverified_windows, ts) and (not speech_spans or inside(speech_spans, ts)):
            # Word-safe pause, but the sentence end is only a coarse-ASR estimate.
            return "safe", "unverified_sentence_boundary"
        if not speech_spans:
            return "unchecked", "speech_timing_unavailable"
        if not inside(speech_spans, ts):
            return "safe", "outside_detected_speech"
        return "blocking", "inside_detected_speech"

    return classify


def _nearest_safe_edges(classify, edge, clip, boundary_windows, speech_spans, video_duration,
                        tolerance, frame_snap=None):
    """The closest safe edge times before and after a blocked edge, within the search span.

    Candidates are pause-window bounds, the first instants clear of a speech span (past the
    gate tolerance), and the source start/end; each is re-checked with the gate itself and
    must leave the clip a positive length. With `frame_snap(edge, clip, ts)` (where the
    frame-grid pass would move that edge) each candidate is reported where it lands on the
    source frame grid, and only when that landing is safe too: a candidate between two frames
    (a speech start minus the 60 ms clearance is half a frame at 25 fps) is never handed back
    for the frame pass to re-decide, so sending the suggestion back cannot be blocked again or
    moved onto the other side of a source hard cut.
    """
    ts = clip["source_start"] if edge == "start" else clip["source_end"]

    def usable(when):
        if not 0.0 <= when <= video_duration or abs(when - ts) > _SAFE_EDGE_SEARCH_SECONDS:
            return False
        return when < clip["source_end"] if edge == "start" else when > clip["source_start"]

    margin = tolerance + 0.01
    candidates = {0.0, round(video_duration, 3)}
    for row in boundary_windows:
        candidates.update((row["start"], row["end"]))
    for row in speech_spans:
        candidates.update((row["start"] - margin, row["end"] + margin))
    safe = {}
    for when in sorted(round(c, 3) for c in candidates):
        if not usable(when) or classify(edge, when)[0] != "safe":
            continue
        if frame_snap is not None:
            when = round(frame_snap(edge, clip, when), 3)
        status, reason = classify(edge, when)
        if status == "safe" and usable(when):
            safe.setdefault(when, reason)
    before = [when for when in safe if when < ts]
    after = [when for when in safe if when > ts]

    def suggestion(when):
        return {"time": when, "reason": safe[when], "delta": round(when - ts, 3)}

    return {"before": suggestion(max(before)) if before else None,
            "after": suggestion(min(after)) if after else None}


def enforce_clip_sentence_boundaries(
    plan, boundary_windows, speech_spans, video_duration, tolerance=_GATE_TOLERANCE,
    video_start=0.0, frame_snap=None,
):
    """Block any audible clip edge that falls inside detected source speech.

    Safe edges are: source start (up to a prompt first video frame, `video_start`) and end, a
    sentence/quiet pause (`unverified_sentence_boundary` when only a coarse-ASR sentence
    estimate covers it), or a truly contiguous same-source join (no media is removed).
    Missing ASR timing degrades to `unchecked` rather than inventing speech. Once ASR says
    an edge is speech-owned, failure to snap is blocking, and the check names the nearest
    safe edge times on either side (`nearest_safe`, frame-checked through `frame_snap`).
    """
    clips = plan["clips"]
    checks, new_blockers = [], []
    classify = _edge_classifier(boundary_windows, speech_spans, video_duration, tolerance,
                                video_start)

    for idx, clip in enumerate(clips):
        for edge, ts in (("start", clip["source_start"]), ("end", clip["source_end"])):
            status, reason = classify(edge, ts, _edge_joined(clips, idx, edge, tolerance))
            check = {
                "clip_id": clip["clip_id"],
                "source_id": clip.get("source_id"),
                "edge": edge,
                "time": round(ts, 3),
                "status": status,
                "reason": reason,
            }
            if status == "blocking":
                check["nearest_safe"] = _nearest_safe_edges(
                    classify, edge, clip, boundary_windows, speech_spans, video_duration,
                    tolerance, frame_snap,
                )
            checks.append(check)
            if status == "blocking":
                new_blockers.append(
                    {
                        "code": "unsafe_clip_sentence_boundary",
                        **check,
                        "message": "剪辑边界仍落在原声讲话区间内，必须移动到句末锚点，不能截断原声句子。"
                        "nearest_safe 给出前后最近的安全边界时间。",
                    }
                )

    qc = plan.setdefault("qc", {})
    qc.setdefault("boundary_status", {})["sentence_checks"] = checks
    existing = [
        row
        for row in qc.get("blocking", [])
        if row["code"] != "unsafe_clip_sentence_boundary"
    ]
    if existing or new_blockers:
        qc["blocking"] = existing + new_blockers
    else:
        qc.pop("blocking", None)
    return plan
