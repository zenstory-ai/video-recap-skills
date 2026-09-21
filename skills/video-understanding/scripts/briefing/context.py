"""Load and format research, consolidation, and substrate context."""

import json
import re
from pathlib import Path

from lib import CONFIG, file_identity


def _load_background_research(work_dir):
    """Load the agent-authored background_research.json ({} when absent)."""
    path = Path(work_dir) / "background_research.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _clip_text(text, limit):
    """Collapse whitespace and clip. Research fields are optional, so None clips to ""."""
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


def _format_background_research(research, limit=1800):
    """Render background_research.json into a bounded Story-context brief section."""
    if not research:
        return []
    lines = [
        "## Story context (from background_research.json)",
        "",
        "Research is context_only: use it for aliases/names/world terms and weak background only; do NOT reveal future plot or upgrade research-only relationships/causes into current on-screen facts without visual/ASR support.",
        "",
    ]
    for key, label in (
        ("synopsis", "Synopsis"),
        ("worldbuilding", "Worldbuilding"),
        ("episode_context", "Episode context"),
    ):
        value = _clip_text(research.get(key), 500)
        if value:
            lines.append(f"- {label}: {value}")

    characters = research.get("characters", {})
    if characters:
        lines.append("- Characters:")
        for name, desc in list(characters.items())[:12]:
            lines.append(f"    - {_clip_text(name, 60)}: {_clip_text(desc, 160)}")

    details = research.get("character_details", {})
    if details:
        lines.append("- Character details:")
        for name, info in list(details.items())[:8]:
            bits = []
            aliases = [_clip_text(alias, 40) for alias in info.get("aliases", [])[:4]]
            if aliases:
                bits.append("别名 " + "/".join(aliases))
            role = _clip_text(info.get("role"), 80)
            if role:
                bits.append(role)
            rels = [_clip_text(rel, 80) for rel in info.get("relationships", [])[:4]]
            if rels:
                bits.append("；".join(rels))
            if bits:
                lines.append(f"    - {_clip_text(name, 60)}: {'; '.join(bits)}")

    arcs = research.get("plot_arcs", [])
    if arcs:
        lines.append("- Plot arcs:")
        for arc in arcs[:8]:
            name = _clip_text(arc.get("name"), 80)
            desc = _clip_text(arc.get("description"), 180)
            status = _clip_text(arc.get("status"), 40)
            if name or desc:
                tail = f" [{status}]" if status else ""
                lines.append(f"    - {name}: {desc}{tail}".rstrip())

    notes = research.get("cultural_notes", [])
    if notes:
        lines.append("- Cultural notes:")
        for note in notes[:6]:
            item = _clip_text(note.get("item"), 80)
            expl = _clip_text(note.get("explanation"), 160)
            if item and expl:
                lines.append(f"    - {item}: {expl}")
            elif item or expl:
                lines.append(f"    - {item or expl}")

    lines.extend(
        [
            "",
            'Use these names, relationships, and stakes in the narration instead of generic labels like "男子"/"白发女子".',
            "",
        ]
    )
    text = "\n".join(lines)
    if len(text) <= limit:
        return lines
    clipped = text[:limit].rsplit("\n", 1)[0].rstrip()
    return [
        *clipped.splitlines(),
        "",
        "[Story context clipped to keep ASR/visual evidence in context]",
        "",
    ]


def assess_understanding_substrate(
    scenes_analysis, asr_result, *, has_story_context=False
):
    """Measure how much real signal the writing agent has to work with.

    Recap quality collapses to generic 看图说话 when the agent has no story spine and
    only literal frame descriptions to paraphrase. A spine is substantial dialogue
    (ASR) OR researched/given story context — frame-fact VOLUME alone (a visually busy
    but storyless clip, e.g. an anime whose dialogue ASR could not read) is NOT a spine,
    so it must not grade as "rich"; otherwise the sparse-substrate warning, the research
    directive, and the density relief never fire for exactly that cold-narration case.
    """
    asr_chars = sum(len(seg["text"]) for seg in asr_result)
    scenes_with_facts = sum(1 for s in scenes_analysis if s.get("frame_facts"))
    desc_lens = [len(s["description"]) for s in scenes_analysis]
    avg_desc = sum(desc_lens) // len(desc_lens) if desc_lens else 0

    has_asr = asr_chars >= 20
    has_facts = scenes_with_facts > 0
    has_story_spine = asr_chars >= 200 or bool(has_story_context)
    if not has_asr and not has_facts and avg_desc < 25:
        level = "empty"
    elif (has_asr or has_facts) and has_story_spine:
        level = "rich"
    else:
        level = "thin"
    return {
        "level": level,
        "asr_chars": asr_chars,
        "scene_count": len(scenes_analysis),
        "scenes_with_frame_facts": scenes_with_facts,
        "avg_description_len": avg_desc,
        "has_story_context": bool(has_story_context),
    }


def _format_substrate_warning(assessment):
    """Render a loud brief banner when the understanding substrate is weak."""
    if assessment["level"] == "rich":
        return []
    if assessment["level"] == "empty":
        head = "⚠️ UNDERSTANDING SUBSTRATE IS EMPTY — narration will be generic guesswork unless you fix this first."
    else:
        head = '⚠️ Understanding substrate is THIN — narration risks generic "看图说话" without more grounding.'
    return [
        head,
        f"  ASR chars: {assessment['asr_chars']} | scenes: {assessment['scene_count']} | "
        f"scenes with frame_facts: {assessment['scenes_with_frame_facts']} | avg description: {assessment['avg_description_len']} chars",
        "  Before writing: do background research (write background_research.json with names/relationships/plot),",
        "  lean on any --context provided, and use ASR dialogue + frame_facts as the factual spine.",
        "  Do NOT invent plot. If you truly have nothing, keep beats sparse and factual rather than fabricating drama.",
        "",
    ]


def _write_json_artifact(work_dir, name, payload):
    path = Path(work_dir) / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _format_asr_chunks_for_brief(chunks, max_chunks=24):
    if not chunks:
        return []
    lines = [
        "## ASR writing chunks (semantic windows)",
        "",
        "Use these chunks as the dialogue spine instead of swallowing the full transcript at once.",
        "Full data: `asr_writing_chunks.json`.",
        "",
    ]
    for chunk in chunks[:max_chunks]:
        scene_ids = ",".join(str(sid) for sid in chunk["scene_ids"]) or "n/a"
        text = chunk["text"]
        if len(text) > 900:
            text = text[:897] + "..."
        lines.extend(
            [
                f"### ASR chunk {chunk['chunk_id'] + 1}: {chunk['start']:.1f}-{chunk['end']:.1f}s | scenes {scene_ids} | {chunk['char_count']} units",
                text or "(empty transcript chunk)",
                "",
            ]
        )
    if len(chunks) > max_chunks:
        lines.append(
            f"... {len(chunks) - max_chunks} more chunks in `asr_writing_chunks.json`."
        )
        lines.append("")
    return lines


def _format_timeline_fusion_for_brief(fusion, max_items=40):
    if not fusion:
        return []
    lines = [
        "## Timeline fusion (VLM + ASR + quiet slots)",
        "",
        "This is the pre-aligned multimodal view. Use `narration_slots` when available; otherwise write ducked-bed beats around dialogue.",
        "Full data: `timeline_fusion.json`.",
        "",
    ]
    for item in fusion[:max_items]:
        start, end = item["time_range"]
        slot_text = (
            ", ".join(
                f"{slot['start']:.1f}-{slot['end']:.1f}s/{slot['char_budget']}字"
                for slot in item["narration_slots"][:4]
            )
            or "none"
        )
        dialogue_text = (
            "; ".join(
                f"{seg['start']:.1f}-{seg['end']:.1f}s {seg['text'][:80]}"
                for seg in item["dialogue_segments"][:3]
                if seg["text"]
            )
            or "none"
        )
        lines.extend(
            [
                f"### Fusion scene {item['scene_id']}: {start:.1f}-{end:.1f}s ({item['recommended_mode']})",
                f"- Visual: {item['visual_description']}",
                f"- Dialogue overlap: {item['dialogue_overlap_seconds']:.1f}s | {dialogue_text}",
                f"- Narration slots: {slot_text}",
                "",
            ]
        )
    if len(fusion) > max_items:
        lines.append(
            f"... {len(fusion) - max_items} more fused scenes in `timeline_fusion.json`."
        )
        lines.append("")
    return lines


def _consolidation_model():
    return CONFIG["vlm_model"]


def _load_consolidation(work_dir, scenes_analysis):
    """Load consolidate.py's understanding_index.json only when its provenance matches
    the current vlm_analysis.json and model ({} otherwise)."""
    work_dir = Path(work_dir)
    path = work_dir / "understanding_index.json"
    meta_path = work_dir / "understanding_index.json.meta.json"
    if not path.exists() or not meta_path.exists():
        return {}
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        source = file_identity(work_dir / "vlm_analysis.json")
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(meta, dict):
        return {}
    expected = {
        "source": source,
        "model": _consolidation_model(),
    }
    if scenes_analysis:
        expected["scene_count"] = len(scenes_analysis)
    if not meta.items() >= expected.items():
        return {}
    try:
        index = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(index, dict) or not all(
        isinstance(index.get(key), list)
        for key in ("characters", "relationships", "plot_points", "entities")
    ):
        return {}
    if not all(isinstance(item, dict) for item in index["characters"]):
        return {}
    if not all(isinstance(item, dict) for item in index["relationships"]):
        return {}
    return index


def _format_consolidation(index):
    """Render consolidate.py's understanding_index.json into a compact brief section.

    plot_points/entities items are model output and may be bare strings or objects."""
    if not index:
        return []
    lines = ["## Understanding index (from consolidate.py)", ""]
    chars = index["characters"]
    if chars:
        lines.append("- Characters:")
        for c in chars:
            lines.append(
                f"    - {c.get('name', '?')}: {str(c.get('description', '')).strip()}"
            )
    rels = index["relationships"]
    if rels:
        lines.append("- Relationships:")
        for r in rels:
            lines.append(
                f"    - {r.get('a', '?')} — {r.get('relation', '?')} — {r.get('b', '?')}"
            )
    plot = index["plot_points"]
    if plot:
        lines.append("- Plot spine:")
        lines.extend(
            f"    {i + 1}. {p.get('text', p) if isinstance(p, dict) else p}"
            for i, p in enumerate(plot)
        )
    ents = index["entities"]
    if ents:
        ent_names = [str(e.get("name", e) if isinstance(e, dict) else e) for e in ents]
        lines.append(f"- Entities: {', '.join(ent_names)}")
    lines.append("")
    return lines
