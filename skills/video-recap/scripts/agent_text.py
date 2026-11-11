"""Budget text, count spoken characters, and format frame facts for brief and lint."""

import re

from lib import CONFIG

TTS_UTTERANCE_OVERHEAD_SECONDS = 0.45


def _text_char_count(text):
    """计算文本的有效字数（去除标点和空白，这些不占 TTS 朗读时间）。"""
    return len(
        re.sub(
            r'[，。！？、；：…“”‘’《》〈〉\s"\'「」『』（）()【】\[\]—～·,.!?;:\\-]',
            "",
            text,
        )
    )


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
