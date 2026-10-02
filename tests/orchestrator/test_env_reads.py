"""Every env var the skills read is a tuning knob, except the provider ``*_API_KEY`` reads."""

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

CREDENTIAL_MARKERS = ("KEY", "SECRET", "PASSWORD")
ENV_READERS = {"env_bool", "_env_bool", "env_int", "_optional_env_int", "env_float", "env_str"}


def _skill_script_trees():
    """Every installed skill's scripts directory, so a new skill cannot escape the audit."""
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


def test_literal_env_reads_are_not_credential_shaped_except_api_keys():
    trees = _skill_script_trees()
    assert len(trees) >= 6
    reads = set()
    for tree in trees:
        reads |= _literal_env_reads(tree)
    # The scanner must still see real reads, including names that only look token-like.
    assert {"MIMO_TOKEN_PLAN_CLUSTER", "VLM_MAX_TOKENS", "MIMO_API_KEY"} <= reads
    credential_reads = {
        name for name in reads
        if _credential_shaped(name) and not name.endswith("_API_KEY")
    }
    assert credential_reads == set()
