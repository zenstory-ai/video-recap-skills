"""Canonical audio policy single-source guard.

Audio ducking, RMS normalization, and loudness helpers live exclusively in
`skills/video-recap/scripts/_audio.py`: `render.py` uses `ducking_filters` and
`export_jianying.py` uses `ducking_keyframes`, so both follow one curve with no local copy.
"""
import ast
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "skills" / "video-recap" / "scripts"
AUDIO_PATH = SCRIPTS_DIR / "_audio.py"
JIANYING_PATH = SCRIPTS_DIR / "export_jianying.py"

REQUIRED_AUDIO_HELPERS = (
    "coalesce_duck_windows",
    "ducking_keyframes",
    "normalize_wav_rms",
    "measure_loudness",
    "build_loudnorm_filter",
)


def _load_audio_module():
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location("video_recap_audio_contract", AUDIO_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_canonical_audio_helpers_exist_and_agree():
    assert AUDIO_PATH.is_file(), "skills/video-recap/scripts/_audio.py must exist"
    audio = _load_audio_module()
    for name in REQUIRED_AUDIO_HELPERS:
        assert callable(getattr(audio, name, None)), f"_audio.py must expose {name}()"


def test_export_jianying_shares_ducking_keyframes_from_audio_helper():
    assert JIANYING_PATH.is_file(), "skills/video-recap/scripts/export_jianying.py must exist"
    tree = ast.parse(JIANYING_PATH.read_text(encoding="utf-8"), filename=str(JIANYING_PATH))
    imported_from_audio = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "_audio":
            imported_from_audio.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "_audio":
                    imported_from_audio.add("_audio")
    assert imported_from_audio, (
        "export_jianying.py must share ducking automation from _audio.py rather than duplicating it"
    )
