"""Plugin packaging guards: plugin.json has exactly the 4 Anthropic keys, the two self-contained
skills (video-recap for unified production and video-reference for on-demand reference breakdown)
are both user-invocable with single-line frontmatter descriptions, and marketplace.json is the
single Claude-compatible marketplace catalog (also imported by OpenClaw's `plugins install`); the
plugin version stays single-sourced in plugin.json (bump it during explicit release preparation so
installed users receive each shipped version)."""
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]


def test_plugin_manifest_has_exactly_four_keys():
    manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert set(manifest.keys()) == {"name", "version", "description", "author"}
    assert manifest["name"] == "video-recap-skills"
    assert re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", manifest["version"])
    version_text = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert version_text == manifest["version"]
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{manifest['version']}]" in changelog


def test_marketplace_json_present_and_valid():
    mp = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert mp["name"] == "video-recap"
    assert mp["owner"]["name"]
    plugins = mp["plugins"]
    assert len(plugins) == 1
    entry = plugins[0]
    # the marketplace entry name must match the plugin manifest name
    assert entry["name"] == plugin["name"] == "video-recap-skills"
    assert entry["source"] == "./"
    # source "./" must point at the dir that actually holds the plugin manifest
    assert (ROOT / ".claude-plugin" / "plugin.json").exists()
    # version stays single-sourced in plugin.json; do not pin it in the marketplace entry
    assert "version" not in entry


def _frontmatter(skill_name):
    path = ROOT / "skills" / skill_name / "SKILL.md"
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", text, re.DOTALL)
    assert match, f"{path} must start with YAML frontmatter"

    values = {}
    for line in match.group(1).splitlines():
        if not line or line[0].isspace():
            continue
        key, separator, raw_value = line.partition(":")
        assert separator, (path, line)
        value = raw_value.strip()
        if value in {"true", "false"}:
            values[key] = value == "true"
        else:
            values[key] = value.strip("\"'")
    return values


def test_every_discovered_skill_has_matching_frontmatter_identity():
    skill_dirs = sorted(
        path
        for path in (ROOT / "skills").iterdir()
        if path.is_dir() and (path / "SKILL.md").is_file()
    )
    assert [p.name for p in skill_dirs] == ["video-recap", "video-reference"]
    for skill_dir in skill_dirs:
        frontmatter = _frontmatter(skill_dir.name)
        assert frontmatter.get("name") == skill_dir.name
        assert frontmatter.get("user-invocable", True) is True


def test_skill_descriptions_are_single_line_values():
    # OpenClaw falls back to a single-line-only frontmatter parser when YAML parsing fails;
    # a block scalar (`description: >`) or a wrapped plain value would read as empty there.
    for skill_dir in sorted((ROOT / "skills").iterdir()):
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            continue
        text = skill_md.read_text(encoding="utf-8")
        match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", text, re.DOTALL)
        assert match, f"{skill_md} must start with YAML frontmatter"
        raw_lines = match.group(1).splitlines()
        for idx, line in enumerate(raw_lines):
            if line.startswith("description:"):
                assert idx + 1 >= len(raw_lines) or not raw_lines[idx + 1][:1].isspace(), (
                    f"{skill_md}: description must not wrap onto indented continuation lines"
                )
        description = _frontmatter(skill_dir.name).get("description")
        assert isinstance(description, str) and description, skill_dir.name
        assert description[0] not in ">|", skill_dir.name
        assert len(description) <= 1024, (skill_dir.name, len(description))
