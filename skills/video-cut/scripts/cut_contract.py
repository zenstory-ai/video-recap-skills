"""Normalize cut plans and maintain render-cache provenance."""

import hashlib

import json
import os

import re


from pathlib import Path

from lib import CONFIG, get_video_duration, log

EDITED_SOURCE_RENDER_ALGORITHM_VERSION = "edited-source-render-v3"

GEOMETRY_RENDER_ALGORITHM_VERSION = "geometry-weighted-orientation-area-fps-v2"


def parse_duration_seconds(value):
    """Parse seconds, 10m/1h forms, or HH:MM:SS into seconds."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds <= 0:
            raise ValueError("duration must be positive")
        return seconds

    text = str(value).strip().lower()
    if not text:
        return None

    if ":" in text:
        parts = text.split(":")
        if len(parts) not in (2, 3):
            raise ValueError(f"invalid duration: {value}")
        try:
            nums = [float(p) for p in parts]
        except ValueError as exc:
            raise ValueError(f"invalid duration: {value}") from exc
        if any(n < 0 for n in nums):
            raise ValueError("duration must be positive")
        if nums[-1] >= 60 or (len(nums) == 3 and nums[-2] >= 60):
            raise ValueError(f"invalid duration: {value}")
        if len(nums) == 2:
            seconds = nums[0] * 60 + nums[1]
        else:
            seconds = nums[0] * 3600 + nums[1] * 60 + nums[2]
        if seconds <= 0:
            raise ValueError("duration must be positive")
        return seconds

    # One or more <number><unit> tokens: "600", "10m", "500ms", "2m30s", "1h5m30s".
    # A bare number is read as seconds; units may be combined (compound durations).
    factors = {"ms": 0.001, "s": 1, "m": 60, "h": 3600}
    sign = 1.0
    body = text
    if body[:1] in "+-":
        sign = -1.0 if body[0] == "-" else 1.0
        body = body[1:]
    token_re = re.compile(r"([0-9]+(?:\.[0-9]+)?)(ms|s|m|h)?")
    pos = 0
    seconds = 0.0
    matched = False
    for m in token_re.finditer(body):
        if m.start() != pos:
            break
        pos = m.end()
        matched = True
        seconds += float(m.group(1)) * factors[m.group(2) or "s"]
    if not matched or pos != len(body):
        raise ValueError(f"invalid duration: {value}")
    seconds *= sign
    if seconds <= 0:
        raise ValueError("duration must be positive")
    return seconds


def _overlaps_authored_range(ranges, start, end):
    """Whether [start,end) collides with an already-accepted clip, as AUTHORED.

    Overlap is judged on the agent's own in/out points, never on the padded ones.
    `clip_padding` deliberately widens every clip by the same amount on both ends, so
    judging padded ranges makes any two back-to-back clips (…, 10) and (10, …) look like
    duplicate footage and hard-fails a perfectly ordinary plan. Padding is an output
    nicety; only what the agent actually asked for defines duplication.
    """
    return any(start < other_end and end > other_start for other_start, other_end in ranges)


def _clip_value(raw, *names):
    for name in names:
        if name in raw:
            return raw[name]
    return None


def load_clip_plan(path):
    """Load `clip_plan.json`, accepting either a list or {"clips": [...]} object."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _stable_json_dumps(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )


def value_fingerprint(value):
    """Return a stable fingerprint for JSON-serializable non-secret values."""
    return hashlib.md5(_stable_json_dumps(value).encode("utf-8")).hexdigest()


def cut_plan_fingerprint(validated_plan):
    """Hash the exact normalized clip plan that determines edited_source.mp4 bytes."""
    payload = dict(validated_plan)
    # Provenance for raw-plan freshness is not part of the edited media bytes.
    payload.pop("raw_plan_fingerprint", None)
    # QC is observability derived from the media plan, not an input range decision.
    payload.pop("qc", None)
    return value_fingerprint(payload)


_FILE_FINGERPRINT_MEMO = {}


def _file_identity(path):
    """(device, inode, size, mtime_ns) — changes whenever the bytes could have changed."""
    st = os.stat(os.fspath(path))
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def file_fingerprint(path, chunk_size=1024 * 1024):
    """Return a full-content fingerprint for cache-correct identity checks.

    The digest covers CONTENT only — never the path or mtime — so a copied video or
    artifact is still recognised as the same asset, while any byte change invalidates
    the cache even if timestamps, size, head, or tail bytes are misleading.

    Identity metadata is used ONLY to memoize within a single process. One understanding
    run fingerprints the same source video 8-10 times and the whole extracted frame set
    2-3 times; on a 40-minute video at fps=1 that is gigabytes of redundant reads before
    any real work starts. A file rewritten in place gets a new (size, mtime_ns) and is
    re-hashed, so the memo can never serve a stale digest.
    """
    key = _file_identity(path)
    memoized = _FILE_FINGERPRINT_MEMO.get(key)
    if memoized is not None:
        return memoized
    h = hashlib.sha256()
    with open(os.fspath(path), "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    digest = h.hexdigest()
    _FILE_FINGERPRINT_MEMO[key] = digest
    return digest


def _edited_source_meta_path(output_path):
    return Path(str(output_path) + ".meta.json")


def _load_edited_source_meta(output_path):
    """None when never rendered; a sidecar this skill wrote but cannot parse raises."""
    meta_path = _edited_source_meta_path(output_path)
    if not meta_path.exists():
        return None
    return json.loads(meta_path.read_text(encoding="utf-8"))


def _source_fingerprints_for_plan(validated_plan, input_video=None):
    """Fingerprint every media file that can affect edited_source.mp4 bytes."""
    # Single-source plans carry no per-clip source_path; the CLI video is the only input.
    paths = {clip["source_path"] for clip in validated_plan["clips"] if "source_path" in clip}
    if not paths and input_video is not None:
        paths.add(str(input_video))
    return {str(Path(path)): file_fingerprint(path) for path in sorted(paths)}


def edited_source_render_cache_payload():
    """Render-affecting settings that invalidate edited_source.mp4 cache reuse.

    Keep this payload limited to inputs/algorithms that can change rendered media bytes.
    Observational QC produced after validation/render is intentionally excluded.
    """
    return {
        "render_algorithm_version": EDITED_SOURCE_RENDER_ALGORITHM_VERSION,
        "geometry_render_algorithm_version": GEOMETRY_RENDER_ALGORITHM_VERSION,
        "clip_join_audio_fade_ms": round(CONFIG["clip_join_audio_fade_ms"], 3),
    }


def edited_source_render_fingerprint():
    return value_fingerprint(edited_source_render_cache_payload())


def _write_edited_source_meta(output_path, validated_plan, input_video=None):
    meta = {
        "schema_version": 2,
        "clip_plan_fingerprint": cut_plan_fingerprint(validated_plan),
        "render_fingerprint": edited_source_render_fingerprint(),
        "render_cache": edited_source_render_cache_payload(),
        "source_fingerprints": _source_fingerprints_for_plan(validated_plan, input_video),
        "edited_source_fingerprint": file_fingerprint(output_path),
        "total_duration": validated_plan["total_duration"],
        "clip_count": len(validated_plan["clips"]),
    }
    _edited_source_meta_path(output_path).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def should_reuse_edited_source(output_path, validated_plan, input_video=None):
    """Return True only when edited_source.mp4 matches source media and cut params."""
    output_path = Path(output_path)
    if not output_path.exists():
        return False
    meta = _load_edited_source_meta(output_path)
    if meta is None:
        return False
    if (
        meta["clip_plan_fingerprint"] != cut_plan_fingerprint(validated_plan)
        or meta["render_fingerprint"] != edited_source_render_fingerprint()
        or meta["source_fingerprints"]
        != _source_fingerprints_for_plan(validated_plan, input_video)
    ):
        return False
    return meta["edited_source_fingerprint"] == file_fingerprint(output_path)


def _manifest_source_entries(sources_manifest):
    """Return source rows from common multi-source manifest shapes."""
    if isinstance(sources_manifest, dict):
        if isinstance(sources_manifest.get("sources"), list):
            return sources_manifest["sources"]
        rows = []
        for source_id, value in sources_manifest.items():
            if source_id in {"schema_version", "version"}:
                continue
            if isinstance(value, dict):
                row = dict(value)
                row.setdefault("source_id", source_id)
                rows.append(row)
        if rows:
            return rows
    elif isinstance(sources_manifest, list):
        return sources_manifest
    raise ValueError(
        "sources manifest must be a list, a {sources:[...]} object, or a source_id map"
    )


def normalize_sources_manifest(sources_manifest):
    """Normalize source manifest rows to {source_id: {source_path, duration}}."""
    sources = {}
    for idx, raw in enumerate(_manifest_source_entries(sources_manifest)):
        if not isinstance(raw, dict):
            raise ValueError(f"source #{idx + 1} must be an object")
        source_id = raw.get("source_id", raw.get("id", raw.get("name")))
        if source_id in (None, ""):
            raise ValueError(f"source #{idx + 1} is missing source_id")
        source_id = str(source_id)
        source_path = raw.get(
            "source_path",
            raw.get("path", raw.get("video_path", raw.get("video", raw.get("file")))),
        )
        if not source_path:
            raise ValueError(f"source {source_id} is missing source_path/path")
        duration = raw.get(
            "duration", raw.get("duration_seconds", raw.get("source_duration"))
        )
        if duration in (None, ""):
            duration = get_video_duration(source_path)
        try:
            duration = float(duration)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"source {source_id} has invalid duration") from exc
        if duration <= 0:
            raise ValueError(f"source {source_id} has invalid duration")
        sources[source_id] = {
            "source_id": source_id,
            "source_path": str(source_path),
            "duration": duration,
        }
        if raw.get("source_work_dir") not in (None, ""):
            sources[source_id]["source_work_dir"] = str(raw["source_work_dir"])
    if not sources:
        raise ValueError("sources manifest has no sources")
    return sources


def normalize_multi_source_clip_plan(
    raw_plan,
    sources_manifest,
    target_duration=None,
    clip_padding=0.0,
    min_clip_duration=0.3,
    allow_overlap=False,
):
    """Validate a multi-source clip plan and map source_id clips to source paths/durations.

    Clip order follows the raw plan; overlap validation is isolated per source_id.
    """
    sources = normalize_sources_manifest(sources_manifest)
    if isinstance(raw_plan, dict):
        raw_clips = raw_plan.get("clips", [])
        plan_target = raw_plan.get("target_duration") or raw_plan.get(
            "target_duration_seconds"
        )
        if target_duration is None and plan_target not in (None, ""):
            target_duration = parse_duration_seconds(plan_target)
    elif isinstance(raw_plan, list):
        raw_clips = raw_plan
    else:
        raise ValueError(
            "clip_plan.json must be a JSON array or an object with a clips array"
        )

    if not isinstance(raw_clips, list):
        raise ValueError("clip_plan.json field `clips` must be an array")

    padding = max(0.0, clip_padding)
    min_duration = max(0.05, min_clip_duration)
    clips = []
    source_ranges = {}
    cursor = 0.0

    for idx, raw in enumerate(raw_clips):
        if not isinstance(raw, dict):
            log(f"  跳过无效 clip #{idx + 1}: not an object")
            continue
        source_id = raw.get("source_id", raw.get("id"))
        if source_id in (None, ""):
            raise ValueError(f"clip #{idx + 1} is missing source_id")
        source_id = str(source_id)
        source = sources.get(source_id)
        if not source:
            raise ValueError(
                f"clip #{idx + 1} references unknown source_id: {source_id}"
            )
        try:
            raw_start = float(_clip_value(raw, "start", "source_start", "in"))
            raw_end = float(_clip_value(raw, "end", "source_end", "out"))
        except (TypeError, ValueError):
            log(f"  跳过无效 clip #{idx + 1}: missing numeric start/end")
            continue
        if raw_end - raw_start < min_duration:
            log(f"  跳过过短 clip #{idx + 1}: {raw_start:.1f}-{raw_end:.1f}s")
            continue
        source_duration = source["duration"]
        start = round(max(0.0, min(raw_start - padding, source_duration)), 3)
        end = round(max(0.0, min(raw_end + padding, source_duration)), 3)
        if end - start < min_duration:
            log(f"  跳过过短 clip #{idx + 1}: {start:.1f}-{end:.1f}s")
            continue
        ranges = source_ranges.setdefault(source_id, [])
        if not allow_overlap and _overlaps_authored_range(ranges, raw_start, raw_end):
            raise ValueError(
                f"clip #{idx + 1} overlaps an earlier source range for source_id {source_id}; "
                "split or remove duplicate source footage before mapping narration"
            )
        ranges.append((raw_start, raw_end))

        duration = round(end - start, 3)
        clip = {
            "clip_id": len(clips),
            "source_id": source_id,
            "source_path": source["source_path"],
            "source_start": start,
            "source_end": end,
            "output_start": round(cursor, 3),
            "output_end": round(cursor + duration, 3),
            "duration": duration,
            "reason": str(raw.get("reason", raw.get("note", ""))).strip(),
        }
        clips.append(clip)
        cursor += duration

    if not clips:
        raise ValueError("clip_plan.json has no valid clips")

    total_duration = round(sum(c["duration"] for c in clips), 3)
    plan = {
        "clips": clips,
        "total_duration": total_duration,
        "target_duration": round(target_duration, 3) if target_duration else None,
        "sources": {
            sid: {
                "source_path": s["source_path"],
                "duration": round(s["duration"], 3),
                **(
                    {"source_work_dir": s["source_work_dir"]}
                    if s.get("source_work_dir")
                    else {}
                ),
            }
            for sid, s in sources.items()
        },
        "allow_overlap": bool(allow_overlap),
    }
    if target_duration and total_duration > target_duration * 1.15:
        plan["warning"] = (
            f"validated clips total {total_duration:.1f}s exceeds target "
            f"{target_duration:.1f}s by more than 15%"
        )
        log(f"警告: {plan['warning']}")
    return plan


def normalize_clip_plan(
    raw_plan,
    video_duration,
    target_duration=None,
    clip_padding=0.0,
    min_clip_duration=0.3,
    allow_overlap=False,
):
    """Validate and enrich an agent-authored clip plan.

    Returns a dict with validated `clips`, `total_duration`, and target metadata.
    Clip order follows the agent-provided order, so montage ordering is possible.
    """
    if isinstance(raw_plan, dict):
        raw_clips = raw_plan.get("clips", [])
        plan_target = raw_plan.get("target_duration") or raw_plan.get(
            "target_duration_seconds"
        )
        if target_duration is None and plan_target not in (None, ""):
            target_duration = parse_duration_seconds(plan_target)
    elif isinstance(raw_plan, list):
        raw_clips = raw_plan
    else:
        raise ValueError(
            "clip_plan.json must be a JSON array or an object with a clips array"
        )

    if not isinstance(raw_clips, list):
        raise ValueError("clip_plan.json field `clips` must be an array")

    padding = max(0.0, clip_padding)
    min_duration = max(0.05, min_clip_duration)
    clips = []
    source_ranges = []
    cursor = 0.0

    for idx, raw in enumerate(raw_clips):
        if not isinstance(raw, dict):
            log(f"  跳过无效 clip #{idx + 1}: not an object")
            continue
        try:
            raw_start = float(_clip_value(raw, "start", "source_start", "in"))
            raw_end = float(_clip_value(raw, "end", "source_end", "out"))
        except (TypeError, ValueError):
            log(f"  跳过无效 clip #{idx + 1}: missing numeric start/end")
            continue
        if raw_end - raw_start < min_duration:
            log(f"  跳过过短 clip #{idx + 1}: {raw_start:.1f}-{raw_end:.1f}s")
            continue
        start = round(max(0.0, min(raw_start - padding, video_duration)), 3)
        end = round(max(0.0, min(raw_end + padding, video_duration)), 3)
        if end - start < min_duration:
            log(f"  跳过过短 clip #{idx + 1}: {start:.1f}-{end:.1f}s")
            continue
        if not allow_overlap and _overlaps_authored_range(source_ranges, raw_start, raw_end):
            raise ValueError(
                f"clip #{idx + 1} overlaps an earlier source range; "
                "split or remove duplicate source footage before mapping narration"
            )
        source_ranges.append((raw_start, raw_end))

        duration = round(end - start, 3)
        clip = {
            "clip_id": len(clips),
            "source_start": start,
            "source_end": end,
            "output_start": round(cursor, 3),
            "output_end": round(cursor + duration, 3),
            "duration": duration,
            "reason": str(raw.get("reason", raw.get("note", ""))).strip(),
        }
        clips.append(clip)
        cursor += duration

    if not clips:
        raise ValueError("clip_plan.json has no valid clips")

    total_duration = round(sum(c["duration"] for c in clips), 3)
    plan = {
        "clips": clips,
        "total_duration": total_duration,
        "target_duration": round(target_duration, 3) if target_duration else None,
        "source_duration": round(video_duration, 3),
        "allow_overlap": bool(allow_overlap),
    }
    if target_duration and total_duration > target_duration * 1.15:
        plan["warning"] = (
            f"validated clips total {total_duration:.1f}s exceeds target "
            f"{target_duration:.1f}s by more than 15%"
        )
        log(f"警告: {plan['warning']}")
    return plan
