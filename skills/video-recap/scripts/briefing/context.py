"""Load and format background research and moderation context for the brief."""

import re


def _scene_number(scene_id):
    """1-based scene number for every human-facing label in the brief (JSON keeps the
    0-based `scene_id`); a scene split across kept clips ("3.1") reads "4 part 2"."""
    base, _, part = str(scene_id).partition(".")
    return f"{int(base) + 1} part {int(part) + 1}" if part else str(int(base) + 1)


def _clip_text(text, limit):
    """Collapse whitespace and clip. Research fields are optional, so None clips to ""."""
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


def _as_list(value):
    if isinstance(value, list):
        return value
    return [value] if value else []


def normalize_background_research(research):
    """Coerce agent-written background_research.json into the shapes the formatters read.

    Agents write bare strings where the research guide documents objects (plot_arcs,
    cultural_notes) or lists (aliases, relationships); non-object character entries drop.
    """
    characters = research.get("characters")
    details = research.get("character_details")
    return {
        "synopsis": research.get("synopsis"),
        "worldbuilding": research.get("worldbuilding"),
        "episode_context": research.get("episode_context"),
        "characters": characters if isinstance(characters, dict) else {},
        "character_details": {
            name: {
                "aliases": _as_list(info.get("aliases")),
                "role": info.get("role"),
                "relationships": _as_list(info.get("relationships")),
            }
            for name, info in (details.items() if isinstance(details, dict) else ())
            if isinstance(info, dict)
        },
        "plot_arcs": [
            arc if isinstance(arc, dict) else {"description": arc}
            for arc in _as_list(research.get("plot_arcs"))
        ],
        "cultural_notes": [
            note if isinstance(note, dict) else {"item": note}
            for note in _as_list(research.get("cultural_notes"))
        ],
    }


def _format_background_research(research, limit=1800):
    """Render background_research.json into a bounded Story-context brief section."""
    if not research:
        return []
    research = normalize_background_research(research)
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
        value = _clip_text(research[key], 500)
        if value:
            lines.append(f"- {label}: {value}")

    characters = research["characters"]
    if characters:
        lines.append("- Characters:")
        for name, desc in list(characters.items())[:12]:
            lines.append(f"    - {_clip_text(name, 60)}: {_clip_text(desc, 160)}")

    details = research["character_details"]
    if details:
        lines.append("- Character details:")
        for name, info in list(details.items())[:8]:
            bits = []
            aliases = [_clip_text(alias, 40) for alias in info["aliases"][:4]]
            if aliases:
                bits.append("别名 " + "/".join(aliases))
            role = _clip_text(info["role"], 80)
            if role:
                bits.append(role)
            rels = [_clip_text(rel, 80) for rel in info["relationships"][:4]]
            if rels:
                bits.append("；".join(rels))
            if bits:
                lines.append(f"    - {_clip_text(name, 60)}: {'; '.join(bits)}")

    arcs = research["plot_arcs"]
    if arcs:
        lines.append("- Plot arcs:")
        for arc in arcs[:8]:
            name = _clip_text(arc.get("name"), 80)
            desc = _clip_text(arc.get("description"), 180)
            status = _clip_text(arc.get("status"), 40)
            if name or desc:
                text = f"{name}: {desc}" if name and desc else name or desc
                tail = f" [{status}]" if status else ""
                lines.append(f"    - {text}{tail}")

    notes = research["cultural_notes"]
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


def _format_moderation_refusals(scenes_analysis):
    """One brief line naming the scenes MiMo moderation refused, so blank descriptions read as
    refusals (vlm.analyze_scenes sets analysis_status), not as empty footage."""
    refused = [
        scene for scene in scenes_analysis or []
        if isinstance(scene, dict) and scene.get("analysis_status") == "moderation_refused"
    ]
    if not refused:
        return []
    labels = ", ".join(_scene_number(scene["scene_id"]) for scene in refused[:12])
    more = f" …(+{len(refused) - 12})" if len(refused) > 12 else ""
    return [
        f"- Moderation-refused scenes: {len(refused)}/{len(scenes_analysis)} "
        f"(Scene {labels}{more}) — MiMo refused the frame VLM request, so their frame "
        f"description is blank and they carry no frame_facts. This is a "
        "content-moderation refusal, not empty footage: ground them in ASR, burned-in "
        "subtitles, or background research, or skip them in cut mode.",
        "",
    ]
