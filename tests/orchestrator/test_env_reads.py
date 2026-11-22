"""Every env var the skills read is strictly bounded and non-credential except ``*_API_KEY``."""

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MAX_ENV_VARS_TOTAL = 25
CREDENTIAL_MARKERS = ("KEY", "SECRET", "PASSWORD")
ENV_READERS = {"env_bool", "_env_bool", "env_int", "env_float", "env_str"}


def _skill_script_trees():
    return sorted(
        path / "scripts"
        for path in (REPO / "skills").iterdir()
        if (path / "scripts").is_dir()
    )


def _literal_env_reads(tree):
    reads = set()
    for path in tree.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        parsed = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(parsed):
            if (
                isinstance(node, ast.Call)
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                direct_environment = (
                    isinstance(func, ast.Attribute)
                    and name in {"get", "getenv"}
                    and (
                        name == "getenv"
                        or isinstance(func.value, ast.Name) and func.value.id == "environ"
                        or isinstance(func.value, ast.Attribute) and func.value.attr == "environ"
                    )
                )
                if name in ENV_READERS or direct_environment:
                    reads.add(node.args[0].value)
            if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
                value = node.value
                if isinstance(value, ast.Attribute) and value.attr == "environ":
                    reads.add(node.slice.value)
    return reads


def _credential_shaped(name):
    return any(marker in name for marker in CREDENTIAL_MARKERS) or name.endswith("_TOKEN")


def test_literal_env_reads_are_bounded_and_safe():
    trees = _skill_script_trees()
    assert len(trees) == 2
    reads = set()
    for tree in trees:
        reads |= _literal_env_reads(tree)

    assert any(name.endswith("_API_KEY") for name in reads), (
        f"Expected at least one optional *_API_KEY read so the AST scanner stays live; found {sorted(reads)}"
    )
    assert len(reads) <= MAX_ENV_VARS_TOTAL, (
        f"Env var surface ({len(reads)}) exceeds budget ({MAX_ENV_VARS_TOTAL}): {sorted(reads)}"
    )
    credential_reads = {
        name for name in reads
        if _credential_shaped(name) and not name.endswith("_API_KEY")
    }
    assert credential_reads == set(), f"Unexpected credential-shaped env reads: {sorted(credential_reads)}"
