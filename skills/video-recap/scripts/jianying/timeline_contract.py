"""Timeline validation at the JianYing adapter boundary."""

import copy
import math


CURRENT_SCHEMA_VERSION = 2
# Hand-authoring fields the exporter once mapped to JianYing (speed, reverse,
# transitions, masks, LUTs, green screen, rich text, resource tracks). No pipeline
# stage emits them; they are rejected so an old hand-written timeline fails loudly
# instead of silently exporting a draft without the effect it asked for.
REMOVED_ITEM_FIELDS = (
    "chroma", "compound", "flip", "green_background", "lut", "mask", "opacity",
    "reverse", "reverse_path", "rotation_degrees", "speed", "style", "style_id",
    "transition", "words",
)
REMOVED_ROOT_FIELDS = ("resource_packages", "style_presets")


def _error(path, expectation):
    raise ValueError(f"invalid timeline {path}: {expectation}")


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _field_path(path, key):
    return f"{path}.{key}" if path else key


def _require_number(container, key, path, *, minimum=None):
    field_path = _field_path(path, key)
    if key not in container or not _is_number(container[key]):
        _error(field_path, "must be a finite number")
    value = container[key]
    if minimum is not None and value < minimum:
        _error(field_path, f"must be >= {minimum}")
    return value


def _require_string(container, key, path):
    value = container.get(key)
    if not isinstance(value, str) or not value:
        _error(_field_path(path, key), "must be a non-empty string")
    return value


def _reject_removed(container, fields, path):
    for field in fields:
        if field in container:
            _error(_field_path(path, field), "JianYing authoring extension is no longer supported")


def _validate_span(item, path, start_key="timeline_start", end_key="timeline_end"):
    start = _require_number(item, start_key, path, minimum=0)
    end = _require_number(item, end_key, path, minimum=0)
    if end <= start:
        _error(f"{path}.{end_key}", f"must be greater than {start_key}")


def _validate_item(item, path):
    if not isinstance(item, dict):
        _error(path, "must be an object")
    _reject_removed(item, REMOVED_ITEM_FIELDS, path)
    _validate_span(item, path)
    for field in ("scale", "position"):
        if field in item and not isinstance(item[field], dict):
            _error(f"{path}.{field}", "must be an object")


def _validate_video_clip(clip, path):
    _validate_item(clip, path)
    _require_string(clip, "source_path", path)
    _validate_span(clip, path, "source_start", "source_end")
    if "audio" in clip and not isinstance(clip["audio"], dict):
        _error(f"{path}.audio", "must be an object")


def _validate_segment(segment, path, kind):
    _validate_item(segment, path)
    if kind in {"audio", "image"}:
        _require_string(segment, "source_path", path)
    elif not isinstance(segment.get("text"), str):
        _error(f"{path}.text", "must be a string")


def _validate_track(track, path):
    if not isinstance(track, dict):
        _error(path, "must be an object")
    kind = _require_string(track, "kind", path)
    if "name" in track and (not isinstance(track["name"], str) or not track["name"]):
        _error(f"{path}.name", "must be a non-empty string")

    if kind == "video":
        clips = track.get("clips")
        if not isinstance(clips, list):
            _error(f"{path}.clips", "must be an array")
        for index, clip in enumerate(clips):
            _validate_video_clip(clip, f"{path}.clips[{index}]")
        return

    if kind in {"audio", "image", "text"}:
        segments = track.get("segments")
        if not isinstance(segments, list):
            _error(f"{path}.segments", "must be an array")
        if kind == "audio":
            if "role" in track and not isinstance(track["role"], str):
                _error(f"{path}.role", "must be a string")
            if "loop" in track and not isinstance(track["loop"], bool):
                _error(f"{path}.loop", "must be a boolean")
        for index, segment in enumerate(segments):
            _validate_segment(segment, f"{path}.segments[{index}]", kind)
        return

    _error(f"{path}.kind", f"unsupported track kind {kind!r}")


def _validate(timeline):
    canvas = timeline.get("canvas")
    if not isinstance(canvas, dict):
        _error("canvas", "must be an object")
    for dimension in ("width", "height"):
        value = canvas.get(dimension)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            _error(f"canvas.{dimension}", "must be a positive integer")
    fps = _require_number(canvas, "fps", "canvas")
    if fps <= 0:
        _error("canvas.fps", "must be greater than 0")

    _require_number(timeline, "duration", "", minimum=0)
    _reject_removed(timeline, REMOVED_ROOT_FIELDS, "")
    tracks = timeline.get("tracks")
    if not isinstance(tracks, list):
        _error("tracks", "must be an array")
    for index, track in enumerate(tracks):
        _validate_track(track, f"tracks[{index}]")


def normalize_timeline(timeline):
    """Return a validated copy of a schema-v2 timeline (the version timeline.py emits)."""
    if not isinstance(timeline, dict):
        _error("root", "must be an object")
    schema_version = timeline.get("schema_version")
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        _error("schema_version", f"must be integer {CURRENT_SCHEMA_VERSION}")
    if schema_version != CURRENT_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported timeline schema_version {schema_version}; "
            f"only {CURRENT_SCHEMA_VERSION} is supported (a v1 timeline only needs "
            f"schema_version set to {CURRENT_SCHEMA_VERSION})"
        )

    normalized = copy.deepcopy(timeline)
    _validate(normalized)
    return normalized
