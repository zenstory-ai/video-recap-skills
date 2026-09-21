"""Shared constants for the self-contained video-assemble skill."""

from fractions import Fraction
import math

ASSEMBLY_MANIFEST = "assembly_manifest.json"
ASSEMBLY_QC = "assembly_qc.json"
VISUAL_QC = "visual_qc.json"
VISUAL_OVERLAYS = "visual_overlays.json"
SEGMENT_AUDIO_SCHEMA_VERSION = 1

# The picture codecs every packet/frame clock proof in this skill accepts.
SUPPORTED_PICTURE_CODECS = frozenset({"h264", "hevc"})

# The one exact output audio clock every explicit-sound artifact in this skill uses.
OUTPUT_SAMPLE_RATE = 48_000


def frame_clock_samples(frame, fps, rate=OUTPUT_SAMPLE_RATE):
    """Project an exact frame boundary of a video clock onto the output sample clock.

    Broadcast rates such as 30000/1001 put frame boundaries between whole samples, so
    the exact Fraction position is rounded half-up once, here. Every sample bound in
    the skill - a segment edge and the whole-picture `total_samples` alike - is this
    single projection, so an integral clock is unchanged and a fractional one stays
    consistent across the explicit-sound tools.
    """
    exact = Fraction(int(frame) * rate, 1) / Fraction(fps)
    return math.floor(exact + Fraction(1, 2))


FILTER_SCRIPT_THRESHOLD_BYTES = 8000

# The default subtitle metrics were tuned in this reference canvas.
SUBTITLE_STYLE_REF_W = 1280
SUBTITLE_STYLE_REF_H = 720
_SUBTITLE_TERMINAL_PUNCTUATION = "。！？!?…."
_SUBTITLE_CLOSING_QUOTES = "」』”’）)]】》〉\"'"

_MIN_GAP_TO_SUBTITLE = 0.8
_MIN_READABLE_SECONDS = 0.3
_MIN_ASR_CLIP_OVERLAP = 0.05
# timeline.py serializes interval bounds onto a 1e-4 second grid, flooring starts
# and ceiling ends, so two bounds that were identical before serialization can come
# back one grid step apart. Contiguity joins must tolerate that whole step.
_TIMELINE_TIME_GRID_SECONDS = 1e-4
_CLIP_CONTIGUITY_TOLERANCE = 1.5 * _TIMELINE_TIME_GRID_SECONDS
_MAX_ORIGINAL_READ_CPS = 9.0
_AUTO_ORIGINAL_READ_CPS = 6.0

_SUPPORTED_VISUAL_OVERLAY_TYPES = {"top_title", "inline_label_or_callout"}
