"""Audio policy must agree wherever it is actually consumed.

Previously every skill declared the full ~30-key audio policy and this file asserted all
five copies matched. That parity was circular: the keys only needed to agree because
lib.py had been copied, and most skills never read them. Now a skill declares a knob only
if its own code reads it, so parity is asserted over the real overlap — which is where a
divergence could actually change a rendered recap.
"""
import ast
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LIBS = {
    "recap": ROOT / "skills/video-recap/scripts/lib.py",
    "reference": ROOT / "skills/video-reference/scripts/lib.py",
}
ENV_READERS = {"env_bool", "_env_bool", "env_int", "env_float", "env_str"}

# Knobs that shape the rendered audio. A skill is held to these only if it declares them,
# i.e. only if its own code reads them — see test_audio_policy_keys_are_declared_where_read.
# Exactly the inputs narration_tempo_budget() reads. A skill that carries these must
# compute the same cap from them, because voiceover and assemble both enforce it.
TEMPO_KEYS = (
    "narration_speed",
    "narration_cumulative_tempo_max",
    "narration_cumulative_tempo_hard_max",
    "tts_segment_tempo_max",
)
MIX_KEYS = (
    "fade_ms",
    "ducking_narr_weight",
    "zone_ducking_volume",
    # `zone_fade_seconds` used to sit here. It was declared in all five lib.py copies and
    # read by nothing in the entire repository — kept alive only by the old blanket parity
    # assertion, which proved the copies agreed without anyone consuming the value.
    "idle_orig_volume",
    "duck_fade_seconds",
    "bgm_volume",
    "bgm_ducking_volume",
    "speech_ducking_volume",
    "final_loudnorm",
    "target_lufs",
    "target_true_peak",
    "target_lra",
    "final_limiter_peak",
    "loudness_limiter_max_db",
    "tts_segment_normalize",
    "tts_segment_target_rms_dbfs",
    "tts_segment_peak_limit",
)


def _load_lib(name, path):
    spec = importlib.util.spec_from_file_location(f"audio_policy_{name}_lib", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def libs():
    return {name: _load_lib(name, path) for name, path in LIBS.items()}


def _readable_source(path, *, include_lib=True):
    """Everything in a skill that could read CONFIG, excluding the CONFIG literal itself."""
    scripts = path.parent
    parts = [
        p.read_text(encoding="utf-8")
        for p in sorted(scripts.rglob("*.py"))
        if p.name != "lib.py" and "__pycache__" not in p.parts
    ]
    if include_lib:
        lib_source = path.read_text(encoding="utf-8")
        config_span = set()
        for node in ast.walk(ast.parse(lib_source)):
            if (
                isinstance(node, ast.Assign)
                and any(getattr(t, "id", "") == "CONFIG" for t in node.targets)
                and isinstance(node.value, ast.Dict)
            ):
                config_span = set(range(node.lineno, node.end_lineno + 1))
        parts.append(
            "\n".join(
                line
                for number, line in enumerate(lib_source.splitlines(), start=1)
                if number not in config_span
            )
        )
    return "\n".join(parts)


def _uses_env_reader(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name in ENV_READERS or name == "getenv":
                return True
            if (
                isinstance(func, ast.Attribute)
                and name == "get"
                and (
                    (isinstance(func.value, ast.Name) and func.value.id == "environ")
                    or (isinstance(func.value, ast.Attribute) and func.value.attr == "environ")
                )
            ):
                return True
        elif (
            isinstance(child, ast.Subscript)
            and isinstance(child.value, ast.Attribute)
            and child.value.attr == "environ"
        ):
            return True
    return False


def _unread_env_declarations(path: Path) -> list[str]:
    """Module-level env-backed assignments in lib.py that nothing in the skill ever loads."""
    lib_tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    declared = {}
    for stmt in lib_tree.body:
        if isinstance(stmt, ast.Assign) and _uses_env_reader(stmt.value):
            for target in stmt.targets:
                if isinstance(target, ast.Name) and target.id != "CONFIG":
                    declared[target.id] = stmt

    if not declared:
        return []

    loaded = set()
    for stmt in lib_tree.body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if declared.get(node.id) is not stmt:
                    loaded.add(node.id)
    for other in sorted(path.parent.rglob("*.py")):
        if other == path or "__pycache__" in other.parts:
            continue
        other_tree = ast.parse(other.read_text(encoding="utf-8"), filename=str(other))
        for node in ast.walk(other_tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                loaded.add(node.id)

    return sorted(name for name in declared if name not in loaded)


def test_every_skill_lib_is_registered():
    discovered = set((ROOT / "skills").glob("*/scripts/lib.py"))
    assert set(LIBS.values()) == discovered


@pytest.mark.parametrize("key", TEMPO_KEYS + MIX_KEYS)
def test_audio_policy_value_agrees_across_every_skill_that_declares_it(libs, key):
    declarers = {name: lib for name, lib in libs.items() if key in getattr(lib, "CONFIG", {})}
    assert declarers, f"no skill declares {key!r} any more; drop it from this contract"
    values = {name: lib.CONFIG[key] for name, lib in declarers.items()}
    assert len(set(map(repr, values.values()))) == 1, f"{key} diverged: {values}"


def test_tempo_budget_helper_agrees_wherever_the_tempo_knobs_are_declared(libs):
    """narration_tempo_budget turns the tempo knobs into the cap voiceover and assemble both
    enforce. Any skill carrying those knobs must compute the same budget from them."""
    declarers = {
        name: lib
        for name, lib in libs.items()
        if all(key in getattr(lib, "CONFIG", {}) for key in TEMPO_KEYS)
    }
    assert "recap" in declarers, (
        "video-recap enforces the tempo budget and must declare its inputs"
    )
    for offset in (-0.05, 0.0, 0.05, 0.08):
        expected = declarers["recap"].narration_tempo_budget(offset)
        for name, lib in declarers.items():
            assert hasattr(lib, "narration_tempo_budget"), name
            assert lib.narration_tempo_budget(offset) == expected, name


def test_no_skill_declares_config_it_never_reads(libs):
    """The invariant that replaces blanket parity.

    A key declared where nothing reads it is dead weight at best and a lie at worst:
    a knob once declared in five CONFIGs and reported as active was read by none of them.
    """
    offenders = {}
    unread_env = {}
    for name, path in LIBS.items():
        config = getattr(libs[name], "CONFIG", None)
        if config is not None:
            readable = _readable_source(path)
            unread = {
                key
                for key in config
                if f'"{key}"' not in readable and f"'{key}'" not in readable
            }
            if unread:
                offenders[name] = sorted(unread)
        dead_env = _unread_env_declarations(path)
        if dead_env:
            unread_env[name] = dead_env
    assert not offenders, (
        f"CONFIG keys declared but read by nothing in their own skill: {offenders}. "
        "Declare a knob in the skill that implements it, not in every copy of lib.py."
    )
    assert not unread_env, (
        f"Environment-backed declarations in lib.py read by nothing in their own skill: {unread_env}"
    )
