"""The cut render and the final render label the delivered picture with the same rules.

edited_source.mp4 is the final render's input in cut mode, so both encodes must agree on
which colour tags a source gets; each skill keeps its own copy of the helpers (skills
share no code), and this test keeps the copies identical by function.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ASSEMBLE = ROOT / "skills" / "video-assemble" / "scripts" / "media.py"
CUT = ROOT / "skills" / "video-cut" / "scripts" / "media_geometry.py"

SHARED_FUNCTIONS = (
    "_probe_video_format",
    "_output_color_tags",
    "_color_tag_filter",
    "_color_tag_args",
)
SHARED_CONSTANTS = ("_COLOR_FIELDS", "_UNTAGGED_COLOR", "_RGB_COLOR_SPACE", "_KNOWN_COLOR_VALUES",
                    "_COLOR_OPTION_SPELLING")


def _top_level(path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path)).body


def _function(path, name):
    for node in _top_level(path):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{path}: missing top-level function {name}")


def _constant(path, name):
    for node in _top_level(path):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return node.value
    raise AssertionError(f"{path}: missing top-level constant {name}")


@pytest.mark.parametrize("name", SHARED_FUNCTIONS)
def test_colour_tag_functions_stay_identical(name):
    assert ast.dump(_function(ASSEMBLE, name)) == ast.dump(_function(CUT, name)), (
        f"{name} drifted between the final render (media.py) and the cut render "
        "(media_geometry.py). Apply the same change to both local copies."
    )


@pytest.mark.parametrize("name", SHARED_CONSTANTS)
def test_colour_tag_tables_stay_identical(name):
    assert ast.dump(_constant(ASSEMBLE, name)) == ast.dump(_constant(CUT, name))
