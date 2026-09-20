"""Keep the public env inventory complete and free of credential names."""

import ast
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INVENTORY = REPO / "skills/video-recap/references/env-inventory-v1.json"

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
    for path in tree.glob("*.py"):
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


def test_public_env_contract_classifies_all_literal_reads_and_no_credentials():
    contract = json.loads(INVENTORY.read_text(encoding="utf-8"))["variables"]
    for name in contract:
        assert not any(marker in name for marker in CREDENTIAL_MARKERS), name
        assert not name.endswith("_TOKEN"), name
    assert "MIMO_TOKEN_PLAN_CLUSTER" in contract
    assert {"REVIEW_NARRATION", "REQUIRE_NARRATION_REVIEW", "INDEX_TTS_CACHE_REVISION"} <= set(contract)

    trees = _skill_script_trees()
    assert len(trees) >= 6
    reads = set()
    for tree in trees:
        reads |= _literal_env_reads(tree)
    unclassified = {
        name for name in reads
        if name not in contract and not name.endswith("API_KEY")
    }
    assert unclassified == set()
