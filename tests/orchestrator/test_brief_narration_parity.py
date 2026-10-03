"""Anti-drift guards for code intentionally copied into self-contained skills."""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
UNDERSTANDING_SCRIPTS = ROOT / "skills" / "video-understanding" / "scripts"
SCRIPT_SCRIPTS = ROOT / "skills" / "video-script" / "scripts"

# video-understanding sizes the brief's narration slots and ASR chunks with the same
# primitives video-script lints the written narration against. Only these functions are
# shared; each side owns the rest of its module (brief half vs lint half). The owning
# module differs per skill, so they are compared by function, not by file.
SHARED_FUNCTIONS = {
    "_recommended_char_budget": ("agent_text.py", "agent_text.py"),
    "_scene_available_seconds": ("agent_text.py", "agent_text.py"),
    "_overlap_seconds": ("agent_text.py", "agent_text.py"),
    "_sentence_pieces": ("agent_text.py", "deslop_qc.py"),
    "_text_units": ("agent_text.py", "deslop_qc.py"),
}
# CONFIG keys the shared budget functions read: a brief budget and a lint budget built
# from different defaults would disagree even with byte-identical code.
SHARED_BUDGET_KEYS = (
    "speech_rate",
    "speech_safety_margin",
    "narration_speed",
    "narration_tail_pad_seconds",
)
# Modules that used to be byte-identical whole-file copies. Each now lives in exactly
# the one skill that executes it.
SINGLE_OWNER_MODULES = {
    "narration_lint.py": SCRIPT_SCRIPTS,
    "speech_ownership.py": SCRIPT_SCRIPTS,
    "deslop_qc.py": SCRIPT_SCRIPTS,
    "timeline_fusion.py": UNDERSTANDING_SCRIPTS,
}


def _function_node(path, name):
    for node in ast.parse(path.read_text(encoding="utf-8"), filename=str(path)).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{path}: missing top-level function {name}")


@pytest.mark.parametrize("name", sorted(SHARED_FUNCTIONS))
def test_shared_text_primitives_stay_identical(name):
    understanding_module, script_module = SHARED_FUNCTIONS[name]
    first = _function_node(UNDERSTANDING_SCRIPTS / understanding_module, name)
    second = _function_node(SCRIPT_SCRIPTS / script_module, name)
    assert ast.dump(first) == ast.dump(second), (
        f"{name} drifted between video-understanding/{understanding_module} and "
        f"video-script/{script_module}. Apply the same change to both local copies; "
        "do not replace them with a cross-skill path."
    )


def _called_names(scripts_dir):
    called = set()
    for path in scripts_dir.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                called.add(node.func.id)
    return called


@pytest.mark.parametrize(
    "scripts_dir", [UNDERSTANDING_SCRIPTS, SCRIPT_SCRIPTS], ids=["understanding", "script"]
)
def test_every_shared_primitive_is_called_in_its_own_skill(scripts_dir):
    """A copy kept only to satisfy parity is dead code; drop it from SHARED_FUNCTIONS."""
    uncalled = sorted(set(SHARED_FUNCTIONS) - _called_names(scripts_dir))
    assert not uncalled, f"{scripts_dir}: shared copies nothing calls: {uncalled}"


def _config_value_dumps(lib_path, keys):
    tree = ast.parse(lib_path.read_text(encoding="utf-8"))
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "CONFIG"
        ):
            values = {
                key.value: ast.dump(value)
                for key, value in zip(node.value.keys, node.value.values)
                if isinstance(key, ast.Constant) and key.value in keys
            }
            assert set(values) == set(keys), f"{lib_path}: missing {set(keys) - set(values)}"
            return values
    raise AssertionError(f"{lib_path}: missing top-level CONFIG")


def test_shared_budget_config_matches():
    understanding, script = (
        _config_value_dumps(scripts / "lib.py", SHARED_BUDGET_KEYS)
        for scripts in (UNDERSTANDING_SCRIPTS, SCRIPT_SCRIPTS)
    )
    drifted = sorted(key for key in SHARED_BUDGET_KEYS if understanding[key] != script[key])
    assert not drifted, f"brief and lint budget defaults drifted: {drifted}"


@pytest.mark.parametrize("module", sorted(SINGLE_OWNER_MODULES))
def test_formerly_copied_modules_have_one_owner(module):
    owners = [
        scripts
        for scripts in (UNDERSTANDING_SCRIPTS, SCRIPT_SCRIPTS)
        if (scripts / module).exists()
    ]
    assert owners == [SINGLE_OWNER_MODULES[module]], (
        f"{module} must live only in {SINGLE_OWNER_MODULES[module]}; found in {owners}"
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

