import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'skills' / 'video-recap' / 'scripts'))

import asr  # noqa: E402


def test_strip_reasoning_residue_removes_think_leakage():
    """Full-mode ASR must strip leaked MiMo <think> reasoning residue too; clean transcript
    text stays untouched."""
    assert asr._strip_reasoning_residue("think>\n 真相比逃跑更狼。").strip() == "真相比逃跑更狼。"
    assert asr._strip_reasoning_residue("<think>x\n</think>\n开门的却是个陌生男人").strip() == "开门的却是个陌生男人"
    assert asr._strip_reasoning_residue("<think>未闭合 后续").strip() == ""
    assert asr._strip_reasoning_residue("普通转写文本。") == "普通转写文本。"
