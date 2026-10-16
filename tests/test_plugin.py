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
# The one stub, in two flavors: the command-world engine (the local and repo
# archetypes) and the service engine.
STUBS = {
    "command": WORLDS / "archetypes" / "local" / "template" / "adapter.py",
    "service": WORLDS / "archetypes" / "service" / "template" / "adapter.py",
}
CODE = re.compile(r"^[MSH]-[ERF]-[CNP]-[0UX]$")
MANIFEST_FIELDS = {
    "name", "displayName", "version", "description", "author", "homepage", "repository", "license", "keywords",
}

sys.path.insert(0, str(WORLDS / "archetypes"))
from command_world import load_recipe  # noqa: E402
from service_world import load_service_recipe  # noqa: E402


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
    assert [path.parent.name for path in skills] == ["attach", "report", "run"]
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


def test_the_run_skill_drives_the_session_mode_and_names_its_outcomes():
    run = (REPO / "skills" / "run" / "SKILL.md").read_text()
    assert "${CLAUDE_PLUGIN_ROOT}/tools/eval/__main__.py session --run-dir <run dir>" in run
    session = (REPO / "tools" / "eval" / "session.py").read_text()
    outcomes = set(re.findall(r'outcome(?:, detail)? = "([A-Z_]+)"', session)) | set(re.findall(r'"(WIN|CAP|USD_CAP)"', session))
    outcomes |= set(re.findall(r'record\["ended_because"\] = outcome = "([A-Z_]+)"', session))
    assert outcomes == {
        "WIN", "CAP", "USD_CAP", "STOPPED", "SESSIONS_EXHAUSTED", "TIME_EXHAUSTED", "USD_EXHAUSTED",
        "START_FAILED", "OWNER_NEEDED", "SELF_STOP", "PLAYER_FAILING",
    }
    for outcome in outcomes:
        assert outcome in run, outcome
    # The player's prompt tells it how to declare a stop, in the gate's words.
    start = (REPO / "skills" / "attach" / "start.md").read_text()
    assert "begins with STOP: and says why" in start and "/assay:run" in start
    assert "/assay:report" in run


def test_the_report_skill_audits_then_reports_and_reads_a_stop_against_the_gaps():
    skill = (REPO / "skills" / "report" / "SKILL.md").read_text()
    assert "${CLAUDE_PLUGIN_ROOT}/bin/assay --run-dir <run dir> audit" in skill
    assert "${CLAUDE_PLUGIN_ROOT}/tools/eval/__main__.py report --run-dir <run dir>" in skill
    assert '--check "python3 <pack>/checks/score.py --run {run_dir} --json"' in skill
    for word in ("SELF_STOP", "INVALID FOR SCORING", "Never exercised", "/assay:run"):
        assert word in skill, word
    # Every pack's checker takes the run directory as the skill passes it.
    for pack in _packs():
        assert "--run" in (pack / "checks" / "score.py").read_text()
    # The document's section names the skill quotes are the report's own.
    report = (REPO / "tools" / "eval" / "report.py").read_text()
    assert '"## Never exercised"' in report and "### The external check" in report


def _packs() -> list[Path]:
    return sorted(path for path in (WORLDS / "recipes").iterdir() if path.is_dir())


def _templates() -> list[Path]:
    return sorted((WORLDS / "archetypes").glob("*/template"))


def test_the_catalog_lists_every_pack():
    readme = (WORLDS / "README.md").read_text()
    packs = _packs()
    assert [path.name for path in packs] == ["legacy-api", "spreadsheet-reconcile"]
    for pack in packs:
        assert f"`recipes/{pack.name}`" in readme
        for name in ("recipe.json", "registry.json", "adapter.py", "README.md"):
            assert (pack / name).is_file(), f"{pack.name}: {name}"


def _body(path: Path) -> str:
    return path.read_text().split("from __future__ import annotations", 1)[1]


def test_the_two_stubs_differ_only_in_their_engine():
    command, service = (_body(path).splitlines() for path in STUBS.values())
    differing = [(a, b) for a, b in zip(command, service) if a != b]
    assert len(command) == len(service) and len(differing) == 4, differing


def test_every_pack_and_template_agrees_with_itself():
    for world in (*_packs(), *_templates()):
        raw = json.loads((world / "recipe.json").read_text())
        flavor = "service" if raw["world"].get("mode") == "service" else "command"
        recipe = load_service_recipe(world / "recipe.json") if flavor == "service" else load_recipe(world / "recipe.json")
        registry = json.loads((world / "registry.json").read_text())
        assert sorted(action["name"] for action in registry["actions"]) == sorted(recipe.actions), world
        assert CODE.match(recipe.card["code"]), world
        assert recipe.card["clock"] in ("s", "h", "d", "rt") and recipe.card["family"], world
        assert _body(world / "adapter.py") == _body(STUBS[flavor]), f"{world}/adapter.py differs from the {flavor} stub"
        assert recipe.extra.get("player", {}).get("note"), world
