"""Anti-drift guards for files intentionally copied into self-contained skills."""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BRIEF = ROOT / "skills" / "video-understanding" / "scripts" / "brief.py"
NARRATION = ROOT / "skills" / "video-script" / "scripts" / "narration.py"
UNDERSTANDING_SCRIPTS = BRIEF.parent
SCRIPT_SCRIPTS = NARRATION.parent
UNDERSTANDING_DESLOP = (
    ROOT / "skills" / "video-understanding" / "scripts" / "deslop_qc.py"
)
SCRIPT_DESLOP = ROOT / "skills" / "video-script" / "scripts" / "deslop_qc.py"
RECAP_CREATIVE_PLAYBOOK = (
    ROOT / "skills" / "video-recap" / "references" / "creative-editing-playbook.md"
)
SCRIPT_CREATIVE_PLAYBOOK = (
    ROOT / "skills" / "video-script" / "references" / "creative-editing-playbook.md"
)

SYNC_PAIRS = (
    pytest.param(BRIEF, NARRATION, id="agent-brief-entry"),
    *[
        pytest.param(
            UNDERSTANDING_SCRIPTS / name,
            SCRIPT_SCRIPTS / name,
            id=f"agent-brief-{Path(name).stem}",
        )
        for name in (
            "agent_brief.py",
            "agent_text.py",
            "brief_context.py",
            "brief_inputs.py",
            "brief_timeline.py",
            "narration_lint.py",
            "speech_ownership.py",
            "timeline_fusion.py",
        )
    ],
    pytest.param(UNDERSTANDING_DESLOP, SCRIPT_DESLOP, id="deslop-qc"),
    pytest.param(
        RECAP_CREATIVE_PLAYBOOK, SCRIPT_CREATIVE_PLAYBOOK, id="creative-playbook"
    ),
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
        UNDERSTANDING_SCRIPTS / "brief_inputs.py",
        SCRIPT_SCRIPTS / "brief_inputs.py",
    }
    values = {
        str(path.relative_to(ROOT)): _top_level_literal(path, "_ASR_SPAN_TOL")
        for path in paths
    }

    assert set(values.values()) == {0.05}, (
        f"_ASR_SPAN_TOL drifted across files: {values}"
    )


def test_script_brief_is_standalone_without_understanding_producer(tmp_path):
    import shutil
    import subprocess
    import sys
    copied = tmp_path / 'script'
    shutil.copytree(SCRIPT_SCRIPTS, copied, ignore=shutil.ignore_patterns('__pycache__'))
    assert not (copied / 'asr_timing_evidence.py').exists()
    program = '''import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from agent_brief import build_agent_brief
work=Path(sys.argv[2]); work.mkdir()
text=build_agent_brief([], [], [], 2.0, work).read_text()
assert 'MISSING_OR_STALE' in text
assert 'UNKNOWN_NOT_PROVEN_SILENCE' in text
assert 'ASR [start–end] times + Quiet windows below as safe cut points' not in text
'''
    # -I ignores PYTHON* environment variables, so force UTF-8 stdio with -X utf8 (Windows).
    result = subprocess.run([sys.executable, '-I', '-X', 'utf8', '-c', program, str(copied), str(tmp_path/'work')],
                            cwd=tmp_path, capture_output=True, text=True, encoding='utf-8',
                            errors='replace')
    assert result.returncode == 0, result.stderr
