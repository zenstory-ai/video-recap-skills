"""Count and budget agent-written narration text for lint."""

import re

from lib import CONFIG


def _text_char_count(text):
    """计算文本的有效字数（去除标点和空白，这些不占 TTS 朗读时间）。"""
    return len(
        re.sub(
            r'[，。！？、；：…“”‘’《》〈〉\s"\'「」『』（）()【】\[\]—～·,.!?;:\\-]',
            "",
            text,
        )
    )


def _find_scene_for_midpoint(scenes_analysis, start, end):
    mid = (start + end) / 2
    for scene in scenes_analysis:
        if scene["start"] <= mid <= scene["end"]:
            return scene
    return None


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


# Lint-only realism on top of the brief's budget. Every block is one TTS utterance, and the
# provider pads each utterance with edge silence that voiceover keeps and assemble must
# place inside the window. Measured on 98 MiMo blocks from real runs (-40 dBFS): 0.40 /
# 0.54 / 0.62 s at p10 / p50 / p90 before the 1.15x narration atempo, i.e. about 0.47 s
# placed. On a 2-3 s window that is a fifth of the room, so without it a short window
# passed lint and failed in assemble as no_safe_fit after TTS was billed.
TTS_UTTERANCE_OVERHEAD_SECONDS = 0.45


def _lint_char_budget(start, end):
    """The brief's budget for the window minus one utterance's TTS edge silence."""
    return _recommended_char_budget(start, end - TTS_UTTERANCE_OVERHEAD_SECONDS)
