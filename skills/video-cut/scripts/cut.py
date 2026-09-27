#!/usr/bin/env python3
"""Public API and CLI entrypoint for the self-contained video-cut skill."""

from cut_cli import main
from cut_contract import (
    edited_source_render_cache_payload,
    normalize_clip_plan,
    normalize_multi_source_clip_plan,
    parse_duration_seconds,
    should_reuse_edited_source,
)
from cut_render import build_edited_source_video
from media_geometry import VideoGeometry
from narration_mapping import update_cut_qc
from sentence_boundaries import (
    enforce_clip_sentence_boundaries,
    snap_clip_ends_to_lines,
    snap_clip_starts_to_lines,
    snap_clips_off_shot_changes,
    snap_multi_source_clips,
)

__all__ = [
    "VideoGeometry",
    "build_edited_source_video",
    "edited_source_render_cache_payload",
    "enforce_clip_sentence_boundaries",
    "main",
    "normalize_clip_plan",
    "normalize_multi_source_clip_plan",
    "parse_duration_seconds",
    "should_reuse_edited_source",
    "snap_clip_ends_to_lines",
    "snap_clip_starts_to_lines",
    "snap_clips_off_shot_changes",
    "snap_multi_source_clips",
    "update_cut_qc",
]

if __name__ == "__main__":
    main()
