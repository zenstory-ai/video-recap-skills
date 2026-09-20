"""Own recap timeline artifacts, continuation state, and cut QC surfaces."""

import hashlib
import json
import os
import shlex
import sys
from pathlib import Path

from lib import load_json
from recap_runtime import (
    _coerce_videos,
    _entry,
    _load_run_manifest,
    _multi_run_manifest_payload,
    _run_manifest_payload,
)
from recap_source import audio_binding, uses_narration

ASSEMBLY_MANIFEST = "assembly_manifest.json"

PHASE_LEDGER = "recap_phase.json"

CUT_TIMELINE_CRAFT_BULLETS = [
    "- 片段顺序必须服务同一条故事主线，而不是无序高光；可使用 0–1 个 cold open，随后回到 setup → turn → escalation → payoff。",
    "- 每个片段必须对应 `recap_story_plan.json` 中的一个 change-based beat；删除后不损失因果、人物或情绪的片段通常不保留。",
    "- 已从源证据确认的必保问答、反应或兑现段，写入同一 `clip_plan.json.required_evidence`（nodes登记id/source绝对路径/start/end原片秒/track/content，before登记必要先后）；只锁具体区间，不锁整个beat。剪点吸附后工具会核验，reason或花字不能代替缺失的源段。",
    "- `reason` 统一写成 `beat_id | function | change | POV | preferred moment | 入点 | 出点`，不能只写 hook、重要剧情或事件摘要。",
    "- 优先保留因果、揭示、决定、关系移动、情绪转向与不可替代的表演/反应；跳过片尾、广告、重复静态画面和水印废片段。",
    "- 片段追求最短但完整：建立镜头可以短，关键表演/反应允许多停一点；在完整台词、完整动作或自然声音边界结束，避免原声从半句中切入或切出。",
    "- 新人物/地点/情绪场景先给画面建立空间，再进旁白；不要在原片叠化或闪白中间再切一次。scene score 只用于定位候选接点，最终必须播放接点前后判断。",
    "- 对短时间内密集的 scene 候选先区分来源：原片的无关短镜头整段删除，相关短镜头扩展到完整动作/反应；本次拼接制造的切点优先移动边界、恢复同源连续运动或合并片段，能消除就不保留。",
]

MULTI_SOURCE_NARRATION_CRAFT_BULLETS = [
    "- `narration.json` 只使用 edited_source.mp4 的 OUTPUT 时间线，不使用任一原片时间。",
    "- 先为每个 beat 指定 `audio_owner`，再决定是否需要旁白；允许 original_dialogue、action_sound、ambience、music、silence 或 narration 主导。",
    "- 旁白只承担 context、causal_link、foreshadow、interpretation 或 transition；`narration_job=none` 的 beat 不写旁白。",
    "- 7:3 不是配额，只是素材无法给出更好判断时的粗略回退；强对白、动作声、环境或沉默可以完整拥有一个 beat。",
    "- 旁白拥有 beat 时才写成一个连续思路的 BLOCK；句数不是目标，字幕拆 cue 也不能切碎 TTS。不要把每个 beat 都机械变成旁白块或固定原声留白。",
    "- 使用 kept-clip map 与每个 source work_dir 核对人物、事实、ASR 和上下文；跨源转场必须有明确叙事任务。",
]

_ALLOWED_VISUAL_OVERLAY_TYPES = {"top_title", "inline_label_or_callout"}

_VISUAL_OVERLAYS = "visual_overlays.json"


def _canonical_visual_overlay(overlay, segment):
    """Return the first-release assemble overlay contract, or None for unsupported types.

    Recap owns the handoff artifact but does not invent richer overlay semantics: only the
    two overlay types implemented by assemble pass through, an overlay without its own
    start/end inherits the narration segment's, and renderer placement hints are preserved.
    """
    if overlay["type"] not in _ALLOWED_VISUAL_OVERLAY_TYPES:
        return None
    item = {
        "type": overlay["type"],
        "text": overlay["text"],
        "start": overlay.get("start", segment["start"]),
        "end": overlay.get("end", segment["end"]),
    }
    for key in ("anchor", "x", "y", "max_width", "style"):
        if key in overlay:
            item[key] = overlay[key]
    return item


def _write_canonical_visual_overlays(work_dir, narration_path):
    """Write assemble's canonical work_dir/visual_overlays.json recap handoff.

    Direct assemble still supports user-authored/manual visual_overlays.json files. Once
    recap owns the handoff for a narration, the canonical artifact is a deterministic
    reflection of the current narration so reused work_dirs cannot render stale overlays
    from a previous run; unsupported types are filtered out and represented as an explicit
    empty overlay list.
    """
    path = Path(work_dir) / _VISUAL_OVERLAYS
    overlays = [
        item
        for segment in load_json(narration_path)
        for overlay in segment.get("visual_overlays", [])
        if (item := _canonical_visual_overlay(overlay, segment)) is not None
    ]
    payload = {"schema_version": 1, "overlays": overlays}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[video-recap] 🧩 visual overlays: {len(overlays)} → {path}", flush=True)
    return path


def _print_grounding_qc_pointer(work_dir):
    qc_path = Path(work_dir) / "grounding_qc.json"
    if not qc_path.exists():
        return
    data = load_json(qc_path)
    ranges = data["review_coverage"]["time_ranges"]
    warnings = data["warnings"]
    suffix = f" · warnings {len(warnings)}" if warnings else ""
    print(
        f"[video-recap] 🧭 Grounding QC: {data['verdict']} · ranges {len(ranges)}{suffix} → {qc_path}",
        flush=True,
    )


def _print_narration_review_pointer(work_dir, *, review_ran=True):
    """Surface the advisory narration review produced by this run, if any.

    Review is optional/fail-open. Avoid surfacing a stale narration_review.md from an
    older run when review was disabled or failed before producing fresh artifacts.
    """
    _print_grounding_qc_pointer(work_dir)
    if not review_ran:
        return
    review_md = Path(work_dir) / "narration_review.md"
    if not review_md.exists():
        return
    review_json = Path(work_dir) / "narration_review.json"
    if not review_json.exists():
        print(f"[video-recap] 📋 解说评审（建议性，不拦截）→ {review_md}")
        return
    try:
        data = load_json(review_json)
    except (OSError, ValueError):
        print(f"[video-recap] 📋 解说评审（建议性，不拦截）→ {review_md}")
        return
    n_err = sum(1 for f in data["findings"] if f["severity"] == "error")
    print(
        f"[video-recap] 📋 解说评审（建议性，不拦截）: {data['verdict']} · "
        f"{len(data['findings'])} 条意见（error {n_err}）→ {review_md}"
    )


def _settings_for_compare(settings):
    """Settings that, if changed, invalidate reusing an existing work_dir on resume.

    `consolidate`/`consolidate_asr` are EXCLUDED: they only ADD an optional understanding
    artifact and never re-run Phase A on a Phase-B resume, so a stored manifest carrying the
    old default (or missing the key entirely, pre-dating it) must still resume — otherwise
    flipping `--consolidate`'s default ON would hard-fail every in-flight work_dir.
    """
    s = dict(settings)
    s.pop("consolidate", None)
    s.pop("consolidate_asr", None)
    return s


def _manifest_mismatches(work_dir, video, args):
    expected = _run_manifest_payload(video, args)
    actual = _load_run_manifest(work_dir)
    if actual is None:
        return ["缺少 recap_run_manifest.json；不能证明 work_dir 属于当前视频/参数"]
    # A multi-source manifest carries `sources` instead of these keys, so .get() reports it
    # as a mismatch rather than a crash.
    mismatches = [
        f"{key}: expected {expected[key]!r}, got {actual.get(key)!r}"
        for key in ("source_video", "source_video_fingerprint")
        if actual.get(key) != expected[key]
    ]
    if _settings_for_compare(actual["settings"]) != _settings_for_compare(expected["settings"]):
        mismatches.append("settings: 当前 CLI/env 参数与 Phase A manifest 不匹配")
    actual_audio = actual.get("audio", {"mode": "narration", "selected_stream_index": 0})
    expected_audio = audio_binding(args)
    if actual_audio != expected_audio:
        mismatches.append(
            f"audio: expected {expected_audio!r}, got {actual_audio!r}"
        )
    return mismatches


def _multi_manifest_mismatches(work_dir, videos, args, source_records):
    expected = _multi_run_manifest_payload(videos, args, source_records)
    actual = _load_run_manifest(work_dir)
    if actual is None:
        return ["缺少 recap_run_manifest.json；不能证明 work_dir 属于当前多视频/参数"]
    if actual.get("mode") != "multi_source":
        return [f"mode: expected 'multi_source', got {actual.get('mode')!r}"]
    mismatches = []
    identity = ("source_id", "source_path", "source_video_fingerprint")
    if [{k: s[k] for k in identity} for s in actual["sources"]] != [
        {k: s[k] for k in identity} for s in expected["sources"]
    ]:
        mismatches.append(
            "sources: 当前输入视频列表/顺序/source_id/fingerprint 与 Phase A manifest 不匹配"
        )
    if _settings_for_compare(actual["settings"]) != _settings_for_compare(expected["settings"]):
        mismatches.append("settings: 当前 CLI/env 参数与 Phase A manifest 不匹配")
    actual_audio = actual.get("audio", {"mode": "narration", "selected_stream_index": 0})
    expected_audio = audio_binding(args)
    if actual_audio != expected_audio:
        mismatches.append(
            f"audio: expected {expected_audio!r}, got {actual_audio!r}"
        )
    return mismatches


def _read_assembly_output(work_dir):
    return Path(load_json(Path(work_dir) / ASSEMBLY_MANIFEST)["final_output"])


def _file_md5(path):
    return hashlib.md5(Path(path).read_bytes()).hexdigest()


def _read_phase_ledger(work_dir):
    """Phase ledger (cut mode): which artifacts exist and the clip_plan/narration they match.

    Lets resume be driven by recorded phase state rather than bare file existence — the
    prerequisite for the cut-first/narrate-second two-pause flow, and the guard that keeps a
    narration written for one clip_plan from silently driving a different cut into TTS.
    None before the first cut pass has recorded anything.
    """
    path = Path(work_dir) / PHASE_LEDGER
    return load_json(path) if path.exists() else None


def _write_phase_ledger(work_dir, **fields):
    ledger = _read_phase_ledger(work_dir) or {}
    ledger.update(fields)
    (Path(work_dir) / PHASE_LEDGER).write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return ledger


def _cut_narration_is_stale(ledger, current_clip_plan_fp):
    """Two-pass cut: the narration is authored against the rendered cut shown at the A2 pause,
    i.e. against the clip_plan recorded in the ledger. If clip_plan changed since (a re-cut)
    while that narration is still present, it describes the OLD cut — stale."""
    return ledger is not None and ledger["clip_plan_fingerprint"] != current_clip_plan_fp


def _continuation_command(video, work_dir, args):
    parts = [
        sys.executable,
        str(_entry("video-recap", "recap.py")),
        *[str(v) for v in _coerce_videos(video)],
        "--work-dir",
        str(work_dir),
    ]
    if args.context:
        parts += ["--context", args.context]
    if args.scene_threshold is not None:
        parts += ["--scene-threshold", str(args.scene_threshold)]
    if args.style != "纪录片":
        parts += ["--style", args.style]
    if args.edit_mode != "full":
        parts += ["--edit-mode", args.edit_mode]
    if getattr(args, "audio_mode", "narration") != "narration":
        parts += ["--audio-mode", args.audio_mode]
    if getattr(args, "audio_stream_index", 0) != 0:
        parts += ["--audio-stream-index", str(args.audio_stream_index)]
    if args.target_duration:
        parts += ["--target-duration", args.target_duration]
    if args.allow_duration_drift:
        parts.append("--allow-duration-drift")
    if args.skip_asr:
        parts.append("--skip-asr")
    if args.mimo_video_overview:
        parts.append("--mimo-video-overview")
    if args.mimo_qc != "off":
        parts += ["--mimo-qc", args.mimo_qc]
    if args.mimo_qc_refresh:
        parts.append("--mimo-qc-refresh")
    if not args.consolidate:  # default is ON; only the opt-out needs to round-trip
        parts.append("--no-consolidate")
    if args.consolidate_asr:
        parts.append("--consolidate-asr")
    if uses_narration(args):
        if args.mimo_tts_voice:
            parts += ["--mimo-tts-voice", args.mimo_tts_voice]
        if args.tts_provider != "auto":
            parts += ["--tts-provider", args.tts_provider]
        if args.voice_ref:
            parts += ["--voice-ref", args.voice_ref]
        if args.allow_partial_tts:
            parts.append("--allow-partial-tts")
        if getattr(args, "preserve_approved_text", False):
            parts.append("--preserve-approved-text")
    if args.burn_subtitles is not None:
        parts.append("--burn-subtitles" if args.burn_subtitles else "--no-burn-subtitles")
    if args.subtitle_y_top is not None:
        parts += ["--subtitle-y-top", str(args.subtitle_y_top)]
    if args.subtitle_y_bot is not None:
        parts += ["--subtitle-y-bot", str(args.subtitle_y_bot)]
    if args.output_dir:
        parts += ["--output-dir", args.output_dir]
    if args.export_jianying:
        parts.append("--export-jianying")
    if args.jianying_bundle_media:
        parts.append("--jianying-bundle-media")
    if args.jianying_no_bundle_media:
        parts.append("--jianying-no-bundle-media")
    if uses_narration(args):
        if args.review_narration is not None:
            parts.append(
                "--review-narration" if args.review_narration else "--no-review-narration"
            )
        if args.require_narration_review:
            parts.append("--require-narration-review")
    if args.material_library_dir:
        parts += ["--material-library-dir", args.material_library_dir]
    if args.use_materials:
        parts.append("--use-materials")
    if args.save_materials:
        parts.append("--save-materials")
    if getattr(args, "require_final_qc", False):
        parts.append("--require-final-qc")
    return " ".join(shlex.quote(part) for part in parts)


def _source_work_dir(project_work_dir, source_record):
    return Path(project_work_dir) / source_record["source_work_dir"]


def _understand_args_for_source(source_record, source_work_dir, args):
    uargs = [
        source_record["source_path"],
        "--work-dir",
        str(source_work_dir),
        "--style",
        args.style,
        "--edit-mode",
        args.edit_mode,
    ]
    if args.context:
        uargs += ["--context", args.context]
    if args.scene_threshold is not None:
        uargs += ["--scene-threshold", str(args.scene_threshold)]
    if args.target_duration:
        uargs += ["--target-duration", args.target_duration]
    if args.skip_asr:
        uargs.append("--skip-asr")
    if args.mimo_video_overview:
        uargs.append("--mimo-video-overview")
    uargs.append("--consolidate" if args.consolidate else "--no-consolidate")
    if args.consolidate_asr:
        uargs.append("--consolidate-asr")
    return uargs


def _brief_excerpt(path, limit=1200):
    text = Path(path).read_text(encoding="utf-8")
    if len(text) <= limit:
        return text
    marker = "\n…\n"

    def section(heading):
        lines = text.splitlines()
        try:
            start = lines.index(heading)
        except ValueError:
            return ""
        end = next(
            (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
            len(lines),
        )
        return "\n".join(lines[start:end]).strip()

    def clipped(value, budget):
        if len(value) <= budget:
            return value
        usable = budget - len(marker)
        head = round(usable * 0.7)
        return value[:head].rstrip() + marker + value[len(value) - (usable - head):].lstrip()

    # The generic writing contract occupies the front of every generated brief. A raw
    # prefix therefore drops the source facts that multi-source planning actually needs.
    # Give evidence-bearing sections equal space and retain both their opening and tail.
    preferred = [
        value
        for value in (
            section("## Story context (from background_research.json)"),
            section("## Understanding index (from consolidate.py)"),
            section("## ASR writing chunks (semantic windows)"),
            section("## Scene timing guide"),
        )
        if value
    ]
    if preferred:
        per_section = (limit - 2 * (len(preferred) - 1)) // len(preferred)
        return "\n\n".join(clipped(value, per_section) for value in preferred)[:limit]
    usable = limit - len(marker)
    head = usable // 4
    return text[:head].rstrip() + marker + text[len(text) - (usable - head):].lstrip()


def _write_multi_source_clip_brief(work_dir, source_records, args):
    lines = [
        "# Multi-source Clip Plan Brief",
        "",
        "你正在做多视频剪辑复盘。当前 MVP 只支持 `--edit-mode cut`：先做跨素材的故事与视听决定，再写 `clip_plan.json`；下一步会剪出 `edited_source.mp4`，最后按 OUTPUT 时间轴写 `narration.json`。",
        "",
        "## 创作决定",
        "",
        "先判断创作控制模式：CREATE 比较至少两个可行剪辑假设；DIRECTED 落实用户指定结构；REVISION 只改最新反馈点并冻结未点名内容。然后写或更新 `recap_story_plan.json` 与 `visual_audio_board.json`。前者记录观众承诺、POV、戏剧问题、选定主线及 change-based beats；后者记录每拍的具体画面/反应、入点/出点、原声锚点、`audio_owner` 与 `narration_job`。",
        "",
        "多视频不是把每个来源各做一段小总结。每个来源片段都必须服务同一条主线，并用 `source_id` 保留证据归属。这两份计划是 Agent 与建议型评审使用的工作记录，不是 CLI 渲染门禁。",
        "",
        "## 必须写入的格式",
        "",
        "```json",
        '{"target_duration":"10m","clips":[{"source_id":"src_xxx","start":12.0,"end":38.0,"reason":"b01 | hook | knowledge: unknown→threat | POV=主角 | 保留倾听反应 | 入点=问题已问出 | 出点=沉默落地"}]}',
        "```",
        "",
        "- 每个 clip 必须带 `source_id`。",
        "- `start`/`end` 是对应 source 原视频时间（秒）。",
        "- 不同 `source_id` 的相同时间段不算重叠；同一 `source_id` 内不要重复/重叠，除非你明确接受稀疏/重复剪辑风险。",
        '- 素材库是文件系统 JSON/MD/JSONL；需要找历史素材时直接 `grep -R "关键词" <material-library-dir>`。',
        "",
        "## 剪辑规则",
        *CUT_TIMELINE_CRAFT_BULLETS,
        "- 跨来源选择必须服务共享主线；除非本来就是 setup / turn / payoff 的设计，不要让某个来源变成脱节的小复盘。",
        "- 选片前使用下方 source work_dir（`sources/<source_id>`）核对 scenes.json、ASR、索引和逐来源 brief。",
    ]
    if args.target_duration:
        lines.append(f"- 目标时长：`{args.target_duration}`。")
    lines += ["", "## Sources", ""]
    for s in source_records:
        swd = _source_work_dir(work_dir, s)
        lines += [
            f"### {s['source_id']} — {s['source_name']}",
            f"- path: `{s['source_path']}`",
            f"- work_dir: `{swd}`",
            f"- fingerprint: `{s['source_video_fingerprint']}`",
            f"- material_id: `{s['material_id']}`",
        ]
        excerpt = _brief_excerpt(swd / "agent_narration_brief.md")
        if excerpt:
            lines += ["", "#### per-source brief excerpt", "", excerpt, ""]
    (Path(work_dir) / "agent_narration_brief.md").write_text(
        "\n".join(lines).rstrip() + "\n", encoding="utf-8"
    )


def _source_speech_rows(source_dir):
    """The cleaned transcript wins when it carries text; same precedence as
    audio_mix._handoff_speech_evidence and sentence_boundaries._load_source_speech_spans."""
    clean = source_dir / "asr_clean.json"
    if clean.exists():
        rows = load_json(clean)["segments"]
        if any(row["text"].strip() for row in rows):
            return rows
    raw = source_dir / "asr_result.json"
    return load_json(raw) if raw.exists() else []


def _write_multi_source_output_speech_evidence(work_dir, source_records, plan):
    """Map each source's speech and quiet evidence onto the combined output clock."""
    source_by_id = {row["source_id"]: row for row in source_records}
    cache = {}

    def load_source(source_id):
        if source_id not in cache:
            source_dir = _source_work_dir(work_dir, source_by_id[source_id])
            anchors_path = source_dir / "speech_boundary_anchors.json"
            quiet_path = source_dir / "silence_periods.json"
            cache[source_id] = (
                load_json(anchors_path)["sentence_anchors"] if anchors_path.exists() else [],
                _source_speech_rows(source_dir),
                load_json(quiet_path) if quiet_path.exists() else [],
            )
        return cache[source_id]

    mapped_anchors, mapped_speech, mapped_quiet = [], [], []
    for clip in plan["clips"]:
        source_id = clip["source_id"]
        source_start = float(clip["source_start"])
        source_end = float(clip["source_end"])
        output_start = float(clip["output_start"])
        anchors, speech_rows, quiet_rows = load_source(source_id)
        for anchor in anchors:
            when = float(anchor["time"])
            if not (source_start - 0.05 <= when <= source_end + 0.05):
                continue
            try:
                pause = float(anchor["pause_start"])
            except (TypeError, ValueError):
                pause = when
            pause = max(source_start, min(pause, when))
            item = dict(anchor)
            item.update(
                source_id=source_id,
                source_time=round(when, 3),
                time=round(output_start + when - source_start, 3),
                source_pause_start=round(pause, 3),
                pause_start=round(output_start + pause - source_start, 3),
            )
            mapped_anchors.append(item)
        for rows, destination, require_text in (
            (speech_rows, mapped_speech, True),
            (quiet_rows, mapped_quiet, False),
        ):
            for row in rows:
                if require_text and not row["text"].strip():
                    continue
                if not require_text and row["has_speech"]:
                    continue
                start = max(source_start, float(row["start"]))
                end = min(source_end, float(row["end"]))
                if end <= start:
                    continue
                item = dict(row)
                item.update(
                    source_id=source_id,
                    source_start=round(start, 3),
                    source_end=round(end, 3),
                    start=round(output_start + start - source_start, 3),
                    end=round(output_start + end - source_start, 3),
                )
                destination.append(item)

    payload = {
        "schema_version": 2,
        "artifact": "speech_boundary_anchors_output.json",
        "timeline": "cut_output",
        "source_artifact": "multi_source_manifest.json",
        "clip_plan_fingerprint": hashlib.md5(
            json.dumps(
                plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
            ).encode("utf-8")
        ).hexdigest(),
        "sentence_anchors": sorted(mapped_anchors, key=lambda row: row["time"]),
        "speech_spans": sorted(mapped_speech, key=lambda row: (row["start"], row["end"])),
        "quiet_windows": sorted(mapped_quiet, key=lambda row: (row["start"], row["end"])),
    }
    Path(work_dir, "speech_boundary_anchors_output.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload


def _write_multi_source_output_brief(work_dir, source_records, validated_plan_path):
    plan = load_json(validated_plan_path)
    source_by_id = {s["source_id"]: s for s in source_records}
    speech_evidence = _write_multi_source_output_speech_evidence(
        work_dir, source_records, plan
    )
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
        "## narration.json 格式",
        "",
        "```json",
        '[{"start":0.0,"end":4.0,"narration":"解说文本。","pause_after_ms":250,"overlaps_speech":true,"emotion":"平静"}]',
        "```",
        "",
        "注意：`start`/`end` 是剪后成片时间，不是原视频时间。",
        "",
        "## 输出时间线写作规则",
        *MULTI_SOURCE_NARRATION_CRAFT_BULLETS,
        "- 旁白增加上下文、因果、预期、证据支持的解释或跨源过渡，不复述画面像素；先保人物与原声，再润色句子。",
        "",
        "## Kept clips (output → source)",
    ]
    for c in plan["clips"]:
        src = source_by_id[c["source_id"]]
        reason = f" — {c['reason']}" if c["reason"] else ""
        lines.append(
            f"- output {_fmt_range(c['output_start'], c['output_end'])} → "
            f"{c['source_id']} `{src['source_path']}` "
            f"source {_fmt_range(c['source_start'], c['source_end'])}{reason}"
        )
    anchors = speech_evidence["sentence_anchors"]
    if anchors:
        lines += ["", "## 原声句末安全切入点"]
        lines.extend(
            f"- {row['time']:.3f}s ({row['source_id']})"
            for row in anchors
            if row["confidence"] in {"high", "medium"}
        )
    lines += ["", "## Source work dirs"]
    for s in source_records:
        lines.append(f"- {s['source_id']}: `{_source_work_dir(work_dir, s)}`")
    (Path(work_dir) / "agent_narration_brief.md").write_text(
        "\n".join(lines).rstrip() + "\n", encoding="utf-8"
    )


def _cut_qc_summary_line(qc):
    geometry = qc["output_geometry"]
    parts = [
        f"target_duration_status={qc['target_duration_status']}",
        f"total_duration={qc['total_duration']}",
        f"clip_count={qc['clip_count']}",
        f"join_fade_ms={qc['join_fade_ms']}",
        f"output_geometry={geometry['width']}x{geometry['height']}@{geometry['fps']}fps"
        f" reason={qc['output_geometry_reason']}",
    ]
    if qc.get("warnings"):
        parts.append(f"warnings={len(qc['warnings'])}")
    return "[video-recap] cut QC: " + "; ".join(parts)


def _surface_cut_qc(work_dir):
    """Print the cut QC video-cut recorded; cut.py itself already fails on blocking QC."""
    qc = load_json(Path(work_dir) / "clip_plan_validated.json")["qc"]
    print(_cut_qc_summary_line(qc), flush=True)
    return qc


def _fmt_range(start, end):
    return f"{start:.3f}-{end:.3f}s"


def _material_library_dir(args):
    return args.material_library_dir or os.environ.get("VIDEO_RECAP_MATERIAL_LIBRARY_DIR") or None


def _materials_enabled(args):
    return bool(_material_library_dir(args) and args.use_materials)


def _save_materials_enabled(args):
    return bool(_material_library_dir(args) and args.save_materials)


def _pause_for_agent(work_dir, need_text, cont, inspect_hint=None):
    brief = Path(work_dir) / "agent_narration_brief.md"
    print("=" * 50)
    if "Research the story FIRST" in brief.read_text(encoding="utf-8"):
        print(
            "[video-recap] ⚑ 理解素材偏薄：先按 brief 顶部「Research the story FIRST」调研并写 "
            "background_research.json，再写解说，避免看图说话。"
        )
    print(f"[video-recap] ⏸  阅读 {brief}（按 video-script 规则）后写入 {need_text}")
    if inspect_hint:
        print(f"[video-recap]    先核对状态/时间轴（建议性）: {inspect_hint}")
    print(f"[video-recap]    写完后重跑继续: {cont}")
    print("=" * 50)
