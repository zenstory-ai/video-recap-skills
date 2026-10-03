"""Load and remap source evidence for narration review."""

import json
from pathlib import Path


def _load(work_dir, name):
    """Pipeline-written artifact: None when never written; corrupt JSON raises."""
    path = Path(work_dir) / name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _load_required(work_dir, name, hint):
    """Pipeline-written artifact the stage cannot run without; corrupt JSON raises."""
    path = Path(work_dir) / name
    if not path.exists():
        raise SystemExit(f"缺少 {path}；{hint}")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_agent_optional(work_dir, name):
    """Optional agent-authored artifact: missing or unparseable both fail open to None."""
    path = Path(work_dir) / name
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _asr_segments(payload):
    """Accept both raw ASR lists and consolidated ``{"segments": [...]}`` artifacts."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("segments"), list):
        return payload["segments"]
    return []


def _load_review_grounding(work_dir):
    """Load grounding for single-source and project-level multi-source work dirs.

    Multi-source recap intentionally stores each source's understanding artifacts below
    ``sources/<source_id>``.  Reviewing only project-root ``vlm_analysis.json`` /
    ``asr_result.json`` therefore produced a formally valid but empty evidence bundle.  Load
    and label each source here so output-timeline remapping can retain provenance.
    """
    work_dir = Path(work_dir)
    manifest = _load(work_dir, "multi_source_manifest.json")
    if manifest is not None:
        combined_vlm = []
        combined_asr = []
        for source in manifest["sources"]:
            source_id = str(source["source_id"])
            source_dir = (work_dir / source["source_work_dir"]).resolve(strict=False)
            try:
                source_dir.relative_to(work_dir.resolve(strict=False))
            except ValueError:
                continue
            for scene in _load(source_dir, "vlm_analysis.json") or []:
                combined_vlm.append({**scene, "source_id": source_id})
            for segment in _preferred_asr(source_dir):
                combined_asr.append({**segment, "source_id": source_id})
        if combined_vlm or combined_asr:
            return combined_vlm, combined_asr

    return _load(work_dir, "vlm_analysis.json") or [], _preferred_asr(work_dir)


def _preferred_asr(work_dir):
    """asr_clean.json wins over asr_result.json, as in lint, cut and assemble."""
    clean = _load(work_dir, "asr_clean.json")
    return _asr_segments(clean if clean is not None else _load(work_dir, "asr_result.json"))


def _load_cut_clip_spans(work_dir):
    """Load explicit source→output spans from the validated cut plan only.

    `cut_output` review compares output-time narration to output-time evidence. A raw
    clip_plan can be pre-padding/pre-snap and may omit output spans, so using it would
    look grounded while disagreeing with edited_source.mp4. Missing/stale validated data
    should fail this advisory stage and let the orchestrator fail-open visibly.
    """
    work_dir = Path(work_dir)
    plan = _load(work_dir, "clip_plan_validated.json")
    if plan is None:
        return None
    raw_path = work_dir / "clip_plan.json"
    if (
        raw_path.exists()
        and (work_dir / "clip_plan_validated.json").stat().st_mtime_ns
        < raw_path.stat().st_mtime_ns
    ):
        return None
    spans = [
        {
            "source_start": clip["source_start"],
            "source_end": clip["source_end"],
            "output_start": clip["output_start"],
            "output_end": clip["output_end"],
            # video-cut labels source_id only for multi-source plans.
            "source_id": str(clip.get("source_id", "0")),
            "source_clip_id": clip["clip_id"],
            "output_segment_index": index,
        }
        for index, clip in enumerate(plan["clips"])
    ]
    return spans or None


def _source_output_overlaps(start, end, spans, *, source_id=None):
    source_id = str(source_id) if source_id is not None else None
    if source_id not in (None, "", "0"):
        spans = [
            span for span in (spans or []) if str(span.get("source_id")) == source_id
        ]
    overlaps = []
    for span in spans or []:
        source_start = max(start, span["source_start"])
        source_end = min(end, span["source_end"])
        if source_end <= source_start:
            continue
        output_start = span["output_start"] + (source_start - span["source_start"])
        output_end = span["output_start"] + (source_end - span["source_start"])
        overlaps.append(
            {
                "source_start": source_start,
                "source_end": source_end,
                "output_start": output_start,
                "output_end": output_end,
                "source_id": span.get("source_id", "0"),
                "source_clip_id": span.get("source_clip_id"),
                "output_segment_index": span.get("output_segment_index"),
            }
        )
    return overlaps


def _remap_frame_facts(frame_facts, overlap):
    if not isinstance(frame_facts, dict):
        return frame_facts
    out = {}
    for raw_ts, vals in frame_facts.items():
        try:
            ts = float(raw_ts)
        except (TypeError, ValueError):
            continue
        if not (overlap["source_start"] <= ts <= overlap["source_end"]):
            continue
        out_ts = overlap["output_start"] + (ts - overlap["source_start"])
        out[f"{out_ts:.3f}"] = vals
    return out


def remap_grounding_to_output_timeline(vlm_analysis, asr_result, clip_spans):
    """Return VLM/ASR grounding clipped/remapped from source time to cut output time."""
    if not clip_spans:
        return vlm_analysis or [], asr_result or []

    remapped_scenes = []
    for scene in vlm_analysis or []:
        start = float(scene["start"])
        end = float(scene["end"])
        overlaps = _source_output_overlaps(
            start,
            end,
            clip_spans,
            source_id=scene.get("source_id") if "source_id" in scene else None,
        )
        for part_idx, overlap in enumerate(overlaps):
            item = dict(scene)
            item["start"] = round(overlap["output_start"], 3)
            item["end"] = round(overlap["output_end"], 3)
            item["frame_facts"] = _remap_frame_facts(scene.get("frame_facts"), overlap)
            item["source_start"] = round(overlap["source_start"], 3)
            item["source_end"] = round(overlap["source_end"], 3)
            item["source_id"] = overlap.get("source_id", "0")
            item["source_clip_id"] = overlap.get("source_clip_id")
            item["output_segment_index"] = overlap.get("output_segment_index")
            if len(overlaps) > 1:
                item["scene_id"] = f"{scene.get('scene_id', '?')}.{part_idx}"
            remapped_scenes.append(item)

    remapped_asr = []
    for seg in asr_result or []:
        start = float(seg["start"])
        end = float(seg["end"])
        text = str(seg.get("text", "")).strip()
        if not text:
            continue
        for overlap in _source_output_overlaps(
            start,
            end,
            clip_spans,
            source_id=seg.get("source_id") if "source_id" in seg else None,
        ):
            item = dict(seg)
            item["start"] = round(overlap["output_start"], 3)
            item["end"] = round(overlap["output_end"], 3)
            item["source_start"] = round(overlap["source_start"], 3)
            item["source_end"] = round(overlap["source_end"], 3)
            item["source_id"] = overlap.get("source_id", "0")
            item["source_clip_id"] = overlap.get("source_clip_id")
            item["output_segment_index"] = overlap.get("output_segment_index")
            remapped_asr.append(item)

    remapped_scenes.sort(key=lambda x: (x["start"], x["end"]))
    remapped_asr.sort(key=lambda x: (x["start"], x["end"]))
    return remapped_scenes, remapped_asr
