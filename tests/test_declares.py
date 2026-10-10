"""Declarations reach the journal on every step of a batch and are accepted on
a reset, so a module demand can key on a reset (a conclusion expressed as
giving up on the current state) and an analytics pass can count them. A
module's demand also sees the parsed outcomes of the action about to be
taken, on the live path."""

from __future__ import annotations

import json
import os
from pathlib import Path

from conftest import FAKE_ADAPTER, run_cli, stop_run

ACTIONS = [
    {"name": "INC", "params": {"amount": {"type": "int", "min": 1, "max": 2}}},
    {"name": "NOOP", "params": {}},
]

PARK_DEMAND = '''
class _ParkDemand:
    NAME = "park_demand"
    CONSTITUTION = "a reset must say what would reopen the abandoned line"
    MODE = "block"

    def trigger(self, view, pending):
        return None

    def demand(self, view, pending):
        if not pending or pending.get("kind") != "reset":
            return None
        if str((pending.get("declares") or {}).get("parked", "")).strip():
            return None
        return {"parked": "name the test that would reopen this line"}

    def telemetry(self, view):
        return {}


MODULE = _ParkDemand()
'''

OUTCOME_KINDS = '''
class _OutcomeKinds:
    NAME = "outcome_kinds"
    CONSTITUTION = "a module that reads the outcomes of the action about to be taken"
    MODE = "advise"

    def trigger(self, view, pending):
        return None

    def demand(self, view, pending):
        if pending is not None:
            view.record(
                "outcome_kinds_seen",
                name=pending["name"],
                kinds=[outcome.kind for outcome in pending["outcomes"]],
            )
        return None

    def telemetry(self, view):
        return {}


MODULE = _OutcomeKinds()
'''


def _events(run: Path) -> list[dict]:
    lines = (run / ".assay" / "events.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _start(run: Path, spec: dict):
    run.mkdir()
    (run / "reg.json").write_text(json.dumps(spec))
    return run_cli(
        run, "start", "fake1", "--adapter", f"{FAKE_ADAPTER}:factory",
        "--registry", str(run / "reg.json"),
    )


def test_commit_steps_carry_the_declaration(tmp_path):
    run = tmp_path / "steps"
    # Redaction happens in the daemon, which inherits its environment at spawn.
    os.environ["ASSAY_TEST_DECLARE_SECRET"] = "declaresecret42"
    try:
        started = _start(run, {"actions": ACTIONS, "budget": {"actions": 20},
                               "secrets": ["ASSAY_TEST_DECLARE_SECRET"]})
        assert started.returncode == 0, started.stderr
        committed = run_cli(
            run, "commit",
            "--step", "NOOP :: noop", "--step", "NOOP :: noop",
            "--declare", "revised=probe both halves declaresecret42",
        )
        assert committed.returncode == 0, committed.stderr
        events = _events(run)
        assert [event["action"] for event in events[1:]] == ["NOOP", "NOOP"]
        for event in events[1:]:
            assert event["declares"] == {
                "revised": "probe both halves [REDACTED:ASSAY_TEST_DECLARE_SECRET]"
            }
        plain = run_cli(run, "commit", "--step", "NOOP :: noop")
        assert plain.returncode == 0
        assert "declares" not in _events(run)[-1]
        assert "AUDIT | CLEAN" in run_cli(run, "audit").stdout
    finally:
        os.environ.pop("ASSAY_TEST_DECLARE_SECRET", None)
        stop_run(run)


def test_reset_accepts_a_declaration_and_a_module_can_demand_it(tmp_path):
    module = tmp_path / "park_demand.py"
    module.write_text(PARK_DEMAND)
    run = tmp_path / "reset"
    try:
        started = _start(run, {"actions": ACTIONS, "budget": {"actions": 20},
                               "modules": [str(module)]})
        assert started.returncode == 0, started.stderr
        assert run_cli(run, "act", "INC", "amount=1", "--predict", "change").returncode == 0
        refused = run_cli(run, "reset", "--because", "dead end")
        assert refused.returncode == 2
        assert "MODULE park_demand | declaration demanded" in refused.stderr
        assert "MODULE park_demand | declaration demanded before this action: parked: name the test that would reopen this line" in refused.stderr
        assert 'NEXT | repeat the command with --declare "parked=<text>" (one flag per field); any named, non-empty text unlocks the action' in refused.stderr
        assert len(_events(run)) == 2  # nothing spent
        accepted = run_cli(
            run, "reset", "--because", "dead end",
            "--declare", "parked=reopen if INC ever moves the lamp",
        )
        assert accepted.returncode == 0, accepted.stderr
        assert "RESULT | RESET" in accepted.stdout
        event = _events(run)[-1]
        assert event["action"] == "RESET"
        assert event["declares"] == {"parked": "reopen if INC ever moves the lamp"}
        assert "AUDIT | CLEAN" in run_cli(run, "audit").stdout
    finally:
        stop_run(run)


def test_a_module_reads_the_pending_outcomes_on_the_live_path(tmp_path):
    """`pending["outcomes"]` holds the parsed outcomes of the action about to
    be taken, as `records.Outcome` records (the key was `claims` before
    1.3.0): on a real act, a module's demand reads their kinds and records
    them, and the activity log carries what it saw."""
    module = tmp_path / "outcome_kinds.py"
    module.write_text(OUTCOME_KINDS)
    run = tmp_path / "pending"
    try:
        started = _start(run, {"actions": ACTIONS, "budget": {"actions": 20},
                               "modules": [str(module)]})
        assert started.returncode == 0, started.stderr
        assert run_cli(run, "state", "declare", "counter", "--path", "counter").returncode == 0
        acted = run_cli(
            run, "act", "INC", "amount=1",
            "--predict", "change; ch counter delta = 1; the lamp stays",
        )
        assert acted.returncode == 0, acted.stderr
        assert "RESULT | PREDICTED | result matched the prediction" in acted.stdout
        seen = [
            json.loads(line)
            for line in (run / ".assay" / "activity.jsonl").read_text().splitlines()
            if json.loads(line).get("kind") == "outcome_kinds_seen"
        ]
        assert len(seen) == 1 and seen[0]["name"] == "INC"
        assert seen[0]["kinds"] == ["change", "channel_delta", "note"]
    finally:
        stop_run(run)
