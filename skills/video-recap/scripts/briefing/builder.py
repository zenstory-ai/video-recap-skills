"""Build the agent narration brief from validated local evidence."""

import json
from pathlib import Path

from lib import CONFIG, load_background_research, log

from agent_text import _format_frame_facts
from briefing.context import (
    _format_background_research,
    _format_moderation_refusals,
    _scene_number,
)
from briefing.timeline import (
    _anchor_boundary_use,
    _format_output_clip_list,
    _format_sentence_entry_anchors_for_brief,
    _load_cut_output_spans_for_brief,
    _parse_target_seconds,
    _remap_brief_evidence_to_output_timeline,
    write_multi_source_output_speech_evidence,
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
    """'45s' / '2min' / '1m42s' / '2h' / '2h01m' / '2h01m05s', rounded to whole seconds."""
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


def _missing_research_callout(research, context_info=""):
    if research or str(context_info or "").strip():
        return []
    return [
        "## ⚠️ Missing Background Research (Action Required for Narrative Depth)",
        "",
        "`background_research.json` was not found and `--context` is empty. Without character names, "
        "relationships, worldbuilding, and episode stakes, narration easily degrades into shallow "
        "surface description ('看图说话'). Before locking `recap_story_plan.json` or `narration.json`, "
        "verify who the characters are, what history/conflict binds them, and write `background_research.json` "
        "(then rerun `understand.py --brief-only` if needed).",
        "",
    ]


def _floor_gates_and_emotion_section():
    return [
        "",
        "## 底线质量与审美硬门禁（CLI 自动拦截）",
        "",
        f"- **开场 3 秒抓人（`weak_cold_open_silence`）**：成片前 {CONFIG['max_cold_open_silence_seconds']:.1f}s 内必须进入首句悬念旁白或高张力原声对白，禁止慢热空镜冷场。",
        f"- **零死气空窗（`excessive_dead_air`）**：全片任何连续 > {CONFIG['max_dead_air_seconds']:.1f}s 既无旁白也无原声对白的空窗都会被硬阻断。",
        "- **旁白与原声双向句末闭环（`incomplete_sentence` / `interrupts_source_sentence` / `unsafe_source_release`）**：旁白必须以句末标点（`！？。…`）完整收句；切入与退出原声对白都必须对齐句末停顿锚点，绝不半句掐断或半句放出。",
        f"- **零残镜头闪帧与自然落点（`flash_frame_sliver` / `shot_speech_conflict`）**：单段 clip 最短 ≥ {CONFIG['min_clip_duration']:.1f}s，成片含 ≤ {CONFIG['flash_sliver_max_seconds']:.2f}s 残帧会被 `shot_review` 硬阻断；句末自动留出呼吸落点与片尾音频淡出，严禁最后 1 秒突兀硬切。",
        "- **禁看图说话、笼统代称与议论升华腔（`generic_character_label` / `editorial_commentary_tone`）**：旁白只讲背景、人物关系、动机筹码、潜台词与因果行动；60s 内短片最多点名 1–2 个核心角色（次要角色用关系/身份称呼）；严禁写“这意味着/这是一场关于……的较量/让权力退场”等评论总结腔，结局由画面与原声演完即可，绝不在结局兑现后再补总结升华旁白。",
        "",
        "## 原声留白字幕 `original_subtitles.json`（校对原声台词）",
        "",
        '你在解说块之间留出的原声留白，可通过 `original_subtitles.json` 把留白里真正听得到的原声台词烧成字幕（与解说字幕用 `「」` 区分）：按 OUTPUT 时间轴写成 `[{"start": 秒, "end": 秒, "text": "台词"}]`。',
        "- 只写留白里【实际出声且完整】的原声台词；被解说压低盖过、或已被剪掉的句子不要写。",
        "- 订正 ASR 错别字与人名，每条短到一行（≤ 约 20 字）；省略该文件时留白区间不自动猜测粗粒度 ASR 字幕。",
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


def build_agent_brief(
    scenes_analysis,
    asr_result,
    silence_periods,
    video_duration,
    work_dir,
    style="纪录片",
    *,
    asr_evidence=None,
):
    """Write a compact, depth-first brief that tells the agent how to author recap artifacts."""
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
    cut_pass2 = edit_mode == "cut" and (Path(work_dir) / "edited_source.mp4").exists()
    if cut_pass2:
        spans = _load_cut_output_spans_for_brief(work_dir)
        output_seconds = max(span["output_end"] for span in spans)
    elif edit_mode == "cut":
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
        asr_evidence = {"status": "MISSING_OR_STALE"}

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
            "First determine the creative-control mode from the current user request and existing artifacts: CREATE explores a new cut; DIRECTED implements user-specified structure/shots/lines; REVISION changes an existing viewed version while freezing everything not named in the latest feedback. This is separate from full/cut.",
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

    lines.extend(_format_moderation_refusals(scenes_analysis))
    research = load_background_research(work_dir)
    lines.extend(_missing_research_callout(research, CONFIG["context_info"]))
    lines.extend(_format_background_research(research))

    brief_scenes, brief_asr, brief_silence = (
        scenes_analysis,
        asr_result,
        silence_periods,
    )
    if cut_pass2:
        brief_scenes, brief_asr, brief_silence = (
            _remap_brief_evidence_to_output_timeline(
                work_dir, scenes_analysis, asr_result, silence_periods
            )
        )
    lines.extend(_format_sentence_entry_anchors_for_brief(work_dir, edit_mode))

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
                    "- Open strong in the first 3 seconds: lead with a high-tension moment, confrontation, or decisive anomaly that raises an immediate dramatic question.",
                    "- Build ONE complete causal arc: hook → setup → escalating turns → payoff/landing — every jump must preserve cause-and-effect so viewers never feel a logical skip.",
                    "- Keep clips that carry causality, a reveal, a decision, or a strong emotional beat; cut establishing/transition/repeated/static shots.",
                    "- SKIP non-story footage: 片头/片尾 credits, 演职员表, 广告/赞助, 台标/水印 stretches, and any scene the analysis marks rejected/无法描述 (often a watermark).",
                    "- Prefer scenes that have a real visual description in the guide; favor faces, action and dialogue over scenery.",
                    "- Clip order is the story spine, not unordered highlights: you may use 0–1 optional cold-open/high-impact clip first, then return to the coherent main arc (setup → turn → payoff) and escalate to a resonant ending.",
                    "- Use `reason` to preserve the actual edit decision: `beat_id | function | change | POV | preferred moment | 入点 | 出点`, not merely 'important plot'. Function is `cold_open`, `setup`, `turn`, `escalation`, or `payoff`.",
                    "- Clip length follows the moment (minimum 0.8s per clip; never leave <= 0.20s flash slivers). Give closing beats enough reaction/breath room to land cleanly.",
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
                    "have no narration item. Open crisp within the first 0.2-2.5s (unless original dialogue opens the film) and size end-start to the text.",
                    "```json",
                    "[",
                    '  {"start": 0.2, "end": 11.2, "narration": "【主角】表面只是旁观者，暗中却握着关键线索。这一次，TA要赌上全部去查清旧案真相。", "pause_after_ms": 250, "overlaps_speech": true, "emotion": "紧张", "source_entry_policy": "sentence_boundary"}',
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
                '  {"start": 0.2, "end": 11.2, "narration": "【主角】表面只是旁观者，暗中却握着关键线索。这一次，TA要赌上全部去查清旧案真相。", "pause_after_ms": 250, "overlaps_speech": true, "emotion": "平静", "source_entry_policy": "sentence_boundary"}',
                "]",
                "```",
            ]
        )

    lines.extend(_floor_gates_and_emotion_section())
    if cut_pass2:
        lines.extend(
            [
                "## Scene timing guide (OUTPUT time)",
                "",
                "Only footage kept in `edited_source.mp4`, timed on its OUTPUT timeline (0 .. total); "
                "scenes the cut left out are not listed. Read subtext & dialogue first; treat visual ref strictly as cut-point verification, never as copy to paraphrase.",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "## Scene timing guide",
                "",
                "Read subtext & dialogue first; treat visual ref strictly as cut-point verification, never as copy to paraphrase into narration.",
                "",
            ]
        )

    for scene in brief_scenes:
        duration = scene["end"] - scene["start"]
        quiets = _quiet_windows_for_scene(brief_silence, scene)
        quiet_text = ", ".join(f"{s:.1f}-{e:.1f}s" for s, e in quiets) or "none"
        span = f"{scene['start']:.1f}-{scene['end']:.1f}s"
        lines.append(
            f"### OUTPUT {span} (source scene {_scene_number(scene['scene_id'])})"
            if cut_pass2
            else f"### Scene {_scene_number(scene['scene_id'])}: {span}"
        )
        lines.append(f"- Duration: {duration:.1f}s")
        if scene.get("depth_analysis"):
            lines.append(f"- Deeper analysis: {scene['depth_analysis']}")
        asr_lines = _scene_asr_lines(brief_asr, scene)
        if asr_lines:
            lines.append("- ASR overlap:")
            lines.extend(asr_lines[:8])
        lines.append(f"- Quiet windows: {quiet_text}")
        lines.append(
            f"- Visual ref (cut-point check only): {scene.get('description', '')}"
            + (
                " [moderation_refused]"
                if scene.get("analysis_status") == "moderation_refused"
                else ""
            )
        )
        facts = _format_frame_facts(scene)
        if facts:
            lines.append(facts.rstrip())
        lines.append("")

    brief_path = Path(work_dir) / "agent_narration_brief.md"
    brief_path.write_text("\n".join(lines), encoding="utf-8")
    log(f"已写入 Agent 解说写作 brief: {brief_path}")
    return brief_path


def write_multi_source_clip_brief(work_dir, source_records, args):
    """Write the Pass-1 multi-source clip-planning brief using the shared brief context."""
    work_dir = Path(work_dir)
    research = load_background_research(work_dir)
    lines = [
        "# Multi-source Clip Plan Brief",
        "",
        "你正在做多视频剪辑复盘。当前 MVP 只支持 `--edit-mode cut`：先做跨素材的故事与视听决定，再写 `clip_plan.json`；下一步会剪出 `edited_source.mp4`，最后按 OUTPUT 时间轴写 `narration.json`。",
        "",
        "## 创作决定",
        "",
        "先判断创作控制模式：CREATE 比较至少两个可行剪辑假设；DIRECTED 落实用户指定结构；REVISION 只改最新反馈点并冻结未点名内容。然后写或更新 `recap_story_plan.json` 与 `visual_audio_board.json`。前者记录观众承诺、POV、戏剧问题、选定主线及 change-based beats；后者记录每拍的具体画面/反应、入点/出点、原声锚点、`audio_owner` 与 `narration_job`。",
        "",
        "多视频不是把每个来源各做一段小总结。每个来源片段都必须服务同一条主线，并用 `source_id` 保留证据归属。这两份计划是 Agent 的工作记录，不是 CLI 渲染门禁。",
        "",
    ]
    lines.extend(_missing_research_callout(research, getattr(args, "context", "")))
    lines.extend(_format_background_research(research))
    lines.extend(
        [
            "## 必须写入的格式",
            "",
            "```json",
            '{"target_duration":"10m","clips":[{"source_id":"src_xxx","start":12.0,"end":38.0,"reason":"b01 | hook | knowledge: unknown→threat | POV=主角 | 保留倾听反应 | 入点=问题已问出 | 出点=沉默落地"}]}',
            "```",
            "",
            "- 每个 clip 必须带 `source_id`，单段时长 ≥ 0.8s，严禁切出 ≤ 0.20s 的残镜头闪帧。",
            "- 开场第一拍必须在 3 秒内立住核心冲突或异常，收尾最后一拍留足情绪/动作落点。",
            "- `start`/`end` 是对应 source 原视频时间（秒）。",
            "- 已从源证据确认的必保问答、反应或兑现段，写入同一 `clip_plan.json.required_evidence`（nodes登记id/source绝对路径/start/end原片秒/track/content，before登记必要先后）。",
            "- 不同 `source_id` 的相同时间段不算重叠；同一 `source_id` 内不要重复/重叠，除非你明确接受稀疏/重复剪辑风险。",
            "- 选片前使用下方 source work_dir（`sources/<source_id>`）核对 scenes.json、ASR 和逐来源 brief。",
        ]
    )
    if getattr(args, "target_duration", None):
        lines.append(f"- 目标时长：`{args.target_duration}`。")
    lines += ["", "## Sources", ""]
    for s in source_records:
        swd = work_dir / s["source_work_dir"]
        lines += [
            f"### {s['source_id']} — {s['source_name']}",
            f"- path: `{s['source_path']}`",
            f"- work_dir: `{swd}`",
            f"- material_id: `{s.get('material_id')}`",
        ]
    path = work_dir / "agent_narration_brief.md"
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def write_multi_source_output_brief(work_dir, source_records, validated_plan_path):
    """Write the Pass-2 multi-source OUTPUT narration brief using shared context & gates."""
    work_dir = Path(work_dir)
    plan = json.loads(Path(validated_plan_path).read_text(encoding="utf-8"))
    source_by_id = {s["source_id"]: s for s in source_records}
    speech_evidence = write_multi_source_output_speech_evidence(
        work_dir, source_records, plan
    )
    research = load_background_research(work_dir)
    lines = [
        "# Multi-source Output Narration Brief",
        "",
        "现在 `edited_source.mp4` 已经由多个源视频剪好。请对剪后成片的 OUTPUT 时间轴写 `narration.json`。",
        "",
        "## 更新创作决定",
        "",
        "先查看 `edited_source.mp4` 与下方 kept-clip map，在 `visual_audio_board.json` 中补齐 OUTPUT 起止时间，并根据实际成片重新确认每拍的 `audio_owner`、原声锚点与 `narration_job`。如剪后顺序改变了主线或情绪路径，同时更新 `recap_story_plan.json`。",
        "",
        "beat 对应关系保留在视听板中；`narration.json` 仍只承载时间、文本与朗读参数，CLI 不声称校验计划映射。",
        "",
    ]
    lines.extend(_missing_research_callout(research))
    lines.extend(_format_background_research(research))
    lines.extend(
        [
            "## narration.json 格式",
            "",
            "```json",
            '[{"start":0.2,"end":4.2,"narration":"解说文本。","pause_after_ms":250,"overlaps_speech":true,"emotion":"紧张"}]',
            "```",
            "",
            "注意：`start`/`end` 是剪后成片时间，不是原视频时间。",
            "",
            "## 输出时间线写作规则",
            "- `narration.json` 只使用 edited_source.mp4 的 OUTPUT 时间线，不使用任一原片时间。",
            "- 先为每个 beat 指定 `audio_owner` 与 `narration_job`；7:3 不是配额，只是素材无法给出更好判断时的粗略回退。",
            "- 使用 kept-clip map 与每个 source work_dir 核对人物、事实、ASR 和上下文；只讲故事背景、人物筹码与因果潜台词，严禁看图说话。",
        ]
    )
    lines.extend(_floor_gates_and_emotion_section())
    lines.append("## Kept clips (output → source)")
    for c in plan["clips"]:
        src = source_by_id[c["source_id"]]
        reason = f" — {c['reason']}" if c.get("reason") else ""
        lines.append(
            f"- output {c['output_start']:.3f}-{c['output_end']:.3f}s → "
            f"{c['source_id']} `{src['source_path']}` "
            f"source {c['source_start']:.3f}-{c['source_end']:.3f}s{reason}"
        )
    anchors = [
        (row, use)
        for row in speech_evidence["sentence_anchors"]
        if (use := _anchor_boundary_use(row)) != "none"
    ]
    if anchors:
        lines += [
            "",
            "## 原声句末安全切入点",
            "",
            "`unverified` 点是粗粒度 ASR 窗口里的标点位置估计、再吸附到短停顿：不切断单词，"
            "但不保证原声句子已说完（误差上限见 `±`）；门禁照常生效。",
        ]
        for row, use in anchors:
            if use == "verified":
                label = row["confidence"]
            elif "timing_bound_seconds" in row:
                label = f"unverified ±{float(row['timing_bound_seconds']):.1f}s"
            else:
                label = "unverified"
            lines.append(f"- {row['time']:.3f}s [{label}] ({row['source_id']})")
    lines += ["", "## Source work dirs"]
    for s in source_records:
        lines.append(f"- {s['source_id']}: `{work_dir / s['source_work_dir']}`")
    path = work_dir / "agent_narration_brief.md"
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path
