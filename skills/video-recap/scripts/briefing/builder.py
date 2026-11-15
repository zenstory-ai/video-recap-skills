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


def _extract_manifest_tts_provider(manifest):
    if not isinstance(manifest, dict):
        return None
    for candidate in (
        manifest.get("tts_provider"),
        (manifest.get("audio") or {}).get("tts_provider") if isinstance(manifest.get("audio"), dict) else None,
        (manifest.get("settings") or {}).get("tts_provider") if isinstance(manifest.get("settings"), dict) else None,
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip().lower()
    argv = manifest.get("argv")
    if isinstance(argv, list):
        for idx, token in enumerate(argv):
            if token == "--tts-provider" and idx + 1 < len(argv) and isinstance(argv[idx + 1], str):
                return argv[idx + 1].strip().lower()
            if isinstance(token, str) and token.startswith("--tts-provider="):
                return token.split("=", 1)[1].strip().lower()
    return None


def _resolve_tts_provider_hint(work_dir, tts_provider=None):
    explicit = tts_provider or CONFIG.get("_explicit_tts_provider")
    if isinstance(explicit, str) and explicit.strip() and explicit.strip().lower() != "auto":
        return explicit.strip().lower()
    if work_dir is not None:
        base = Path(work_dir)
        candidates = [base / "recap_run_manifest.json"]
        if base.parent.name == "sources":
            candidates.append(base.parent.parent / "recap_run_manifest.json")
        for manifest_path in candidates:
            if manifest_path.is_file():
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    found = _extract_manifest_tts_provider(manifest)
                    if found:
                        return found
                except (OSError, ValueError):
                    pass
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip().lower()
    return str(CONFIG.get("tts_provider") or "auto").strip().lower()


def _narration_example_item(provider, *, end=11.2, text, emotion="紧张", include_policy=True):
    item = {
        "start": 0.2,
        "end": end,
        "narration": text,
        "pause_after_ms": 250,
        "overlaps_speech": True,
    }
    if provider in {"auto", "mimo-tts"}:
        item["emotion"] = emotion
    if include_policy:
        item["source_entry_policy"] = "sentence_boundary"
    return json.dumps(item, ensure_ascii=False)


_TAIL_CREDITS_MARKERS = ("演职员表", "制作人员", "制作组名单", "片尾字幕", "片尾滚动", "下集预告")


def _visual_subtitle_and_credits_callout(
    scenes_analysis, brief_scenes, video_duration, *, cut_pass2=False
):
    """Surface hardcoded-caption and tail-credits signals already observed by VLM."""
    caption_hits = 0
    for scene in brief_scenes or []:
        if not isinstance(scene, dict):
            continue
        desc = str(scene.get("description") or "")
        facts_obj = scene.get("frame_facts")
        facts_text = (
            " ".join(
                str(item)
                for items in facts_obj.values()
                if isinstance(items, list)
                for item in items
            )
            if isinstance(facts_obj, dict)
            else ""
        )
        cleaned = (desc + " " + facts_text).replace("片尾字幕", "").replace("滚动字幕", "")
        if "字幕" in cleaned:
            caption_hits += 1

    credit_scenes = []
    if not cut_pass2 and float(video_duration or 0.0) >= 60.0:
        cutoff = float(video_duration) * 0.55
        for scene in scenes_analysis or []:
            if not isinstance(scene, dict) or float(scene.get("start", 0.0)) < cutoff:
                continue
            desc = str(scene.get("description") or "")
            if any(marker in desc for marker in _TAIL_CREDITS_MARKERS):
                credit_scenes.append(scene)

    bullets = []
    if caption_hits >= 3:
        bullets.extend([
            "- **检测到源片多处画面带有内嵌硬字幕**：",
            "  - **专有名词校准**：ASR 易把人名、地名或设定词转写成同音字，定稿前请对照下方场景的画面文字 /【帧标签】与 `background_research.json` 核准字形。",
            "  - **烧录防叠字**：若成片烧录解说字幕，请确认是否开启底部遮罩（`MASK_SOURCE_SUBTITLES=1` 或实测 `--subtitle-y-top/--subtitle-y-bot`）或错开字幕位置；未全片不透明遮罩留白段时无需重复写 `original_subtitles.json`。",
            "  - **封面避字**：在 `promo_copy.json` 中挑选 `cover.timestamp` 时，优先选无底部硬字幕干扰的干净镜头帧。",
        ])
    if len(credit_scenes) >= 3:
        c_start = min(float(s["start"]) for s in credit_scenes)
        c_end = max(float(s["end"]) for s in credit_scenes)
        bullets.append(
            f"- **检测到片尾演职员表 / 预告区间参考**：源片约 `{c_start:.1f}s–{c_end:.1f}s`（共 {len(credit_scenes)} 个场景），选段剪辑时注意剔除（除非其中含正片关键彩蛋）。"
        )
    if not bullets:
        return []
    return ["## 源片画面文字与片尾提示", "", *bullets, ""]


def _missing_research_callout(research, context_info=""):
    if research or str(context_info or "").strip():
        return []
    return [
        "## ⚠️ 缺少背景资料（建议先补齐再定稿）",
        "",
        "当前未找到 `background_research.json`，且 `--context` 为空。缺少角色真名、身份关系与前情利益冲突时，解说容易滑向复述画面表象。",
        "定稿 `recap_story_plan.json` 或 `narration.json` 前，建议先核实人物关系与故事背景并写入 `background_research.json`（随后可用 `understand.py --brief-only` 刷新本简报）。",
        "",
    ]


def _emotion_section(provider):
    if provider == "index-tts":
        return [
            "## 旁白语气标签（`emotion` 字段）",
            "",
            "当前 TTS 供应商为 `index-tts`：端点请求体固定为 `{\"voice\", \"text\"}`，不接收段级 `emotion`、`style`、`rate`、`pitch`，请勿在 `narration.json` 中填写这些字段（否则配音阶段会直接报错退出）。",
            "",
        ]
    if provider == "fish-audio":
        return [
            "## 旁白语气标签（`emotion` 字段）",
            "",
            "当前 TTS 供应商为 `fish-audio`：引擎不消费段级 `emotion` 标签，可省略该字段，主要靠选词节奏与 `pause_after_ms` 控制呼吸感。",
            "",
        ]
    return [
        "## 旁白语气标签（`emotion` → MiMo TTS 语气指令）",
        "",
        "使用 MiMo TTS 时，每段 `emotion` 可写 1–2 个简短中文语气词（如 `\"紧张 深沉\"` 或 `\"无奈\"`）：",
        "- 基础情绪：开心 悲伤 愤怒 恐惧 惊讶 兴奋 委屈 平静 冷漠",
        "- 复合情绪：怅然 欣慰 无奈 愧疚 释然 嫉妒 厌倦 忐忑 动情",
        "- 整体语调：温柔 高冷 活泼 严肃 慵懒 俏皮 深沉 干练 凌厉",
        "仅中性铺垫用「平静」，冲突推进优先选贴合戏剧张力的语气词；若后续改跑 `index-tts`，需去掉段级 `emotion` 字段。",
        "",
    ]


def _floor_gates_and_emotion_section(provider="auto"):
    return [
        "",
        "## 底线校验与质量提示（validate.py / shot_review.py）",
        "",
        "- **硬阻断（不过检不进 TTS / 合成）**：",
        "  - 句尾与原声切入完整性（`incomplete_sentence` / `interrupts_source_sentence`）：旁白须以句末标点收尾；切入原声对白须对齐句末停顿锚点或静音窗，避免半句掐断。",
        f"  - 残镜闪帧与镜头切词（`flash_frame_sliver` / `shot_speech_conflict`）：单段剪辑最短 ≥ {CONFIG['min_clip_duration']:.1f}s，`shot_review` 会拦截 ≤ {CONFIG['flash_sliver_max_seconds']:.2f}s 的残帧。",
        "  - 定稿字数与占位符（`over_budget` / `placeholder_leakage`）：full 模式下超出窗口硬上限的段落会退回改稿，不静默删字；严禁残留模板占位词。",
        "- **非阻断提示（写入报告供复核）**：",
        "  - 原声释放复核（`unsafe_source_release`）：旁白收口落在粗粒度/对白区间内且后续 3s 无句末停顿锚点时提醒试听确认，避免在角色半句话中途恢复原声。",
        f"  - 开场与留白节奏（`weak_cold_open_silence` / `excessive_dead_air`）：开场静默 > {CONFIG['max_cold_open_silence_seconds']:.1f}s 或连续 > {CONFIG['max_dead_air_seconds']:.1f}s 无旁白无对白时给出提示；若是有意留给动作或冷开场的呼吸空间可保留。",
        "  - 角色称呼与套话提醒（`generic_character_label` / `editorial_commentary_tone`）：已有角色真名时提醒避免外貌泛称；提醒删掉空泛升华总结腔，结局由画面与原声自然落定即可。",
        "",
        "## 原声留白字幕 `original_subtitles.json`（可选）",
        "",
        "解说块之间留出的原声对白，可通过 `original_subtitles.json` 烧录成字幕（与解说字幕以 `「」` 区分）：按成片 OUTPUT 时间轴写成 `[{\"start\": 秒, \"end\": 秒, \"text\": \"台词\"}]`。",
        "- 只写留白里实际听得清且完整的原声台词；被旁白压低盖过或已剪掉的句子不要写。",
        "- 订正 ASR 错别字与人名，单条尽量控制在约 20 字以内；不写该文件时留白区间不会自动烧录粗粒度 ASR。",
        "",
        *_emotion_section(provider),
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
    extra_context=None,
):
    """Write a compact Chinese brief that guides the agent to author recap artifacts."""
    effective_rate = (
        CONFIG["speech_rate"]
        * CONFIG["speech_safety_margin"]
        * CONFIG["narration_speed"]
    )
    breath_sec = CONFIG["breath_ms"] / 1000
    target_pause_ms = CONFIG["breath_ms"]
    edit_mode = CONFIG["edit_mode"]
    target_duration = CONFIG["target_duration"] or "(未指定)"
    provider = _resolve_tts_provider_hint(work_dir)
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
        "# 解说创作简报（Agent Narration Brief）",
        "",
        "请根据当前工作目录中的画面与语音证据编写所需 JSON 产物；CLI 只负责时间与底线校验、TTS 配音及音画合成，不代写正文。",
        "",
        f"- 风格指引 (--style，原样自由文本): {style}",
        "- 请直接吸收 `--style` 的语气与节奏要求结合本片证据落笔，不要把 `--style` 映射成固定枚举或套用模板腔。",
        f"- 剪辑模式: {edit_mode}",
        f"- 源视频时长: {video_duration:.1f}s",
        f"- 目标成片时长 (cut 模式): {target_duration}",
        f"- 推荐语速预算: 扣除 {breath_sec:.2f}s 气口后约 {effective_rate:.2f} 汉字/秒",
    ]
    if asr_evidence is None:
        asr_evidence = {"status": "MISSING_OR_STALE"}

    research = load_background_research(work_dir)
    if extra_context is not None:
        displayed_context = extra_context.strip() or ("(已并入下方背景调研)" if research else "(无)")
    else:
        displayed_context = CONFIG["context_info"] or "(无)"

    lines.extend(
        [
            "",
            "## ASR 时间证据说明",
            "",
            f"- 状态: {asr_evidence['status']}",
            "- 空文本窗口表示未检出文本（UNKNOWN_NOT_PROVEN_SILENCE），仅定位大致区间，不等同于已核实的静音。",
            "- 时间粒度: 供应商粗粒度窗口；逐词对齐: NOT_PERFORMED；对白边界: NOT_VERIFIED。",
            "- ASR 起止时间与安静窗口仅供参考，不能直接当作已验证的对白切点；保留或裁剪原声台词前请直接试听并核对画面。",
            "",
            f"- 段落默认气口: {target_pause_ms}ms",
            f"- 补充上下文: {displayed_context}",
            "",
            "## 动笔前的创作规划",
            "",
            "先根据用户意图与现有产物判断创作控制模式：`CREATE` 从零构建叙事主线；`DIRECTED` 落实用户已指定的结构、镜头或台词；`REVISION` 只修改最新反馈点并冻结其余已确认内容。",
            "- `CREATE` 主线清晰时直接写 1 个选定方案即可，仅在源片存在多种剪法取舍时比较 2–3 个候选假设；`DIRECTED` / `REVISION` 直接沿用既有主线。",
            "- `REVISION` 先列「本次修改项」与「冻结项」：改文风/字幕更 `style_card.json`，改选镜/原声分工更 `visual_audio_board.json`，仅当主视角（POV）或核心节拍变化时更 `recap_story_plan.json`。",
            "写 `clip_plan.json` 或 `narration.json` 前，先写好或更新 `recap_story_plan.json` 与 `visual_audio_board.json`：",
            "- `recap_story_plan.json` 记录导演立意、POV、主线因果以及由局势/关系/筹码变化驱动的节拍（`hook | setup | turn | escalation | payoff`）。",
            "- `visual_audio_board.json` 记录每拍选用的具体画面与反应、入出点依据、原声锚点、`audio_owner` 与 `narration_job`。",
            "- 叙事与声画分工定妥后再写 `style_card.json`（只管口吻与句式节奏，不做预设分类表）。",
            "- 成片画布视觉包装写入 `packaging_plan.json`（`portrait_cinema` 等预设）；若用户需要平台标题、封面文案等外部宣发备忘，单独写入 `promo_copy.json`，切勿混用同名文件。",
            "",
        ]
    )

    lines.extend(_format_moderation_refusals(scenes_analysis))
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
    lines.extend(
        _visual_subtitle_and_credits_callout(
            scenes_analysis, brief_scenes, video_duration, cut_pass2=cut_pass2
        )
    )
    lines.extend(_format_sentence_entry_anchors_for_brief(work_dir, edit_mode))

    sample_text = "林砚刚推开档案室的门，就看见桌上那份三年前的结案报告被人换掉了签名。"
    if edit_mode == "cut":
        cut_target_example = (
            target_duration if target_duration != "(未指定)" else "30m"
        )
        if not cut_pass2:
            lines.extend(
                [
                    "## Cut 模式 — 第 1/2 步：完成故事与声画设计，仅写出 `clip_plan.json`",
                    "",
                    f"目标：从 {source_label} 源片剪出约 ~{output_label} 的解说成片（体量按 ~{output_label} CUT OUTPUT 规划，而非按 {source_label} 源片铺满）。",
                    "先确认观众承诺、POV 与戏剧问题并落盘 `recap_story_plan.json` + `visual_audio_board.json`，再挑选源片片段写入 `clip_plan.json`；CLI 渲染出 `edited_source.mp4` 后会进入第 2 步对成片写解说，本步不要写 `narration.json`。",
                    "选段要点（结合下方场景时间参考与 ASR）：",
                    "- 开场直入冲突或异常，建立一条连贯的因果主线（`hook → setup → turn → escalation → payoff`），避免拼凑无因果关联的集锦。",
                    "- 保留真正推动局势翻转、亮底牌或关键情绪反应的镜头；删掉空泛过渡镜、重复寒暄、片头片尾演职员表、广告赞助及大面积水印片段。",
                    "- `reason` 用简明自然的中文写清该段承担的节拍作用与入出点依据（无需按固定段数拼竖线模板）。",
                    "- 单段剪辑时长随戏走（最短 ≥ 0.8s，不留 ≤ 0.20s 残帧），收尾段留出动作或表情落定的气口。",
                    "- 锁定切点前排查密集镜头切换：对原片自带切点，整段删掉无关碎镜并把有效镜头延展到动作/反应完整；对本次拼接制造的切点，挪动边界、恢复同源连续动作或合并相邻段，不要用转场遮掩坏接点。",
                    "- 保持原声台词完整：ASR 窗口边界仅供参考，落刀前请直接试听并核对画面；`cut.py` 也会结合 `silence_periods.json` 与 `speech_boundary_anchors.json` 做句界吸附与切词拦截。",
                    "",
                    "### clip_plan.json 格式（填写源片时间戳）",
                    "",
                    "```json",
                    "{",
                    f'  "target_duration": {json.dumps(cut_target_example, ensure_ascii=False)},',
                    '  "clips": [',
                    '    {"start": 12.0, "end": 38.0, "reason": "b01 (hook)：亮出结案报告签名被换的异常，入点卡推门定住，出点卡看清签名抬头"}',
                    "  ]",
                    "}",
                    "```",
                ]
            )
        else:
            lines.extend(_format_output_clip_list(work_dir))
            example_row = _narration_example_item(
                provider, end=11.2, text=sample_text, emotion="紧张"
            )
            lines.extend(
                [
                    "## Cut 模式 — 第 2/2 步：按 OUTPUT 成片时间轴编写 `narration.json`",
                    "",
                    "先查看剪后故事板与上方保留片段映射，在 `visual_audio_board.json` 中补齐 OUTPUT 时间段并复核每拍的 `audio_owner` 与 `narration_job`。",
                    f"剪辑成片已渲染为 `edited_source.mp4` (~{output_label})，全片按 ~{output_label} CUT OUTPUT 时间轴（0 .. 总时长）落点，切勿填写源片时间戳。",
                    "",
                    "### narration.json 格式（OUTPUT 成片时间戳，0..总时长）",
                    "",
                    "每条记录对应一段连贯口播；交由画面、原声对白或静音承担的拍不写条目。示例文本仅供格式参考，请按本片证据撰写：",
                    "```json",
                    "[",
                    f"  {example_row}",
                    "]",
                    "```",
                ]
            )
    else:
        example_row = _narration_example_item(
            provider, end=11.2, text=sample_text, emotion="平静"
        )
        lines.extend(
            [
                "## narration.json 格式要求",
                "",
                "写 `narration.json` 前先完成 `recap_story_plan.json` 与 `visual_audio_board.json`，按视听板决定哪些节拍需要旁白。",
                "beat 对应关系记录在 `visual_audio_board.json`；`narration.json` 仍只承载时间、文本与朗读参数。",
                "每条记录对应一段连贯口播；交由画面、原声对白或静音承担的拍不写条目。示例文本仅供格式参考，请按本片证据撰写：",
                "```json",
                "[",
                f"  {example_row}",
                "]",
                "```",
            ]
        )

    lines.extend(_floor_gates_and_emotion_section(provider))
    if cut_pass2:
        lines.extend(
            [
                "## 场景时间参考（OUTPUT 成片时间轴）",
                "",
                "以下仅列出保留在 `edited_source.mp4` 中的画面片段（已换算为 0 .. 总时长 OUTPUT 时间轴，剪除段落不列出）。写作时优先依据深层潜台词与 ASR 对白，画面参考仅用于核对切点，切勿改写成看图说话。",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "## 场景时间参考",
                "",
                "写作时优先依据深层潜台词与 ASR 对白；画面参考仅用于核对切点，切勿改写成看图说话。",
                "",
            ]
        )

    for scene in brief_scenes:
        duration = scene["end"] - scene["start"]
        quiets = _quiet_windows_for_scene(brief_silence, scene)
        quiet_text = ", ".join(f"{s:.1f}-{e:.1f}s" for s, e in quiets) or "无"
        span = f"{scene['start']:.1f}-{scene['end']:.1f}s"
        lines.append(
            f"### 成片 {span}（源场景 {_scene_number(scene['scene_id'])}）"
            if cut_pass2
            else f"### 场景 {_scene_number(scene['scene_id'])}: {span}"
        )
        lines.append(f"- 时长: {duration:.1f}s")
        if scene.get("depth_analysis"):
            lines.append(f"- 深层分析: {scene['depth_analysis']}")
        asr_lines = _scene_asr_lines(brief_asr, scene)
        if asr_lines:
            lines.append("- ASR 对白重叠:")
            lines.extend(asr_lines[:8])
        lines.append(f"- 安静窗口: {quiet_text}")
        lines.append(
            f"- 画面参考（仅供核对切点）: {scene.get('description', '')}"
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
        "# 多视频剪辑规划简报（Multi-source Clip Plan Brief）",
        "",
        "当前为多视频跨片源剪辑解说（通过 `--edit-mode cut` 跨源选段）：先完成跨素材的故事主线与声画设计，写出 `clip_plan.json`；下一步剪出 `edited_source.mp4` 后，再按成片 OUTPUT 时间轴写 `narration.json`。",
        "",
        "## 创作决定",
        "",
        "先判断创作控制模式：`CREATE` 主线明确时写 1 个方案，存在多路线取舍时比较 2–3 个候选假设；`DIRECTED` 落实用户指定结构；`REVISION` 只改最新反馈点并冻结其余已确认内容。随后落盘 `recap_story_plan.json` 与 `visual_audio_board.json`：前者记录观众承诺、POV、戏剧问题与节拍推进；后者记录每拍选用的画面/反应、入出点、原声锚点、`audio_owner` 与 `narration_job`。",
        "",
        "多视频剪辑不是把每个片源各做一段小总结，所有选中片段都应推进同一条主线，并用 `source_id` 标明素材来源。",
        "",
    ]
    lines.extend(_missing_research_callout(research, getattr(args, "context", "")))
    lines.extend(_format_background_research(research))
    lines.extend(
        [
            "## 必须写入的格式",
            "",
            "```json",
            '{"target_duration":"10m","clips":[{"source_id":"src_xxx","start":12.0,"end":38.0,"reason":"b01 (hook)：亮出结案报告签名被换的异常，入点卡推门定住，出点卡看清签名抬头"}]}',
            "```",
            "",
            "- 每个 clip 必须带 `source_id`，单段时长 ≥ 0.8s，不留 ≤ 0.20s 的残镜头闪帧。",
            "- `reason` 用简明中文写清该段在主线中的作用与入出点依据，收尾拍留足情绪或动作落点。",
            "- `start`/`end` 填写对应 `source_id` 源视频的时间（秒）。",
            "- 已从源证据确认必保的问答、反应或兑现段，可写入 `clip_plan.json.required_evidence`（`nodes` 登记 `id`/`source` 绝对路径/`start`/`end` 源片秒/`track`/`content`，`before` 登记先后顺序）。",
            "- 不同 `source_id` 的相同时间段不算重叠；同一 `source_id` 内避免重复或重叠选段。",
            "- 选片前请查看下方各片源目录（`sources/<source_id>`）中的 `scenes.json`、ASR 与单源简报。",
        ]
    )
    if getattr(args, "target_duration", None):
        lines.append(f"- 目标时长：`{args.target_duration}`。")
    lines += ["", "## 片源列表（Sources）", ""]
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


def write_multi_source_output_brief(
    work_dir, source_records, validated_plan_path, *, tts_provider=None
):
    """Write the Pass-2 multi-source OUTPUT narration brief using shared context & gates."""
    work_dir = Path(work_dir)
    plan = json.loads(Path(validated_plan_path).read_text(encoding="utf-8"))
    source_by_id = {s["source_id"]: s for s in source_records}
    speech_evidence = write_multi_source_output_speech_evidence(
        work_dir, source_records, plan
    )
    research = load_background_research(work_dir)
    provider = _resolve_tts_provider_hint(work_dir, tts_provider=tts_provider)
    example_row = _narration_example_item(
        provider,
        end=4.2,
        text="林砚推开档案室的门，发现结案报告早已被人换掉签名。",
        emotion="紧张",
        include_policy=False,
    )
    lines = [
        "# 多视频成片解说简报（Multi-source Output Narration Brief）",
        "",
        "`edited_source.mp4` 已由多个源视频拼接渲染完成，请按剪后成片的 OUTPUT 时间轴编写 `narration.json`。",
        "",
        "## 更新创作决定",
        "",
        "先查看 `edited_source.mp4` 与下方成片保留片段映射，在 `visual_audio_board.json` 中补齐 OUTPUT 起止时间，并根据实际成片确认每拍的 `audio_owner`、原声锚点与 `narration_job`；若剪后顺序调整了主线推进，同步更新 `recap_story_plan.json`。",
        "",
        "beat 对应关系保留在 `visual_audio_board.json` 中；`narration.json` 只承载时间、文本与朗读参数。",
        "",
    ]
    lines.extend(_missing_research_callout(research))
    lines.extend(_format_background_research(research))
    lines.extend(
        [
            "## narration.json 格式",
            "",
            "```json",
            f"[{example_row}]",
            "```",
            "",
            "注意：`start`/`end` 是剪后成片时间，不是任一源视频时间。",
            "",
            "## 输出时间线写作规则",
            "- `narration.json` 只使用 `edited_source.mp4` 的 OUTPUT 时间线。",
            "- 先为每拍确定 `audio_owner` 与 `narration_job`；旁白与原声比例（如 7:3）不是配额，只在素材本身难以判断声画主次时作粗略参考。",
            "- 对照下方保留片段映射与各片源 `work_dir` 核对人物、台词与前情，只讲背景、筹码与因果潜台词，避免复述画面表象。",
        ]
    )
    lines.extend(_floor_gates_and_emotion_section(provider))
    lines.append("## 成片保留片段映射（output → source）")
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
            "`unverified` 点由粗粒度 ASR 窗口标点估算并吸附到短停顿：不会切断字词，但未必是整句结束（误差上限见 `±`），切入前请试听确认。",
        ]
        for row, use in anchors:
            if use == "verified":
                label = row["confidence"]
            elif "timing_bound_seconds" in row:
                label = f"unverified ±{float(row['timing_bound_seconds']):.1f}s"
            else:
                label = "unverified"
            lines.append(f"- {row['time']:.3f}s [{label}] ({row['source_id']})")
    lines += ["", "## 片源目录（Source work dirs）"]
    for s in source_records:
        lines.append(f"- {s['source_id']}: `{work_dir / s['source_work_dir']}`")
    path = work_dir / "agent_narration_brief.md"
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path
