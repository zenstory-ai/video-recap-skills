"""Where an interrupting narration block may be told to move.

`interrupts_source_sentence` suggests a sentence-end anchor the whole block can shift to.
These helpers keep that suggestion between the block's neighbours and clear of every other
block, and decide when a block is a back-to-back handoff that needs no entry check.
"""

import math


def _is_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


# A narration block that starts within this gap after another block's end is a
# back-to-back handoff: the source track is never exposed between the two.
CONNECTED_HANDOFF_SECONDS = 0.15
# A suggested entry farther than this from the authored one moves the block off the
# pictures it was written for; past it the author must rethink the block instead.
SUGGESTED_ANCHOR_MAX_SHIFT_SECONDS = 10.0


def _shift_is_clear(new_start, duration, other_spans):
    """The block moved whole to new_start neither overlaps nor abuts another block."""
    new_end = new_start + duration
    gap = CONNECTED_HANDOFF_SECONDS
    return all(
        new_end < other_start - gap or new_start > other_end + gap
        for other_start, other_end in other_spans
    )


def _nearest_safe_anchor(start, end, anchors, other_spans, window=(None, None)):
    """The anchor closest to `start` the whole block can move to (duration kept) without
    overlapping or abutting another block or leaving `window`, within
    SUGGESTED_ANCHOR_MAX_SHIFT_SECONDS; a later anchor wins a tie. None when none qualifies.

    `window` is the open (low, high) range the moved block must sit strictly inside: the
    neighbours' ends with the handoff gap, so a block never jumps over a neighbour (story
    order) and two adjacent blocks never get colliding suggestions. Either side may be None.
    """
    duration = end - start
    low, high = window
    candidates = [
        anchor
        for anchor in anchors
        if abs(anchor["time"] - start) <= SUGGESTED_ANCHOR_MAX_SHIFT_SECONDS
        and anchor["time"] >= 0
        and (low is None or anchor["time"] > low)
        and (high is None or anchor["time"] + duration < high)
        and _shift_is_clear(anchor["time"], duration, other_spans)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda anchor: (abs(anchor["time"] - start), -anchor["time"]))


def _valid_span(seg):
    """(start, end) of a block with a usable numeric window, else None."""
    if not isinstance(seg, dict):
        return None
    start, end = seg.get("start"), seg.get("end")
    if _is_number(start) and _is_number(end) and end > start:
        return start, end
    return None


def _other_block_spans(narration, idx):
    """(start, end) of every other block with a usable numeric window."""
    return [
        span
        for other_idx, other in enumerate(narration)
        if other_idx != idx and (span := _valid_span(other))
    ]


# Clip/timeline edges are allowed positions; the open window bound sits this far outside.
_EDGE_EPSILON = 1e-6


def _suggestion_window(narration, idx, suggested_spans, limits=(None, None)):
    """Open (low, high) range a suggested move of block `idx` must stay strictly inside.

    Bounded by the previous and next valid blocks (narration is chronological) with the
    handoff gap. The previous block counts at both its authored and its own suggested
    window, so applying any subset of the suggestions keeps adjacent blocks apart and in
    order. `limits` is the closed (first, last) range the block must stay in: its clip in
    cut mode, the output duration in cut_output; either side may be None.
    """
    gap = CONNECTED_HANDOFF_SECONDS
    low = high = None
    for prev_idx in range(idx - 1, -1, -1):
        span = _valid_span(narration[prev_idx])
        if span:
            low = max(span[1], suggested_spans.get(prev_idx, span)[1]) + gap
            break
    for next_idx in range(idx + 1, len(narration)):
        span = _valid_span(narration[next_idx])
        if span:
            high = span[0] - gap
            break
    first, last = limits
    if first is not None:
        low = max(x for x in (low, first - _EDGE_EPSILON) if x is not None)
    if last is not None:
        high = min(x for x in (high, last + _EDGE_EPSILON) if x is not None)
    return low, high


def _has_connected_predecessor(narration, idx, start):
    """A back-to-back narration handoff (<=150ms gap) does not expose the source track,
    so the next TTS block is not a new source-speech entry."""
    for other_idx, other in enumerate(narration):
        if other_idx == idx or not isinstance(other, dict):
            continue
        other_start, other_end = other.get("start"), other.get("end")
        if not _is_number(other_start) or not _is_number(other_end):
            continue
        if other_start < start and -0.001 <= start - other_end <= CONNECTED_HANDOFF_SECONDS:
            return True
    return False
