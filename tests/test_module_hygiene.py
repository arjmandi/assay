"""Module hygiene of the sonnet-haiku-5-5 experiment: wall_spend at
thresholds, the miss streak by bucket, and the gamble streak's demand for a
named win-rule hypothesis."""

from __future__ import annotations

from pathlib import Path

from conftest import event_of, run_of


def _grade(bucket: str, ok: bool, kind: str) -> dict:
    return {"kind": kind, "text": kind, "ok": ok, "actual": "", "bucket": bucket}


def _events(grades: list[list[dict]], *, extra_paid: int = 0):
    events = [event_of(id=0, action="START", data=None, counts_action=False)]
    for index in range(1, extra_paid + 1):
        events.append(event_of(id=index))
    for grade in grades:
        index = len(events)
        events.append(event_of(id=index, predict="x", predict_ok=all(item["ok"] for item in grade), grade=grade))
    return events


def _view(events):
    from assay.core import RunPaths
    from assay.modules import ModuleView

    return ModuleView(run_of(RunPaths(Path("/nonexistent/assay-hygiene")), events, registry={"actions": []}))


def _module(name: str):
    from assay.modules import BUILTINS

    return next(module for module in BUILTINS if module.NAME == name)


def test_wall_spend_speaks_at_thresholds_only():
    wall = _module("wall_spend")
    assert wall.trigger(_view(_events([], extra_paid=24)), None) is None
    line = wall.trigger(_view(_events([], extra_paid=25)), None)
    assert line.startswith("25 paid actions on this unit; check the model offline")
    assert "stop manual probing" not in line
    assert wall.trigger(_view(_events([], extra_paid=26)), None) is None
    assert wall.trigger(_view(_events([], extra_paid=40)), None) is None
    assert wall.trigger(_view(_events([], extra_paid=50)), None).startswith("50 paid actions")


def test_miss_streak_counts_world_model_misses_apart_from_gambles():
    streak = _module("miss_streak")
    gambles = _events([[_grade("gamble", False, "level_up"), _grade("world_model", True, "change")]] * 4)
    assert streak.trigger(_view(gambles), None) is None
    misses = _events([[_grade("world_model", False, "change")]] * 3)
    assert streak.trigger(_view(misses), None) == (
        "3 of the last 3 world-model predictions missed: the mechanics story in NOTES.md is wrong; "
        "fix it before spending more actions"
    )
    telemetry = streak.telemetry(_view(gambles))
    assert telemetry["gamble_missed"] == 4 and telemetry["world_model_missed"] == 0


def test_the_gamble_streak_asks_a_win_rule_of_the_next_gamble_only():
    from assay.records import Outcome

    streak = _module("gamble_streak")
    level_up = Outcome(kind="level_up", text="level+1")
    change = Outcome(kind="change", text="change")

    def pending(outcomes, declares=None):
        return {"kind": "act", "name": "INC", "params": None, "outcomes": outcomes, "declares": declares or {}}

    two = _view(_events([[_grade("gamble", False, "level_up")]] * 2))
    assert streak.demand(two, pending([level_up])) is None
    three = _view(_events([[_grade("gamble", False, "level_up")]] * 3))
    demand = streak.demand(three, pending([level_up, change]))
    assert list(demand) == ["win_rule"] and demand["win_rule"].startswith("3 milestone gambles missed on this unit")
    assert "predict `change` instead" in streak.trigger(three, pending([level_up]))
    # A prediction without a milestone outcome is never held, and a named
    # hypothesis unlocks the gamble.
    assert streak.demand(three, pending([change])) is None
    assert streak.demand(three, pending([level_up], {"win_rule": "the bar must be full"})) is None
    assert streak.telemetry(three) == {"gamble_missed_on_unit": 3}
