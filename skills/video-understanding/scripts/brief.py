"""Public narration-brief API for this self-contained skill."""

from briefing.builder import build_agent_brief
from briefing.context import assess_understanding_substrate

__all__ = [
    "assess_understanding_substrate",
    "build_agent_brief",
]
