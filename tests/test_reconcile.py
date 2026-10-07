"""Orphan recovery runs only at `assay start`, and only once the daemon is
confirmed dead or absent. While the daemon is between the mutation append and
the event append (verifiers and extractors run in that window), no other
command may recover the mutation, or the run is double-counted the moment the
daemon appends its own graded event.

From 1.2.0 on the mutation record carries the step's parsed claims and
recovery keeps the prediction: the claims are regraded against the stored
response and the recovered event is gated by its fields (docs/ARCHITECTURE.md
section 6.5). A record without claims (written before 1.2.0, or by a
model-plan step) is recovered UNGATED, as it always was, and the audit says
why."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from conftest import ASSAY_CLI, FAKE_ADAPTER, run_cli, stop_run

ACTIONS = [
    {"name": "INC", "params": {"amount": {"type": "int", "min": 1, "max": 2}}},
    {"name": "NOOP", "params": {}},
]

# Sleeps inside the grading window: the mutation is already journaled, the
# event is not. The identity probe runs it a second time, so the window is
# about twice this long.
SLOW_VERIFIER = """\
import time

def verify(before, after):
    time.sleep(2.0)
    return before["data"] == after["data"], "slow but true"
"""

CHECKER = Path(__file__).resolve().parents[1] / "verify" / "assay_verify.py"
RECOVERED_NOTE = "recovered from broker mutation journal"
PREDICT = "verify:checks/slow.py; noop"


def _prepare(run: Path) -> None:
    run.mkdir()
    (run / "reg.json").write_text(json.dumps({"actions": ACTIONS, "budget": {"actions": 20}}))
    (run / "checks").mkdir()
    (run / "checks" / "slow.py").write_text(SLOW_VERIFIER)


def _start(run: Path):
    return run_cli(
        run, "start", "fake1", "--adapter", f"{FAKE_ADAPTER}:factory",
        "--registry", str(run / "reg.json"),
    )


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _events(run: Path) -> list[dict]:
    return _lines(run / ".assay" / "events.jsonl")


def _mutations(run: Path) -> list[dict]:
    return _lines(run / ".assay" / "mutations.jsonl")


def _activity(run: Path) -> list[dict]:
    return _lines(run / ".assay" / "activity.jsonl")


def _act_in_background(run: Path, predict: str = PREDICT) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, str(ASSAY_CLI), "--run-dir", str(run),
         "act", "NOOP", "--predict", predict],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _wait_for_mutation(run: Path, seconds: float = 10.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if (run / ".assay" / "mutations.jsonl").exists() and _mutations(run):
            return
        time.sleep(0.05)
    raise AssertionError("the daemon never journaled the mutation")


def _crash_between_spend_and_record(run: Path, predict: str) -> None:
    """Start the run, act with this prediction, and kill the daemon once the
    mutation is on disk and the event is not: the slow verifier holds the
    daemon inside the grading window."""
    assert _start(run).returncode == 0
    pid = json.loads((run / ".assay" / "broker.json").read_text())["pid"]
    acting = _act_in_background(run, predict)
    _wait_for_mutation(run)
    os.kill(pid, signal.SIGKILL)  # between spend and record
    acting.wait(timeout=60)
    assert acting.returncode != 0
    assert len(_events(run)) == 1 and len(_mutations(run)) == 1


def _checker_report(run: Path) -> dict:
    """The independent checker's report; it exits 0 on CLEAN and 1 on
    INVALID FOR SCORING, and 2 only when it cannot read the journal."""
    checked = subprocess.run(
        [sys.executable, str(CHECKER), str(run), "--json"],
        capture_output=True, text=True, timeout=60,
    )
    assert checked.returncode in (0, 1), checked.stdout + checked.stderr
    return json.loads(checked.stdout)


def test_offline_commands_never_recover_while_the_daemon_writes(tmp_path):
    run = tmp_path / "inflight"
    _prepare(run)
    try:
        assert _start(run).returncode == 0
        acting = _act_in_background(run)
        _wait_for_mutation(run)
        acting.kill()  # the client (and its run lock) is gone; the daemon is grading
        acting.wait(timeout=10)
        assert len(_events(run)) == 1 and len(_mutations(run)) == 1
        # status and audit see the gap and leave it alone.
        status = run_cli(run, "status")
        assert status.returncode == 0, status.stderr
        audited = run_cli(run, "audit")
        assert "1 spend(s) in the mutation journal not yet in the timeline [1]" in audited.stdout
        assert len(_events(run)) == 1
        # The daemon finishes grading and appends its own event: exactly one,
        # graded, for that mutation.
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and len(_events(run)) < 2:
            time.sleep(0.1)
        events = _events(run)
        assert len(events) == 2
        assert events[1]["mutation_id"] == 1 and events[1]["predict_ok"] is True
        assert "recovered" not in str(events[1].get("note"))
        audited = run_cli(run, "audit")
        assert "AUDIT | CLEAN" in audited.stdout and "not yet in the timeline" not in audited.stdout
        resumed = _start(run)
        assert resumed.returncode == 0 and "RESUMED" in resumed.stdout
        assert len(_events(run)) == 2
    finally:
        stop_run(run)


def test_start_recovers_an_orphan_with_its_prediction_once_the_daemon_is_dead(tmp_path):
    run = tmp_path / "crash"
    _prepare(run)
    try:
        _crash_between_spend_and_record(run, PREDICT)
        # The record carries the reasoning and the parsed claims, the
        # verifier's with the hash admitted before the spend.
        digest = hashlib.sha256(SLOW_VERIFIER.encode()).hexdigest()
        record = _mutations(run)[0]
        assert record["reasoning"] == {"predict": PREDICT}
        assert [claim["kind"] for claim in record["claims"]] == ["verify", "noop"]
        assert record["claims"][0]["verifier_hash"] == digest
        # Offline commands still do not recover; they report the gap.
        audited = run_cli(run, "audit")
        assert "not yet in the timeline [1]" in audited.stdout
        assert len(_events(run)) == 1
        # start finds no live daemon, recovers exactly one event, replays.
        resumed = _start(run)
        assert resumed.returncode == 0, resumed.stderr
        assert "JOURNAL | recovered 1 paid action(s) into timeline" in resumed.stdout
        assert "RECOVERED" in resumed.stdout
        events = _events(run)
        assert len(events) == 2
        event = events[1]
        assert event["mutation_id"] == 1
        assert event["note"] == RECOVERED_NOTE
        # The recovered event keeps its prediction: the claims regraded from
        # the record against the stored response, the verifier through its
        # stored copy under .assay/verifiers/.
        assert event["predict"] == PREDICT
        assert event["predict_ok"] is True
        grades = {grade["kind"]: grade for grade in event["grade"]}
        assert set(grades) == {"verify", "noop"}
        assert grades["verify"]["ok"] is True and grades["verify"]["actual"] == "slow but true"
        assert grades["verify"]["verifier"] is True
        assert grades["verify"]["verifier_hash"] == digest
        assert (run / ".assay" / "verifiers" / f"{digest}.py").read_text() == SLOW_VERIFIER
        assert grades["noop"]["ok"] is True and grades["noop"]["actual"] == "no observed change"
        assert "declares" not in event
        recovery = [item for item in _activity(run) if item["kind"] == "mutation_recovery"]
        assert [(item["event"], item["recovered"]) for item in recovery] == [(1, 1)]
        # Gated by construction: the audit and the independent checker read
        # the fields, and the recovered orphan stays informational.
        audited = run_cli(run, "audit")
        assert "AUDIT | CLEAN" in audited.stdout
        assert "UNGATED" not in audited.stdout
        assert "recovered orphan events [1]" in audited.stdout
        assert "recovered without its prediction" not in audited.stdout
        assert "not yet in the timeline" not in audited.stdout
        report = json.loads((run / ".assay" / "audit.json").read_text())
        assert report["recovered_orphans"] == [1]
        assert report["recovered_without_prediction"] == []
        assert report["invalid_for_scoring"] is False
        checked = _checker_report(run)
        assert checked["verdict"] == "CLEAN"
        assert checked["ungated"] == [] and checked["recovered_orphans"] == [1]
        # Running start again recovers nothing more.
        again = _start(run)
        assert again.returncode == 0 and "JOURNAL | recovered" not in again.stdout
        assert len(_events(run)) == 2
    finally:
        stop_run(run)


def test_a_windowed_claim_is_ungradable_on_recovery(tmp_path):
    run = tmp_path / "window"
    _prepare(run)
    try:
        predict = "verify:checks/slow.py; noop @within 1s"
        _crash_between_spend_and_record(run, predict)
        resumed = _start(run)
        assert resumed.returncode == 0, resumed.stderr
        assert "JOURNAL | recovered 1 paid action(s) into timeline" in resumed.stdout
        event = _events(run)[1]
        assert event["note"] == RECOVERED_NOTE
        assert event["predict"] == predict
        # The step's duration died with the process: the windowed claim is
        # UNGRADABLE with the recovery actual, its own outcome, so predict_ok
        # is null; the verifier claim carries no window and is graded.
        assert event["predict_ok"] is None
        grades = {grade["kind"]: grade for grade in event["grade"]}
        assert grades["noop"]["ungradable"] is True and grades["noop"]["ok"] is False
        assert grades["noop"]["actual"] == "UNGRADABLE: recovered, step duration unknown"
        assert grades["noop"]["window_s"] == 1
        assert grades["verify"]["ok"] is True and grades["verify"]["actual"] == "slow but true"
        audited = run_cli(run, "audit")
        assert "AUDIT | CLEAN" in audited.stdout
        assert "UNGATED" not in audited.stdout
        assert "recovered orphan events [1]" in audited.stdout
        assert _checker_report(run)["verdict"] == "CLEAN"
    finally:
        stop_run(run)


def test_a_record_without_claims_is_recovered_ungated_and_the_audit_says_why(tmp_path):
    """A record written before 1.2.0 (or by a model-plan step) carries no
    `claims`: the event is recovered without its prediction, UNGATED as it
    always was, and the audit says why."""
    run = tmp_path / "old"
    _prepare(run)
    try:
        assert _start(run).returncode == 0
        stop_run(run)
        start = _events(run)[0]
        # The pre-1.2.0 shape of the record, by hand. NOOP leaves the fake
        # world as event 0 saw it, so the replay at start reproduces it.
        record = {
            "mutation_id": 1,
            "action": "NOOP",
            "data": None,
            "reasoning": {"predict": "noop"},
            "observation": {
                "state": start["state"],
                "levels_completed": start["levels_completed"],
                "win_levels": start["win_levels"],
                "available_actions": start["available_actions"],
                "data": start["observation"],
            },
            "timestamp": start["timestamp"],
        }
        with (run / ".assay" / "mutations.jsonl").open("a") as handle:
            handle.write(json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n")
        resumed = _start(run)
        assert resumed.returncode == 0, resumed.stderr
        assert "JOURNAL | recovered 1 paid action(s) into timeline" in resumed.stdout
        events = _events(run)
        assert len(events) == 2
        event = events[1]
        assert event["mutation_id"] == 1 and event["note"] == RECOVERED_NOTE
        assert "predict" not in event and "predict_ok" not in event and "grade" not in event
        recovery = [item for item in _activity(run) if item["kind"] == "mutation_recovery"]
        assert [(item["event"], item["recovered"]) for item in recovery] == [(1, 1)]
        audited = run_cli(run, "audit")
        assert "INVALID FOR SCORING" in audited.stdout
        assert "UNGATED events [1]" in audited.stdout
        assert "recovered orphan events [1]" in audited.stdout
        assert (
            "AUDIT | 1 of them recovered without its prediction: the record predates "
            "1.2.0 or was a model-plan step; counted UNGATED above"
        ) in audited.stdout
        report = json.loads((run / ".assay" / "audit.json").read_text())
        assert report["ungated"] == [1] and report["ungated_permitted"] == []
        assert report["recovered_orphans"] == [1]
        assert report["recovered_without_prediction"] == [1]
        checked = _checker_report(run)
        assert checked["verdict"] == "INVALID FOR SCORING" and checked["ungated"] == [1]
    finally:
        stop_run(run)
