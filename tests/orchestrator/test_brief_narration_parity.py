"""Anti-drift guards for files intentionally copied into self-contained skills."""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
UNDERSTANDING_SCRIPTS = ROOT / "skills" / "video-understanding" / "scripts"
SCRIPT_SCRIPTS = ROOT / "skills" / "video-script" / "scripts"
UNDERSTANDING_DESLOP = UNDERSTANDING_SCRIPTS / "deslop_qc.py"
SCRIPT_DESLOP = SCRIPT_SCRIPTS / "deslop_qc.py"

SYNC_PAIRS = (
    *[
        pytest.param(
            UNDERSTANDING_SCRIPTS / name,
            SCRIPT_SCRIPTS / name,
            id=f"agent-brief-{Path(name).stem}",
        )
        for name in (
            "agent_text.py",
            "narration_lint.py",
            "speech_ownership.py",
            "timeline_fusion.py",
        )
    ],
    pytest.param(UNDERSTANDING_DESLOP, SCRIPT_DESLOP, id="deslop-qc"),
)


@pytest.mark.parametrize(("first", "second"), SYNC_PAIRS)
def test_intentional_local_copies_stay_byte_identical(first, second):
    assert first.is_file() and second.is_file()
    assert first.read_bytes() == second.read_bytes(), (
        f"Self-contained copies drifted: {first} != {second}. "
        "Apply the same change to both local copies; do not replace them with a cross-skill path."
    )


def _top_level_literal(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{path}: missing top-level constant {name}")


def test_asr_span_tol_matches_across_files():
    paths = {
        ROOT / "skills/video-understanding/scripts/consolidate.py",
        UNDERSTANDING_SCRIPTS / "briefing" / "inputs.py",
    }
    values = {
        str(path.relative_to(ROOT)): _top_level_literal(path, "_ASR_SPAN_TOL")
        for path in paths
    }

    assert set(values.values()) == {0.05}, (
        f"_ASR_SPAN_TOL drifted across files: {values}"
    )


def _top_level_definitions(path, names):
    """ast.dump of each named top-level def/assignment (decorators included, comments not)."""
    found = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.FunctionDef):
            name = node.name
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
        else:
            continue
        if name in names:
            found[name] = ast.dump(node)
    return found


def test_ffmpeg_filter_file_helpers_stay_identical():
    """video-assemble and video-cut each keep the ffmpeg filter-file probe in their own lib."""
    names = {"_LEGACY_FILTER_FILE_OPTIONS", "_ffmpeg_reads_option_files", "filter_file_args"}
    assemble, cut = (
        _top_level_definitions(ROOT / "skills" / skill / "scripts" / "lib.py", names)
        for skill in ("video-assemble", "video-cut")
    )
    assert set(assemble) == names
    assert assemble == cut

