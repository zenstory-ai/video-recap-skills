import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = ROOT / "skills"
SKILL_NAMES = tuple(
    path.name
    for path in sorted(SKILLS_ROOT.iterdir())
    if path.is_dir() and (path / "SKILL.md").is_file()
)
STAGE_SKILL_NAMES = tuple(name for name in SKILL_NAMES if name != "video-recap")


def _skill_path(skill_name: str) -> Path:
    return ROOT / "skills" / skill_name / "SKILL.md"


def _markdown_headings_outside_fences(text: str, prefix: str) -> list[str]:
    headings = []
    in_fence = False
    for line in text.splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence and line.startswith(prefix):
            headings.append(line)
    return headings


def _json_fences(path: Path) -> list[dict | list]:
    text = path.read_text(encoding="utf-8")
    return [json.loads(raw) for raw in re.findall(r"```json\s*\n(.*?)\n```", text, re.DOTALL)]


def test_all_skill_headings_are_sequential_numbered_chinese():
    for skill_name in SKILL_NAMES:
        headings = _markdown_headings_outside_fences(
            _skill_path(skill_name).read_text(encoding="utf-8"),
            "## ",
        )
        assert headings, skill_name
        numbers = []
        for heading in headings:
            match = re.fullmatch(r"## (\d+)\. (.+)", heading)
            assert match, (skill_name, heading)
            numbers.append(int(match.group(1)))
            assert re.search(r"[\u3400-\u9fff]", match.group(2)), (skill_name, heading)
        assert numbers == list(range(1, len(numbers) + 1)), (skill_name, numbers)


def test_skill_json_examples_are_parseable_and_timeline_v3_contract_is_documented():
    for path in sorted(SKILLS_ROOT.rglob("*.md")):
        _json_fences(path)

    schema_doc = SKILLS_ROOT / "video-recap" / "references" / "timeline-schema.md"
    examples = _json_fences(schema_doc)
    assert examples, "timeline-schema.md must include a canonical timeline.json example"
    timeline = examples[0]
    assert timeline["schema_version"] == 3
    assert {"canvas", "sources", "video", "audio", "overlays", "subtitles", "mix"} <= set(timeline)


def test_markdown_references_are_local_and_resolve_inside_each_skill():
    for skill_name in SKILL_NAMES:
        skill_dir = ROOT / "skills" / skill_name
        for markdown_path in skill_dir.rglob("*.md"):
            text = markdown_path.read_text(encoding="utf-8")
            references = re.findall(r"`((?:\.\.?/|references/)[^`\n]+\.md)`", text)
            for reference in references:
                base = skill_dir if reference.startswith("references/") else markdown_path.parent
                resolved = (base / reference).resolve()
                try:
                    resolved.relative_to(skill_dir.resolve())
                except ValueError:
                    pytest.fail(f"{markdown_path}: reference escapes its skill: {reference}")
                assert resolved.is_file(), (markdown_path, reference)

            for script_name in re.findall(r"`([A-Za-z0-9_.-]+\.py)`", text):
                assert (skill_dir / "scripts" / script_name).is_file(), (markdown_path, script_name)


def test_stage_sources_never_point_to_a_sibling_skill_path():
    paths = [
        markdown_path
        for skill_name in SKILL_NAMES
        for markdown_path in (ROOT / "skills" / skill_name).rglob("*.md")
    ]
    paths.extend(
        source_path
        for skill_name in SKILL_NAMES
        for source_path in (ROOT / "skills" / skill_name / "scripts").rglob("*.py")
        if "__pycache__" not in source_path.parts
    )

    for path in paths:
        current_name = path.relative_to(ROOT / "skills").parts[0]
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"(?:\.\./)+video-[a-z-]+/", text), path
        assert not re.search(r"\bskills/video-[a-z-]+/", text), path
        for target_name in re.findall(r"\b(video-[a-z-]+)/(?:references|scripts)/", text):
            assert target_name == current_name, (path, target_name)
        if path.suffix == ".md":
            for target_name in re.findall(r"`(video-[a-z-]+)/[^`]+\.(?:py|md)`", text):
                assert target_name == current_name, (path, target_name)


def test_stage_markdown_never_names_a_sibling_skill():
    """On-demand sibling skills describe artifact contracts, never another skill implementation."""
    known_names = set(SKILL_NAMES)
    for skill_name in STAGE_SKILL_NAMES:
        skill_dir = SKILLS_ROOT / skill_name
        sibling_names = known_names - {skill_name}
        for markdown_path in skill_dir.rglob("*.md"):
            text = markdown_path.read_text(encoding="utf-8")
            mentioned = sorted(name for name in sibling_names if re.search(rf"\b{re.escape(name)}\b", text))
            assert not mentioned, (markdown_path, mentioned)


@pytest.mark.parametrize("skill_name", SKILL_NAMES)
def test_each_skill_imports_all_python_modules_from_an_isolated_copy(skill_name, tmp_path):
    isolated_skill = tmp_path / skill_name
    shutil.copytree(ROOT / "skills" / skill_name, isolated_skill)
    scripts_dir = isolated_skill / "scripts"
    code = """
import importlib
import json
from pathlib import Path
import sys

scripts_dir = Path.cwd().resolve()
sys.path.insert(0, str(scripts_dir))

def module_name(path):
    parts = path.relative_to(scripts_dir).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)

names = sorted(
    name
    for name in (
        module_name(path)
        for path in scripts_dir.rglob("*.py")
        if "__pycache__" not in path.parts
    )
    if name
)
for name in names:
    module = importlib.import_module(name)
    module_path = Path(module.__file__).resolve()
    assert scripts_dir in module_path.parents, (name, module_path, scripts_dir)
print(json.dumps(names))
"""

    result = subprocess.run(
        [sys.executable, "-I", "-c", code],
        cwd=scripts_dir,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    def _module_name(path):
        parts = path.relative_to(scripts_dir).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        return ".".join(parts)

    expected = sorted(
        name
        for name in (
            _module_name(path)
            for path in scripts_dir.rglob("*.py")
            if "__pycache__" not in path.parts
        )
        if name
    )
    assert json.loads(result.stdout) == expected
