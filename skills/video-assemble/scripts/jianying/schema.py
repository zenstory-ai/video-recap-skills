"""Schema constants and skeleton factories for JianYing export.

This module is intentionally data-oriented: it owns draft version metadata and
the full `materials` parallel-array shape.
"""

from jianying.templates import template

# The full 剪映 materials object: ~45 parallel arrays. Only arrays backed by a
# production builder are populated; retaining the full shape preserves compatibility.
MATERIAL_KEYS = ["ai_translates", "audio_balances", "audio_effects", "audio_fades", "audio_track_indexes", "audios", "beats", "canvases", "chromas", "color_curves", "digital_humans", "drafts", "effects", "flowers", "green_screens", "handwrites", "hsl", "images", "log_color_wheels", "loudnesses", "manual_deformations", "masks", "common_mask", "material_animations", "material_colors", "multi_language_refs", "placeholders", "plugin_effects", "primary_color_wheels", "realtime_denoises", "shapes", "smart_crops", "smart_relights", "sound_channel_mappings", "speeds", "stickers", "tail_leaders", "text_templates", "texts", "time_marks", "transitions", "video_effects", "video_trackings", "videos", "vocal_beautifys", "vocal_separations"]


def us(seconds):
    """Seconds (float) -> integer microseconds. The single seconds->µs boundary."""
    return int(round(float(seconds) * 1_000_000))


def full_materials(filled):
    """Return a complete JianYing `materials` object with all known arrays."""
    out = {k: [] for k in MATERIAL_KEYS}
    out.update(filled)
    return out


def scrub_platform_identity(project):
    """Remove hardware fingerprints carried by the pinned upstream templates."""
    for platform_key in ("last_modified_platform", "platform"):
        for identity_key in ("device_id", "hard_disk_id", "mac_address"):
            project[platform_key][identity_key] = ""
    return project


def draft_content_skeleton(draft_id, width, height, fps, total_us, materials, tracks):
    """Build the root `draft_content.json` / `draft_info.json` skeleton."""
    content = template("project")
    scrub_platform_identity(content)
    content["canvas_config"] = {"width": width, "height": height, "ratio": "original"}
    content["duration"] = int(total_us)
    content["fps"] = float(fps)
    content["id"] = draft_id
    content["materials"] = full_materials(materials)
    content["tracks"] = tracks
    return content


def meta_info(draft_id, total_us):
    """Build the companion `draft_meta_info.json` skeleton."""
    meta = template("meta")
    meta["draft_id"] = draft_id
    meta["draft_timeline_materials_size_"] = 0
    meta["tm_duration"] = int(total_us)
    return meta
