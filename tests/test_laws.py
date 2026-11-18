"""The laws of the tree, as tests: the kernel names no world and makes no
model calls, a dict world needs neither the grid extra nor pillow, the manual
teaches only what the command line has, the writing rule holds, and the
shipped worlds meet the adapter contract."""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from assay import cli, grammar, world
from conftest import REPO, SRC

KERNEL = sorted((SRC / "assay").rglob("*.py"))
TEXT = {".py", ".md", ".toml", ".yml", ".txt", ".json"}


def _tracked() -> list[Path]:
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout
    return [REPO / name for name in listed.split("\0") if name and (REPO / name).is_file()]


def test_the_kernel_names_no_world_and_calls_no_model() -> None:
    world_names = re.compile(r"arc[-_ ]?agi|factorio|oolong|counter_world", re.IGNORECASE)
    clients = re.compile(r"^\s*(?:import|from)\s+(anthropic|openai|litellm|requests|httpx|urllib|http)\b", re.MULTILINE)
    for file in KERNEL:
        text = file.read_text()
        assert not world_names.search(text), file
        assert not clients.search(text), file


def test_a_dict_world_loads_neither_the_grid_extra_nor_pillow() -> None:
    probe = (
        "import sys, assay.cli, assay.daemon, assay.show, assay.modules, assay.diagnose\n"
        "print(sorted(name for name in sys.modules if name.startswith(('assay.grid', 'PIL'))))"
    )
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, env={"PYTHONPATH": str(SRC)})
    assert done.stdout.strip() == "[]", done.stdout + done.stderr


def test_the_writing_rule() -> None:
    """No em dash and no arrow in the source or the docs, and no machine path."""
    typography = re.compile("[" + chr(0x2014) + chr(0x2192) + "]")
    machine = re.compile("/" + "Users/|/" + "home/")
    for file in _tracked():
        if file.suffix in TEXT or file.name == "assay":
            text = file.read_text()
            assert not typography.search(text), file
            assert not machine.search(text), file


def _parsers(parser: Any) -> list[Any]:
    """A parser and every subcommand parser under it."""
    found = [parser]
    for action in parser._actions:
        for sub in getattr(action, "choices", None) or {}:
            if hasattr(action.choices[sub], "_actions"):
                found += _parsers(action.choices[sub])
    return found


def test_the_manual_teaches_only_what_the_command_line_has() -> None:
    manual = (REPO / "CONSTITUTION.md").read_text()
    parsers = _parsers(cli.parser())
    commands = {name for parser in parsers for action in parser._actions for name in (getattr(action, "choices", None) or {})}
    flags = {flag for parser in parsers for action in parser._actions for flag in action.option_strings}
    used = set(re.findall(r'"\$ASSAY" ([a-z]+)', manual)) | set(re.findall(r"`assay ([a-z]+)", manual))
    assert used and used <= commands, used - commands
    assert not {"start", "stop"} & used, "the run is the operator's"
    assert set(re.findall(r"(--[a-z][a-z-]+)", manual)) <= flags
    # The table of outcome forms is the grammar's, and an example of each form parses.
    assert re.findall(r"^\| `([^`|]+)`", manual, re.MULTILINE) == [
        "noop", "change", "level+1", "win", "verify:PATH.py", "ch NAME = V [± TOL]", "ch NAME delta = / >= / <= V",
        "ch NAME delta sign +", "ch NAME crosses V [from below/above]",
    ]
    examples = (
        "noop; change; level+1; win; verify:checks/a.py; ch tick = 3; ch rate = 20 ± 5; ch n delta = -5; "
        "ch n delta >= 1; ch n delta <= 1; ch n delta sign +; ch n delta sign -; ch n crosses 16; "
        "ch n crosses 16 from below; ch n crosses 16 from above; change @within 5s"
    )
    assert all(outcome["kind"] != "note" for outcome in grammar.parse(examples))
    for line in re.findall(r'--predict "([^"]+)"', manual):
        assert all(outcome["kind"] != "note" for outcome in grammar.parse(line)), line
    for step in re.findall(r'--step "[^:"]+ :: ([^"]+)"', manual):
        assert all(outcome["kind"] != "note" for outcome in grammar.parse(step)), step


@pytest.mark.parametrize("pack", sorted(path.name for path in (REPO / "worlds").iterdir() if path.is_dir()))
def test_a_shipped_world_is_an_adapter_and_a_valid_registry(pack: str) -> None:
    directory = REPO / "worlds" / pack
    registry = world.load_registry(directory / "registry.json")
    assert registry["actions"] and registry["goal"]["text"]
    source = (directory / "adapter.py").read_text()
    assert "def factory(root" in source and "def step(self, action" in source and "def observation(self)" in source
    assert "import assay" not in source and "from assay" not in source


def test_the_arc_adapter_against_a_stand_in_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The adapter's own logic, with the client library stood in for: the
    observation it hands the kernel, and a reset on an untouched level."""

    class Frame:
        def __init__(self, level: int, state: str = "NOT_FINISHED") -> None:
            self.frame = [[[0, 1], [2, 3]]]
            self.state = types.SimpleNamespace(value=state)
            self.levels_completed, self.win_levels = level, 2
            self.available_actions = [types.SimpleNamespace(value=1), 6, 0]

    class Environment:
        def __init__(self) -> None:
            self.level, self.stepped = 0, []
            self.observation_space = Frame(0)

        def step(self, action: str, data: Any = None, reasoning: Any = None) -> Frame:
            self.stepped.append((action, data))
            self.level += action == "ACTION5"
            self.observation_space = Frame(self.level, "WIN" if self.level == 2 else "NOT_FINISHED")
            return self.observation_space

    closed = []
    environment = Environment()
    arcade = types.SimpleNamespace(make=lambda game, seed: environment, close_scorecard=lambda: closed.append(True))
    monkeypatch.setitem(sys.modules, "arc_agi", types.SimpleNamespace(
        Arcade=lambda **options: arcade, OperationMode=types.SimpleNamespace(OFFLINE=1, NORMAL=2, COMPETITION=3),
    ))
    monkeypatch.setitem(sys.modules, "arcengine", types.SimpleNamespace(
        GameAction=types.SimpleNamespace(from_name=lambda name: name),
    ))
    monkeypatch.setenv("ASSAY_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("ARC_API_KEY", "k")
    monkeypatch.delenv("ASSAY_ARC_MODE", raising=False)
    spec = importlib.util.spec_from_file_location("arc_pack", REPO / "worlds" / "arc-agi-3" / "adapter.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    session = module.factory(tmp_path, {"world_id": "ft09", "seed": 0})
    assert session.replayable is True
    observed = world.observe(session.observation)
    assert observed == {
        "state": "NOT_FINISHED", "levels_completed": 0, "win_levels": 2,
        "available_actions": ["ACTION1", "ACTION6", "RESET"], "frames": [["01", "23"]],
    }
    session.step("RESET", None)  # nothing done on this level yet: the game is not restarted
    assert environment.stepped == []
    session.step("ACTION6", {"x": 1, "y": 0})
    session.step("RESET", None)
    session.step("RESET", None)
    assert environment.stepped == [("ACTION6", {"x": 1, "y": 0}), ("RESET", None)]
    session.step("ACTION5", None)
    session.step("RESET", None)  # a fresh level again
    assert len(environment.stepped) == 3 and not closed
    assert session.step("ACTION5", None)["state"] == "WIN" and closed == [True]
    json.dumps(world.observe(session.observation))
