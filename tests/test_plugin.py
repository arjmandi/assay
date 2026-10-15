"""The repository as a Claude Code plugin and its catalog of worlds: the
marketplace names the repository root as the plugin, the manifest carries the
fields the directory shows, every skill's frontmatter names its directory and
every plugin path a skill names exists, the launcher is in bin/ so the plugin
puts it on the Bash PATH, no hook file applies to every session, and every
pack and template agrees with itself: the recipe loads, its actions are the
registry's, its card has a world code, and its adapter is the one stub."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORLDS = REPO / "worlds"
STUB = WORLDS / "archetypes" / "local" / "template" / "adapter.py"
CODE = re.compile(r"^[MSH]-[ERF]-[CNP]-[0UX]$")
MANIFEST_FIELDS = {
    "name", "displayName", "version", "description", "author", "homepage", "repository", "license", "keywords",
}

sys.path.insert(0, str(WORLDS / "archetypes"))
from command_world import load_recipe  # noqa: E402


def _frontmatter(path: Path) -> tuple[dict[str, str], str]:
    text = path.read_text()
    assert text.startswith("---\n"), path
    head, body = text[4:].split("\n---\n", 1)
    fields = {}
    for line in head.splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip().strip('"')
    return fields, body


def test_the_marketplace_names_the_repository_root_as_the_plugin():
    marketplace = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text())
    assert marketplace["name"] == "assay"
    assert marketplace["owner"]["name"]
    assert [(entry["name"], entry["source"]) for entry in marketplace["plugins"]] == [("assay", ".")]


def test_the_manifest():
    manifest = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "assay"
    assert set(manifest) <= MANIFEST_FIELDS
    assert manifest["license"] == "Apache-2.0"
    assert manifest["homepage"].startswith("https://") and manifest["description"]


def test_the_launcher_is_where_the_plugin_puts_it_on_path():
    launcher = REPO / "bin" / "assay"
    assert launcher.is_file() and os.access(launcher, os.X_OK)
    assert sorted(path.name for path in (REPO / "bin").iterdir()) == ["assay"]


def test_no_hook_file_applies_to_every_session():
    # The hooks are installed per run directory by `assay hooks install`; a
    # plugin hooks file would fire in every session the plugin is enabled in.
    assert not (REPO / "hooks" / "hooks.json").exists()
    assert not (REPO / ".mcp.json").exists()


def test_every_skill_names_its_directory_and_its_paths_exist():
    skills = sorted((REPO / "skills").glob("*/SKILL.md"))
    assert [path.parent.name for path in skills] == ["attach"]
    for path in skills:
        fields, body = _frontmatter(path)
        assert fields["name"] == path.parent.name
        assert len(fields["description"]) > 80
        for relative in re.findall(r"\$\{CLAUDE_PLUGIN_ROOT\}/([A-Za-z0-9_./<>|-]+)", body):
            relative = relative.rstrip(".")
            if "<" in relative:
                relative = relative.split("<")[0]
            assert (REPO / relative).exists(), f"{path}: {relative}"


def test_the_attach_skill_carries_its_rubric_and_start_procedure():
    attach = REPO / "skills" / "attach"
    assert sorted(path.name for path in attach.iterdir()) == ["SKILL.md", "rubric.md", "start.md"]
    rubric = (attach / "rubric.md").read_text()
    for question in ("**Q1. Where does the agent act?**", "**Q2. After one action", "**Q3. How do you know", "**Q4. What is the worst"):
        assert question in rubric
    assert "WORLD CARD" in rubric
    start = (attach / "start.md").read_text()
    assert "--owner-token-file ~/assay/tokens/" in start and "hooks install --check" in start


def _packs() -> list[Path]:
    return sorted(path for path in (WORLDS / "recipes").iterdir() if path.is_dir())


def _templates() -> list[Path]:
    return sorted((WORLDS / "archetypes").glob("*/template"))


def test_the_catalog_lists_every_pack():
    readme = (WORLDS / "README.md").read_text()
    packs = _packs()
    assert [path.name for path in packs] == ["spreadsheet-reconcile"]
    for pack in packs:
        assert f"`recipes/{pack.name}`" in readme
        for name in ("recipe.json", "registry.json", "adapter.py", "README.md"):
            assert (pack / name).is_file(), f"{pack.name}: {name}"


def test_every_pack_and_template_agrees_with_itself():
    stub_body = STUB.read_text().split("from __future__ import annotations", 1)[1]
    for world in (*_packs(), *_templates()):
        recipe = load_recipe(world / "recipe.json")
        registry = json.loads((world / "registry.json").read_text())
        assert sorted(action["name"] for action in registry["actions"]) == sorted(recipe.actions), world
        assert CODE.match(recipe.card["code"]), world
        assert recipe.card["clock"] in ("s", "h", "d", "rt") and recipe.card["family"], world
        body = (world / "adapter.py").read_text().split("from __future__ import annotations", 1)[1]
        assert body == stub_body, f"{world}/adapter.py differs from the stub below its docstring"
        assert recipe.extra.get("player", {}).get("note"), world
