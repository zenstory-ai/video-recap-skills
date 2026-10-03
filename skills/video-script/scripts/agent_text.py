"""Normalize, budget, and de-duplicate agent-written narration text."""

import copy
import re

from lib import CONFIG, log


def _text_char_count(text):
    """计算文本的有效字数（去除标点和空白，这些不占 TTS 朗读时间）。"""
    return len(
        re.sub(
            r'[，。！？、；：…“”‘’《》〈〉\s"\'「」『』（）()【】\[\]—～·,.!?;:\\-]',
            "",
            text,
        )
    )


def _truncate_at_sentence(text, max_chars):
    """在句子边界截断，不产生残句。max_chars 按有效字符计（不含标点空白）。"""
    if _text_char_count(text) <= max_chars:
        return text
    eff = 0
    cutoff = len(text)
    for i, ch in enumerate(text):
        eff += 1 if _text_char_count(ch) else 0
        if eff > max_chars:
            cutoff = i + 1
            break
    idx = max(text[:cutoff].rfind(sep) for sep in ["。", "！", "？", "!", "?"])
    if idx > 0:
        return text[: idx + 1]
    idx = max(text[:cutoff].rfind(sep) for sep in ["，", "、", "；", ","])
    if idx > 3:
        return text[:idx] + "。"
    return ""


def _char_bigrams(text):
    return {text[i : i + 2] for i in range(len(text) - 1) if text[i : i + 2].strip()}


def _post_dedup_narration(narration):
    """去除相邻相似解说段（bigram 重叠 >60% 则合并）。"""
    if len(narration) < 2:
        return narration
    result = [narration[0]]
    for seg in narration[1:]:
        prev = result[-1]
        set_a, set_b = _char_bigrams(prev["narration"]), _char_bigrams(seg["narration"])
        if not set_a or not set_b:
            result.append(seg)
            continue
        overlap = len(set_a & set_b) / min(len(set_a), len(set_b))
        # Only merge near-identical adjacent beats. Short Chinese beats share many
        # bigrams by chance, so a low threshold collapses intentional parallel beats
        # ("他不再试探" / "他直接赌上全力") and silently drops density below target.
        if overlap > 0.6:
            # Validation is also the handoff boundary for renderer metadata: keep the
            # overlays authored on either merged beat.
            merged_overlays = copy.deepcopy(
                prev.get("visual_overlays", []) + seg.get("visual_overlays", [])
            )
            if len(seg["narration"]) > len(prev["narration"]):
                prev["narration"] = seg["narration"]
            prev["end"] = seg["end"]
            prev["pause_after_ms"] = seg["pause_after_ms"]
            if merged_overlays:
                prev["visual_overlays"] = merged_overlays
            log(f"  去重合并: {prev['start']:.0f}-{prev['end']:.0f}s")
        else:
            result.append(seg)
    removed = len(narration) - len(result)
    if removed:
        log(f"  去重: {len(narration)} → {len(result)} 段 (合并 {removed} 段)")
    return result


def _find_scene_for_midpoint(scenes_analysis, start, end):
    mid = (start + end) / 2
    for scene in scenes_analysis:
        if scene["start"] <= mid <= scene["end"]:
            return scene
    return None


def _normalise_narration_segment(seg):
    """Normalize one lint-validated narration segment for the full-mode rewrite.

    Authored timing is a delivery contract: scene boundaries are approximate
    visual-analysis buckets, so start/end are never clamped here.
    """
    item = {
        "start": round(seg["start"], 2),
        "end": round(seg["end"], 2),
        "narration": str(seg["narration"]).strip(),
        "pause_after_ms": int(seg.get("pause_after_ms", CONFIG["breath_ms"])),
        "overlaps_speech": bool(seg.get("overlaps_speech", True)),
    }
    for optional_key in (
        "source_start",
        "source_end",
        "source_clip_id",
        "source_entry_policy",
        "source_entry_reason",
    ):
        if optional_key in seg:
            item[optional_key] = seg[optional_key]
    # carry the per-beat emotion/tone tag (MiMo TTS instruct) through lint untouched
    if seg.get("emotion"):
        item["emotion"] = seg["emotion"].strip()
    # Renderer-owned metadata survives the validation rewrite; the recap orchestrator
    # filters supported overlay kinds later.
    if "visual_overlays" in seg:
        item["visual_overlays"] = copy.deepcopy(seg["visual_overlays"])
    return item


def _clean_narration_punctuation(text):
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r'[，：、；,]["\']?[。！？]', "。", text)
    return re.sub(r'["\']。$', "。", text)


def _scene_available_seconds(start, end):
    return max(0.0, end - start - CONFIG["narration_tail_pad_seconds"])


def _recommended_char_budget(start, end):
    # account for the global narration atempo (CONFIG['narration_speed']) so a beat's text
    # is budgeted against the FINAL sped-up audio, not the raw TTS rate — otherwise windows
    # are over-sized and the bed shows long silent gaps between sentences.
    effective_rate = (
        CONFIG["speech_rate"] * CONFIG["speech_safety_margin"] * CONFIG["narration_speed"]
    )
    return int(_scene_available_seconds(start, end) * effective_rate)


def _overlap_seconds(start, end, other_start, other_end):
    return max(0.0, min(end, other_end) - max(start, other_start))
