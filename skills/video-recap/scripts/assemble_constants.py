"""Shared constants for the assemble stage."""

ASSEMBLY_MANIFEST = "assembly_manifest.json"
ASSEMBLY_QC = "assembly_qc.json"
VISUAL_QC = "visual_qc.json"

FILTER_SCRIPT_THRESHOLD_BYTES = 8000

# The default subtitle metrics were tuned in this reference canvas.
SUBTITLE_STYLE_REF_W = 1280
SUBTITLE_STYLE_REF_H = 720
_SUBTITLE_TERMINAL_PUNCTUATION = "。！？!?…."
_SUBTITLE_DISPLAY_STRIP_PUNCTUATION = "，。！？、；：…,.!?;:"
_SUBTITLE_CLOSING_QUOTES = "」』”’）)]】》〉\"'"

_MIN_GAP_TO_SUBTITLE = 0.8
_MIN_READABLE_SECONDS = 0.3
# timeline.py serializes interval bounds onto a 1e-4 second grid, flooring starts
# and ceiling ends, so two bounds that were identical before serialization can come
# back one grid step apart. Contiguity joins must tolerate that whole step.
_TIMELINE_TIME_GRID_SECONDS = 1e-4
_CLIP_CONTIGUITY_TOLERANCE = 1.5 * _TIMELINE_TIME_GRID_SECONDS
