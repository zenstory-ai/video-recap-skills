"""Build review prompts and normalize model review responses."""

import json


import re

from pathlib import Path


from evidence_bundle import (
    _clip_text,
    _in_ranges,
    _safe_time,
    build_evidence_bundle,
    render_evidence_bundle,
)
from review_grounding import _load_agent_optional

CATEGORIES = [
    "hallucination",
    "weak_hook",
    "no_throughline",
    "narrating_picture",
    "density",
    "pacing",
    "cliche",
    "incomplete",
    "disjoint_handoff",
    "promise_mismatch",
    "low_information_gain",
    "not_write_for_ear",
    "grounding_risk",
    "original_audio_conflict",
    "subtitle_readability",
    "ai_flavor",
    "weak_payoff",
    "style_mismatch",
    "packaging_mismatch",
    "example_entity_leak",
    "other",
]

FACTUAL_CATEGORIES = {"hallucination", "incomplete"}

RUBRIC = """你是中文视频解说的创作复核编辑。依据素材证据和已有创作计划审阅草稿，只指出真实问题，宁缺毋滥：
1. 反幻觉（最重要）：解说里的人物、动作、因果、关系必须由带标签的 evidence 支撑。画面/对白是 timeline evidence（clock=SOURCE 或 OUTPUT）；背景资料/user_context 只能作为 context-only（clock=null）辅助识别/消歧。research-only 不能升级成当前画面强事实；若与 research 一致但画面/对白里看不到，最多 severity=suggestion/category=grounding_risk，不要判 error；只有与全部可得证据矛盾才是 severity=error, category=hallucination，并指出冲突证据。
2. 导演意图：若提供 recap_story_plan.json，检查草稿是否兑现 viewer promise、POV、dramatic question、情绪路径和 chosen_hypothesis；不要另起一条更“吸睛”但不属于该计划的故事。偏离主线 → no_throughline；承诺不兑现 → promise_mismatch/weak_payoff。
3. change-based beats：每个 beat 应改变知识、权力、目标、关系、情绪或风险。精简时不能只留下“发生了什么”，还要保住人物动机、接受条件及随后犹豫/行动的必要前提。若一段只重复上一段、删除后什么都不损失，可报 low_information_gain/pacing；不要用固定段数或秒数代替判断。
4. 钩子：开头要提出正文真实兑现的戏剧问题/利害，不是交代场景，也不是无关的留存话术。弱钩子 → weak_hook。
5. 给信息而非念画面：观众看得见动作表情；解说只增加上下文、因果、预期、证据支持的解释或跨越。复述画面 → narrating_picture。
6. 视听分工：若提供 visual_audio_board.json，检查 narration_job=none 或 audio_owner=original_dialogue/action_sound/ambience/music/silence 的拍是否被旁白无故覆盖；必须听见的原声被盖住 → original_audio_conflict。沉默和低旁白覆盖本身不是问题。这些拍与 original_subtitles 的原声字幕块按计划没有旁白，不是跳过或漏写。
7. 人物、反应与动作兑现：不要用旁白解释掉素材中已经能成立的表演、停顿或反应。有情感回应或知情变化的反应镜不可机械删；反打是否保留取决于它是否提供新增信息，而非是否达到统一时长。以某个可见结果为看点时，只写 evidence 已呈现的结果，不推断更强的结果（例：证据只到受击，就不能写成倒地或胜负）。当前评审只能检查计划/稿件一致性，不能凭少量帧声称最终剪点一定好坏。
8. 密度/节奏与因果边界：7:3 不是配额。只在旁白没有任务、墙到墙压住原声、碎成一句一停，或无意长空档导致因果断裂时，报 density/pacing。不得把跨场镜头拼成同场动作/反应的虚假因果，也不用花字替补源证据或提前宣布结果。
9. 去废词：删空泛形容（"危机四伏""震撼人心"）→ cliche。
10. 完整句子：旁白本身半句话/未收尾 → incomplete。某拍没有旁白不算 incomplete。
11. 段落衔接：解说块要为随后的原声留白铺垫，下一块要承接原声刚呈现的变化；若两块各说各的、原声进来接不上 → disjoint_handoff。
12. 结尾回收：结尾要兑现开头承诺/主线情绪，不要突然停、只复述最后画面、没有情绪/信息回报；弱回收 → weak_payoff。
13. 风格一致性与修改范围：若提供 style_card.json，把它当作表达意图/语气/节奏边界；不符合意图 → style_mismatch。不要把 style_card 当标题/封面/首句包装计划。只提能定位到具体段落、beat 或镜头边界的具体局部修法；REVISION 未点名层默认冻结，不借局部问题重做故事、声音或包装。
14. 包装一致性：只有提供 packaging_plan.json 时才评估标题/封面/首句/卖点承诺与正文兑现；缺失不扣分。不一致 → packaging_mismatch。不要让包装反过来改写故事判断。
15. 去AI味：若出现模板化、空泛拔高、过度对仗、机械转折、明显 agent 示例残留，可报 ai_flavor；若出现示例人物/占位实体泄漏（如未替换示例名、模板角色）→ example_entity_leak。“不是 A，而是 B”本身不是错误，只有在先虚构旧判断再制造假洞察、或反复机械使用时才建议改写。
只返回 JSON（不要额外解释），格式：
{"verdict":"PASS|REVISE|FAIL","summary":"一两句总体判断","findings":[{"segment":<草稿段号(从0起)或null表示整体>,"severity":"error|warning|suggestion","category":"<上面类别之一>","issue":"问题","fix":"具体改法"}]}"""


def merge_review_findings(chunks):
    """Merge chunk review findings deterministically, keeping highest severity."""
    severity_rank = {"error": 3, "warning": 2, "suggestion": 1}
    by_key = {}
    for chunk in chunks:
        for f in chunk["findings"]:
            key = (f["segment"], f["category"], f["issue"])
            old = by_key.get(key)
            if old is None or severity_rank[f["severity"]] > severity_rank[old["severity"]]:
                by_key[key] = dict(f)
    return sorted(
        by_key.values(),
        key=lambda f: (
            f["segment"] is None,
            f["segment"] if f["segment"] is not None else 10**9,
            f["category"],
            f["issue"],
        ),
    )


def _bundle_prompt_size(bundle):
    return len(render_evidence_bundle(bundle))


def _chunk_evidence_bundle(bundle, *, max_items=80, max_chars=12000):
    """Split oversized evidence bundles for actual review calls.

    Chunks are deterministic and range-oriented: selected coverage ranges remain the
    contract, but each chunk carries only the timeline items whose spans overlap that
    range. Context-only research remains advisory and is repeated in each chunk so the
    judge can still use alias/background hints without upgrading them to timeline facts.
    """
    items = list(bundle["items"])
    if len(items) <= max_items and _bundle_prompt_size(bundle) <= max_chars:
        one = dict(bundle)
        one["chunk_index"] = 0
        one["chunk_count"] = 1
        one["metadata"] = dict(bundle["metadata"])
        return [one]
    ranges = bundle["coverage"]["selected_ranges"]
    chunks = []
    used_ids = set()
    for r in ranges:
        r_items = [
            item
            for item in items
            if _in_ranges(
                _safe_time(item, "start"),
                _safe_time(item, "end", _safe_time(item, "start")),
                [r],
            )
        ]
        if not r_items:
            continue
        for start in range(0, len(r_items), max_items):
            part = r_items[start : start + max_items]
            used_ids.update(id(item) for item in part)
            chunk = dict(bundle)
            chunk["items"] = part
            chunk["coverage"] = {**bundle["coverage"], "selected_ranges": [r]}
            chunk["chunk_index"] = len(chunks)
            chunks.append(chunk)
    leftovers = [item for item in items if id(item) not in used_ids]
    for start in range(0, len(leftovers), max_items):
        chunk = dict(bundle)
        chunk["items"] = leftovers[start : start + max_items]
        chunk["coverage"] = {**bundle["coverage"], "selected_ranges": []}
        chunk["chunk_index"] = len(chunks)
        chunks.append(chunk)
    if not chunks:
        chunk = dict(bundle)
        chunk["items"] = []
        chunk["chunk_index"] = 0
        chunks = [chunk]
    count = len(chunks)
    for chunk in chunks:
        chunk["chunk_count"] = count
        chunk["metadata"] = {**bundle["metadata"], "chunked_review": count > 1}
    return chunks


def _merge_chunk_reviews(chunk_reviews):
    if not chunk_reviews:
        return parse_review_response("")
    if len(chunk_reviews) == 1:
        return chunk_reviews[0]
    verdict_rank = {"PASS": 0, "REVISE": 1, "FAIL": 2}
    best = max(chunk_reviews, key=lambda r: verdict_rank[r["verdict"]])
    merged = dict(best)
    merged["findings"] = merge_review_findings(chunk_reviews)
    if any(f["severity"] == "error" for f in merged["findings"]):
        merged["verdict"] = "FAIL" if best["verdict"] == "FAIL" else "REVISE"
    summaries = [r["summary"] for r in chunk_reviews if r["summary"]]
    merged["summary"] = summaries[0] if summaries else best["summary"]
    merged["chunked_review"] = {
        "chunk_count": len(chunk_reviews),
        "findings_before_merge": sum(len(r["findings"]) for r in chunk_reviews),
        "findings_after_merge": len(merged["findings"]),
    }
    return merged


def _load_review_research_context(work_dir):
    """Agent-authored background_research.json: {} when absent; corrupt JSON raises
    (same policy as the brief's loader), a non-object document fails open to {}."""
    path = Path(work_dir) / "background_research.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _format_review_research_context(research, limit=1200):
    """Compact background_research.json for the quality reviewer.

    Background research is rendered as context-only/advisory: it can help aliases and
    disambiguation, but it is not timeline grounding for current visual/ASR facts.
    """
    if not isinstance(research, dict) or not research:
        return ""
    lines = []
    for key, label in (
        ("synopsis", "Synopsis"),
        ("episode_context", "Episode context"),
        ("worldbuilding", "Worldbuilding"),
    ):
        value = _clip_text(research.get(key), 500)
        if value:
            lines.append(f"- {label}: {value}")

    characters = research.get("characters")
    if isinstance(characters, dict) and characters:
        lines.append("- Characters:")
        for name, desc in list(characters.items())[:12]:
            clean_name = _clip_text(name, 60)
            clean_desc = _clip_text(desc, 160)
            if clean_name:
                lines.append(f"    - {clean_name}：{clean_desc}")

    details = research.get("character_details")
    if isinstance(details, dict) and details:
        lines.append("- Character details:")
        for name, info in list(details.items())[:8]:
            if not isinstance(info, dict):
                continue
            bits = []
            aliases = info.get("aliases")
            if isinstance(aliases, list) and aliases:
                bits.append(
                    "别名 "
                    + "/".join(
                        _clip_text(alias, 40)
                        for alias in aliases[:4]
                        if _clip_text(alias, 40)
                    )
                )
            role = _clip_text(info.get("role"), 80)
            if role:
                bits.append(role)
            rels = info.get("relationships")
            if isinstance(rels, list) and rels:
                bits.append(
                    "；".join(
                        _clip_text(rel, 80) for rel in rels[:4] if _clip_text(rel, 80)
                    )
                )
            clean_name = _clip_text(name, 60)
            if clean_name and bits:
                lines.append(f"    - {clean_name}：{'；'.join(bits)}")

    arcs = research.get("plot_arcs")
    if isinstance(arcs, list) and arcs:
        lines.append("- Plot arcs:")
        for arc in arcs[:8]:
            if isinstance(arc, dict):
                name = _clip_text(arc.get("name"), 80)
                desc = _clip_text(arc.get("description"), 180)
                status = _clip_text(arc.get("status"), 40)
                if name or desc:
                    tail = f" [{status}]" if status else ""
                    lines.append(f"    - {name}：{desc}{tail}")
            else:
                val = _clip_text(arc, 180)
                if val:
                    lines.append(f"    - {val}")

    text = "\n".join(lines).strip()
    return text[:limit]


def _load_optional_json(work_dir, name):
    if work_dir is None:
        return None
    return _load_agent_optional(Path(work_dir), name)


def _format_json_context(title, value, limit=3000):
    if value is None:
        return f"## {title}\n(无)"
    text = json.dumps(value, ensure_ascii=False, indent=2)
    return f"## {title}\n{text[:limit]}"


# audio_owner values that leave a beat to the source track (playbook: visual_audio_board).
ORIGINAL_AUDIO_OWNERS = frozenset(
    ("original_dialogue", "action_sound", "ambience", "music", "silence")
)
_MAX_ORIGINAL_AUDIO_LINES = 40


def _beat_window(beat, clock):
    """(label, start, end) on `clock` when the beat carries it, else on the other clock."""
    for name in (clock, "source" if clock == "output" else "output"):
        start, end = beat.get(f"{name}_start"), beat.get(f"{name}_end")
        if (
            isinstance(start, (int, float))
            and isinstance(end, (int, float))
            and not isinstance(start, bool)
            and not isinstance(end, bool)
            and end > start
        ):
            return name.upper(), float(start), float(end)
    return None


def _original_audio_beats(board, plan):
    """Beats the plan leaves to the source track: audio_owner is original audio or
    narration_job is none. visual_audio_board owns audio decisions, so a story-plan beat
    counts only when the board does not list the same beat_id."""
    board_items = board.get("items") if isinstance(board, dict) else None
    plan_beats = plan.get("beats") if isinstance(plan, dict) else None
    board_items = [b for b in board_items or [] if isinstance(b, dict)]
    board_ids = {str(b.get("beat_id")) for b in board_items if b.get("beat_id") is not None}
    candidates = board_items + [
        b
        for b in plan_beats or []
        if isinstance(b, dict) and str(b.get("beat_id")) not in board_ids
    ]
    return [
        beat
        for beat in candidates
        if str(beat.get("audio_owner", "")).strip() in ORIGINAL_AUDIO_OWNERS
        or str(beat.get("narration_job", "")).strip() == "none"
    ]


def _format_original_audio_holds(board, plan, subtitles, clock):
    """Name every interval the plan deliberately leaves without narration.

    The planning JSON is clipped to a few thousand characters, so without this list a
    reviewer can see a story beat with no narration and report it as skipped.
    """
    lines = []
    for beat in _original_audio_beats(board, plan):
        window = _beat_window(beat, clock)
        when = f"{window[0]} {window[1]:.1f}-{window[2]:.1f}s" if window else "时间未标"
        owner = str(beat.get("audio_owner", "")).strip() or "?"
        job = str(beat.get("narration_job", "")).strip() or "?"
        anchor = _clip_text(
            beat.get("original_audio_anchor") or beat.get("must_keep_moment"), 60
        )
        tail = f" 原声锚点：{anchor}" if anchor else ""
        lines.append(
            f"- [{when}] beat {beat.get('beat_id', '?')} audio_owner={owner} narration_job={job}{tail}"
        )
    for row in subtitles if isinstance(subtitles, list) else []:
        if not isinstance(row, dict):
            continue
        start, end = _safe_time(row, "start", None), _safe_time(row, "end", None)
        text = _clip_text(row.get("text"), 60)
        if start is None or end is None or end <= start or not text:
            continue
        lines.append(f"- [{clock.upper()} {start:.1f}-{end:.1f}s] 原声字幕块「{text}」")
    if not lines:
        return ""
    dropped = len(lines) - _MAX_ORIGINAL_AUDIO_LINES
    lines = lines[:_MAX_ORIGINAL_AUDIO_LINES]
    if dropped > 0:
        lines.append(f"- …另有 {dropped} 条同类区间未列出")
    return (
        "## 计划内留给原声的区间（不是漏写）\n"
        "以下拍在 visual_audio_board/recap_story_plan 里由原声或沉默拥有，或是 original_subtitles 的原声字幕块。"
        "这些区间没有旁白是有意的：不要报为跳过、缺失或漏写，也不要据此给 incomplete、no_throughline、"
        "low_information_gain 或 density。只有旁白闯入并盖住必须听见的原声时报 original_audio_conflict；"
        "相邻旁白块与这段原声接不上时报 disjoint_handoff。\n" + "\n".join(lines)
    )


def _format_draft(narration):
    lines = []
    for i, seg in enumerate(narration or []):
        if not isinstance(seg, dict):
            continue
        start = seg.get("start", 0)
        end = seg.get("end", 0)
        text = str(seg.get("narration", "")).strip()
        overlap = seg.get("overlaps_speech")
        tag = "" if overlap is None else (" [盖原声]" if overlap else " [静音槽]")
        lines.append(f"{i}. [{float(start):.1f}-{float(end):.1f}s]{tag} {text}")
    return "\n".join(lines)


def build_review_messages(
    narration,
    vlm_analysis,
    asr_result,
    work_dir=None,
    research_context=None,
    evidence_bundle=None,
):
    """Pure: assemble the reviewer chat messages (testable without the API)."""
    draft = _format_draft(narration)
    research_obj = (
        _load_review_research_context(work_dir) if work_dir is not None else {}
    )
    if research_context is None:
        research_context = _format_review_research_context(research_obj)
    bundle = evidence_bundle or build_evidence_bundle(
        vlm_analysis,
        asr_result,
        narration,
        timeline="cut_output"
        if any("source_start" in x for x in (vlm_analysis or []) if isinstance(x, dict))
        else "source",
        research=research_obj,
    )
    evidence_text = render_evidence_bundle(bundle)
    # Optional, agent-authored planning artifacts. Bad JSON returns None via _load, matching
    # existing fail-open optional artifact behavior; these only sharpen the craft findings.
    packaging = _load_optional_json(work_dir, "packaging_plan.json")
    story_plan = _load_optional_json(work_dir, "recap_story_plan.json")
    av_board = _load_optional_json(work_dir, "visual_audio_board.json")
    style_card = _load_optional_json(work_dir, "style_card.json")
    holds = _format_original_audio_holds(
        av_board,
        story_plan,
        _load_optional_json(work_dir, "original_subtitles.json"),
        bundle["clock"],
    )
    holds_block = f"{holds}\n\n" if holds else ""
    user = (
        f"{RUBRIC}\n\n"
        "## 创作计划参考\n"
        "若 work_dir 提供了 packaging_plan/recap_story_plan/visual_audio_board/style_card，则结合评审："
        "packaging_plan 只负责标题/封面/首句/卖点承诺与正文兑现；style_card 只负责表达意图、语气、节奏和禁忌；"
        "recap_story_plan 是导演意图/备选假设/chosen POV/change-based beats 的基线；visual_audio_board 是画面/表演/原声/audio_owner/narration_job/剪辑锚点的基线。"
        "若未提供，则基于解说本身与画面/对白证据评审，但不得因计划文件缺失给 error。统一评估：hook 是否真实兑现；每段是否产生变化/信息增量而非看图说话；"
        "结尾是否兑现开头承诺/主线情绪；是否写给耳朵听（连续完整思路、自然口语、TTS可呼吸，而非一句一停）；人物/关系/因果断言是否有 visual/ASR timeline evidence；research/user_context 仅可作为 context-only 辅助。\n"
        "审美/风格/包装/去AI味项是 advisory：可 REVISE，但除事实矛盾/残句外不要给 error。\n\n"
        f"{_format_json_context('packaging_plan.json（标题/封面/首句/卖点包装承诺，可能为空）', packaging)}\n\n"
        f"{_format_json_context('recap_story_plan.json（主线/beats/original moments，可能为空）', story_plan)}\n\n"
        f"{_format_json_context('visual_audio_board.json（画面/原声/字幕/剪辑锚点，可能为空）', av_board)}\n\n"
        f"{_format_json_context('style_card.json（表达意图/语气/节奏/禁忌，不负责包装，可能为空）', style_card)}\n\n"
        f"{holds_block}"
        f"## 背景资料（context-only/advisory：只辅助识别/消歧/弱背景，不是当前画面强事实）\n"
        f"Guardrail: clock=null/context_only；不得把未来剧情或 research-only 关系/因果升级为当前事实。\n"
        f"{research_context or '(无)'}\n\n"
        f"{evidence_text}\n\n"
        f"## 解说草稿（共 {len([s for s in (narration or []) if isinstance(s, dict)])} 段）\n{draft or '(空)'}\n"
    )
    return [{"role": "user", "content": user}]


def parse_review_response(text):
    """Pure: robustly extract the reviewer JSON; fall back to a REVISE-unknown shell."""
    raw = str(text or "")
    candidate = raw
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fence:
        candidate = fence.group(1)
    else:
        first, last = raw.find("{"), raw.rfind("}")
        if first != -1 and last > first:
            candidate = raw[first : last + 1]
    try:
        data = json.loads(candidate)
    except ValueError:
        # Same shape as a parsed review so every consumer reads one contract.
        return {
            **_normalise_review({}),
            "summary": "评审输出无法解析为 JSON，请人工检查。",
            "parse_error": True,
            "raw": raw[:2000],
        }
    return _normalise_review(data)


def _normalise_review(data):
    verdict = str(data.get("verdict", "REVISE")).upper()
    # The prompt asks for PASS/REVISE/FAIL only; a stray "OK" from the judge means approval.
    if verdict == "OK":
        verdict = "PASS"
    if verdict not in ("PASS", "REVISE", "FAIL"):
        verdict = "REVISE"
    findings = []
    for f in data.get("findings", []) or []:
        if not isinstance(f, dict):
            continue
        sev = str(f.get("severity", "warning")).lower()
        if sev not in ("error", "warning", "suggestion"):
            sev = "warning"
        cat = str(f.get("category", "other")).lower()
        if cat not in CATEGORIES:
            cat = "other"
        # Only factual defects may gate strict mode (severity=error). Craft findings
        # (weak_hook, narrating_picture, cliche, disjoint_handoff, ...) are advisory:
        # clamp them to at most "warning" so they never block on subjective judgement.
        if cat not in FACTUAL_CATEGORIES and sev == "error":
            sev = "warning"
        findings.append(
            {
                "segment": f.get("segment"),
                "severity": sev,
                "category": cat,
                "issue": str(f.get("issue", "")).strip(),
                "fix": str(f.get("fix", "")).strip(),
            }
        )
    return {
        "verdict": verdict,
        "summary": str(data.get("summary", "")).strip(),
        "findings": findings,
    }


def _segment_label(segment):
    """The draft lists blocks from 0 (`segment` keeps that index); readers see 段 N from 1."""
    if segment is None:
        return "整体"
    if isinstance(segment, str) and segment.strip().isdigit():
        segment = int(segment)
    elif isinstance(segment, float) and segment.is_integer():
        segment = int(segment)
    if isinstance(segment, int) and not isinstance(segment, bool) and segment >= 0:
        return f"段 {segment + 1}"
    # Any other shape is not a block index; show the raw value, never as a 段 number.
    return f"段 ?（模型返回 {json.dumps(segment, ensure_ascii=False, default=str)}）"


def format_review_md(review):
    """Render a parse_review_response() review as markdown."""
    order = {"error": 0, "warning": 1, "suggestion": 2}
    findings = sorted(review["findings"], key=lambda f: order[f["severity"]])
    counts = {
        s: sum(1 for f in findings if f["severity"] == s)
        for s in ("error", "warning", "suggestion")
    }
    out = [
        "# Narration review",
        "",
        f"Verdict: **{review['verdict']}**  "
        f"(errors {counts['error']}, warnings {counts['warning']}, suggestions {counts['suggestion']})",
        "",
        review["summary"] or "_(no summary)_",
        "",
        "## Findings",
    ]
    if not findings:
        out.append("- (none)")
    for f in findings:
        seg = _segment_label(f["segment"])
        out.append(f"- **[{f['severity']}/{f['category']}] {seg}** — {f['issue']}")
        if f["fix"]:
            out.append(f"  - 改法: {f['fix']}")
    return "\n".join(out) + "\n"
