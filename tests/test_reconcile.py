"""Orphan recovery runs only at `assay start`, and only once the daemon is
confirmed dead or absent. While the daemon is between the mutation append and
the event append (verifiers and extractors run in that window), no other
command may recover the mutation, or the run is double-counted the moment the
daemon appends its own graded event."""

from __future__ import annotations

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


def _events(run: Path) -> list[dict]:
    lines = (run / ".assay" / "events.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _mutations(run: Path) -> list[dict]:
    lines = (run / ".assay" / "mutations.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _act_in_background(run: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, str(ASSAY_CLI), "--run-dir", str(run),
         "act", "NOOP", "--predict", "verify:checks/slow.py; noop"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _wait_for_mutation(run: Path, seconds: float = 10.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if (run / ".assay" / "mutations.jsonl").exists() and _mutations(run):
            return
        time.sleep(0.05)
    raise AssertionError("the daemon never journaled the mutation")


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


def test_start_recovers_an_orphan_once_the_daemon_is_dead(tmp_path):
    run = tmp_path / "crash"
    _prepare(run)
    try:
        assert _start(run).returncode == 0
        pid = json.loads((run / ".assay" / "broker.json").read_text())["pid"]
        acting = _act_in_background(run)
        _wait_for_mutation(run)
        os.kill(pid, signal.SIGKILL)  # between spend and record
        acting.wait(timeout=60)
        assert acting.returncode != 0
        assert len(_events(run)) == 1 and len(_mutations(run)) == 1
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
        assert events[1]["mutation_id"] == 1
        assert "recovered from broker mutation journal" in events[1]["note"]
        # A recovered event carries no prediction: the run is invalid for
        # scoring, and the audit says exactly that and nothing worse.
        audited = run_cli(run, "audit")
        assert "INVALID FOR SCORING" in audited.stdout
        assert "UNGATED events [1]" in audited.stdout
        assert "recovered orphan events [1]" in audited.stdout
        assert "not yet in the timeline" not in audited.stdout
        # Running start again recovers nothing more.
        again = _start(run)
        assert again.returncode == 0 and "JOURNAL | recovered" not in again.stdout
        assert len(_events(run)) == 2
    finally:
        stop_run(run)
