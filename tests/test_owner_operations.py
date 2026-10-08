"""The owner operations as daemon operations (docs/ARCHITECTURE.md sections
7.2 and 8.1): `approve`, `waive` and `goal ratify` are checked inside the
daemon against the token hash it holds. An approval lives in the daemon's
memory for its 600 seconds, is consumed there by the paid path and is gone
with a stop; a waiver is the activity record, rebuilt into the held set at
every start; a ratification is the daemon's write of goal.json. None of the
three has a file the agent could write, each is refused with OWNER_TOKEN under
a wrong token (an `owner_refused` record naming the operation, so probing is
visible) and with DAEMON_UNAVAILABLE without a daemon, each prints what it
always printed, and each takes the token from a file outside the run
directory (`--token-file`) so it never shows in the process list. A grant is
consumed after the disk is verified, so a tamper refusal leaves it held. The
e2e scenarios drive the real CLI and the real daemon with the fake adapter;
the expiry is a unit test over a moved clock."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from conftest import FAKE_ADAPTER, run_cli, run_of, stop_run

ACTIONS = [
    {"name": "INC", "params": {"amount": {"type": "int", "min": 1, "max": 2}}},
    {"name": "NOOP", "params": {}},
]

OWNER_TOKEN_REFUSAL = (
    "ERROR | OWNER_TOKEN | owner authority required: pass --token <the token printed at "
    "start>. The agent proposes; the owner ratifies.\n"
    "NEXT | ask the operator to run this command with --token; the agent never holds the token\n"
)


def _prepare(run: Path, actions: list[dict], **extra) -> None:
    run.mkdir()
    (run / "reg.json").write_text(json.dumps({"actions": actions, "budget": {"actions": 30}, **extra}))


def _start(run: Path):
    return run_cli(
        run, "start", "fake1", "--adapter", f"{FAKE_ADAPTER}:factory",
        "--registry", str(run / "reg.json"),
    )


def _token(stdout: str) -> str:
    found = re.search(r"OWNER TOKEN \| (\S+) \|", stdout)
    assert found, stdout
    return found.group(1)


def _activity(run: Path, kind: str) -> list[dict]:
    """The activity records of one kind, without the stamp every line carries."""
    lines = (run / ".assay" / "activity.jsonl").read_text().splitlines()
    records = [json.loads(line) for line in lines if line.strip()]
    return [{k: v for k, v in record.items() if k != "timestamp"} for record in records if record["kind"] == kind]


def _no_file(run: Path) -> None:
    """Neither grant has a file of its own (section 6.3)."""
    assert not (run / ".assay" / "approvals.json").exists()
    assert not (run / ".assay" / "waivers.json").exists()


def _unavailable(command: str, again: str) -> str:
    return (
        f"ERROR | DAEMON_UNAVAILABLE | {command} is a daemon operation and this run's environment "
        f"owner is not running\nNEXT | resume it with `assay start WORLD_ID`, then {again} again\n"
    )


def test_an_approval_is_granted_in_the_daemon_used_once_and_gone_with_a_stop(tmp_path):
    run = tmp_path / "approve"
    _prepare(run, [ACTIONS[0], {"name": "NOOP", "params": {}, "approval": True}])
    try:
        started = _start(run)
        assert started.returncode == 0, started.stderr
        token = _token(started.stdout)
        denied = run_cli(run, "act", "NOOP", "--predict", "noop")
        assert denied.returncode == 2
        assert denied.stderr == (
            "ERROR | APPROVAL_REQUIRED | NOOP is approval-gated (default-deny) and has no fresh approval\n"
            "NEXT | ask the operator to grant one use with `assay approve NOOP --token ...`\n"
        )
        # The wrong token and no token: refused by the daemon, nothing
        # granted, and the probe on record (the operation, never the token).
        for wrong in (["--token", "wrong"], []):
            refused = run_cli(run, "approve", "noop", *wrong)
            assert refused.returncode == 2 and refused.stderr == OWNER_TOKEN_REFUSAL
        assert not _activity(run, "approval_granted")
        assert _activity(run, "owner_refused") == [{"kind": "owner_refused", "operation": "approve"}] * 2
        assert "wrong" not in (run / ".assay" / "activity.jsonl").read_text()
        granted = run_cli(run, "approve", "noop", "--token", token)
        assert granted.returncode == 0, granted.stderr
        assert granted.stdout == "APPROVED | one use of NOOP granted (expires in 10 minutes, consumed on use)\n"
        assert [record["action"] for record in _activity(run, "approval_granted")] == ["NOOP"]
        _no_file(run)
        # One use: the paid path consumes the held grant; the next act is refused again.
        acted = run_cli(run, "act", "NOOP", "--predict", "noop")
        assert acted.returncode == 0, acted.stderr
        assert "OUTCOME | PREDICTED" in acted.stdout
        assert [record["action"] for record in _activity(run, "approval_used")] == ["NOOP"]
        again = run_cli(run, "act", "NOOP", "--predict", "noop")
        assert again.returncode == 2 and "has no fresh approval" in again.stderr
        # A grant does not survive the daemon that holds it: nothing on disk
        # carries it into the next life, the activity record included.
        assert run_cli(run, "approve", "NOOP", "--token", token).returncode == 0
        assert run_cli(run, "stop").returncode == 0
        offline = run_cli(run, "approve", "NOOP", "--token", token)
        assert offline.returncode == 2 and offline.stderr == _unavailable("approve", "approve")
        assert _start(run).returncode == 0
        stale = run_cli(run, "act", "NOOP", "--predict", "noop")
        assert stale.returncode == 2 and "has no fresh approval" in stale.stderr
        assert len(_activity(run, "approval_granted")) == 2
        assert len(_activity(run, "approval_used")) == 1
        # --json: the lines, as for every command without a result record.
        machine = run_cli(run, "approve", "NOOP", "--token", token, "--json")
        assert machine.returncode == 0 and machine.stderr == ""
        assert json.loads(machine.stdout) == {
            "lines": ["APPROVED | one use of NOOP granted (expires in 10 minutes, consumed on use)"]
        }
        _no_file(run)
    finally:
        stop_run(run)


def test_an_approval_expires_after_its_600_seconds(paths, monkeypatch):
    """The expiry over a moved clock: a grant 600 seconds old is still fresh,
    one older is refused with the expiry and stays unconsumed, and a new
    grant replaces it."""
    from assay import agenda
    from assay.core import AssayError

    run = run_of(paths)
    run.owner_hash = hashlib.sha256(b"tok").hexdigest()
    clock = [1000.0]
    # The grant is stamped on the monotonic clock: a wall-clock change
    # neither extends nor cuts it.
    monkeypatch.setattr(agenda.time, "monotonic", lambda: clock[0])
    assert agenda.grant_approval(run, "fire", "tok") == "FIRE"
    assert run.approvals == {"FIRE": 1000.0}
    clock[0] = 1600.0
    agenda.consume_approval(run, "FIRE")
    assert run.approvals == {}
    assert agenda.grant_approval(run, "FIRE", "tok") == "FIRE"
    clock[0] = 2200.5
    with pytest.raises(AssayError, match="^FIRE's approval expired after 600s \\(default-deny with timeout\\)$") as expired:
        agenda.consume_approval(run, "FIRE")
    assert expired.value.code == "APPROVAL_REQUIRED"
    assert expired.value.hint == "ask the operator to run `assay approve FIRE --token ...` again"
    assert run.approvals == {"FIRE": 1600.0}
    assert agenda.grant_approval(run, "FIRE", "tok") == "FIRE"
    assert run.approvals == {"FIRE": 2200.5}
    # The check leaves the grant held; the consumption uses it up.
    assert agenda.check_approval(run, "fire") == "FIRE" and run.approvals == {"FIRE": 2200.5}
    agenda.consume_approval(run, "fire")
    assert run.approvals == {}
    with pytest.raises(AssayError, match="has no fresh approval$"):
        agenda.consume_approval(run, "FIRE")
    kinds = [record["kind"] for record in _activity(paths.root, "approval_granted")]
    assert kinds == ["approval_granted"] * 3
    assert len(_activity(paths.root, "approval_used")) == 2


def test_a_tamper_refusal_leaves_the_grant_held(tmp_path):
    """The gate checks the grant before any spend and the daemon consumes it
    after the disk is verified (section 8.3): an action refused over a
    changed file burns no approval and journals no use."""
    run = tmp_path / "held"
    _prepare(run, [ACTIONS[0], {"name": "NOOP", "params": {}, "approval": True}])
    try:
        started = _start(run)
        assert started.returncode == 0, started.stderr
        token = _token(started.stdout)
        assert run_cli(run, "approve", "NOOP", "--token", token).returncode == 0
        registry = run / ".assay" / "registry.json"
        spec = json.loads(registry.read_text())
        spec["budget"] = {"actions": 999}
        registry.write_text(json.dumps(spec))
        refused = run_cli(run, "act", "NOOP", "--predict", "noop")
        assert refused.returncode == 5 and "ERROR | TAMPER_DETECTED | registry.json (held " in refused.stderr
        assert not _activity(run, "approval_used")
        assert len(_activity(run, "approval_granted")) == 1
        # The gate's own refusal still comes first: the second use of the one
        # grant is refused by the gate, and the daemon verified nothing for it.
        assert len(_activity(run, "tamper_detected")) == 1
        again = run_cli(run, "act", "INC", "amount=1", "--predict", "change")
        assert again.returncode == 5 and "ERROR | TAMPER_DETECTED |" in again.stderr
        assert len(_activity(run, "tamper_detected")) == 1
    finally:
        stop_run(run)


def test_the_token_comes_from_a_file_outside_the_run_directory(tmp_path):
    """`--token-file PATH` on the four owner commands: the token never shows
    in the process list; a path inside the run directory is refused as
    `--owner-token-file` refuses it, both flags at once are refused, and a
    file that cannot be read is named."""
    run = tmp_path / "token-file"
    _prepare(run, [ACTIONS[0], {"name": "NOOP", "params": {}, "approval": True}])
    token_file = tmp_path / "tokens" / "fake1.token"
    try:
        started = run_cli(
            run, "start", "fake1", "--adapter", f"{FAKE_ADAPTER}:factory",
            "--registry", str(run / "reg.json"), "--owner-token-file", str(token_file),
        )
        assert started.returncode == 0, started.stderr
        assert "OWNER TOKEN | written to" in started.stdout
        granted = run_cli(run, "approve", "NOOP", "--token-file", str(token_file))
        assert granted.returncode == 0, granted.stderr
        assert granted.stdout == "APPROVED | one use of NOOP granted (expires in 10 minutes, consumed on use)\n"
        inside = run / "fake1.token"
        inside.write_text(token_file.read_text())
        refused = run_cli(run, "waive", "INC", "--token-file", str(inside), "--because", "x")
        assert refused.returncode == 2
        assert refused.stderr == f"ERROR | PATH_INVALID | --token-file must point outside the run directory, got {inside.resolve()}\n"
        both = run_cli(run, "goal", "ratify", "1", "--token", "t", "--token-file", str(token_file))
        assert both.returncode == 2
        assert both.stderr == "ERROR | COMMAND_ARGS | pass the owner token as --token or as --token-file, not both\n"
        missing = run_cli(run, "module", "install", str(tmp_path / "none.py"), "--token-file", str(tmp_path / "nope"))
        assert missing.returncode == 2 and missing.stderr.startswith(
            f"ERROR | FILE_NOT_FOUND | --token-file {tmp_path / 'nope'} cannot be read: FileNotFoundError"
        )
        assert not _activity(run, "owner_refused")
        wrong = tmp_path / "wrong.token"
        wrong.write_text("nope\n")
        denied = run_cli(run, "approve", "NOOP", "--token-file", str(wrong))
        assert denied.returncode == 2 and denied.stderr == OWNER_TOKEN_REFUSAL
        assert _activity(run, "owner_refused") == [{"kind": "owner_refused", "operation": "approve"}]
    finally:
        stop_run(run)


def test_a_waiver_is_the_activity_record_and_survives_a_stop_and_a_start(tmp_path):
    run = tmp_path / "waive"
    _prepare(run, [dict(ACTIONS[0], liveness="live", rehearsal_quota=2), ACTIONS[1]])
    try:
        started = _start(run)
        assert started.returncode == 0, started.stderr
        token = _token(started.stdout)
        refused = run_cli(run, "act", "INC", "amount=1", "--predict", "change")
        assert refused.returncode == 2 and "rehearsal quota of 2 that is not met" in refused.stderr
        for wrong in (["--token", "wrong"], []):
            denied = run_cli(run, "waive", "inc", *wrong, "--because", "no")
            assert denied.returncode == 2 and denied.stderr == OWNER_TOKEN_REFUSAL
        unexplained = run_cli(run, "waive", "INC", "--token", token)
        assert unexplained.returncode == 2
        assert unexplained.stderr == "ERROR | COMMAND_ARGS | a liveness waiver needs --because <why it is safe now>\n"
        assert not _activity(run, "liveness_waived")
        waived = run_cli(run, "waive", "inc", "--token", token, "--because", "the test world is safe")
        assert waived.returncode == 0, waived.stderr
        assert waived.stdout == "WAIVED | rehearsal quota for INC (journaled)\n"
        assert _activity(run, "liveness_waived") == [
            {"kind": "liveness_waived", "action": "INC", "because": "the test world is safe"}
        ]
        _no_file(run)
        acted = run_cli(run, "act", "INC", "amount=1", "--predict", "change")
        assert acted.returncode == 0, acted.stderr
        # The record is the waiver: the next daemon rebuilds the set from it.
        assert run_cli(run, "stop").returncode == 0
        offline = run_cli(run, "waive", "INC", "--token", token, "--because", "x")
        assert offline.returncode == 2 and offline.stderr == _unavailable("waive", "waive")
        assert _start(run).returncode == 0
        acted = run_cli(run, "act", "INC", "amount=1", "--predict", "change")
        assert acted.returncode == 0, acted.stderr
        assert "OUTCOME | PREDICTED" in acted.stdout
        from assay.core import RunPaths
        from assay.run import Run

        loaded = Run.load(RunPaths(run), strict=False)
        assert loaded.waivers == {"INC"} and loaded.approvals == {}
        _no_file(run)
    finally:
        stop_run(run)


def test_a_ratification_is_the_daemons_write_of_the_goal(tmp_path):
    run = tmp_path / "ratify"
    _prepare(run, ACTIONS, goal={"text": "light the lamp, then win"})
    try:
        started = _start(run)
        assert started.returncode == 0, started.stderr
        token = _token(started.stdout)
        proposed = run_cli(run, "goal", "propose", "win faster", "--because", "test")
        assert proposed.returncode == 0 and "proposal #1" in proposed.stdout
        for wrong in (["--token", "wrong"], []):
            denied = run_cli(run, "goal", "ratify", "1", *wrong)
            assert denied.returncode == 2 and denied.stderr == OWNER_TOKEN_REFUSAL
        assert not (run / ".assay" / "goal.json").exists()
        missing = run_cli(run, "goal", "ratify", "9", "--token", token)
        assert missing.returncode == 2 and missing.stderr == "ERROR | GOAL_PROPOSAL | no goal proposal with id 9\n"
        ratified = run_cli(run, "goal", "ratify", "1", "--token", token)
        assert ratified.returncode == 0, ratified.stderr
        assert ratified.stdout == "GOAL | ratified #1: win faster; status now re-presents it as the standing goal\n"
        goal = json.loads((run / ".assay" / "goal.json").read_text())
        assert goal["text"] == "win faster" and goal["ratified_from"] == 1
        assert _activity(run, "goal_ratified") == [{"kind": "goal_ratified", "id": 1, "text": "win faster"}]
        status = run_cli(run, "status")
        assert "AGENDA | goal (ratified): win faster | not achieved" in status.stdout
        listed = run_cli(run, "goal", "list")
        assert "#1 [ratified] win faster; test" in listed.stdout
        twice = run_cli(run, "goal", "ratify", "1", "--token", token)
        assert twice.returncode == 2 and twice.stderr == "ERROR | GOAL_PROPOSAL | proposal 1 is already ratified\n"
        assert run_cli(run, "stop").returncode == 0
        assert run_cli(run, "goal", "propose", "win slower").returncode == 0  # the agent's lane stays offline
        offline = run_cli(run, "goal", "ratify", "2", "--token", token)
        assert offline.returncode == 2 and offline.stderr == _unavailable("goal ratify", "ratify")
        assert "AGENDA | goal (ratified): win faster" in run_cli(run, "status").stdout
    finally:
        stop_run(run)
