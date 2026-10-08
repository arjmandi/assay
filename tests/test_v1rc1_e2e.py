"""End-to-end tests for the v1-rc1 layer: the daemon-side gate, channels,
the model tier + batching law, agenda/owner authority, carryover, hazard
teeth, destructive gate, notes cap, spend feed, aggregates, windows, audit.

Every scenario drives the real CLI subprocess -> real broker daemon -> fake
adapter, exactly like a live run."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from conftest import FAKE_ADAPTER, run_cli, stop_run

BASE_ACTIONS = [
    {"name": "INC", "params": {"amount": {"type": "int", "min": 1, "max": 2}}},
    {"name": "SET_LAMP", "params": {"state": {"type": "str", "enum": ["on", "off"]}}},
    {"name": "NOOP", "params": {}},
]

MODEL_SOURCE = '''"""Exact model of the fake counter world (counter channel only)."""

CHANNELS = ["counter"]


def next(obs, action, params):
    params = params or {}
    data = dict(obs["data"])
    if action == "INC":
        data["counter"] = int(data["counter"]) + int(params["amount"])
    elif action == "SET_LAMP":
        data["lamp"] = str(params["state"])
    elif action == "NOOP":
        pass
    else:
        return None
    out = dict(obs)
    out["data"] = data
    return out


def actions(obs):
    return [("INC", {"amount": 1}), ("INC", {"amount": 2}), ("NOOP", {})]
'''


def _write_registry(run_dir: Path, **extra) -> Path:
    spec = {
        "actions": [dict(item) for item in BASE_ACTIONS],
        "budget": {"actions": 60},
        **extra,
    }
    target = run_dir / "reg.json"
    target.write_text(json.dumps(spec))
    return target


def _start(run_dir: Path, registry: Path, *extra_args: str):
    return run_cli(
        run_dir,
        "start",
        "fake1",
        "--adapter",
        f"{FAKE_ADAPTER}:factory",
        "--registry",
        str(registry),
        *extra_args,
    )


def _events(run_dir: Path) -> list[dict]:
    lines = (run_dir / ".assay" / "events.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _owner_token(start_stdout: str) -> str:
    found = re.search(r"OWNER TOKEN \| (\S+) \|", start_stdout)
    assert found, start_stdout
    return found.group(1)


def test_daemon_gate_refuses_bare_step(tmp_path):
    run = tmp_path / "gate"
    run.mkdir()
    registry = _write_registry(run)
    try:
        started = _start(run, registry)
        assert started.returncode == 0, started.stderr
        # A gated act works through the daemon and journals normally.
        acted = run_cli(run, "act", "INC", "amount=1", "--predict", "change")
        assert acted.returncode == 0, acted.stderr
        assert "OUTCOME | PREDICTED" in acted.stdout
        # The bypass channel: speaking the socket protocol directly with the
        # bare step op, retired with the operation table (docs/ARCHITECTURE.md
        # section 7.2): the daemon knows no such operation and refuses it by
        # name (#13 names the code); no mutation is journaled.
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from assay.broker import _request
        from assay.core import AssayError, RunPaths, load_jsonl

        paths = RunPaths(run)
        mutations_before = len(load_jsonl(paths.mutations))
        with pytest.raises(AssayError, match="^unknown broker operation 'step'$") as refused:
            _request(paths, {"op": "step", "action": "NOOP", "data": None}, timeout=5.0)
        assert refused.value.code == "UNKNOWN_OPERATION" and refused.value.kind == "usage"
        assert len(load_jsonl(paths.mutations)) == mutations_before
        # The chain is live and the audit is clean.
        audited = run_cli(run, "audit")
        assert audited.returncode == 0, audited.stderr
        assert "AUDIT | CLEAN" in audited.stdout
        assert "chain intact" in audited.stdout
    finally:
        stop_run(run)


def test_channels_grade_and_misreference(tmp_path):
    run = tmp_path / "chan"
    run.mkdir()
    registry = _write_registry(run)
    try:
        assert _start(run, registry).returncode == 0
        declared = run_cli(run, "channel", "declare", "counter", "--path", "counter")
        assert declared.returncode == 0, declared.stderr
        # A correct channel claim grades PREDICTED.
        acted = run_cli(
            run, "act", "INC", "amount=1", "--predict", "ch counter delta = 1"
        )
        assert acted.returncode == 0, acted.stderr
        assert "OUTCOME | PREDICTED" in acted.stdout
        # A wrong channel claim is a graded miss with the counter-fact.
        missed = run_cli(run, "act", "INC", "amount=1", "--predict", "ch counter = 99")
        assert missed.returncode == 0, missed.stderr
        assert "OUTCOME | SURPRISE" in missed.stdout
        assert "ch counter = 2" in missed.stdout
        # An unregistered channel is refused FREE and counted (mis-reference).
        before = len(_events(run))
        refused = run_cli(run, "act", "NOOP", "--predict", "ch ghost = 1")
        assert refused.returncode == 2
        assert "unregistered channel" in refused.stderr
        assert len(_events(run)) == before
        status = run_cli(run, "status")
        assert "MIS-REFERENCE | 1" in status.stdout
        assert "CHANNELS | registered:" in status.stdout
        assert "EMERGENCE |" in status.stdout and "declared channels 1" in status.stdout
        # A claim on a declared channel grades in the world_model bucket; only
        # the goal and level channels gamble.
        event = _events(run)[-1]
        assert event["grade"][0]["bucket"] == "world_model"
    finally:
        stop_run(run)


def test_model_tier_promotion_and_plan(tmp_path):
    run = tmp_path / "model"
    run.mkdir()
    registry = _write_registry(run)
    try:
        assert _start(run, registry).returncode == 0
        assert run_cli(run, "channel", "declare", "counter", "--path", "counter").returncode == 0
        # Twenty transitions recorded before the model's first replay earn
        # nothing: the fit over them is perfect, none of them is counted.
        for _ in range(20):
            spent = run_cli(run, "act", "NOOP", "--predict", "noop")
            assert spent.returncode == 0, spent.stderr
        (run / "model.py").write_text(MODEL_SOURCE)
        replayed = run_cli(run, "model", "replay")
        assert replayed.returncode == 0, replayed.stderr
        assert (
            "MODEL | replay-fit 100.00% | held 20 missed 0 unknown 0 over 20 "
            "transitions | recent-quarter graded 5 | promotion not earned"
        ) in replayed.stdout
        assert (
            "MODEL | counted 0 transition(s) recorded after this model's first replay "
            "at e20, 0 in the most recent quarter of the journal | promotion still "
            "needs 20 more counted, 5 more in the most recent quarter"
        ) in replayed.stdout
        fit = json.loads((run / ".assay" / "model_fit.json").read_text())
        assert fit["admitted_at_event"] == 20 and fit["computed_at_event"] == 20
        assert fit["graded"] == 20 and fit["counted"] == 0 and fit["counted_recent"] == 0
        assert fit["promotion"] is False
        status = run_cli(run, "status")
        assert "MODEL | fit 100% over 20 graded | batching rights: no" in status.stdout
        assert (
            "replay-fit not promoted: counted 0 since the admission at e20, missed 0, "
            "recent 0 (needs missed=0, counted>=20, recent>=5)"
        ) in status.stdout
        # A >cap hand batch is refused by the batching law (default cap 3),
        # and the refusal names the counted transitions.
        long_batch = run_cli(
            run,
            "commit",
            *sum((["--step", "NOOP :: noop"] for _ in range(4)), []),
        )
        assert long_batch.returncode == 2
        assert "batching law" in long_batch.stderr
        assert "counted 0 since the admission at e20" in long_batch.stderr
        # Earn the promotion: twenty transitions recorded after the admission,
        # the model holds on all of them, and the admission event stays put.
        for _ in range(20):
            spent = run_cli(run, "act", "NOOP", "--predict", "noop")
            assert spent.returncode == 0, spent.stderr
        replayed = run_cli(run, "model", "replay")
        assert replayed.returncode == 0, replayed.stderr
        assert (
            "MODEL | replay-fit 100.00% | held 40 missed 0 unknown 0 over 40 "
            "transitions | recent-quarter graded 10 | promotion EARNED: model plans "
            "lift the batch cap"
        ) in replayed.stdout
        assert (
            "MODEL | counted 20 transition(s) recorded after this model's first replay "
            "at e20, 10 in the most recent quarter of the journal | promotion earned "
            "on the counted transitions"
        ) in replayed.stdout
        fit = json.loads((run / ".assay" / "model_fit.json").read_text())
        assert fit["admitted_at_event"] == 20 and fit["computed_at_event"] == 40
        assert fit["counted"] == 20 and fit["counted_recent"] == 10
        assert fit["promotion"] is True
        activity = (run / ".assay" / "activity.jsonl").read_text().splitlines()
        replays = [
            json.loads(line) for line in activity if '"kind":"model_replay"' in line
        ]
        assert [item["event"] for item in replays] == [20, 40]
        assert [item["admitted_at_event"] for item in replays] == [20, 20]
        assert [item["counted"] for item in replays] == [0, 20]
        status = run_cli(run, "status")
        assert "batching rights: YES" in status.stdout
        # Solve to the goal reading and execute the machine plan to the WIN.
        solved = run_cli(run, "model", "solve", "--to", "ch counter = 3")
        assert solved.returncode == 0, solved.stderr
        assert "SOLVE | plan found" in solved.stdout
        committed = run_cli(run, "commit", "@.assay/model_plan.json")
        assert committed.returncode == 0, committed.stderr
        assert "OUTCOME | GAME_COMPLETE" in committed.stdout
        # The modules' advisories ride on a model-plan receipt like on every
        # other paid receipt (#24; before, the plan consulted them and
        # discarded what they said): forty paid actions on this unit is
        # past the wall_spend threshold.
        wall = (
            "MODULE wall_spend | 40 paid actions on this unit; stop manual probing; "
            "model the mechanics offline (`assay python`, or the `assay model` tier: "
            "replay-verified models earn batching rights)"
        )
        assert wall in committed.stdout.splitlines()
        receipt = json.loads(sorted((run / ".assay" / "receipts").glob("*.json"))[-1].read_text())
        assert receipt["plan"] == "model" and receipt["modules"] == [wall]
        # Machine plan steps are gated (predict_ok set) and marked machine.
        events = _events(run)
        assert events[-1]["state"] == "WIN"
        assert events[-1]["predict_ok"] is True
        assert all(item.get("machine") for item in events[-1]["grade"])
        audited = run_cli(run, "audit")
        assert "AUDIT | CLEAN" in audited.stdout
    finally:
        stop_run(run)


def test_agenda_owner_authority_and_approval(tmp_path):
    run = tmp_path / "agenda"
    run.mkdir()
    registry = _write_registry(
        run,
        goal={"text": "light the lamp, then win"},
        actions=[*BASE_ACTIONS, {"name": "FIRE", "params": {}, "approval": True}],
    )
    try:
        started = _start(run, registry)
        assert started.returncode == 0, started.stderr
        token = _owner_token(started.stdout)
        status = run_cli(run, "status")
        assert "AGENDA | goal (registry): light the lamp, then win" in status.stdout
        # The agent proposes; ratification without the token is refused.
        proposed = run_cli(run, "goal", "propose", "win faster", "--because", "test")
        assert proposed.returncode == 0 and "proposal #1" in proposed.stdout
        status = run_cli(run, "status")
        assert "goal proposal(s) awaiting the owner" in status.stdout
        denied = run_cli(run, "goal", "ratify", "1", "--token", "wrong")
        assert denied.returncode == 2 and "owner authority" in denied.stderr
        ratified = run_cli(run, "goal", "ratify", "1", "--token", token)
        assert ratified.returncode == 0, ratified.stderr
        status = run_cli(run, "status")
        assert "AGENDA | goal (ratified): win faster" in status.stdout
        # Approval-gated action: default-deny, one-shot, owner-granted.
        denied = run_cli(run, "act", "FIRE", "--predict", "change")
        assert denied.returncode == 2 and "approval-gated" in denied.stderr
        granted = run_cli(run, "approve", "FIRE", "--token", token)
        assert granted.returncode == 0, granted.stderr
        # FIRE is unknown to the adapter -> the daemon refuses AT the adapter,
        # but the approval gate itself passed (error names the adapter, not
        # the approval). The affordance check refuses first, in fact (FIRE is
        # not advertised), which is also fine: the approval was consumed after
        # the gate order. Use the error text to assert we got PAST approval.
        acted = run_cli(run, "act", "FIRE", "--predict", "change")
        assert acted.returncode == 2
        assert "approval-gated" not in acted.stderr
    finally:
        stop_run(run)


def test_destructive_gate_and_hazard_carryover(tmp_path):
    run = tmp_path / "hazard"
    run.mkdir()
    registry = _write_registry(
        run,
        actions=[*BASE_ACTIONS, {"name": "BOMB", "params": {}, "destructive": True}],
        module_modes={"hazard": "block"},
    )
    try:
        assert _start(run, registry).returncode == 0
        # Destructive teeth: refused without the structural declaration.
        refused = run_cli(run, "act", "BOMB", "--predict", "change")
        assert refused.returncode == 2
        assert "worst case and recovery" in refused.stderr
        # Banned inside batches regardless of declarations.
        banned = run_cli(run, "commit", "--step", "BOMB :: change")
        assert banned.returncode == 2 and "banned inside batches" in banned.stderr
        # Declared: always executable at the price of one declaration.
        acted = run_cli(
            run,
            "act",
            "BOMB",
            "--predict",
            "change",
            "--declare",
            "worst_case=game over on this level",
            "--declare",
            "recovery=assay reset restarts the level",
        )
        assert acted.returncode == 0, acted.stderr
        assert "OUTCOME | GAME_OVER" in acted.stdout
        event = _events(run)[-1]
        assert event["declares"]["worst_case"].startswith("game over")
        # The hazard module tagged the class from the outcome signature.
        status = run_cli(run, "status")
        assert "HAZARDS | 1 tagged action class(es): BOMB(entered_loss_state)" in status.stdout
        recovered = run_cli(run, "reset", "--because", "game over")
        assert recovered.returncode == 0, recovered.stderr
        # Export, then import into a FRESH same-registration run: the tag is
        # ACTIVE on arrival (the carve-out) and blocks an undeclared use.
        exported = run_cli(run, "export")
        assert exported.returncode == 0, exported.stderr
        knowledge = run / "assay_knowledge.json"
        assert knowledge.exists()
        run2 = tmp_path / "hazard2"
        run2.mkdir()
        registry2 = _write_registry(
            run2,
            actions=[*BASE_ACTIONS, {"name": "BOMB", "params": {}, "destructive": True}],
            module_modes={"hazard": "block"},
        )
        started2 = _start(run2, registry2, "--import", str(knowledge))
        assert started2.returncode == 0, started2.stderr
        assert "IMPORTED |" in started2.stdout and "hazards active 1" in started2.stdout
        status2 = run_cli(run2, "status")
        assert "FOREIGN | imported knowledge from world fake1" in status2.stdout
        assert "PRIOR-NOTES" in status2.stdout
        assert (run2 / ".assay" / "PRIOR-NOTES.md").exists()
        # The imported demand fires BEFORE the hazard does (block mode), and
        # the destructive flag demands the same fields; either message proves
        # the declaration demand is live pre-incident.
        refused2 = run_cli(run2, "act", "BOMB", "--predict", "change")
        assert refused2.returncode == 2
        assert "worst" in refused2.stderr.lower()
        events2 = _events(run2)
        assert all(event["action"] != "BOMB" for event in events2)
    finally:
        stop_run(run)
        stop_run(tmp_path / "hazard2")


def test_notes_cap_spend_feed_and_aggregates(tmp_path):
    run = tmp_path / "caps"
    run.mkdir()
    registry = _write_registry(
        run, notes_cap=200, budget={"actions": 60, "usd": 5.0}
    )
    try:
        assert _start(run, registry).returncode == 0
        assert run_cli(run, "channel", "declare", "counter", "--path", "counter").returncode == 0
        # Aggregate claims: additive-only is enforced at parse; open + resolve.
        refused = run_cli(
            run, "act", "NOOP", "--predict",
            "agg ch counter mean >= 0 over 2a horizon 3a on-fail advise",
        )
        assert refused.returncode == 2 and "additive" in refused.stderr
        opened = run_cli(
            run, "act", "NOOP", "--predict",
            "noop; agg ch counter mean >= 0 over 2a horizon 3a on-fail advise",
        )
        assert opened.returncode == 0, opened.stderr
        assert "AGGREGATE | open" in opened.stdout
        for _ in range(2):
            assert run_cli(run, "act", "NOOP", "--predict", "noop").returncode == 0
        resolved = run_cli(run, "act", "NOOP", "--predict", "noop")
        assert resolved.returncode == 0, resolved.stderr
        assert "AGGREGATE | HELD" in resolved.stdout
        status = run_cli(run, "status")
        assert "AGGREGATES | open 0 | held 1" in status.stdout
        # Validity window: a microsecond window is always stale -> UNGRADABLE.
        stale = run_cli(run, "act", "NOOP", "--predict", "noop @within 0.000001s")
        assert stale.returncode == 0, stale.stderr
        assert "OUTCOME | INVALID_CLAIM" in stale.stdout
        event = _events(run)[-1]
        assert event["predict_ok"] is None
        assert event["grade"][0]["ungradable"] is True
        # Spend feed: idempotent by id; the usd cap refuses paid actions.
        reported = run_cli(run, "spend", "report", "--usd", "4.0", "--tokens", "100", "--id", "t1")
        assert reported.returncode == 0 and "$4.00" in reported.stdout
        status = run_cli(run, "status")
        assert "SPEND | reported $4.00 of $5.00 cap" in status.stdout
        assert run_cli(run, "act", "NOOP", "--predict", "noop").returncode == 0
        over = run_cli(run, "spend", "report", "--usd", "5.5", "--tokens", "150", "--id", "t1")
        assert over.returncode == 0 and "$5.50" in over.stdout
        refused = run_cli(run, "act", "NOOP", "--predict", "noop")
        assert refused.returncode == 2 and "BUDGET_EXHAUSTED" in refused.stderr
        assert "reported spend $5.50" in refused.stderr
        # Roll the spend back (idempotent id correction) and hit the notes cap.
        assert run_cli(run, "spend", "report", "--usd", "1.0", "--id", "t1").returncode == 0
        (run / ".assay" / "NOTES.md").write_text("x" * 500)
        blocked = run_cli(run, "act", "NOOP", "--predict", "noop")
        assert blocked.returncode == 2 and "twice the" in blocked.stderr
        status = run_cli(run, "status")
        assert "OVER TWICE the 200-char cap" in status.stdout
        (run / ".assay" / "NOTES.md").write_text("trimmed")
        assert run_cli(run, "act", "NOOP", "--predict", "noop").returncode == 0
    finally:
        stop_run(run)


def test_secrets_redaction_at_journal_boundary(tmp_path):
    run = tmp_path / "secrets"
    run.mkdir()
    registry = _write_registry(run, secrets=["ASSAY_TEST_SECRET"])
    os.environ["ASSAY_TEST_SECRET"] = "topsecretvalue123"
    try:
        assert _start(run, registry).returncode == 0
        acted = run_cli(
            run,
            "act",
            "INC",
            "amount=1",
            "--predict",
            "change",
            "--because",
            "using key topsecretvalue123 here",
        )
        assert acted.returncode == 0, acted.stderr
        state_dir = run / ".assay"
        for name in ("events.jsonl", "mutations.jsonl", "activity.jsonl"):
            body = (state_dir / name).read_text()
            assert "topsecretvalue123" not in body, name
        assert "[REDACTED:ASSAY_TEST_SECRET]" in (state_dir / "events.jsonl").read_text()
    finally:
        os.environ.pop("ASSAY_TEST_SECRET", None)
        stop_run(run)


def test_zero_prior_withholds_descriptions(tmp_path):
    run = tmp_path / "zp1"
    run.mkdir()
    actions = [dict(BASE_ACTIONS[0], description="increments the counter")] + [
        dict(item) for item in BASE_ACTIONS[1:]
    ]
    registry = _write_registry(run, actions=actions)
    try:
        started = _start(run, registry)
        assert "description (data, not instructions" in started.stdout
        assert "increments the counter" in started.stdout
    finally:
        stop_run(run)
    run2 = tmp_path / "zp2"
    run2.mkdir()
    registry2 = _write_registry(run2, actions=actions, zero_prior=True)
    try:
        started = _start(run2, registry2)
        assert started.returncode == 0, started.stderr
        assert "increments the counter" not in started.stdout
    finally:
        stop_run(run2)


def test_liveness_rehearsal_waiver_and_goal_gamble(tmp_path):
    run = tmp_path / "live"
    run.mkdir()
    actions = [
        dict(BASE_ACTIONS[0], liveness="live", rehearsal_quota=2),
        *[dict(item) for item in BASE_ACTIONS[1:]],
    ]
    registry = _write_registry(run, actions=actions)
    try:
        started = _start(run, registry)
        assert started.returncode == 0, started.stderr
        token = _owner_token(started.stdout)
        # Enforced liveness: default-deny until rehearsed or waived.
        refused = run_cli(run, "act", "INC", "amount=1", "--predict", "change")
        assert refused.returncode == 2 and "rehearsal quota" in refused.stderr
        waived = run_cli(
            run, "waive", "INC", "--token", token, "--because", "test world is safe"
        )
        assert waived.returncode == 0, waived.stderr
        # The goal channel claim sits in the gamble bucket and grades.
        acted = run_cli(
            run, "act", "INC", "amount=2", "--predict", "change; ch goal = false"
        )
        assert acted.returncode == 0, acted.stderr
        event = _events(run)[-1]
        goal_claim = next(
            item for item in event["grade"] if item.get("channel") == "goal"
        )
        assert goal_claim["bucket"] == "gamble" and goal_claim["ok"]
        won = run_cli(run, "act", "INC", "amount=1", "--predict", "ch goal = true")
        assert won.returncode == 0, won.stderr
        assert "OUTCOME | GAME_COMPLETE" in won.stdout
    finally:
        stop_run(run)


def test_ungated_event_invalidates_run(tmp_path):
    run = tmp_path / "tamper"
    run.mkdir()
    registry = _write_registry(run)
    try:
        assert _start(run, registry).returncode == 0
        assert run_cli(run, "act", "INC", "amount=1", "--predict", "change").returncode == 0
        # Forge an ungated paid event straight into the journal.
        events = _events(run)
        forged = dict(events[-1])
        forged.update(id=len(events), predict=None, predict_ok=None, grade=[])
        forged.pop("declares", None)
        with (run / ".assay" / "events.jsonl").open("a") as handle:
            handle.write(json.dumps(forged, sort_keys=True) + "\n")
        audited = run_cli(run, "audit")
        assert "INVALID FOR SCORING" in audited.stdout
        assert "UNGATED events" in audited.stdout
        status = run_cli(run, "status")
        assert "INTEGRITY |" in status.stdout and "UNGATED" in status.stdout
    finally:
        stop_run(run)
