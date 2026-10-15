"""The command-world engine behind the local and repo archetypes
(worlds/archetypes/command_world.py): the recipe's checks, the rules over the
observation, and the local archetype's template world through the real CLI
and daemon: a refusal through the observation, a RESET that rewinds the unit,
a resume that replays the journal, a WIN, and a world-script fault that
spends nothing and puts the copy back."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest
from conftest import run_cli, stop_run

REPO = Path(__file__).resolve().parents[1]
ARCHETYPES = REPO / "worlds" / "archetypes"
TEMPLATE = ARCHETYPES / "local" / "template"

sys.path.insert(0, str(ARCHETYPES))
import command_world  # noqa: E402
from command_world import RecipeError, holds, load_recipe, read_path  # noqa: E402


def _world_module():
    spec = importlib.util.spec_from_file_location("switchboard_world", TEMPLATE / "world.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _recipe(tmp_path: Path, **world_overrides) -> Path:
    raw = json.loads((TEMPLATE / "recipe.json").read_text())
    raw["world"].update(world_overrides)
    target = tmp_path / "recipe" / "recipe.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(TEMPLATE / "world.py", target.parent / "world.py")
    target.write_text(json.dumps(raw))
    return target


def test_the_template_recipe_loads():
    recipe = load_recipe(TEMPLATE / "recipe.json")
    assert recipe.name == "switchboard"
    assert recipe.mode == "copy" and recipe.archetype == "local"
    assert recipe.actions == ("TOGGLE",)
    assert recipe.win == {"path": "on", "op": "==", "value": 4}
    assert recipe.card["code"] == "M-E-C-0"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"mode": "sideways"}, "world.mode: one of ['copy', 'worktree']"),
        ({"observe": "python3 world.py observe"}, "world.observe: an argv list of non-empty strings, never a shell line"),
        ({"act": ["{python}", "{interpreter}"]}, "world.act: {interpreter} is not a placeholder"),
        ({"observe": ["{python}", "{action}"]}, "world.observe: {action} belongs to the act command alone"),
        ({"actions": ["toggle"]}, "world.actions: 'toggle' is not an action name"),
        ({"actions": ["RESET"]}, "world.actions: 'RESET' is not an action name (RESET is built in)"),
        ({"actions": ["TOGGLE", "TOGGLE"]}, "world.actions: a name is listed twice"),
        ({"timeout_seconds": 0}, "world.timeout_seconds: a number of seconds above 0"),
        ({"win": {"path": "on", "op": "~", "value": 4}}, "world.win: the op is one of"),
        ({"win": {"path": "on", "op": ">", "value": "four"}}, "world.win: > compares numbers"),
        ({"win": {"all": []}}, 'world.win: "all" takes a non-empty list of conditions'),
        ({"levels": {"total": 0}}, "world.levels.total: at least 1"),
        ({"source": "no-such-directory"}, "world.source:"),
        ({"rooms": 2}, "world.rooms: not a world key"),
    ],
)
def test_a_recipe_that_does_not_fit_is_refused_by_field(tmp_path, overrides, message):
    with pytest.raises(RecipeError) as caught:
        load_recipe(_recipe(tmp_path, **overrides))
    assert message in str(caught.value)


def test_a_worktree_recipe_needs_a_source(tmp_path):
    path = _recipe(tmp_path, mode="worktree")
    with pytest.raises(RecipeError, match="the worktree mode copies a source tree into work/; name it"):
        load_recipe(path)


def test_an_unknown_top_level_key_and_a_wrong_version_are_refused(tmp_path):
    path = _recipe(tmp_path)
    raw = json.loads(path.read_text())
    path.write_text(json.dumps({**raw, "extras": {}}))
    with pytest.raises(RecipeError, match="extras: not a recipe key"):
        load_recipe(path)
    path.write_text(json.dumps({**raw, "recipe": 2}))
    with pytest.raises(RecipeError, match="recipe: the format version must be 1"):
        load_recipe(path)


def test_conditions_over_the_data():
    data = {"on": 4, "lamps": [1, 0], "door": "open", "armed": True, "count": 1, "nested": {"x": 2.5}}
    assert holds({"path": "on", "op": "==", "value": 4}, data)
    assert holds({"path": "lamps.0", "op": "==", "value": 1}, data)
    assert holds({"path": "nested.x", "op": ">", "value": 2}, data)
    assert holds({"all": [{"path": "on", "op": ">=", "value": 4}, {"path": "door", "op": "==", "value": "open"}]}, data)
    assert holds({"any": [{"path": "on", "op": "<", "value": 0}, {"path": "door", "op": "!=", "value": "shut"}]}, data)
    assert holds({"not": {"path": "door", "op": "==", "value": "shut"}}, data)
    # A path the data does not hold makes its test false, whatever the op.
    assert not holds({"path": "missing", "op": "!=", "value": 1}, data)
    assert read_path(data, "lamps.5") is command_world.MISSING
    # JSON keeps true apart from 1.
    assert not holds({"path": "armed", "op": "==", "value": 1}, data)
    assert not holds({"path": "count", "op": "==", "value": True}, data)
    assert holds({"path": "armed", "op": "==", "value": True}, data)
    # Ordering reads numbers only.
    assert not holds({"path": "door", "op": ">", "value": 1}, data)


def test_the_world_python_falls_back_when_the_daemons_interpreter_is_gone(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "executable", str(tmp_path / "gone" / "python"))
    monkeypatch.setattr(sys, "_base_executable", str(tmp_path / "gone" / "python"), raising=False)
    assert command_world.world_python() == (shutil.which("python3") or sys.executable)


# --- the template world, through the real CLI and daemon --------------------------


def _start(run: Path, adapter: Path, *extra: str):
    return run_cli(
        run, "start", "board1", "--adapter", f"{adapter}:factory",
        "--registry", str(TEMPLATE / "registry.json"), *extra,
    )


def _last_event(run: Path) -> dict:
    lines = [line for line in (run / ".assay" / "events.jsonl").read_text().splitlines() if line.strip()]
    return json.loads(lines[-1])


def _lamps(run: Path) -> list[int]:
    return json.loads((run / ".assay" / "world" / "lamps.json").read_text())["lamps"]


def test_the_switchboard_template_end_to_end(tmp_path):
    wiring = _world_module().wiring(3)
    jammed = wiring.index(None) + 1
    working = [switch for switch in range(1, 6) if switch != jammed]
    run = tmp_path / "board"
    run.mkdir()
    try:
        started = _start(run, TEMPLATE / "adapter.py", "--seed", "3", "--owner-token-file", str(tmp_path / "board.token"))
        assert started.returncode == 0, started.stderr
        assert "STARTED | board1 | local simulator | no action-idle lease | replay recovery enabled" in started.stdout
        config = json.loads((run / ".assay" / "config.json").read_text())
        assert config["public_info"]["world"] == "switchboard"
        assert config["public_info"]["archetype"] == "local"
        assert config["public_info"]["card"]["code"] == "M-E-C-0"
        first = _last_event(run)["observation"]
        assert first["workdir"] == ".assay/world" and first["on"] == 0 and first["refusals"] == 0
        assert first["last_result"] == {"action": "START", "status": "ready"}
        for name in ("on", "refusals"):
            assert run_cli(run, "state", "declare", name, "--path", name).returncode == 0
        # The jammed switch: a refusal through the observation, spent and journaled.
        refused = run_cli(run, "act", "TOGGLE", f"switch={jammed}", "--predict", "ch refusals delta = 1; ch on = 0")
        assert refused.returncode == 0 and "RESULT | PREDICTED" in refused.stdout, refused.stdout
        result = _last_event(run)["observation"]["last_result"]
        assert result == {"action": "TOGGLE", "status": "refused", "detail": f"switch {jammed} is jammed"}
        lit = run_cli(run, "act", "TOGGLE", f"switch={working[0]}", "--predict", "ch on = 1")
        assert lit.returncode == 0 and "RESULT | PREDICTED" in lit.stdout, lit.stdout
        # RESET puts the copy back where the unit began.
        rewound = run_cli(run, "reset", "--because", "check the rewind")
        assert rewound.returncode == 0 and "RESULT | RESET" in rewound.stdout, rewound.stdout
        assert _lamps(run) == [0, 0, 0, 0]
        assert run_cli(run, "act", "TOGGLE", f"switch={working[1]}", "--predict", "ch on = 1").returncode == 0
        before = _lamps(run)
        # A resume rebuilds the copy and replays the journal through it.
        assert run_cli(run, "stop").returncode == 0
        resumed = _start(run, TEMPLATE / "adapter.py", "--seed", "3")
        assert resumed.returncode == 0, resumed.stderr
        assert "RECOVERED | board1 | local simulator | replayed 4 paid actions" in resumed.stdout
        assert _lamps(run) == before
        rest = [switch for switch in working if switch != working[1]]
        for switch in rest[:-1]:
            assert run_cli(run, "act", "TOGGLE", f"switch={switch}", "--predict", "ch on delta = 1").returncode == 0
        won = run_cli(run, "act", "TOGGLE", f"switch={rest[-1]}", "--predict", "win; ch on = 4")
        assert won.returncode == 0 and "RESULT | GAME_COMPLETE" in won.stdout, won.stdout
        summary = json.loads((run / ".assay" / "world_summary.json").read_text())
        assert summary["world"] == "switchboard" and summary["refusals"] == 1 and summary["data"]["on"] == 4
        audited = run_cli(run, "audit")
        assert "AUDIT | CLEAN" in audited.stdout
    finally:
        stop_run(run)


def test_a_world_script_fault_spends_nothing_and_puts_the_copy_back(tmp_path):
    world = tmp_path / "board"
    shutil.copytree(TEMPLATE, world)
    script = world / "world.py"
    # The fault comes after the world wrote its file: the engine must undo it.
    clicked = '    return {"status": "ok", "detail": f"switch {switch} clicked"}'
    assert clicked in script.read_text()
    script.write_text(script.read_text().replace(clicked, '    raise SystemExit("the board shorted")'))
    working = next(n for n, lamp in enumerate(_world_module().wiring(2), 1) if lamp is not None)
    run = tmp_path / "run"
    run.mkdir()
    try:
        assert _start(run, world / "adapter.py", "--seed", "2").returncode == 0
        before = (run / ".assay" / "events.jsonl").read_text()
        faulted = run_cli(run, "act", "TOGGLE", f"switch={working}", "--predict", "change")
        assert faulted.returncode != 0
        assert "the act command exited 1: the board shorted" in faulted.stderr, faulted.stderr
        assert (run / ".assay" / "events.jsonl").read_text() == before
        assert _lamps(run) == [0, 0, 0, 0]
        status = run_cli(run, "status")
        assert "paid actions 0" in status.stdout
    finally:
        stop_run(run)


def test_a_recipe_problem_stops_the_start_and_names_the_field(tmp_path):
    world = tmp_path / "board"
    shutil.copytree(TEMPLATE, world)
    raw = json.loads((world / "recipe.json").read_text())
    raw["world"]["mode"] = "sideways"
    (world / "recipe.json").write_text(json.dumps(raw))
    run = tmp_path / "run"
    run.mkdir()
    try:
        refused = _start(run, world / "adapter.py")
        assert refused.returncode != 0
        assert "world.mode: one of ['copy', 'worktree']" in refused.stderr, refused.stderr
    finally:
        stop_run(run)


def test_a_rule_that_reads_what_the_world_never_says_stops_the_start(tmp_path):
    world = tmp_path / "board"
    shutil.copytree(TEMPLATE, world)
    raw = json.loads((world / "recipe.json").read_text())
    raw["world"]["win"] = {"path": "lit", "op": "==", "value": 4}
    (world / "recipe.json").write_text(json.dumps(raw))
    run = tmp_path / "run"
    run.mkdir()
    try:
        refused = _start(run, world / "adapter.py")
        assert refused.returncode != 0
        assert "the recipe's rules read ['lit'], which the first observation does not hold" in refused.stderr
    finally:
        stop_run(run)
