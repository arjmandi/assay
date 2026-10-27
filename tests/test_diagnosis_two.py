"""The second round of the sonnet-haiku-5-5 experiment (the Haiku layer
diagnosis of 2026-10-27): the conclusion gate's verdicts read by the Stop
gate's rule and credited only while owed, wall_spend inside a batch, the
game-over demand, the state cap per progress unit, the frame world's python
names, and the grid pack's regime ledger with the edge and HUD rows left out
of the object ledger."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest
from conftest import event_of, run_of

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
gate = importlib.import_module("tools.eval.gate").load_gate()

NOTES = "# Notes: t\n\n## Verified (cite event ids)\n\n## Assumed / open questions\n\n## Plan\n"


def _module(name: str):
    from assay.modules import BUILTINS

    return next(module for module in BUILTINS if module.NAME == name)


def _paths(root: Path, notes: str = NOTES, activity: list[dict] | None = None):
    from assay.core import RunPaths

    paths = RunPaths(root)
    paths.state.mkdir(parents=True, exist_ok=True)
    paths.notes.write_text(notes)
    paths.activity.write_text("".join(json.dumps(record) + "\n" for record in activity or []))
    return paths


def _view(paths, events, registry: dict | None = None):
    from assay.modules import ModuleView

    return ModuleView(run_of(paths, events, registry=registry or {"actions": []}))


def _events(paid: int = 2, *, state: str = "NOT_FINISHED"):
    events = [event_of(id=0, action="START", data=None, counts_action=False)]
    for index in range(1, paid + 1):
        events.append(event_of(id=index, state=state if index == paid else "NOT_FINISHED"))
    return events


# ---- verdicts ---------------------------------------------------------------------------------

SAMPLES = [
    "STOP: the door has no code\n  STOP:   the budget cannot reach level 3  \nSTOP:\n- STOP: not at the start\n",
    "STOP: none.\nSTOP: (not applicable, the run is live)\nSTOP: not stopped; play continues\nSTOP: n/a\n",
    "## Level 3\nSTOP: L3 closed under the observed movers\nRULES: walk rule (e12)\n",
    "",
]


@pytest.mark.parametrize("notes", SAMPLES)
def test_the_module_reads_stop_lines_by_the_gate_s_own_rule(notes):
    from assay.modules import STOP_PLACEHOLDER, stop_lines

    assert stop_lines(notes) == gate.declarations(notes)
    assert STOP_PLACEHOLDER.pattern == gate.PLACEHOLDER.pattern


def test_verdicts_leave_out_steps_disclaimers_placeholders_and_the_block_s_label():
    from assay.modules import newest_verdict, notes_verdicts

    text = (
        NOTES
        + "- Stop predicting change blindly; probe other lattice cells\n"
        + "- Blocked moves: ACTION1 e124 (vertical divider)\n"
        + "- Status: level 2 in progress; no stop verdict.\n"
        + "- If a dead end shows, park it\n"
        + "1. probe the bar, then park\n"
        + "- Earlier STOP withdrawn after e199\n"
        + "- L4 parked: no route reaches the door\n"
        + "## STOP\n"
        + "STOP: none.\n"
        + "## Findings\n"
        + "STOP: the lamp cannot be lit\n"
        + "RULES: INC adds (e1)\n"
    )
    assert notes_verdicts(text) == ["- L4 parked: no route reaches the door", "STOP: the lamp cannot be lit"]
    # The resume check keys on the newest STOP line when there is one.
    assert newest_verdict(text) == "STOP: the lamp cannot be lit"
    assert newest_verdict(NOTES + "- L4 parked: no route\n") == "- L4 parked: no route"
    assert newest_verdict(NOTES) is None


def test_an_audit_counts_only_while_one_is_owed(tmp_path):
    module = _module("conclusion_gate")
    paths = _paths(tmp_path / "run")
    view = _view(paths, _events())
    declared = {"coverage_audit": "rules e1; untested: amount 2"}
    pending = {"kind": "act", "name": "INC", "params": None, "outcomes": [], "declares": declared}
    # Nothing owed: the text is not recorded, and the receipt says so.
    assert module.trigger(view, pending).startswith("no audit demanded: this coverage_audit text is not recorded")
    module.observe(view, event_of(id=3, declares=declared))
    kinds = [json.loads(line)["kind"] for line in paths.activity.read_text().splitlines()]
    assert "conclusion_audited" not in kinds
    # A verdict new to the notes owes one: the first carrier records it, and
    # the copy a batch carries onto its next step does not.
    paths.notes.write_text(NOTES + "- parked: the counter cannot pass 2\n")
    assert module.trigger(view, pending) is None
    module.observe(view, event_of(id=4, declares=declared))
    module.observe(view, event_of(id=5, declares=declared))
    audited = [json.loads(line) for line in paths.activity.read_text().splitlines() if "conclusion_audited" in line]
    assert [record["event"] for record in audited] == [4]
    # A reset that did not follow GAME_OVER owes one too.
    events = [*_events(), event_of(id=3, action="RESET", data=None, declares=declared)]
    module.observe(_view(paths, events), events[-1])
    audited = [json.loads(line) for line in paths.activity.read_text().splitlines() if "conclusion_audited" in line]
    assert [record["event"] for record in audited] == [4, 3]


# ---- wall_spend in a batch, and the game-over demand ----------------------------------------------


def test_wall_spend_counts_the_steps_before_it_in_a_batch(tmp_path):
    module = _module("wall_spend")
    view = _view(_paths(tmp_path / "run"), _events(paid=23))

    def step(index):
        return {"kind": "commit", "name": "INC", "params": None, "outcomes": [], "declares": {}, "step": index}

    assert module.trigger(view, step(0)) is None and module.trigger(view, step(1)) is None
    assert module.trigger(view, step(2)).startswith("25 paid actions on this")


def test_a_second_non_reset_action_inside_game_over_owes_a_reason(tmp_path):
    module = _module("game_over_retry")
    act = {"kind": "act", "name": "INC", "params": None, "outcomes": [], "declares": {}}
    reset = {"kind": "reset", "name": "RESET", "params": None, "outcomes": [], "declares": {}}
    refused = {"kind": "command_end", "command": "act", "status": "ERROR", "code": "WORLD_ERROR", "event": 2,
               "error": "the world takes only a reset now"}
    events = _events(state="GAME_OVER")
    first = _view(_paths(tmp_path / "first"), events)
    assert module.demand(first, act) is None and module.trigger(first, act) is None
    again = _view(_paths(tmp_path / "again", activity=[refused]), events)
    demand = module.demand(again, act)
    assert list(demand) == ["game_over"] and "ended in GAME_OVER at e2 and 1 non-reset action(s) were refused" in demand["game_over"]
    assert module.demand(again, reset) is None
    # A refusal before the last event is an earlier unit's.
    stale = _view(_paths(tmp_path / "stale", activity=[{**refused, "event": 1}]), events)
    assert module.demand(stale, act) is None
    assert module.demand(_view(_paths(tmp_path / "live", activity=[refused]), _events()), act) is None


# ---- the state cap per progress unit ------------------------------------------------------------


def test_the_state_cap_counts_the_current_unit_s_states(tmp_path):
    from assay.core import AssayError, RunPaths
    from assay.states import declare_state, load_declared, retire_state

    paths = RunPaths(tmp_path / "run")
    paths.state.mkdir(parents=True)
    registry = {"actions": [], "state_cap": 2}
    unit0 = run_of(paths, _events(), registry=registry)
    declare_state(unit0, "one", path="counter")
    declare_state(unit0, "two", path="counter")
    with pytest.raises(AssayError) as refused:
        declare_state(unit0, "three", path="counter")
    assert refused.value.code == "STATE_CAP" and "on one progress unit" in str(refused.value)
    # The next unit has its own places; the earlier unit's states stay readable.
    unit1 = run_of(paths, [*_events(), event_of(id=3, levels_completed=1, level_before=0)], registry=registry)
    declare_state(unit1, "three", path="counter")
    declare_state(unit1, "four", path="counter")
    assert sorted(load_declared(paths)) == ["four", "one", "three", "two"]
    with pytest.raises(AssayError):
        declare_state(unit1, "five", path="counter")
    # Declaring the same referent again is no redefinition; retire still frees a place.
    declare_state(unit1, "one", path="counter")
    retire_state(unit1, "four")
    declare_state(unit1, "five", path="counter")


# ---- the frame world's python names -------------------------------------------------------------


def test_a_name_error_lists_the_names_defined(tmp_path):
    from assay.analysis import run_python
    from assay.core import AssayError, RunPaths

    run = run_of(RunPaths(tmp_path / "run"), _events(), registry={"actions": []})
    with pytest.raises(AssayError) as failed:
        run_python(run, "settled_grid")
    assert "NameError" in str(failed.value) and "the names defined here: " in str(failed.value)
    assert "transitions" in str(failed.value).split("the names defined here: ")[1]


def test_the_grid_namespace_has_json_and_grid_is_the_current_settled_grid():
    pytest.importorskip("PIL")
    from assay_grid import analysis

    rows = ["00", "01"]
    events = [event_of(id=0, action="START", data=None, counts_action=False, frames=[rows]),
              event_of(id=1, action="ACTION1", data=None, frames=[["11", "01"]])]
    names = analysis.namespace(events)
    assert names["json"] is json
    assert names["grid"].tolist() == [[1, 1], [0, 1]] and len(names["settled"]) == 2
    assert names["previous"].tolist() == names["settled"][0].tolist()


# ---- the regime ledger and the object ledger's exclusions ----------------------------------------

def _grid(cells: dict[tuple[int, int], str], hud: str = "0") -> list[str]:
    """A 16x16 frame: floor 0, row 15 the HUD counter, and the given cells."""
    grid = [["0"] * 16 for _ in range(16)]
    for (x, y), color in cells.items():
        grid[y][x] = color
    grid[15] = [hud] * 16
    return ["".join(row) for row in grid]


# Two controls: a 2x2 of color 6 at x=10..11 y=4..5 and a 2x2 of color 9 at
# x=10..11 y=8..9; two marker pads of 2x2: color 6 at x=2..3 y=2..3 and color
# 7 at x=2..3 y=10..11. The mover is one cell of color e.
CONTROLS = {(10, 4): "6", (11, 4): "6", (10, 5): "6", (11, 5): "6",
            (10, 8): "9", (11, 8): "9", (10, 9): "9", (11, 9): "9"}
PADS = {(2, 2): "6", (3, 2): "6", (2, 3): "6", (3, 3): "6",
        (2, 10): "7", (3, 10): "7", (2, 11): "7", (3, 11): "7"}


def _frame(mover: tuple[int, int], tick: int) -> list[str]:
    cells = {**PADS, **CONTROLS, mover: "e"}
    return _grid(cells, hud="0123456789abcdef"[tick % 16])


def _regime_events():
    # The mover walks 5,2 -> 4,2 -> 3,2 (onto pad 6) -> clicks the 9 control
    # -> walks down to 3,10 (pad 7) -> clicks the 9 control and the 6
    # control; it never clicks the 6 control while on pad 6.
    path = [(5, 2), (4, 2), (3, 2), (3, 2), (3, 6), (3, 10), (3, 10), (3, 10)]
    actions = [None, "ACTION3", "ACTION3", ("ACTION6", 10, 8), "ACTION2", "ACTION2", ("ACTION6", 10, 8), ("ACTION6", 10, 4)]
    events = []
    for index, (place, action) in enumerate(zip(path, actions)):
        frame = _frame(place, index)
        if action is None:
            events.append(event_of(id=0, action="START", data=None, counts_action=False, frames=[frame],
                                   available_actions=[2, 3, 6], win_levels=3))
        elif isinstance(action, tuple):
            events.append(event_of(id=index, action=action[0], data={"x": action[1], "y": action[2]}, frames=[frame],
                                   available_actions=[2, 3, 6], win_levels=3))
        else:
            events.append(event_of(id=index, action=action, data=None, frames=[frame],
                                   available_actions=[2, 3, 6], win_levels=3))
    return events


def test_the_regime_ledger_names_the_pair_the_unit_never_exercised():
    pytest.importorskip("PIL")
    from assay_grid import coverage

    events = _regime_events()
    paid = [index for index, event in enumerate(events) if event.counts_action]
    ledger = coverage.regime_ledger(events, paid)
    assert ledger["mover"] == "e" and ledger["hud_rows"] == 1
    # The color match comes first: the 6 control never pressed with the mover on pad 6.
    assert ledger["untested"][0] == "10,4 (color 6) never clicked with the mover on color 6"
    assert "10,8 (color 9) never clicked with the mover on color 0" not in ledger["untested"]
    phrase = coverage.coverage_gap(events, paid)
    assert phrase.startswith("1 regime pair(s) the unit showed and never exercised: 10,4 (color 6)")


def test_the_object_ledger_leaves_out_the_edge_and_the_hud_rows():
    pytest.importorskip("PIL")
    from assay_grid import coverage

    events = _regime_events()
    paid = [index for index, event in enumerate(events) if event.counts_action]
    found = coverage.objects_phrase(events, paid)
    # Pads and controls are objects; the HUD row (it changes every action)
    # and anything on the frame's edge are not.
    assert found is not None and "at 0,15" not in found
    edge = _grid({(0, 5): "4", (0, 6): "4"})
    assert coverage._labels(edge)[1][1]["edge"] is True
