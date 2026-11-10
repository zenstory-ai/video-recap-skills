"""Build the agent narration brief from validated local evidence."""

from pathlib import Path

from lib import CONFIG, load_background_research, log

from agent_text import _format_frame_facts, _recommended_char_budget
from briefing.context import (
    _format_background_research,
    _format_moderation_refusals,
    _scene_number,
)
from briefing.inputs import (
    _format_optional_stage_warnings,
    _load_mimo_overview_for_brief,
)
from briefing.timeline import (
    _format_output_clip_list,
    _format_sentence_entry_anchors_for_brief,
    _load_cut_output_spans_for_brief,
    _parse_target_seconds,
    _remap_brief_evidence_to_output_timeline,
)


def _quiet_windows(silence_periods):
    min_quiet = CONFIG["quiet_window_min"]
    return [
        (s["start"], s["end"])
        for s in silence_periods
        if not s["has_speech"] and s["duration"] >= min_quiet
    ]


def _scene_asr_lines(asr_result, scene):
    return [
        f"  [{seg['start']:.1f}-{seg['end']:.1f}] {seg['text']}"
        for seg in asr_result
        if seg["end"] > scene["start"] and seg["start"] < scene["end"] and seg["text"]
    ]


def _quiet_windows_for_scene(silence_periods, scene):
    return [
        (max(qs, scene["start"]), min(qe, scene["end"]))
        for qs, qe in _quiet_windows(silence_periods)
        if qe > scene["start"]
        and qs < scene["end"]
        and min(qe, scene["end"]) - max(qs, scene["start"]) >= 0.8
    ]


def _duration_label(seconds):
    """'45s' / '2min' / '1m42s' / '2h' / '2h01m' / '2h01m05s', rounded to whole seconds;
    a unit is dropped only when it and everything below it are zero.

    A minute-rounded label hid the real length: a 90 s target read '~2min'."""
    whole = int(seconds + 0.5)
    if whole < 60:
        return f"{whole}s"
    minutes, secs = divmod(whole, 60)
    if minutes < 60:
        return f"{minutes}min" if secs == 0 else f"{minutes}m{secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    if minutes == 0 and secs == 0:
        return f"{hours}h"
    return f"{hours}h{minutes:02d}m" if secs == 0 else f"{hours}h{minutes:02d}m{secs:02d}s"


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
    """Write a compact brief that tells the agent how to author recap artifacts."""
    effective_rate = (
        CONFIG["speech_rate"]
        * CONFIG["speech_safety_margin"]
        * CONFIG["narration_speed"]
    )
    breath_sec = CONFIG["breath_ms"] / 1000
    target_pause_ms = CONFIG["breath_ms"]
    edit_mode = CONFIG["edit_mode"]
    target_duration = CONFIG["target_duration"] or "(not set)"
    output_seconds = video_duration
    if edit_mode == "cut":
        spans = _load_cut_output_spans_for_brief(work_dir, required=False)
        if spans:
            output_seconds = max(span["output_end"] for span in spans)
        else:
            target_seconds = _parse_target_seconds(CONFIG["target_duration"])
            if target_seconds:
                output_seconds = min(video_duration, target_seconds)
    output_label = _duration_label(output_seconds)
    source_label = _duration_label(video_duration)

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
    if asr_evidence is None:
        asr_evidence = {"status": "MISSING_OR_STALE", "glossary_modifications": None}

    lines.extend(
        [
            "",
            "## ASR timing evidence",
            "",
            f"- Status: {asr_evidence['status']}",
            "- Empty text: UNKNOWN_NOT_PROVEN_SILENCE; these windows locate a search region, not subtitle onsets.",
            "- Timing: coarse provider windows; word alignment: NOT_PERFORMED; dialogue boundaries: NOT_VERIFIED.",
            "- ASR window ends and quiet-window recommendations are advisory only. "
            "They must not be treated as verified dialogue boundaries or safe cut points; use direct listening "
            "and picture review before preserving or cutting original dialogue.",
            "",
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
            "",
        ]
    )

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
        )
    )
    lines.extend(_format_moderation_refusals(scenes_analysis))
    lines.extend(_format_background_research(load_background_research(work_dir)))

    cut_pass2 = edit_mode == "cut" and (Path(work_dir) / "edited_source.mp4").exists()
    if cut_pass2:
        guide_scenes, guide_asr, guide_silence = (
            _remap_brief_evidence_to_output_timeline(
                work_dir, scenes_analysis, asr_result, silence_periods, required=True
            )
        )
    else:
        guide_scenes, guide_asr, guide_silence = (
            scenes_analysis,
            asr_result,
            silence_periods,
        )
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
        if not cut_pass2:
            lines.extend(
                [
                    "## Cut mode — step 1 of 2: direct the story, then write `clip_plan.json` ONLY",
                    "",
                    f"Goal: a ~{output_label} recap cut from a {source_label} source (sized to the ~{output_label} CUT OUTPUT, NOT the {source_label} source). In CREATE compare two editorial hypotheses;",
                    "in DIRECTED/REVISION inherit the requested or accepted spine. Then confirm the viewer promise/POV/dramatic question and write or update `recap_story_plan.json` + `visual_audio_board.json`.",
                    "Then choose the footage; the CLI",
                    "then renders the cut and asks you to narrate against that real output. Do NOT write narration.json yet.",
                    "How to choose clips (use the Scene timing guide + ASR below):",
                    "- Build ONE complete arc: a hook, the key turns of the plot, and a cliffhanger/payoff at the end — not a flat highlights reel.",
                    "- Keep clips that carry causality, a reveal, a decision, or a strong emotional beat; cut establishing/transition/repeated/static shots.",
                    "- SKIP non-story footage: 片头/片尾 credits, 演职员表, 广告/赞助, 台标/水印 stretches, and any scene the analysis marks rejected/无法描述 (often a watermark) — they look bad on screen and add nothing.",
                    "- Prefer scenes that have a real visual description in the guide; favor faces, action and dialogue over scenery.",
                    "- Clip order is the story spine, not unordered highlights: you may use 0–1 optional cold-open/high-impact clip first, then return to the coherent main arc (setup → turn → payoff) and escalate to the ending.",
                    "- Use `reason` to preserve the actual edit decision: `beat_id | function | change | POV | preferred moment | 入点 | 出点`, not merely 'important plot'. Function is `cold_open`, `setup`, `turn`, `escalation`, or `payoff`.",
                    "- Clip length follows the moment. Vary pace; after any cold-open, order clips by causality so the cut reads as one coherent story, not a flat highlights reel.",
                    "- Inspect dense scene-change candidates before locking boundaries. For source-authored cuts, delete irrelevant short shots and extend relevant shots to a complete action/reaction; for edit-created joins, move boundaries, restore same-source motion, or merge clips so the artificial cut disappears where possible. Do not hide a bad join with a transition.",
                    "- Preserve complete spoken lines, but do not infer completeness from ASR window ends or quiet-window suggestions. Directly listen and inspect picture around each proposed boundary, then place the cut after the verified utterance/reaction; automatic snapping is only an advisory candidate.",
                    "- Advisory does not mean absent: the cut CLI still snaps clip ends to sentence boundaries by default and blocks a cut inside ASR-detected speech unless it lands in a quiet window or a sentence-end pause estimate, using `silence_periods.json` and `speech_boundary_anchors.json` as the executable safety net under your listening.",
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
            lines.extend(_format_output_clip_list(work_dir))
            lines.extend(
                [
                    "## Cut mode — step 2 of 2: write `narration.json` in OUTPUT time",
                    "",
                    "Inspect the edited storyboard first. Update `visual_audio_board.json` with OUTPUT ranges and re-assign `audio_owner` / `narration_job` based on the cut that actually exists.",
                    f"The cut is rendered as `edited_source.mp4` (~{output_label}) across the ~{output_label} CUT OUTPUT. Write narration timed to THAT output",
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
        ]
    )
    if cut_pass2:
        lines.extend(
            [
                "## Scene timing guide (OUTPUT time)",
                "",
                "Only footage kept in `edited_source.mp4`, timed on its OUTPUT timeline (0 .. total); "
                "scenes the cut left out are not listed.",
                "",
            ]
        )
    else:
        lines.extend(["## Scene timing guide", ""])

    for scene in guide_scenes:
        duration = scene["end"] - scene["start"]
        max_chars = max(5, _recommended_char_budget(scene["start"], scene["end"]))
        quiets = _quiet_windows_for_scene(guide_silence, scene)
        quiet_text = ", ".join(f"{s:.1f}-{e:.1f}s" for s, e in quiets) or "none"
        span = f"{scene['start']:.1f}-{scene['end']:.1f}s"
        lines.extend(
            [
                f"### OUTPUT {span} (source scene {_scene_number(scene['scene_id'])})"
                if cut_pass2
                else f"### Scene {_scene_number(scene['scene_id'])}: {span}",
                f"- Duration: {duration:.1f}s; max budget if fully narrated: {max_chars} chars",
                f"- Quiet windows: {quiet_text}",
                f"- Description: {scene.get('description', '')}"
                + (
                    " [moderation_refused]"
                    if scene.get("analysis_status") == "moderation_refused"
                    and scene.get("description_source") != "mimo_video_overview"
                    else ""
                ),
            ]
        )
        if scene.get("depth_analysis"):
            lines.append(f"- Deeper analysis: {scene['depth_analysis']}")
        facts = _format_frame_facts(scene)
        if facts:
            lines.append(facts.rstrip())
        asr_lines = _scene_asr_lines(guide_asr, scene)
        if asr_lines:
            lines.append("- ASR overlap:")
            lines.extend(asr_lines[:8])
        lines.append("")

    brief_path = Path(work_dir) / "agent_narration_brief.md"
    brief_path.write_text("\n".join(lines), encoding="utf-8")
    log(f"已写入 Agent 解说写作 brief: {brief_path}")
    return brief_path
