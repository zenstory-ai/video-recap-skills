import ast
import importlib.util
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

# Hard anti-bloat guardrails (raise explicitly with an .agents/notes/ rationale if needed).
MAX_SCRIPT_MODULE_LINES = 550
SKILL_TOTAL_PY_LOC_BUDGET = 5600  # 2026-11-25 raised for plan.py + frames --shots, see .agents/notes/implemented/feature/2026-11-25-long-form-recap.md
MAX_PY_FILES_PER_SKILL = 20
SKILL_MD_CHAR_BUDGET = 4300  # 2026-11-25 raised for the long-form entry points, same note
REFERENCE_CHAR_BUDGET = 2500
SKILL_TOTAL_CHAR_BUDGET = 22000

CORE_MODULES = frozenset({
    "probe", "frames", "transcribe", "tts", "render", "check",
    "_common", "_audio", "_subtitles",
})
CORE_CLI_MODULES = frozenset({"probe", "frames", "transcribe", "tts", "render", "check"})
COMPANION_MODULES = frozenset({"export_jianying", "dashboard", "packaging", "library", "plan"})

PUBLIC_ENTRYPOINTS = (
    "skills/video-recap/scripts/probe.py",
    "skills/video-recap/scripts/frames.py",
    "skills/video-recap/scripts/transcribe.py",
    "skills/video-recap/scripts/tts.py",
    "skills/video-recap/scripts/render.py",
    "skills/video-recap/scripts/check.py",
    "skills/video-recap/scripts/export_jianying.py",
    "skills/video-recap/scripts/dashboard.py",
    "skills/video-recap/scripts/packaging.py",
    "skills/video-recap/scripts/library.py",
    "skills/video-recap/scripts/plan.py",
    "skills/video-reference/scripts/analyze_reference.py",
)


def _test_functions(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    ]


def _body_fingerprint(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    body = node.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    return ast.dump(ast.Module(body=body, type_ignores=[]), include_attributes=False)


def test_test_functions_do_not_repeat_an_exact_body():
    seen = {}
    duplicates = []
    for path in sorted((ROOT / "tests").rglob("test_*.py")):
        for node in _test_functions(path):
            fingerprint = _body_fingerprint(node)
            location = f"{path.relative_to(ROOT)}::{node.name}"
            if fingerprint in seen:
                duplicates.append((seen[fingerprint], location))
            else:
                seen[fingerprint] = location

    assert not duplicates, (
        f"Duplicate test bodies should be parametrized or have one canonical owner: {duplicates}"
    )


def test_canonical_runner_includes_every_test_group():
    runner_path = ROOT / "scripts" / "test.py"
    spec = importlib.util.spec_from_file_location("canonical_test_runner", runner_path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    discovered = {
        path.name
        for path in (ROOT / "tests").iterdir()
        if path.is_dir() and any(path.glob("test_*.py"))
    }

    assert len(runner.GROUPS) == len(set(runner.GROUPS))
    assert set(runner.GROUPS) == discovered


def _script_modules():
    return sorted(
        path
        for path in (ROOT / "skills").glob("*/scripts/**/*.py")
        if "__pycache__" not in path.parts
    )


def _module_name(scripts_dir: Path, path: Path) -> str:
    parts = path.relative_to(scripts_dir).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def _imported_top_level_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".", 1)[0])
    return imported


def test_all_skill_script_modules_stay_within_the_strict_line_and_file_budgets():
    modules = _script_modules()
    oversized = {
        str(path.relative_to(ROOT)): _line_count(path)
        for path in modules
        if _line_count(path) > MAX_SCRIPT_MODULE_LINES
    }
    assert not oversized, (
        f"Every skill Python module must stay <= {MAX_SCRIPT_MODULE_LINES} lines: {oversized}"
    )

    total_loc = sum(_line_count(path) for path in modules)
    assert total_loc <= SKILL_TOTAL_PY_LOC_BUDGET, (
        f"Total runtime Python LOC ({total_loc}) exceeds {SKILL_TOTAL_PY_LOC_BUDGET}; "
        "cut equivalent code or raise SKILL_TOTAL_PY_LOC_BUDGET with an agent note."
    )

    per_skill: dict[str, int] = defaultdict(int)
    for path in modules:
        per_skill[path.relative_to(ROOT / "skills").parts[0]] += 1
    too_many = {k: v for k, v in per_skill.items() if v > MAX_PY_FILES_PER_SKILL}
    assert not too_many, (
        f"Each skill may have at most {MAX_PY_FILES_PER_SKILL} .py files: {too_many}"
    )


def test_skill_markdown_stays_within_character_budgets():
    total = 0
    violations = []
    for path in sorted((ROOT / "skills").rglob("*.md")):
        size = len(path.read_text(encoding="utf-8"))
        total += size
        rel = path.relative_to(ROOT / "skills").as_posix()
        if path.name == "SKILL.md" and size > SKILL_MD_CHAR_BUDGET:
            violations.append(f"{rel}: {size} > {SKILL_MD_CHAR_BUDGET} chars")
        elif path.name != "SKILL.md" and size > REFERENCE_CHAR_BUDGET:
            violations.append(f"{rel}: {size} > {REFERENCE_CHAR_BUDGET} chars")
    if total > SKILL_TOTAL_CHAR_BUDGET:
        violations.append(f"total skills/**/*.md: {total} > {SKILL_TOTAL_CHAR_BUDGET} chars")
    assert not violations, (
        "Skill prose exceeded attention budget; trim equivalent prose or raise budget with rationale:\n"
        + "\n".join(violations)
    )


def test_core_and_companion_isolation_is_strictly_unidirectional():
    scripts_dir = ROOT / "skills" / "video-recap" / "scripts"
    violations = []
    for path in sorted(scripts_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        mod = _module_name(scripts_dir, path).split(".", 1)[0]
        imported = _imported_top_level_modules(path)
        if mod in CORE_MODULES:
            bad = sorted(imported & COMPANION_MODULES)
            if bad:
                violations.append(f"core {mod}.py must not import companion(s) {bad}")
        if mod in COMPANION_MODULES:
            bad_peers = sorted(imported & (COMPANION_MODULES - {mod}))
            bad_clis = sorted(imported & CORE_CLI_MODULES)
            if bad_peers:
                violations.append(f"companion {mod}.py must not import peer companion(s) {bad_peers}")
            if bad_clis:
                violations.append(f"companion {mod}.py must not import core CLI(s) {bad_clis}")
    assert not violations, "Unidirectional core -> companion boundary violated:\n" + "\n".join(violations)


def test_skill_scripts_do_not_import_other_skill_scripts():
    module_owners = defaultdict(set)
    skill_script_dirs = {}
    for skill_dir in sorted((ROOT / "skills").iterdir()):
        scripts_dir = skill_dir / "scripts"
        if not scripts_dir.is_dir():
            continue
        paths = sorted(p for p in scripts_dir.rglob("*.py") if "__pycache__" not in p.parts)
        dotted_modules = {_module_name(scripts_dir, p) for p in paths}
        top_level_modules = {name.split(".", 1)[0] for name in dotted_modules}
        local_modules = dotted_modules | top_level_modules
        skill_script_dirs[skill_dir.name] = (scripts_dir, local_modules, paths)
        for module in top_level_modules:
            module_owners[module].add(skill_dir.name)

    violations = []
    for skill_name, (_scripts_dir, local_modules, paths) in skill_script_dirs.items():
        other_script_paths = {
            f"skills/{other_skill}/scripts"
            for other_skill in skill_script_dirs
            if other_skill != skill_name
        }
        for path in paths:
            source = path.read_text(encoding="utf-8")
            for other_path in sorted(other_script_paths):
                if other_path in source:
                    violations.append(f"{path.relative_to(ROOT)} references cross-skill path {other_path}")
            for module in sorted(_imported_top_level_modules(path)):
                if module in module_owners and module not in local_modules:
                    owners = sorted(module_owners[module])
                    violations.append(
                        f"{path.relative_to(ROOT)} imports {module!r} owned by other skill(s) {owners}"
                    )

    assert not violations, f"Every skill must remain self-contained: {violations}"


def test_skill_local_import_graphs_are_acyclic():
    cycles = []
    for scripts_dir in sorted((ROOT / "skills").glob("*/scripts")):
        paths = [p for p in scripts_dir.rglob("*.py") if "__pycache__" not in p.parts]
        modules = {_module_name(scripts_dir, p): p for p in paths}
        graph = {name: set() for name in modules}
        for name, path in modules.items():
            for top_level in _imported_top_level_modules(path):
                if top_level in modules:
                    graph[name].add(top_level)

        visited, active = set(), []

        def visit(module):
            if module in active:
                cycle = active[active.index(module):] + [module]
                cycles.append(f"{scripts_dir.parent.name}: {' -> '.join(cycle)}")
                return
            if module in visited:
                return
            active.append(module)
            for dependency in sorted(graph[module]):
                visit(dependency)
            active.pop()
            visited.add(module)

        for module in sorted(graph):
            visit(module)

    assert not cycles, f"Skill-local script imports must remain acyclic: {cycles}"


def test_public_entrypoints_have_no_private_compatibility_surface():
    violations = []
    for relative in PUBLIC_ENTRYPOINTS:
        path = ROOT / relative
        assert path.is_file(), f"Missing public entrypoint: {relative}"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        private_imports = []
        declared_exports = None
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                private_imports.extend(
                    alias.asname or alias.name
                    for alias in node.names
                    if (alias.asname or alias.name).startswith("_")
                )
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "__all__"
                for target in node.targets
            ):
                declared_exports = ast.literal_eval(node.value)

        if private_imports:
            violations.append(f"{relative} imports private compatibility symbols: {private_imports}")
        if declared_exports is None:
            violations.append(f"{relative} must declare its public __all__")
        elif any(str(name).startswith("_") for name in declared_exports):
            violations.append(f"{relative} exports private compatibility symbols: {declared_exports}")

    assert not violations, f"Entrypoints must expose public APIs only: {violations}"


def test_no_production_scripts_mechanically_consume_reference_outputs():
    recap_scripts = ROOT / "skills" / "video-recap" / "scripts"
    forbidden = ("production_reference.json", "reference_metrics.json", "REFERENCE_NOTES.md")
    offenders = []
    for path in sorted(recap_scripts.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        # Read-only dashboard.py may display REFERENCE_NOTES.md prose to humans.
        checked = (
            ("production_reference.json", "reference_metrics.json")
            if path.name == "dashboard.py"
            else forbidden
        )
        hits = [name for name in checked if name in text]
        if hits:
            offenders.append((str(path.relative_to(ROOT)), hits))
    assert not offenders, (
        "Reference outputs are prose-only inspiration for the Agent, never mechanical inputs: "
        f"{offenders}"
    )


def _text_io_without_encoding(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Name) and func.id == "open":
        encoding_pos, mode_pos = 3, 1
    elif isinstance(func, ast.Attribute) and func.attr == "read_text":
        encoding_pos, mode_pos = 0, None
    elif isinstance(func, ast.Attribute) and func.attr == "write_text":
        encoding_pos, mode_pos = 1, None
    elif (
        isinstance(func, ast.Attribute)
        and func.attr == "open"
        and isinstance(func.value, (ast.Name, ast.BinOp, ast.Call))
        and ast.unparse(func.value) not in ("webbrowser", "wave", "Image", "zipfile", "tarfile")
        and "opener" not in ast.unparse(func.value)
    ):
        encoding_pos, mode_pos = 2, 0
    else:
        return False
    if any(kw.arg in ("encoding", None) for kw in call.keywords) or len(call.args) > encoding_pos:
        return False
    mode = next((kw.value for kw in call.keywords if kw.arg == "mode"), None)
    if mode is None and mode_pos is not None and len(call.args) > mode_pos:
        mode = call.args[mode_pos]
    return not (isinstance(mode, ast.Constant) and "b" in str(mode.value))


def test_text_file_io_names_its_encoding():
    violations = []
    for base in ("skills", "tests", "scripts", "tools"):
        for path in sorted((ROOT / base).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            violations.extend(
                f"{path.relative_to(ROOT)}:{node.lineno}"
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and _text_io_without_encoding(node)
            )
    assert not violations, 'text I/O without encoding="utf-8":\n' + "\n".join(violations)
