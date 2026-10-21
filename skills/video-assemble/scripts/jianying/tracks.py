"""Semantic track ordering and overlap-safe allocation for JianYing export.

The order mirrors duo-video's authoring layout. It is used only to order track
objects; JianYing segment ``render_index`` remains the schema default and must
not be confused with this semantic layout value.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TrackBand:
    kind: str
    track_type: str
    layout_order: int


SEGMENT_RENDER_INDEX = 2


TRACK_LAYOUT_BANDS = {
    "sound": TrackBand("sound", "audio", 10_000),
    "audio": TrackBand("audio", "audio", 20_000),
    "green_screen": TrackBand("green_screen", "video", 30_000),
    "video": TrackBand("video", "video", 40_000),
    "image": TrackBand("image", "video", 50_000),
    "mask": TrackBand("mask", "video", 60_000),
    "effect": TrackBand("effect", "effect", 70_000),
    "video_effect": TrackBand("video_effect", "effect", 70_000),
    "face_effect": TrackBand("face_effect", "effect", 70_010),
    "sticker": TrackBand("sticker", "sticker", 80_000),
    "subtitle": TrackBand("subtitle", "text", 90_000),
    "text": TrackBand("text", "text", 100_000),
    "text_template": TrackBand("text_template", "text", 110_000),
}


@dataclass(frozen=True)
class AllocatedTrack:
    kind: str
    name: str
    track_type: str
    layout_order: int


class TrackAllocator:
    """Allocate deterministic suffix tracks when same-name segments overlap.

    Intervals are half-open, so adjacent segments reuse a track while true
    overlap creates ``name-1``, ``name-2``, and so on.
    """

    def __init__(self):
        self._occupied = {}

    @staticmethod
    def _overlaps(start_us, duration_us, existing):
        end_us = int(start_us) + int(duration_us)
        return any(int(start_us) < old_end and end_us > old_start for old_start, old_end in existing)

    def allocate(self, kind, base_name, start_us, duration_us):
        band = TRACK_LAYOUT_BANDS[kind]
        suffix = 0
        while True:
            name = base_name if suffix == 0 else f"{base_name}-{suffix}"
            key = (kind, name)
            occupied = self._occupied.setdefault(key, [])
            if not self._overlaps(start_us, duration_us, occupied):
                occupied.append((int(start_us), int(start_us) + int(duration_us)))
                return AllocatedTrack(kind, name, band.track_type, band.layout_order + suffix)
            suffix += 1
