"""The coverage audit as a world-neutral built-in: the ledger, every trigger
and demand in advise and block mode on synthetic journals, the block-mode
end-to-end through the real CLI, the frame world's region part, and the
regression on the reduced lf52 prefix where the handoff review says the
re-issue halt must fire."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from conftest import FAKE_ADAPTER, event_of, run_cli, run_of, stop_run

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "lf52_prefix.jsonl.gz"
ACTIONS = [
    {"name": "INC", "params": {"amount": {"type": "int", "min": 1, "max": 2}}},
    {"name": "SET_LAMP", "params": {"state": {"type": "str", "enum": ["on", "off"]}}},
    {"name": "NOOP", "params": {}},
]


def _event(index: int, action: str, *, data=None, observation=None, ok=None, level=0,
           counts=True, grade=None, available=("INC", "NOOP", "SET_LAMP", "BOMB")):
    grade = [{"text": item["kind"], "actual": "", "bucket": "world_model", **item} for item in grade or []]
    return {
        "id": index,
        "action": action,
        "data": data,
        "counts_action": counts,
        "state": "NOT_FINISHED",
        "levels_completed": level,
        "level_before": level if index else None,
        "win_levels": 1,
        "available_actions": list(available),
        "observation": observation if observation is not None else {"counter": 0},
        "predict": None if ok is None else "x",
        "predict_ok": ok,
        "grade": grade,
    }


def _records(events):
    """The synthetic journal as Event records (a dict given here overrides
    the test defaults, so the fixture's full events pass through as they are)."""
    return [event if not isinstance(event, dict) else event_of(**event) for event in events]


def _view(events):
    from assay.core import RunPaths
    from assay.modules import ModuleView

    paths = RunPaths(Path("/nonexistent/assay-coverage-audit"))
    return ModuleView(run_of(paths, _records(events), registry={"actions": []}))


def _module():
    from assay.modules import BUILTINS

    return next(module for module in BUILTINS if module.NAME == "coverage_audit")


def test_ledger_untried_dead_and_stall():
    from assay.modules import coverage_ledger

    events = [_event(0, "START", counts=False)]
    # NOOP seven times, never productive (observation never changes).
    for index in range(1, 8):
        events.append(_event(index, "NOOP", ok=True, grade=[{"kind": "noop", "ok": True}]))
    ledger = coverage_ledger(_records(events))
    assert ledger["untried"] == ["BOMB", "INC", "SET_LAMP"]
    assert ledger["dead"] == ["NOOP"]
    assert ledger["stalled"] is False  # seven, the window is eight
    events.append(_event(8, "NOOP", ok=True, grade=[{"kind": "noop", "ok": True}]))
    assert coverage_ledger(_records(events))["stalled"] is True
    # A productive INC (the observation changed) breaks the stall and leaves
    # the dead list alone.
    events.append(_event(9, "INC", data={"amount": 1}, observation={"counter": 1}, ok=True,
                         grade=[{"kind": "change", "ok": True}]))
    ledger = coverage_ledger(_records(events))
    assert ledger["stalled"] is False and ledger["dead"] == ["NOOP"]
    assert ledger["untried"] == ["BOMB", "SET_LAMP"]


def test_change_signal_prefers_the_grade_then_the_observation():
    from assay.modules import _event_changed

    changed = _event(1, "INC", observation={"counter": 1})
    base = [_event(0, "START", counts=False), changed]
    assert _event_changed(_records(base), 1) is True  # observation differs from START's
    quiet = _event(1, "NOOP", observation={"counter": 0})
    assert _event_changed(_records([base[0], quiet]), 1) is False
    # A graded noop that held says nothing changed, whatever the objects say.
    graded = _event(1, "NOOP", observation={"counter": 9}, ok=True, grade=[{"kind": "noop", "ok": True}])
    assert _event_changed(_records([base[0], graded]), 1) is False
    # Progress is always change.
    progressed = _event(1, "INC", level=1, observation={"counter": 0})
    progressed["level_before"] = 0
    assert _event_changed(_records([base[0], progressed]), 1) is True


def test_frame_change_signal_comes_from_the_observation_kind():
    """The frame branch of the change signal lives behind the kind hook: the
    settled frame against the previous event's, as the kernel compared them
    before the branch moved."""
    pytest.importorskip("PIL")
    from assay.modules import _event_changed

    start = event_of(id=0, action="START", counts_action=False, level_before=None, frames=[["00", "00"]])
    moved = event_of(id=1, action="ACTION1", data=None, frames=[["00", "00"], ["01", "00"]])
    assert _event_changed([start, moved], 1) is True
    settled = event_of(id=1, action="ACTION1", data=None, frames=[["11", "11"], ["00", "00"]])
    assert _event_changed([start, settled], 1) is False


def test_reissue_loop_and_conclusion_triggers_and_demands():
    module = _module()
    events = [_event(0, "START", counts=False),
              _event(1, "INC", data={"amount": 1}, ok=False, observation={"counter": 0},
                     grade=[{"kind": "change", "ok": False}])]
    view = _view(events)
    pending = {"kind": "act", "name": "INC", "params": {"amount": "1"}, "outcomes": [], "declares": {}}
    line = module.trigger(view, pending)
    assert line and line.startswith("re-issuing INC(amount=1) unmodified, it just graded FALSE")
    assert "untried actions [BOMB, NOOP, SET_LAMP]" in line
    assert set(module.demand(view, pending)) == {"revised"}
    pending["declares"] = {"revised": "amount matters, trying 2 next after this"}
    assert module.demand(view, pending) is None
    other = {"kind": "act", "name": "NOOP", "params": None, "outcomes": [], "declares": {}}
    assert module.trigger(view, other) is None and module.demand(view, other) is None
    # Three identical failing moves: the loop halt.
    for index in (2, 3):
        events.append(_event(index, "INC", data={"amount": 1}, ok=False, observation={"counter": 0},
                             grade=[{"kind": "change", "ok": False}]))
    pending["declares"] = {"revised": "still the same"}
    line = module.trigger(_view(events), pending)
    assert line and "has missed 3 times in a row, you are looping" in line
    # A conclusion by declaration demands the audit and names the gaps.
    conclusion = {"kind": "act", "name": "NOOP", "params": None, "outcomes": [],
                  "declares": {"impossible": "the counter cannot reach 3"}}
    line = module.trigger(_view(events), conclusion)
    assert line and line.startswith("impossibility or absence conclusion detected")
    assert "untried actions [BOMB, NOOP, SET_LAMP]" in line
    assert set(module.demand(_view(events), conclusion)) == {"coverage_audit"}
    conclusion["declares"]["coverage_audit"] = "rules: INC adds; exercised e1-e3; gaps: SET_LAMP, BOMB"
    assert module.demand(_view(events), conclusion) is None
    # The conclusion= value scan, and keys that are not sentinels.
    assert module.demand(_view(events), {"kind": "act", "name": "NOOP", "params": None, "outcomes": [],
                                         "declares": {"conclusion": "this unit is unwinnable"}})
    assert module.demand(_view(events), {"kind": "act", "name": "NOOP", "params": None, "outcomes": [],
                                         "declares": {"note": "complete and stuck are not sentinels"}}) is None


def test_status_meter_and_stall_phrase():
    module = _module()
    events = [_event(0, "START", counts=False)]
    assert module.trigger(_view(events), None) is None
    for index in range(1, 4):
        events.append(_event(index, "NOOP", ok=True, grade=[{"kind": "noop", "ok": True}]))
    line = module.trigger(_view(events), None)
    assert line == "coverage unit 1: untried [BOMB, INC, SET_LAMP], no-op-only [none]."
    for index in range(4, 9):
        events.append(_event(index, "NOOP", ok=True, grade=[{"kind": "noop", "ok": True}]))
    line = module.trigger(_view(events), None)
    assert "no-op-only [NOOP]" in line and "STALL: the last 8 actions changed nothing" in line
    assert "never-productive actions [NOOP]" in line
    reset = {"kind": "reset", "name": "RESET", "params": None, "outcomes": [], "declares": {}}
    assert module.trigger(_view(events), reset).startswith("resetting under a stall")
    telemetry = module.telemetry(_view(events))
    assert telemetry == {"unit": 0, "paid_on_unit": 8, "untried": 3, "dead": 1, "stalled": True}


def test_frame_world_adds_the_region_gap_only_in_gap_phrases():
    module = _module()
    settled = [["0" * 16] * 16]
    events = [{**_event(0, "START", counts=False, available=(1, 6)), "frames": settled}]
    events[0].pop("observation")
    for index in range(1, 4):
        event = {**_event(index, "ACTION6", data={"x": 1, "y": 1}, ok=False, available=(1, 6),
                          grade=[{"kind": "change", "ok": False}]), "frames": settled}
        event.pop("observation")
        events.append(event)
    status = module.trigger(_view(events), None)
    assert "regions" not in status  # never in the every-status line
    pending = {"kind": "act", "name": "ACTION6", "params": {"x": "1", "y": "1"}, "outcomes": [], "declares": {}}
    halt = module.trigger(_view(events), pending)
    assert "3/4 grid regions unprobed (e.g. x8-15,y0-7; x0-7,y8-15; x8-15,y8-15)" in halt
    assert "untried actions [ACTION1]" in halt
    telemetry = module.telemetry(_view(events))
    assert telemetry["regions_probed"] == 1 and telemetry["regions_total"] == 4


def test_block_mode_end_to_end(tmp_path):
    run = tmp_path / "block"
    run.mkdir()
    (run / "reg.json").write_text(json.dumps({
        "actions": ACTIONS, "budget": {"actions": 30},
        "module_modes": {"coverage_audit": "block"},
    }))
    try:
        started = run_cli(run, "start", "fake1", "--adapter", f"{FAKE_ADAPTER}:factory",
                          "--registry", str(run / "reg.json"))
        assert started.returncode == 0, started.stderr
        listed = run_cli(run, "module", "list")
        assert "coverage_audit | block | built-in" in listed.stdout
        assert "Provably unsolvable is a property of your model" in listed.stdout
        for _ in range(3):
            assert run_cli(run, "act", "NOOP", "--predict", "noop").returncode == 0
        status = run_cli(run, "status")
        assert "MODULE coverage_audit | coverage unit 1: untried [BOMB, INC, SET_LAMP]" in status.stdout
        missed = run_cli(run, "act", "NOOP", "--predict", "change")
        assert "OUTCOME | SURPRISE" in missed.stdout
        before = len((run / ".assay" / "events.jsonl").read_text().splitlines())
        refused = run_cli(run, "act", "NOOP", "--predict", "change")
        assert refused.returncode == 2
        assert "MODULE coverage_audit | declaration demanded" in refused.stderr
        assert '--declare "revised=<text>"' in refused.stderr
        assert len((run / ".assay" / "events.jsonl").read_text().splitlines()) == before
        unlocked = run_cli(run, "act", "NOOP", "--predict", "change",
                           "--declare", "revised=testing whether NOOP ever changes anything")
        assert unlocked.returncode == 0, unlocked.stderr
        assert "OUTCOME | SURPRISE" in unlocked.stdout
        conclusion = run_cli(run, "act", "NOOP", "--predict", "noop",
                             "--declare", "impossible=the counter never moves")
        assert conclusion.returncode == 2 and '--declare "coverage_audit=<text>"' in conclusion.stderr
        audited = run_cli(run, "audit")
        assert "AUDIT | CLEAN" in audited.stdout
    finally:
        stop_run(run)


def test_reissue_halts_on_the_lf52_prefix():
    """Event 82 of the published lf52 journal re-issues the move that graded
    FALSE at event 81. The built-in must halt there, and the frame part must
    report the regions a point action never probed."""
    with gzip.open(FIXTURE, "rt") as handle:
        events = [json.loads(line) for line in handle if line.strip()]
    assert len(events) == 83 and events[82]["action"] == events[81]["action"]
    module = _module()
    prefix = events[:82]
    pending = {"kind": "act", "name": events[82]["action"],
               "params": {k: str(v) for k, v in (events[82]["data"] or {}).items()},
               "outcomes": [], "declares": {}}
    line = module.trigger(_view(prefix), pending)
    assert line and line.startswith("re-issuing ACTION"), line
    assert "it just graded FALSE" in line
    assert "grid regions unprobed" in line
    assert set(module.demand(_view(prefix), pending)) == {"revised"}
    assert module.trigger(_view(prefix), None).startswith("coverage level ")  # lf52 has ten levels
