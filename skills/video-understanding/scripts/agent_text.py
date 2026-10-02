"""Split, chunk, and budget text for the agent narration brief."""

import re

from lib import CONFIG


def _sentence_pieces(text: str) -> list[str]:
    """Split text into sentence-like pieces while keeping terminal punctuation."""
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    parts = re.split(r"([。！？!?；;.])", text)
    out: list[str] = []
    for idx in range(0, len(parts), 2):
        body = parts[idx].strip()
        punct = parts[idx + 1] if idx + 1 < len(parts) else ""
        if body or punct:
            out.append((body + punct).strip())
    return out or [text]


def _text_units(text: str) -> int:
    """Length unit for prose: CJK characters, otherwise words."""
    if re.search(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", text):
        return len(re.sub(r"\s+", "", text))
    return len(re.findall(r"\b\w+\b", text))


def _format_frame_facts(scene):
    """将帧动作描述格式化为可注入 agent brief 的文本。"""
    facts = scene.get("frame_facts", {})
    if not facts:
        return ""
    lines = [f"    {ts}s: {'; '.join(facts[ts])}" for ts in sorted(facts, key=float)]
    return "\n  帧动作:\n" + "\n".join(lines)


def _split_text_by_sentence_windows(text, min_chars=500, max_chars=800):
    """Clipto-style three-tier sentence boundary splitting for long ASR text."""
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    if _text_units(text) <= max_chars:
        return _sentence_pieces(text)

    result = []
    rest = text
    sentence_marks = "。！？!?；;."
    while _text_units(rest) > max_chars:
        # The min/max thresholds are unit-based but punctuation is char-indexed.
        # For Chinese (the dominant recap target) these are nearly identical; for
        # non-CJK this remains a safe sentence-boundary heuristic around words.
        char_min = min(len(rest), max(1, min_chars))
        char_max = min(len(rest), max(1, max_chars))
        window = rest[:char_max]
        cut = max(window.rfind(mark) for mark in sentence_marks)
        if cut + 1 < char_min:
            outside = -1
            for i, ch in enumerate(rest[char_max:], start=char_max):
                if ch in sentence_marks:
                    outside = i
                    break
            cut = outside if outside >= 0 and outside + 1 <= len(rest) else char_max - 1
        piece = rest[: cut + 1].strip()
        if not piece:
            piece = rest[:char_max].strip()
            cut = char_max - 1
        result.append(piece)
        rest = rest[cut + 1 :].strip()
    if rest:
        result.append(rest)
    return result


def _timed_sentence_pieces(seg, min_chars, max_chars):
    """Split one ASR segment into timed sentence pieces with approximate spans."""
    text = seg["text"].strip()
    if not text:
        return []
    start, end = seg["start"], seg["end"]
    pieces = []
    for sentence in _sentence_pieces(text):
        if _text_units(sentence) > max_chars:
            pieces.extend(
                _split_text_by_sentence_windows(
                    sentence, min_chars=min_chars, max_chars=max_chars
                )
            )
        else:
            pieces.append(sentence)
    total_units = sum(max(1, _text_units(piece)) for piece in pieces)
    duration = end - start
    cursor = start
    timed = []
    for idx, piece in enumerate(pieces):
        units = max(1, _text_units(piece))
        piece_end = end if idx == len(pieces) - 1 else cursor + duration * units / total_units
        timed.append(
            {
                "start": round(cursor, 2),
                "end": round(piece_end, 2),
                "text": piece,
                "char_count": _text_units(piece),
            }
        )
        cursor = piece_end
    return timed


def _scene_ids_for_range(scenes, start, end):
    duration = max(0.001, end - start)
    scene_ids = []
    for scene in scenes:
        overlap = _overlap_seconds(start, end, scene["start"], scene["end"])
        # Ignore tiny boundary tails from approximate ASR sentence timing. A
        # scene id should mean the chunk materially belongs to that scene.
        if overlap and (overlap >= duration * 0.2 or overlap >= 3.0):
            scene_ids.append(scene["scene_id"])
    return scene_ids


def _chunk_asr_for_writing(segments, scenes_analysis, min_chars=None, max_chars=None):
    """Chunk ASR into semantic windows before an agent writes long-dialogue recaps.

    The strategy mirrors Clipto's segment splitter: accumulate a window, prefer
    the last sentence boundary inside max length, allow a slightly longer first
    boundary outside the window, and fall back to the remaining text. CJK text is
    measured by characters; non-CJK text is measured by words.
    """
    min_chars = CONFIG["asr_chunk_min_chars"] if min_chars is None else min_chars
    max_chars = CONFIG["asr_chunk_max_chars"] if max_chars is None else max_chars
    pieces = [
        piece
        for seg in segments
        for piece in _timed_sentence_pieces(seg, min_chars, max_chars)
    ]

    chunks = []
    current = []
    current_units = 0
    current_scene_ids = set()

    def flush():
        nonlocal current, current_units, current_scene_ids
        if not current:
            return
        chunks.append(
            {
                "chunk_id": len(chunks),
                "start": current[0]["start"],
                "end": current[-1]["end"],
                "scene_ids": sorted(
                    current_scene_ids, key=lambda sid: (isinstance(sid, str), sid)
                ),
                "char_count": current_units,
                "text": " ".join(piece["text"] for piece in current).strip(),
                "segments": current,
            }
        )
        current = []
        current_units = 0
        current_scene_ids = set()

    for piece in pieces:
        units = max(1, piece["char_count"])
        piece_scene_ids = set(
            _scene_ids_for_range(scenes_analysis, piece["start"], piece["end"])
        )
        crosses_scene = (
            current
            and current_scene_ids
            and piece_scene_ids
            and not (current_scene_ids & piece_scene_ids)
        )
        if crosses_scene and current_units >= min_chars:
            flush()
        if current and current_units >= min_chars and current_units + units > max_chars:
            flush()
        current.append(piece)
        current_scene_ids.update(piece_scene_ids)
        current_units += units
        if current_units >= max_chars:
            flush()
    flush()
    return chunks


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
