"""Build the agent narration brief from validated local evidence."""

from pathlib import Path

from lib import CONFIG, log

from agent_text import _chunk_asr_for_writing, _format_frame_facts
from brief_context import (
    _format_asr_chunks_for_brief,
    _format_background_research,
    _format_consolidation,
    _format_substrate_warning,
    _format_timeline_fusion_for_brief,
    _load_background_research,
    _load_consolidation,
    _write_json_artifact,
    assess_understanding_substrate,
)
from brief_inputs import (
    _format_optional_stage_warnings,
    _load_clean_asr,
    _load_mimo_overview_for_brief,
)
from brief_timeline import (
    _format_output_clip_list,
    _format_research_directive,
    _format_sentence_entry_anchors_for_brief,
    _load_cut_output_spans_for_brief,
    _parse_target_seconds,
    _remap_brief_evidence_to_output_timeline,
    _write_deslop_qc_requirements,
)
from timeline_fusion import (
    _build_timeline_fusion,
    _quiet_windows_for_scene,
    _scene_asr_lines,
)

def build_agent_brief(
    scenes_analysis,
    asr_result,
    silence_periods,
    video_duration,
    work_dir,
    style="纪录片",
    *,
    mimo_overview_enabled=None,
    mimo_overview_video_path=None,
    asr_evidence=None,
):
    """Write a compact brief that tells the agent exactly how to author recap artifacts."""
    # account for the global narration atempo (CONFIG['narration_speed']) so a beat's text
    # is budgeted against the FINAL sped-up audio, not the raw TTS rate — otherwise windows
    # are over-sized and the bed shows long silent gaps between sentences.
    effective_rate = (
        CONFIG["speech_rate"]
        * CONFIG["speech_safety_margin"]
        * CONFIG["narration_speed"]
    )
    breath_sec = CONFIG["breath_ms"] / 1000
    target_pause_ms = CONFIG["breath_ms"]
    edit_mode = CONFIG["edit_mode"]
    target_duration = CONFIG["target_duration"] or "(not set)"
    # Cut mode sizes narration to the OUTPUT (the kept clips), not the full source.
    # In pass 2, prefer the actual validated edited_source duration; target_duration is
    # only a planning goal and can differ after clip snapping or under/over-selection.
    output_seconds = video_duration
    if edit_mode == "cut":
        spans = _load_cut_output_spans_for_brief(work_dir, required=False)
        if spans:
            output_seconds = max(span["output_end"] for span in spans)
        else:
            target_seconds = _parse_target_seconds(CONFIG["target_duration"])
            if target_seconds:
                output_seconds = min(video_duration, target_seconds)
    # A loose first-draft timing fallback, not a creative quota. The Agent's beat map and audio-owner
    # decisions determine the real count; this only prevents accidental per-sentence fragmentation.
    cov_target = CONFIG["narration_coverage_target"]
    block_seconds = CONFIG["narration_block_seconds"]
    target_count = max(1, round(output_seconds * cov_target / block_seconds))

    has_story_context = (Path(work_dir) / "background_research.json").exists()
    substrate = assess_understanding_substrate(
        scenes_analysis, asr_result, has_story_context=has_story_context
    )
    thin_substrate = substrate["level"] in ("thin", "empty")
    beat_count_phrase = (
        f"at most ~{target_count}" if thin_substrate else f"roughly {target_count}"
    )
    output_label = (
        f"{output_seconds / 60:.0f}min"
        if output_seconds >= 60
        else f"{output_seconds:.0f}s"
    )
    source_label = (
        f"{video_duration / 60:.0f}min"
        if video_duration >= 60
        else f"{video_duration:.0f}s"
    )

    lines = [
        "# Agent Narration Brief",
        "",
        "Write the required JSON artifact(s) manually from the analysis files in this work directory.",
        "The CLI will not generate final narration text; it will only validate timing, run TTS, and assemble the video.",
        "",
        f"- Style (--style, freeform verbatim guidance): {style}",
        "- Do not translate `--style` into a preset, enum, switch, or fallback ladder; synthesize the voice from this freeform text plus evidence.",
        f"- Edit mode: {edit_mode}",
        f"- Source video duration: {video_duration:.1f}s",
        f"- Target duration (cut mode): {target_duration}",
        f"- Effective speech budget: {effective_rate:.2f} Chinese chars/sec after {breath_sec:.2f}s pause allowance",
    ]
    # Understanding validates evidence before calling; the standalone script skill
    # has no producer dependency and must not invent an available ASR status.
    if asr_evidence is None:
        asr_evidence = {"status": "MISSING_OR_STALE", "glossary_modifications": None}

    lines.extend(
        [
            "",
            "## ASR timing evidence",
            "",
            f"- Status: {asr_evidence['status']}",
            f"- Glossary modifications: {asr_evidence.get('glossary_modifications') or '(unverified)'}; details: asr_timing_evidence.json",
            "- Empty text: UNKNOWN_NOT_PROVEN_SILENCE; these windows locate a search region, not subtitle onsets.",
            "- Timing: coarse provider windows; word alignment: NOT_PERFORMED; dialogue boundaries: NOT_VERIFIED.",
            "- ASR window ends and timeline-fusion/quiet-window recommendations are advisory only. "
            "They must not be treated as verified dialogue boundaries or safe cut points; use direct listening "
            "and picture review before preserving or cutting original dialogue.",
            "",
        ]
    )
    if thin_substrate:
        lines.append(
            f"- Narration density: substrate is {substrate['level']} — do NOT chase a beat count. "
            f"Write fewer, grounded blocks; skipping a stretch beats narrating pixels."
        )
    else:
        lines.append(
            "- Content-led audio allocation. Assign every beat to picture/original dialogue/action sound/"
            "ambience/music/silence/narration BEFORE writing prose. When narration owns a beat, write it as "
            "one fluent BLOCK. A 7:3 narration/original split is only a rough fallback when the material gives "
            "no clearer answer; it is not a quota or quality target."
        )
        lines.append(
            "- Narration must have a job: context, causal_link, foreshadow, interpretation, or transition. "
            "Use none when picture, original audio, or silence already carries the beat. Avoid per-sentence "
            "stutter and wall-to-wall talk; size each authored block to its text and dramatic task."
        )
    if edit_mode == "cut":
        lines.append(
            f"- Timing fallback only: {beat_count_phrase} narration BLOCKS across the ~{output_label} CUT OUTPUT "
            f"(sized to the kept clips, NOT the {source_label} source). The beat map may justify fewer or more; never pad to hit this number."
        )
    else:
        lines.append(
            f"- Timing fallback only: {beat_count_phrase} narration BLOCKS across the timeline. "
            "The beat map and audio owners decide the real count; never pad to hit this number."
        )
    lines.extend(
        [
            f"- Default pause between beats: {target_pause_ms}ms",
            f"- Context: {CONFIG['context_info'] or '(none)'}",
            "",
            "## Creative decisions before expression / packaging",
            "",
            "First determine the creative-control mode from the current user request and existing artifacts: CREATE explores a new cut; DIRECTED implements user-specified structure/shots/lines; REVISION changes an existing viewed version while freezing everything not named in the latest feedback. This is separate from full/cut/dub.",
            "- CREATE compares at least two real editorial hypotheses. DIRECTED/REVISION inherit the user-specified or accepted spine and must not invent alternatives to satisfy a template.",
            "- In REVISION, state the current change list and frozen list first. Update `style_card.json` for expression/subtitle feedback and `visual_audio_board.json` for picture/timing/audio feedback; update `recap_story_plan.json` only when the promise, POV, spine, or story beats change. Remove stale descriptions when content is deleted.",
            "Before `clip_plan.json` or final narration, author or update `recap_story_plan.json` and `visual_audio_board.json` from the applicable director intent, beat changes, and picture/audio decisions described below.",
            "- `recap_story_plan.json` owns director intent, the chosen POV/spine, beats defined by changes in knowledge/power/goal/relationship/emotion/risk, and competing hypotheses only when exploration is appropriate.",
            "- `visual_audio_board.json` owns the exact picture/performance/reaction, entry/exit reason, original-audio anchor, `audio_owner`, and `narration_job` for each beat.",
            "- Only after the story/edit decisions are coherent, author `style_card.json` from `--style`, evidence, and user preference. It owns voice and pacing, not story structure, and is not a preset enum, fixed taxonomy, title plan, or packaging promise.",
            "- `packaging_plan.json` is optional and deferred until content lock unless the user explicitly asks for packaging. It must express the story's truthful promise, never drive or distort it.",
            "- `deslop_qc.json` is deterministic report-only tool QC: do not hand-author it, do not treat it as an AIGC detector, and do not auto-rewrite from it. Corrections remain human/agent rewrite work guided by objective blockers and advisory readability signals.",
            "",
        ]
    )

    consolidation_index = _load_consolidation(work_dir, scenes_analysis)
    mimo_overview = _load_mimo_overview_for_brief(
        work_dir,
        scenes_analysis,
        enabled=mimo_overview_enabled,
        video_path=mimo_overview_video_path,
    )
    lines.extend(
        _format_optional_stage_warnings(
            work_dir,
            mimo_overview_enabled=mimo_overview_enabled,
            mimo_overview=mimo_overview,
            consolidation_index=consolidation_index,
        )
    )
    lines.extend(_format_substrate_warning(substrate))
    lines.extend(_format_research_directive(work_dir, substrate))
    lines.extend(_format_background_research(_load_background_research(work_dir)))
    lines.extend(_format_consolidation(consolidation_index))

    asr_for_chunks = _load_clean_asr(work_dir, asr_result) or asr_result
    chunk_scenes, chunk_asr = scenes_analysis, asr_for_chunks
    fusion_scenes, fusion_asr, fusion_silence = (
        scenes_analysis,
        asr_result,
        silence_periods,
    )
    if edit_mode == "cut" and (Path(work_dir) / "edited_source.mp4").exists():
        chunk_scenes, chunk_asr, _ = _remap_brief_evidence_to_output_timeline(
            work_dir, scenes_analysis, asr_for_chunks, [], required=True
        )
        fusion_scenes, fusion_asr, fusion_silence = (
            _remap_brief_evidence_to_output_timeline(
                work_dir, scenes_analysis, asr_result, silence_periods, required=True
            )
        )
    asr_chunks = _chunk_asr_for_writing(chunk_asr, chunk_scenes)
    timeline_fusion = _build_timeline_fusion(fusion_scenes, fusion_asr, fusion_silence)
    _write_json_artifact(work_dir, "asr_writing_chunks.json", asr_chunks)
    _write_json_artifact(work_dir, "timeline_fusion.json", timeline_fusion)
    lines.extend(_format_asr_chunks_for_brief(asr_chunks))
    lines.extend(_format_timeline_fusion_for_brief(timeline_fusion))
    lines.extend(_format_sentence_entry_anchors_for_brief(work_dir, edit_mode))

    overview_text = (mimo_overview or {}).get("content", "").strip()
    if overview_text:
        lines.extend(
            [
                "## MiMo scene-chunk video overview",
                "",
                overview_text[:2000],
                "",
            ]
        )

    if edit_mode == "cut":
        cut_target_example = (
            target_duration if target_duration != "(not set)" else "30m"
        )
        if not (Path(work_dir) / "edited_source.mp4").exists():
            # PASS 1 of 2 (cut-first): pick the footage. Narration comes AFTER the cut is
            # rendered, so it can be written against the real OUTPUT timeline — no source->output
            # mapping, no silent drop/clamp, no desync.
            lines.extend(
                [
                    "## Cut mode — step 1 of 2: direct the story, then write `clip_plan.json` ONLY",
                    "",
                    f"Goal: a ~{output_label} recap cut from a {source_label} source. In CREATE compare two editorial hypotheses;",
                    "in DIRECTED/REVISION inherit the requested or accepted spine. Then confirm the viewer promise/POV/dramatic question and write or update `recap_story_plan.json` + `visual_audio_board.json`.",
                    "Then choose the footage; the CLI",
                    "then renders the cut and asks you to narrate against that real output. Do NOT write narration.json yet.",
                    "How to choose clips (use the Scene timing guide + ASR + index below):",
                    "- Build ONE complete arc: a hook, the key turns of the plot, and a cliffhanger/payoff at the end — not a flat highlights reel.",
                    "- Keep clips that carry causality, a reveal, a decision, or a strong emotional beat; cut establishing/transition/repeated/static shots.",
                    "- SKIP non-story footage: 片头/片尾 credits, 演职员表, 广告/赞助, 台标/水印 stretches, and any scene the analysis marks rejected/无法描述 (often a watermark) — they look bad on screen and add nothing.",
                    "- Prefer scenes that have a real visual description in the guide; favor faces, action and dialogue over scenery.",
                    "- Clip order is the story spine, not unordered highlights: you may use 0–1 optional cold-open/high-impact clip first, then return to the coherent main arc (setup → turn → payoff) and escalate to the ending.",
                    "- Use `reason` to preserve the actual edit decision: `beat_id | function | change | POV | preferred moment | 入点 | 出点`, not merely 'important plot'. Function is `cold_open`, `setup`, `turn`, `escalation`, or `payoff`.",
                    "- Clip length follows the moment. Vary pace; after any cold-open, order clips by causality so the cut reads as one coherent story, not a flat highlights reel.",
                    "- Inspect dense scene-change candidates before locking boundaries. For source-authored cuts, delete irrelevant short shots and extend relevant shots to a complete action/reaction; for edit-created joins, move boundaries, restore same-source motion, or merge clips so the artificial cut disappears where possible. Do not hide a bad join with a transition.",
                    "- Preserve complete spoken lines, but do not infer completeness from ASR window ends or quiet-window suggestions. Directly listen and inspect picture around each proposed boundary, then place the cut after the verified utterance/reaction; automatic snapping is only an advisory candidate.",
                    "- Advisory does not mean absent: the cut CLI still snaps clip ends to sentence boundaries by default and blocks a cut that lands mid-sentence, using `silence_periods.json` and `speech_boundary_anchors.json` as the executable safety net under your listening.",
                    "",
                    "### clip_plan.json shape (original source timestamps)",
                    "",
                    "```json",
                    "{",
                    f'  "target_duration": "{cut_target_example}",',
                    '  "clips": [',
                    '    {"start": 12.0, "end": 38.0, "reason": "b01 | hook | knowledge: unknown→threat | POV=主角 | 保留倾听反应 | 入点=问题已问出 | 出点=沉默落地"}',
                    "  ]",
                    "}",
                    "```",
                ]
            )
        else:
            # PASS 2 of 2: the cut is rendered (edited_source.mp4); narrate in OUTPUT time.
            lines.extend(_format_output_clip_list(work_dir))
            lines.extend(
                [
                    "## Cut mode — step 2 of 2: write `narration.json` in OUTPUT time",
                    "",
                    "Inspect the edited storyboard first. Update `visual_audio_board.json` with OUTPUT ranges and re-assign `audio_owner` / `narration_job` based on the cut that actually exists.",
                    f"The cut is rendered as `edited_source.mp4` (~{output_label}). Write narration timed to THAT output",
                    "timeline (0 .. total), NOT the original source — your timestamps play exactly where you put them, with",
                    "no mapping and no dropping. Use the kept-clip OUTPUT ranges above to know what is on screen when, tell",
                    "one continuous arc across the cut, following the planned beat and audio-owner decisions.",
                    "",
                    "### narration.json shape (OUTPUT timestamps, 0..total)",
                    "",
                    "Each authored narration item is one fluent BLOCK; beats owned by picture, original audio, or silence",
                    "have no narration item. Size end-start to the text and preserve every planned original-audio anchor.",
                    "```json",
                    "[",
                    '  {"start": 2.0, "end": 13.0, "narration": "【主角】表面只是旁观者，暗中却握着关键线索。这一次，TA要赌上全部去查清旧案真相。", "pause_after_ms": 250, "overlaps_speech": true, "emotion": "紧张", "source_entry_policy": "sentence_boundary"}',
                    "]",
                    "```",
                ]
            )
    else:
        lines.extend(
            [
                "## Required JSON shape",
                "",
                "Before narration, write `recap_story_plan.json` and `visual_audio_board.json`, then use the board to decide which planned beats need narration.",
                "beat 对应关系记录在 `visual_audio_board.json`；`narration.json` 仍只承载时间、文本与朗读参数，不声明 CLI 会校验计划映射。",
                "Each authored narration item is one fluent BLOCK; beats owned by picture, original audio, or silence have no narration item.",
                "```json",
                "[",
                '  {"start": 5.0, "end": 16.0, "narration": "【主角】表面只是旁观者，暗中却握着关键线索。这一次，TA要赌上全部去查清旧案真相。", "pause_after_ms": 250, "overlaps_speech": true, "emotion": "平静", "source_entry_policy": "sentence_boundary"}',
                "]",
                "```",
            ]
        )

    lines.extend(
        [
            "",
            "## Writing rules (creative decisions first; blocks are delivery form)",
            "",
            "1. Assign `audio_owner` and `narration_job` before prose. No clear narration job means no narration for that beat.",
            "2. When narration owns a beat, write one fluent BLOCK that completes a continuous thought (often premise -> trigger/action -> change/meaning) for one TTS call. Sentence count is not a target: use one or a few complete connected sentences, and never split TTS merely because captions need shorter cues.",
            "3. 7:3 is a rough fallback, never a coverage quota. A strong dialogue/performance/action/silence beat may contain no narration; an exposition bridge may be narration-led.",
            "4. Default `overlaps_speech` to true only for authored narration windows. If source speech has already started, use a sentence end confirmed by direct listening and picture review; ASR coarse ends are not proof. Do not cover a must-hear original-audio anchor; leave that beat un-narrated or place narration around it.",
            "5. Do not describe what the viewer can already see; narration may add context, causal links, foreshadowing, evidence-grounded interpretation, or transitions.",
            "6. Keep timing visually local: anchor each block to the planned beat and exact footage it covers; don't let prose run past the change it explains.",
            "7. Preserve performance: consider the listener/reaction instead of the speaker/action, and leave enough time for an irreplaceable look, pause, mistake, or action sound to land.",
            "8. Give every block an `emotion` that fits its whole arc; keep it STEADY across the block and shift only at a real emotional turn between blocks.",
            "9. Bridge narration and original audio as one dramatic beat: tee up a must-hear moment before it plays, then let the next block react to the change it caused.",
            "10. 不要在解说文本里使用破折号（——、—）：破折号烧进字幕里很突兀，该停顿就用逗号，该断句就用句号；同理 `original_subtitles.json` 里也不要用破折号。",
            "11. `不是 A，而是 B` is useful only when the material genuinely corrects a plausible prior belief. Do not invent a false contrast for insight; prefer direct action, causality, and consequence.",
            "12. 写完后停止并把控制权交还调用方；本技能只写工作产物，不调用其他技能脚本。",
            "",
            "## 原声留白字幕 `original_subtitles.json`（校对原声台词）",
            "",
            '你在解说块之间留出的原声留白，会把【原声台词】烧成字幕（和解说字幕用 「」 区分开）。请额外写一个 `original_subtitles.json`，把每段留白里真正听得到的原声台词，按 OUTPUT 时间轴写成 `[{"start": 秒, "end": 秒, "text": "台词"}]`：',
            "- 只写留白里【实际出声】的台词；被解说盖过、或已经被剪掉的句子，不要写进来（这正是自动 ASR 兜底会出错的地方）。",
            "- 订正 ASR 的错字和人名（例：叶青眉 → 叶轻眉），删掉口胡和语气词。",
            "- 每条短到一行（≤ 约 20 字），`start`/`end` 对齐它在留白里出声的时间；拿不准就贴着所在留白的区间写。",
            "- 没有清晰原声的留白可以不写；整个文件也可省略——省略时系统会用 ASR 粗略兜底（可能偏多偏乱）。",
            "",
            "## Per-block emotion (`emotion` field → MiMo TTS instruct)",
            "",
            "Each block's `emotion` is a short Chinese tone tag MiMo-v2.5-tts follows for the whole utterance. Pick 1-2 that fit the block:",
            "- 基础情绪: 开心 悲伤 愤怒 恐惧 惊讶 兴奋 委屈 平静 冷漠",
            "- 复合情绪: 怅然 欣慰 无奈 愧疚 释然 嫉妒 厌倦 忐忑 动情",
            "- 整体语调: 温柔 高冷 活泼 严肃 慵懒 俏皮 深沉 干练 凌厉",
            'You may combine, e.g. "紧张 深沉" or "无奈". Default to 平静 only for neutral setup; a recap mostly lives in 紧张/深沉/惊讶/悲伤/动情.',
            "",
            "## Recap craft (what separates a real recap from captions)",
            "",
            "- Hook: the opening must create a truthful dramatic question or stakes that this edit actually pays off; do not manufacture an unrelated retention line.",
            "- Through-line: follow the chosen POV/spine from `recap_story_plan.json`; every beat must change knowledge, power, goal, relationship, emotion, or risk.",
            "- Escalation: raise the stakes or reveal new information as you go; later beats should land harder than earlier ones.",
            '- Curiosity gaps: tease consequences before they happen ("他还不知道，这一步会要命") and pay them off later.',
            "- Payoff: the final 1-2 beats must resolve or twist the spine, leaving an aftertaste — never trail off on a generic line.",
            "- Information, not narration of pixels: every beat should add something the picture alone can't tell (who, why, what's at stake).",
            '- Voice: concrete nouns and verbs, specific names; cut adjectives and vague grandeur ("危机四伏"/"震撼人心" are filler).',
            "- Use the real names, relationships and stakes from the Story context / index above — never generic labels like 男子/白衣女子.",
            "- Show motive and consequence, not actions: say WHY a character does it and what it costs, not what they are doing on screen.",
            "- Performance: when the reaction carries more emotion than the line, let the reaction own the picture and avoid explaining it away.",
            "- 衔接 hand-off: a narration block and the original-audio gap beside it are ONE beat — tee up the original before it plays, then have the next block answer what it showed; never let a block end self-contained and the original come in cold.",
            "- Counterfactual review before TTS: remove each beat, compare speaker vs listener, mute narration, listen audio-only, and replace prose with original audio/silence where that is stronger. Apply the 1-3 highest-return changes first.",
            "",
            "看图说话 (bad) vs recap (good) — same shot:",
            '- ✗ "一个蒙眼的男人抱着一个篮子走在雨里。"  (just describes the frame)',
            '- ✓ "护送者本可以独自离开，却为了保护那个孩子，主动把追兵引向自己。"  (who, why, stakes)',
            "",
            "## Scene timing guide",
            "",
        ]
    )

    for scene in scenes_analysis:
        duration = scene["end"] - scene["start"]
        max_chars = max(5, int(max(1.0, duration - breath_sec) * effective_rate))
        quiets = _quiet_windows_for_scene(silence_periods, scene)
        quiet_text = ", ".join(f"{s:.1f}-{e:.1f}s" for s, e in quiets) or "none"
        lines.extend(
            [
                f"### Scene {scene['scene_id'] + 1}: {scene['start']:.1f}-{scene['end']:.1f}s",
                f"- Duration: {duration:.1f}s; max budget if fully narrated: {max_chars} chars",
                f"- Quiet windows: {quiet_text}",
                f"- Description: {scene.get('description', '')}",
            ]
        )
        if scene.get("depth_analysis"):
            lines.append(f"- Deeper analysis: {scene['depth_analysis']}")
        facts = _format_frame_facts(scene)
        if facts:
            lines.append(facts.rstrip())
        asr_lines = _scene_asr_lines(asr_result, scene)
        if asr_lines:
            lines.append("- ASR overlap:")
            lines.extend(asr_lines[:8])
        lines.append("")

    brief_path = Path(work_dir) / "agent_narration_brief.md"
    brief_path.write_text("\n".join(lines), encoding="utf-8")
    _write_deslop_qc_requirements(work_dir)
    log(f"已写入 Agent 解说写作 brief: {brief_path}")
    return brief_path
