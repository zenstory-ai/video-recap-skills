"""Budget text, count spoken characters, and format frame facts for brief and lint."""

import re

from lib import CONFIG

TTS_UTTERANCE_OVERHEAD_SECONDS = 0.45


INLINE_VOICE_CUE_RE = re.compile(
    r"\(\s*(?:sigh|sighs|sighing|laugh|laughs|laughing|chuckle|chuckles|chuckling|"
    r"gasp|gasps|gasping|whisper|whispers|whispering|excited|sad|angry|calm|"
    r"serious|soft|softly|pause|breath|breathing|sob|sobs|sobbing|crying|"
    r"snicker|yawn|cough|clears\s+throat)\s*\)",
    re.IGNORECASE,
)
_ASCII_WORD_RE = re.compile(r"[A-Za-z]{3,}")
_PUNCT_STRIP_RE = re.compile(
    r'[，。！？、；：…“”‘’《》〈〉\s"\'「」『』（）()【】\[\]—～·,.!?;:\\-]'
)


def strip_inline_voice_cues(text):
    """剥离方括号舞台提示 [...] 与闭集英文语气/呼吸指示词，保留普通圆括号正文。"""
    return INLINE_VOICE_CUE_RE.sub("", re.sub(r"\[[^\]]*\]", "", text))


def _text_char_count(text):
    """计算文本的有效字数（去除方括号舞台提示、闭集语气词、标点和空白，这些不占 TTS 朗读时间）。"""
    return len(_PUNCT_STRIP_RE.sub("", strip_inline_voice_cues(text)))


def _spoken_unit_count(text):
    """估算口播朗读折算字数：剥离舞台提示与闭集语气词，并将多字母英文词按音节折算。"""
    cleaned = strip_inline_voice_cues(text)
    discount = sum(
        len(m.group(0)) - max(1, round(len(m.group(0)) * 0.45))
        for m in _ASCII_WORD_RE.finditer(cleaned)
    )
    raw_count = len(_PUNCT_STRIP_RE.sub("", cleaned))
    return max(0, raw_count - discount)


def _format_frame_facts(scene):
    """将帧动作描述格式化为可注入 agent brief 的文本。"""
    facts = scene.get("frame_facts", {})
    if not facts:
        return ""
    lines = [f"    {ts}s: {'; '.join(facts[ts])}" for ts in sorted(facts, key=float)]
    return "\n  帧动作:\n" + "\n".join(lines)


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


def _lint_char_budget(start, end):
    """The brief's budget for the window minus one utterance's TTS edge silence."""
    return _recommended_char_budget(start, end - TTS_UTTERANCE_OVERHEAD_SECONDS)
