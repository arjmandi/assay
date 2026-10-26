"""The conclusion gate: the coverage audit's demand keyed on the record (a
reset that did not follow GAME_OVER, a verdict the notes gained since the
last audit) and the resume check at status (the sonnet-haiku-5-5
experiment)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from conftest import FAKE_ADAPTER, event_of, run_cli, run_of, stop_run

ACTIONS = [
    {"name": "INC", "params": {"amount": {"type": "int", "min": 1, "max": 2}}},
    {"name": "NOOP", "params": {}},
]
NOTES = "# Notes: t\n\n## Verified (cite event ids)\n\n## Assumed / open questions\n\n## Plan\n"


def _module():
    from assay.modules import BUILTINS

    return next(module for module in BUILTINS if module.NAME == "conclusion_gate")


def _paths(root: Path, notes: str = NOTES):
    from assay.core import RunPaths

    paths = RunPaths(root)
    paths.state.mkdir(parents=True, exist_ok=True)
    paths.notes.write_text(notes)
    paths.activity.write_text("")
    return paths


def _events(*, state: str = "NOT_FINISHED", paid: int = 2):
    events = [event_of(id=0, action="START", data=None, counts_action=False)]
    for index in range(1, paid + 1):
        events.append(event_of(id=index, state=state if index == paid else "NOT_FINISHED",
                               timestamp="2026-01-01T00:00:00+00:00"))
    return events


def _view(paths, events):
    from assay.modules import ModuleView

    return ModuleView(run_of(paths, events, registry={"actions": []}))


def _pending(kind: str, declares: dict | None = None) -> dict:
    name = "RESET" if kind == "reset" else "INC"
    return {"kind": kind, "name": name, "params": None, "outcomes": [], "declares": declares or {}}


def test_verdict_lines_are_plan_lines_and_headings():
    from assay.modules import notes_untested, notes_verdicts

    text = (
        "# Notes: t\n## Verified (cite event ids)\n- the door is blocked by the wall (e4)\n"
        "## Level 7 (parked at e616)\n## Plan\n- L7 BLOCKED: unsolvable under the rules seen\n"
        "- next: try the lamp, x=25 untested\n"
    )
    # A Verified line that says blocked is a fact about the world, not a verdict.
    assert notes_verdicts(text) == ["## Level 7 (parked at e616)", "- L7 BLOCKED: unsolvable under the rules seen"]
    assert notes_untested(text) == ["next: try the lamp, x=25 untested"]
    assert notes_verdicts(NOTES) == []


def test_a_reset_without_game_over_owes_the_audit(tmp_path):
    module = _module()
    paths = _paths(tmp_path / "run")
    view = _view(paths, _events())
    demand = module.demand(view, _pending("reset"))
    assert list(demand) == ["coverage_audit"] and "abandons the line of play" in demand["coverage_audit"]
    assert "did not end in GAME_OVER" in module.trigger(view, _pending("reset"))
    assert module.demand(view, _pending("reset", {"coverage_audit": "rules e1, e2; untested: amount 2"})) is None
    over = _view(paths, _events(state="GAME_OVER"))
    assert module.demand(over, _pending("reset")) is None and module.trigger(over, _pending("reset")) is None


def test_a_new_verdict_in_the_notes_owes_the_audit_once(tmp_path):
    module = _module()
    paths = _paths(tmp_path / "run")
    view = _view(paths, _events())
    assert module.demand(view, _pending("act")) is None
    paths.notes.write_text(NOTES + "- parked: the counter cannot pass 2\n")
    demand = module.demand(view, _pending("act"))
    assert "NOTES.md gained a verdict ('- parked: the counter cannot pass 2')" in demand["coverage_audit"]
    # The act that carries the audit records it; the same verdict asks no more.
    audited = event_of(id=3, declares={"coverage_audit": "rules: INC adds (e1, e2); untested: amount 2"})
    module.observe(view, audited)
    record = json.loads(paths.activity.read_text().splitlines()[-1])
    assert record["kind"] == "conclusion_audited" and record["event"] == 3
    assert module.demand(view, _pending("act")) is None
    assert module.telemetry(view) == {"verdicts": 1, "audited": True}
    # A further verdict is a new conclusion.
    paths.notes.write_text(NOTES + "- parked: the counter cannot pass 2\n- dead end: NOOP does nothing\n")
    assert module.demand(view, _pending("act")) is not None


def test_the_resume_check_fires_while_no_paid_action_follows_the_verdict(tmp_path):
    module = _module()
    paths = _paths(tmp_path / "run", NOTES + "- STOP here: level blocked, the lamp is untested\n")
    events = _events()
    # The notes were written after the last paid action (2026-01-01).
    line = module.trigger(_view(paths, events), None)
    assert line.startswith("RESUME CHECK: the verdict in NOTES.md ('- STOP here: level blocked, the lamp is untested')")
    assert "has had 0 graded probes since e2" in line
    assert "the notes name these untested: STOP here: level blocked, the lamp is untested" in line
    # Notes older than the last paid action: the verdict was probed since.
    os.utime(paths.notes, (1_000_000_000, 1_000_000_000))
    assert module.trigger(_view(paths, events), None) is None


def test_block_mode_refuses_a_bare_reset_end_to_end(tmp_path):
    run = tmp_path / "block"
    run.mkdir()
    (run / "reg.json").write_text(json.dumps({
        "actions": ACTIONS, "budget": {"actions": 30},
        "module_modes": {"conclusion_gate": "block"},
    }))
    try:
        started = run_cli(run, "start", "fake1", "--adapter", f"{FAKE_ADAPTER}:factory",
                          "--registry", str(run / "reg.json"))
        assert started.returncode == 0, started.stderr
        assert "conclusion_gate | block | built-in" in run_cli(run, "module", "list").stdout
        assert run_cli(run, "act", "NOOP", "--predict", "noop").returncode == 0
        refused = run_cli(run, "reset", "--because", "starting over")
        assert refused.returncode == 2
        assert "MODULE conclusion_gate | declaration demanded" in refused.stderr
        assert '--declare "coverage_audit=<text>"' in refused.stderr
        allowed = run_cli(run, "reset", "--because", "starting over",
                          "--declare", "coverage_audit=NOOP changes nothing (e1); untested: INC")
        assert allowed.returncode == 0, allowed.stderr
        kinds = [json.loads(line)["kind"] for line in (run / ".assay" / "activity.jsonl").read_text().splitlines()]
        assert "conclusion_audited" in kinds
        assert "AUDIT | CLEAN" in run_cli(run, "audit").stdout
    finally:
        stop_run(run)
