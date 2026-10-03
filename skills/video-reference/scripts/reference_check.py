"""R1–R8: mechanically keep source facts out of the transferable production reference.

`check` validates the agent's single file (reference_breakdown.json) against the cached
measurements and the optional understanding artifacts, computes `derived` in memory, and
returns {derived, errors, warnings}. `export` writes production_reference.json only when there
are zero errors, then re-scans the serialized result (R8).
"""
import json
import math
import re
from pathlib import Path

from lib import read_json, write_json
from reference_measure import EDGE_S, MEASUREMENTS_FILE
from reference_profile import (
    CUT_MATCH_S, OWNERS, apply_cut_fixes, build_production, derive, is_resolved, resolve)

BREAKDOWN_FILE = "reference_breakdown.json"
BREAKDOWN_SCHEMA = "video-reference.breakdown.v1"
DIMENSIONS = ("narrative_structure", "pacing", "shots_editing", "narration_subtitles", "audio_visual")
APPLIES_TO = ("story_plan", "visual_audio_board", "clip_plan", "narration", "style_card")
FUNCTIONS = ("hook", "setup", "turn", "escalation", "payoff")
NARRATION_JOBS = ("context", "causal_link", "foreshadow", "interpretation", "transition")
TARGET_ROOTS = ("shots", "loudness", "derived")
# Objects a target may export whole; every other target must be a numeric leaf. No list indexing,
# and first_original_at is exported as {fraction} only (its absolute `s` stays local).
_TARGET_OBJECTS = re.compile(
    r"derived\.(?:structure|first_original_at|narration_jobs|by_owner\.[a-z_]+|by_section\.[a-z]+)")
GAP_S = 0.5
T_EPSILON_S = 0.05
CJK_RUN_MIN = 8
LATIN_RUN_MIN = 5

_KEYS = {
    "top": {"schema", "labels", "source_facts", "methods", "skipped_dimensions"},
    "labels": {"audio_spans", "sections", "subtitles", "basis", "cut_fixes"},
    "cut_fixes": {"add", "remove"},
    "span": {"start", "end", "owner", "narration_job"},
    "section": {"start", "end", "function"},
    "subtitles": {"burned", "max_lines", "marks_original", "evidence_t"},
    "fact": {"id", "dimension", "statement", "t", "measure", "entities"},
    "method": {"id", "dimension", "rule", "applies_when", "avoid_when", "applies_to", "evidence", "targets"},
    "target": {"from"},
}
_REQUIRED = {
    "top": {"schema", "labels"},
    "labels": {"audio_spans", "sections"},
    "span": {"start", "end", "owner"},
    "section": {"start", "end", "function"},
    "subtitles": {"burned", "max_lines"},
    "fact": {"id", "dimension", "statement", "entities"},
    "method": {"id", "dimension", "rule", "applies_to", "evidence"},
    "target": {"from"},
}
METHOD_TEXT_FIELDS = ("rule", "applies_when", "avoid_when")
BANNED_EXPORT_KEYS = {"source_facts", "labels", "entities", "evidence", "statement", "from", "path"}
_CJK = re.compile(r"[㐀-鿿豈-﫿]+")
_WORD = re.compile(r"[A-Za-z0-9']+")
# Python's \b treats CJK as word characters, so "在1:23处" has no boundary before "1"; use
# digit/ASCII lookarounds instead. Path segments are ASCII so prose like "旁白/原声/音乐" stays legal.
_NUMERAL = "[0-9一二三四五六七八九十百零两]+"
_TIMECODE = re.compile(
    rf"(?<![\d:])\d{{1,2}}:\d{{2}}(?::\d{{2}})?(?![\d:])|第\s*{_NUMERAL}\s*秒|{_NUMERAL}\s*分钟?\s*{_NUMERAL}\s*秒")
# One-segment absolute paths only for well-known roots, so prose like "原声/BGM" stays legal.
_ABS_PATH = re.compile(
    r"(?<![A-Za-z0-9_.])/(?:[A-Za-z0-9_.-]+/)+[\w.-]*|~/[\w.-]+|(?<![A-Za-z0-9_])[A-Za-z]:[\\/][\w.-]+"
    r"|(?<![A-Za-z0-9_.])/(?:tmp|Users|home|var|private|mnt|Volumes|opt|srv|root|data)(?![\w.-])")
_SPACE = re.compile(r"\s+")
# Short quoted terms in background research: titles, places, organisations, quoted lines.
_QUOTED = re.compile(r"《([^》]{2,8})》|「([^」]{2,8})」|“([^”]{2,8})”|‘([^’]{2,8})’|'([^'\s]{2,8})'")
_NUMBER_IN_RULE = re.compile(r"\d+(?:\.\d+)?\s*(?:秒|%|％|字)")


def _num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _closed(obj, kind, where, errors):
    """R1 closed key set + required keys; returns True when `obj` is a dict."""
    if not isinstance(obj, dict):
        errors.append(f"R1 {where}: 必须是对象")
        return False
    for key in sorted(set(obj) - _KEYS[kind]):
        errors.append(f"R1 {where}: 不允许的键 {key!r}")
    for key in sorted(_REQUIRED.get(kind, set()) - set(obj)):
        errors.append(f"R1 {where}: 缺少键 {key!r}")
    return True


def _timed(items, kind, enum_key, enum, errors):
    """R1 for spans/sections; returns the well-formed items for R2 and derive()."""
    good = []
    for i, item in enumerate(items if isinstance(items, list) else []):
        where = f"labels.{'audio_spans' if kind == 'span' else 'sections'}[{i}]"
        if not _closed(item, kind, where, errors):
            continue
        ok = _num(item.get("start")) and _num(item.get("end"))
        if not ok:
            errors.append(f"R1 {where}: start/end 必须是数字")
        if item.get(enum_key) not in enum:
            errors.append(f"R1 {where}: {enum_key} 必须是 {'|'.join(enum)}，不是 {item.get(enum_key)!r}")
            ok = False
        job = item.get("narration_job")
        if "narration_job" in item and (item.get("owner") != "narration" or job not in NARRATION_JOBS):
            errors.append(f"R1 {where}: narration_job 只用于 narration 段，取值 {'|'.join(NARRATION_JOBS)}")
        if ok:
            good.append(item)
    return good


def _check_cover(items, name, duration, errors):
    """R2: sorted, no overlap, gaps <= 0.5 s, covering [0, duration] within 0.5 s."""
    if not items:
        errors.append(f"R2 labels.{name}: 不能为空")
        return
    for i, item in enumerate(items):
        if item["start"] >= item["end"]:
            errors.append(f"R2 labels.{name}[{i}]: start 必须小于 end")
        if item["end"] > duration + GAP_S:
            errors.append(f"R2 labels.{name}[{i}]: end 超出时长 {duration}")
        if i:
            prev = items[i - 1]
            if item["start"] < prev["end"] - 1e-6:
                errors.append(f"R2 labels.{name}[{i}]: 与上一段重叠或未按时间排序")
            elif item["start"] - prev["end"] > GAP_S:
                errors.append(f"R2 labels.{name}[{i}]: 与上一段间隙 {item['start'] - prev['end']:.2f}s > {GAP_S}s")
    if items[0]["start"] > GAP_S or items[-1]["end"] < duration - GAP_S:
        errors.append(f"R2 labels.{name}: 必须覆盖 [0, {duration}]（首尾容差 {GAP_S}s）")


def _check_cut_fixes(fixes, cuts, duration, errors):
    """R1/R2: removals name a detected cut, additions a cut the detector did not report."""
    if not _closed(fixes, "cut_fixes", "labels.cut_fixes", errors):
        return
    for key in ("add", "remove"):
        values = fixes.get(key, [])
        if not isinstance(values, list) or not all(_num(v) for v in values):
            errors.append(f"R1 labels.cut_fixes.{key}: 必须是秒数列表")
            continue
        for t in values:
            near = any(abs(t - c) <= CUT_MATCH_S for c in cuts)
            if not EDGE_S < t < duration - EDGE_S:
                errors.append(f"R2 labels.cut_fixes.{key}: {t} 不在 (0, {duration}) 内")
            elif key == "remove" and not near:
                errors.append(f"R2 labels.cut_fixes.remove: {t} 附近 ±{CUT_MATCH_S}s 内没有测得的切点")
            elif key == "add" and near:
                errors.append(f"R2 labels.cut_fixes.add: {t} 已是测得的切点（±{CUT_MATCH_S}s）")


def _check_labels(labels, duration, errors, cuts=()):
    if not _closed(labels, "labels", "labels", errors):
        return [], []
    spans = _timed(labels.get("audio_spans"), "span", "owner", OWNERS, errors)
    sections = _timed(labels.get("sections"), "section", "function", FUNCTIONS, errors)
    _check_cover(spans, "audio_spans", duration, errors)
    _check_cover(sections, "sections", duration, errors)
    if "cut_fixes" in labels:
        _check_cut_fixes(labels["cut_fixes"], cuts, duration, errors)
    subtitles = labels.get("subtitles")
    if subtitles is not None and _closed(subtitles, "subtitles", "labels.subtitles", errors):
        where = "R1 labels.subtitles"
        if "burned" in subtitles and not isinstance(subtitles["burned"], bool):
            errors.append(f"{where}.burned: 必须是 true/false")
        lines = subtitles.get("max_lines")
        if "max_lines" in subtitles and (not isinstance(lines, int) or isinstance(lines, bool) or lines < 1):
            errors.append(f"{where}.max_lines: 必须是 ≥1 的整数")
        if "marks_original" in subtitles and not isinstance(subtitles["marks_original"], str):
            errors.append(f"{where}.marks_original: 必须是字符串")
        evidence_t = subtitles.get("evidence_t", [])
        if not isinstance(evidence_t, list):
            errors.append(f"{where}.evidence_t: 必须是数字列表")
            evidence_t = []
        for t in evidence_t:
            if not _num(t) or not 0 <= t <= duration:
                errors.append(f"R2 labels.subtitles.evidence_t: {t!r} 不在 [0, {duration}] 内")
    return spans, sections


def _check_ids(items, prefix, where, errors):
    seen = set()
    for i, item in enumerate(items):
        ident = item.get("id")
        if not isinstance(ident, str) or not re.fullmatch(rf"{prefix}\d+", ident):
            errors.append(f"R1 {where}[{i}]: id 必须形如 {prefix}1")
        elif ident in seen:
            errors.append(f"R1 {where}[{i}]: id {ident} 重复")
        seen.add(ident)
    return seen


def _anchor_ok(path, measurements, derived):
    """A `measure` anchor: resolves under shots/loudness/derived and is not a string leaf."""
    if not isinstance(path, str) or path.split(".")[0] not in TARGET_ROOTS:
        return False
    value = resolve(path, measurements, derived)
    return is_resolved(value) and not isinstance(value, str)


def _check_facts(facts, measurements, derived, duration, errors):
    """R1 + R3: every fact is anchored to a time range or a resolvable measurement."""
    for fact in facts:
        where = f"source_facts.{fact.get('id')}"
        if fact.get("dimension") not in DIMENSIONS:
            errors.append(f"R1 {where}: dimension 必须是 {'|'.join(DIMENSIONS)}")
        if not isinstance(fact.get("statement"), str) or not fact.get("statement").strip():
            errors.append(f"R1 {where}: statement 不能为空")
        if not isinstance(fact.get("entities"), list) or not all(isinstance(e, str) for e in fact.get("entities", [])):
            errors.append(f"R3 {where}: 必须显式写 entities 列表（可为空）")
        if ("t" in fact) == ("measure" in fact):
            errors.append(f"R3 {where}: t 与 measure 必须二选一")
        elif "t" in fact:
            t = fact["t"]
            if not (isinstance(t, list) and len(t) == 2 and all(_num(x) for x in t)
                    and 0 <= t[0] < t[1] <= duration + T_EPSILON_S):
                errors.append(f"R3 {where}: t 必须是 [a, b] 且 0 ≤ a < b ≤ {duration}")
        else:
            paths = fact["measure"]
            if not isinstance(paths, list) or not paths:
                errors.append(f"R3 {where}: measure 必须是非空路径列表")
            for path in paths if isinstance(paths, list) else []:
                if not _anchor_ok(path, measurements, derived):
                    errors.append(f"R3 {where}: 测量路径 {path!r} 无法解析到 shots/loudness/derived 下的测量值")


def _target_ok(path, value):
    if not isinstance(path, str) or not is_resolved(value) or value is None:
        return False
    parts = path.split(".")
    if parts[0] not in TARGET_ROOTS or any(p.isdigit() for p in parts) or path == "derived.first_original_at.s":
        return False
    return _num(value) or _TARGET_OBJECTS.fullmatch(path) is not None


def _check_methods(methods, fact_ids, measurements, derived, errors):
    """R1 + R4 + R5: closed method shape, real evidence, targets only name measurement paths."""
    for method in methods:
        where = f"methods.{method.get('id')}"
        if method.get("dimension") not in DIMENSIONS:
            errors.append(f"R1 {where}: dimension 必须是 {'|'.join(DIMENSIONS)}")
        if method.get("applies_to") not in APPLIES_TO:
            errors.append(f"R1 {where}: applies_to 必须是 {'|'.join(APPLIES_TO)}")
        for field in METHOD_TEXT_FIELDS:
            if field in method and (not isinstance(method[field], str) or not method[field].strip()):
                errors.append(f"R1 {where}: {field} 必须是非空字符串")
        evidence = method.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"R4 {where}: evidence 至少一条")
        for item in evidence if isinstance(evidence, list) else []:
            ok = item in fact_ids or (
                isinstance(item, str) and item.startswith("measure:")
                and _anchor_ok(item[len("measure:"):], measurements, derived))
            if not ok:
                errors.append(f"R4 {where}: evidence {item!r} 既不是已有 fact id，也不是可解析的 measure:路径")
        targets = method.get("targets", {})
        if not isinstance(targets, dict):
            errors.append(f"R1 {where}: targets 必须是对象")
            continue
        for name, target in targets.items():
            twhere = f"{where}.targets.{name}"
            if not re.fullmatch(r"[a-z][a-z0-9_]*", str(name)):
                errors.append(f"R1 {twhere}: 名字只用小写字母、数字和下划线")
            if not _closed(target, "target", twhere, errors):
                continue
            if set(target) & {"value", "provenance"}:
                errors.append(f"R5 {twhere}: 数值与 provenance 由 export 填写，只写 from")
            path = target.get("from")
            if "from" in target and not _target_ok(path, resolve(path, measurements, derived)):
                errors.append(f"R5 {twhere}: from {path!r} 必须是 shots/loudness/derived 下的数值叶子或允许的派生对象")


def _ngrams(text, size):
    """CJK n-grams per punctuation-free run, plus over all CJK joined (so「你可知道，我是…」still matches)."""
    runs = _CJK.findall(text)
    grams = set()
    for run in [*runs, "".join(runs)]:
        grams.update(run[i:i + size] for i in range(len(run) - size + 1))
    return grams


def _word_grams(text, size):
    words = [w.lower() for w in _WORD.findall(text)]
    return {tuple(words[i:i + size]) for i in range(len(words) - size + 1)}


def _mention_names(value):
    """Names one aliases/asr_mentions entry contributes.

    The model writes plain strings; video-understanding's deterministic ASR/research fallback writes
    {text, evidence_id, matched_aliases} dicts, whose names are the matched aliases (the text is the
    ASR window, already in the source-text corpus). Anything else contributes nothing."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        matched = value.get("matched_aliases")
        return [m for m in matched if isinstance(m, str)] if isinstance(matched, list) else []
    return []


def _index_names(index):
    """name / aliases / asr_mentions of characters, entities and research_glossary in understanding_index.json."""
    names = set()
    for key in ("characters", "entities", "research_glossary"):
        items = index.get(key) if isinstance(index, dict) else None
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict):
                if isinstance(item.get("name"), str):
                    names.add(item["name"])
                for alias_key in ("aliases", "asr_mentions"):
                    values = item.get(alias_key)
                    for value in values if isinstance(values, list) else []:
                        names.update(_mention_names(value))
    return names


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)


def _string_items(values):
    """The string entries of a JSON list; a non-list or a non-string entry contributes nothing."""
    return [value for value in values if isinstance(value, str)] if isinstance(values, list) else []


def _research_names(research):
    """Character names and aliases, short cultural-note items, and short quoted terms.

    background_research.json is agent-written: a name or alias that is not a string (a nested
    object, a list) is ignored rather than fed to the name set."""
    names = set()
    if not isinstance(research, dict):
        return names
    characters = research.get("characters")
    if isinstance(characters, dict):
        names.update(characters)
    elif isinstance(characters, list):
        names.update(_string_items([c.get("name") for c in characters if isinstance(c, dict)]))
    details = research.get("character_details")
    for name, info in details.items() if isinstance(details, dict) else []:
        names.add(name)
        names.update(_string_items(info.get("aliases") if isinstance(info, dict) else None))
    notes = research.get("cultural_notes")
    for note in notes if isinstance(notes, list) else []:
        item = note.get("item") if isinstance(note, dict) else None
        if isinstance(item, str) and _CJK.fullmatch(item) and len(item) <= 5:
            names.add(item)
    for text in _strings(research):
        names.update(g for match in _QUOTED.findall(text) for g in match if g)
    return names


def _grams(texts):
    return (set().union(*(_ngrams(t, CJK_RUN_MIN) for t in texts)) if texts else set(),
            set().union(*(_word_grams(t, LATIN_RUN_MIN) for t in texts)) if texts else set())


def leak_corpus(facts, asr_segments, asr_evidence, research, index=None):
    """Entity names and source text that must never reach a transferable method.

    Source text is the ASR (each window and each adjacent pair joined, so a line split by a window
    boundary still matches) plus the background research prose; the agent's own fact statements
    are kept apart so an error can say which side a shared phrase came from.
    """
    names = {e for f in facts for e in (f.get("entities") or []) if isinstance(e, str)}
    names.update(_index_names(index))
    research = research if isinstance(research, dict) else {}
    asr_evidence = asr_evidence if isinstance(asr_evidence, dict) else {}
    names.update(_research_names(research))
    glossary = asr_evidence.get("glossary")
    names.update(_string_items(glossary.get("names") if isinstance(glossary, dict) else None))
    windows = sorted((s for s in asr_segments or [] if isinstance(s, dict)),
                     key=lambda s: float(s.get("start") or 0))
    texts = [str(s.get("text") or "") for s in windows]
    texts += [a + b for a, b in zip(texts, texts[1:])]
    texts += list(_strings(research))
    cjk, latin = _grams(texts)
    fact_cjk, fact_latin = _grams([str(f.get("statement") or "") for f in facts])
    return {
        "names": sorted({_SPACE.sub("", n) for n in names if isinstance(n, str) and len(_SPACE.sub("", n)) >= 2},
                        key=len, reverse=True),
        "cjk": cjk, "latin": latin, "fact_cjk": fact_cjk - cjk, "fact_latin": fact_latin - latin,
    }


def leak_errors(text, where, corpus, rule="R6"):
    """R6 a–d on one string; each error quotes the offending substring."""
    errors = []
    lowered = _SPACE.sub("", text).lower()
    for name in corpus["names"]:
        if name.lower() in lowered:
            errors.append(f"{rule} {where}: 含原片实体名「{name}」")
    cjk, words = _ngrams(text, CJK_RUN_MIN), _word_grams(text, LATIN_RUN_MIN)
    for gram in sorted(cjk & corpus["cjk"])[:1]:
        errors.append(f"{rule} {where}: 与原片台词/背景资料共有连续 {CJK_RUN_MIN}+ 字「{gram}」")
    for gram in sorted(words & corpus["latin"])[:1]:
        errors.append(f"{rule} {where}: 与原片台词/背景资料共有连续 {LATIN_RUN_MIN}+ 词「{' '.join(gram)}」")
    for gram in sorted(cjk & corpus.get("fact_cjk", set()))[:1]:
        errors.append(f"{rule} {where}: 与 source_facts 的描述共有连续 {CJK_RUN_MIN}+ 字「{gram}」"
                      "（若这是通用剪辑措辞，改写 fact 或方法任一侧）")
    for gram in sorted(words & corpus.get("fact_latin", set()))[:1]:
        errors.append(f"{rule} {where}: 与 source_facts 的描述共有连续 {LATIN_RUN_MIN}+ 词「{' '.join(gram)}」")
    for match in _TIMECODE.finditer(text):
        errors.append(f"{rule} {where}: 含绝对时间「{match.group(0)}」，改用相对位置或 targets")
    for match in _ABS_PATH.finditer(text):
        errors.append(f"{rule} {where}: 含绝对路径「{match.group(0)}」")
    return errors


def _warnings(breakdown, methods, asr_segments, asr_status, research, *, index, asr_evidence, source,
              review_windows=None):
    warnings = []
    for method in methods:
        if not method.get("applies_when"):
            warnings.append(f"methods.{method.get('id')}: 没写 applies_when，后续制作难判断何时套用")
        if _NUMBER_IN_RULE.search(str(method.get("rule") or "")):
            warnings.append(f"methods.{method.get('id')}: rule 正文含数字，建议把数值放进 targets")
    if asr_status != "AVAILABLE_COARSE":
        warnings.append(f"ASR 状态为 {asr_status}，旁白语速与泄漏扫描的台词覆盖都不完整")
    has_cjk = any(_CJK.search(str(s.get("text") or "")) for s in asr_segments if isinstance(s, dict))
    if has_cjk and not ((_research_names(research) | _index_names(index)) - {None, ""}):
        warnings.append("ASR 有中文对白，但 background_research.json 与 understanding_index.json 都没有给出名字："
                        "泄漏扫描只认 fact entities 里写到的名字，台词里其他人名、地名、组织名会漏网")
    asr_video = (asr_evidence if isinstance(asr_evidence, dict) else {}).get("source_video")
    if isinstance(asr_video, dict) and {k: asr_video.get(k) for k in ("size", "mtime_ns")} != {
            k: source.get(k) for k in ("size", "mtime_ns")}:
        warnings.append("asr_timing_evidence.json 的 source_video 与测量的成片身份不一致：理解产物可能来自另一部视频，"
                        "语速与泄漏扫描会用错台词和人名")
    labels = breakdown.get("labels") if isinstance(breakdown.get("labels"), dict) else {}
    windows = review_windows or []
    if windows and "cut_fixes" not in labels:
        warnings.append(f"measure 压下了 {len(windows)} 个疑似切点窗口：用 `frames --review` 看图，再写 labels.cut_fixes"
                        "（看过且无需改动就写 {}），切点数与各段切点密度才算复核过")
    if not str(labels.get("basis") or "").strip():
        warnings.append("labels.basis 为空：写明标注依据（如 5s ASR 窗口 + 故事板）")
    return warnings


def check_breakdown(breakdown, measurements, asr_segments=(), asr_evidence=None, research=None, index=None):
    """Run R1–R7; return {derived, errors, warnings}."""
    errors = []
    duration = measurements["source"]["duration_s"]
    if not _closed(breakdown, "top", "breakdown", errors):
        return {"derived": None, "errors": errors, "warnings": []}
    if breakdown.get("schema") != BREAKDOWN_SCHEMA:
        errors.append(f"R1 breakdown.schema 必须是 {BREAKDOWN_SCHEMA}")
    labels = breakdown.get("labels", {})
    errors_before = len(errors)
    spans, sections = _check_labels(labels, duration, errors, measurements["shots"]["cuts"])
    fixes_ok = not any(e.startswith(("R1 labels.cut_fixes", "R2 labels.cut_fixes")) for e in errors[errors_before:])
    measurements = apply_cut_fixes(measurements, labels) if fixes_ok and isinstance(labels, dict) else measurements
    asr_segments = [s for s in (asr_segments or []) if isinstance(s, dict)]
    asr_status = (asr_evidence if isinstance(asr_evidence, dict) else {}).get("status") or "UNKNOWN"
    derived = derive({"audio_spans": spans, "sections": sections}, measurements, asr_segments, asr_status)

    facts, methods = [], []
    for key, kind, bucket in (("source_facts", "fact", facts), ("methods", "method", methods)):
        items = breakdown.get(key, [])
        if not isinstance(items, list):
            errors.append(f"R1 breakdown.{key}: 必须是列表")
            continue
        bucket.extend(item for i, item in enumerate(items) if _closed(item, kind, f"{key}[{i}]", errors))
    fact_ids = _check_ids(facts, "f", "source_facts", errors)
    _check_ids(methods, "m", "methods", errors)
    _check_facts(facts, measurements, derived, duration, errors)
    _check_methods(methods, fact_ids, measurements, derived, errors)

    corpus = leak_corpus(facts, asr_segments, asr_evidence, research, index)
    for method in methods:
        for field in METHOD_TEXT_FIELDS:
            if isinstance(method.get(field), str):
                errors.extend(leak_errors(method[field], f"methods.{method.get('id')}.{field}", corpus))

    skipped = breakdown.get("skipped_dimensions", {})
    if not isinstance(skipped, dict) or set(skipped) - set(DIMENSIONS):
        errors.append(f"R1 skipped_dimensions: 键只能是 {'|'.join(DIMENSIONS)}")
        skipped = {}
    for dimension, reason in skipped.items():
        if not (isinstance(reason, str) and reason.strip()):
            errors.append(f"R1 skipped_dimensions.{dimension}: 原因必须是非空字符串")
        else:   # exported verbatim, so scanned now rather than only at export (R8)
            errors.extend(leak_errors(reason, f"skipped_dimensions.{dimension}", corpus))
    covered = {m.get("dimension") for m in methods}
    for dimension in DIMENSIONS:
        if dimension not in covered and dimension not in skipped:
            errors.append(f"R7 {dimension}: 至少一条 method，或在 skipped_dimensions 写明原因")
    warnings = _warnings(breakdown, methods, asr_segments, asr_status, research,
                         index=index, asr_evidence=asr_evidence, source=measurements["source"],
                         review_windows=measurements["shots"].get("review_windows"))
    return {"derived": derived, "errors": errors, "warnings": warnings}


def _load(work_dir):
    work_dir = Path(work_dir)
    measurements = read_json(work_dir / MEASUREMENTS_FILE)
    breakdown = read_json(work_dir / BREAKDOWN_FILE)
    missing = [name for name, value in ((MEASUREMENTS_FILE, measurements), (BREAKDOWN_FILE, breakdown)) if value is None]
    return measurements, breakdown, {
        "asr_segments": read_json(work_dir / "asr_result.json", []),
        "asr_evidence": read_json(work_dir / "asr_timing_evidence.json"),
        "research": read_json(work_dir / "background_research.json"),
        "index": read_json(work_dir / "understanding_index.json"),
    }, missing


def run_check(work_dir):
    measurements, breakdown, extra, missing = _load(work_dir)
    if missing:
        return {"derived": None, "errors": [f"缺少 {name}" for name in missing], "warnings": []}
    return check_breakdown(breakdown, measurements, **extra)


def _walk(node, where="$"):
    if isinstance(node, dict):
        for key, value in node.items():
            yield "key", str(key), f"{where}.{key}"
            yield from _walk(value, f"{where}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _walk(value, f"{where}[{i}]")
    elif isinstance(node, str):
        yield "str", node, where


def export_errors(production, corpus):
    """R8: re-scan every key and string of the serialized export and reject source-only keys."""
    errors = []
    for kind, text, where in _walk(json.loads(json.dumps(production, ensure_ascii=False))):
        if kind == "key" and text in BANNED_EXPORT_KEYS:
            errors.append(f"R8 {where}: 导出物不能含键 {text!r}")
        errors.extend(leak_errors(text, where, corpus, rule="R8"))
    return errors


def run_export(work_dir, out_path):
    """Check, project, re-scan, then write; returns the report (with `written` on success)."""
    report = run_check(work_dir)
    if report["errors"]:
        return report
    measurements, breakdown, extra, _ = _load(work_dir)
    measurements = apply_cut_fixes(measurements, breakdown.get("labels"))
    production = build_production(breakdown, measurements, report["derived"])
    facts = [f for f in breakdown.get("source_facts") or [] if isinstance(f, dict)]
    corpus = leak_corpus(facts, extra["asr_segments"], extra["asr_evidence"], extra["research"], extra["index"])
    report["errors"] = export_errors(production, corpus)
    if not report["errors"]:
        report["written"] = str(write_json(out_path, production))
    return report
