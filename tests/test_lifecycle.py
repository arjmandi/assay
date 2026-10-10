"""Daemon lifecycle: `assay stop`, process identity before any signal, the
SIGTERM handler that finishes an in-flight step, and the resume rule that never
kills a daemon which is merely busy. Every scenario drives the real CLI, the
real daemon and the slow adapter in this directory."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from conftest import ASSAY_CLI, run_cli, stop_run

SLOW_ADAPTER = Path(__file__).resolve().parent / "slow_adapter.py"
ACTIONS = [
    {"name": "INC", "params": {"amount": {"type": "int", "min": 1, "max": 2}}},
    {"name": "NOOP", "params": {}},
    {"name": "SLEEP", "params": {"seconds": {"type": "int", "min": 1, "max": 20}}},
]


def _prepare(run: Path) -> None:
    run.mkdir()
    (run / "reg.json").write_text(json.dumps({"actions": ACTIONS, "budget": {"actions": 30}}))


def _start(run: Path):
    return run_cli(
        run, "start", "slow1", "--adapter", f"{SLOW_ADAPTER}:factory",
        "--registry", str(run / "reg.json"),
    )


def _broker(run: Path) -> dict:
    return json.loads((run / ".assay" / "broker.json").read_text())


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _events(run: Path) -> list[dict]:
    lines = (run / ".assay" / "events.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _wait_gone(pid: int, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


def test_stop_then_resume_keeps_the_journal(tmp_path):
    run = tmp_path / "stop"
    _prepare(run)
    try:
        assert _start(run).returncode == 0
        assert run_cli(run, "act", "INC", "amount=1", "--predict", "change").returncode == 0
        pid = _broker(run)["pid"]
        stopped = run_cli(run, "stop")
        assert stopped.returncode == 0, stopped.stderr
        assert f"STOPPED | environment owner pid {pid}" in stopped.stdout
        assert not _alive(pid)
        assert _broker(run)["status"] == "STOPPED"
        # Offline commands still work; paid ones name the way back.
        assert "AUDIT | CLEAN" in run_cli(run, "audit").stdout
        refused = run_cli(run, "act", "NOOP", "--predict", "noop")
        assert refused.returncode == 2 and "assay start" in refused.stderr
        # A second stop is a no-op that says so.
        again = run_cli(run, "stop")
        assert again.returncode == 0 and "no live environment owner" in again.stdout
        # Resume replays the journal through a fresh daemon.
        resumed = _start(run)
        assert resumed.returncode == 0, resumed.stderr
        assert "RECOVERED" in resumed.stdout and "replayed 1 paid actions" in resumed.stdout
        assert _broker(run)["pid"] != pid
        assert run_cli(run, "act", "INC", "amount=1", "--predict", "change").returncode == 0
        assert len(_events(run)) == 3
    finally:
        stop_run(run)


def test_stale_pid_is_never_signalled(tmp_path):
    """broker.json names a pid that is alive but is not our daemon (pid reuse
    after a reboot). Neither stop nor resume may signal it."""
    run = tmp_path / "stale"
    _prepare(run)
    try:
        assert _start(run).returncode == 0
        assert run_cli(run, "stop").returncode == 0
        # Forge a descriptor pointing at this very test process.
        descriptor = _broker(run)
        descriptor.update(status="READY", pid=os.getpid())
        (run / ".assay" / "broker.json").write_text(json.dumps(descriptor))
        stopped = run_cli(run, "stop")
        assert stopped.returncode == 0, stopped.stderr
        assert "no live environment owner" in stopped.stdout
        assert _broker(run)["status"] == "STOPPED" and _broker(run).get("stale") is True
        # Resume starts a fresh daemon and leaves this process alone.
        (run / ".assay" / "broker.json").write_text(json.dumps(descriptor))
        resumed = _start(run)
        assert resumed.returncode == 0, resumed.stderr
        assert "RECOVERED" in resumed.stdout
        assert _broker(run)["pid"] != os.getpid()
    finally:
        stop_run(run)


def test_busy_daemon_is_not_killed_by_resume(tmp_path):
    """The acting client dies (a client timeout, a closed terminal) while the
    daemon is still inside the step. The next `assay start` finds a daemon that
    does not answer, is alive, is identified as ours and still owns its socket.
    It must refuse, not kill: the step may already be applied in the world."""
    run = tmp_path / "busy"
    _prepare(run)
    try:
        assert _start(run).returncode == 0
        pid = _broker(run)["pid"]
        acting = subprocess.Popen(
            [sys.executable, str(ASSAY_CLI), "--run-dir", str(run),
             "act", "SLEEP", "seconds=4", "--predict", "change"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(1.0)
        acting.kill()  # the client is gone, its run lock with it
        acting.wait(timeout=10)
        resumed = _start(run)
        assert resumed.returncode == 2, resumed.stdout
        assert "busy or hung" in resumed.stderr and f"pid {pid}" in resumed.stderr
        assert _alive(pid)
        # The daemon finishes the step, journals it (the reply has no reader),
        # and keeps serving. Resume then simply reconnects.
        time.sleep(4.0)
        events = _events(run)
        assert events[-1]["action"] == "SLEEP" and events[-1]["observation"]["slept"] == 4
        resumed = _start(run)
        assert resumed.returncode == 0, resumed.stderr
        assert "RESUMED" in resumed.stdout and _broker(run)["pid"] == pid
        assert "AUDIT | CLEAN" in run_cli(run, "audit").stdout
    finally:
        stop_run(run)


def test_sigterm_during_a_step_finishes_the_step_first(tmp_path):
    """SIGTERM while the daemon is inside a step (what `assay stop` sends, and
    what a supervisor sends): the step completes, is journaled and answered,
    and only then does the daemon exit."""
    run = tmp_path / "midstep"
    _prepare(run)
    try:
        assert _start(run).returncode == 0
        pid = _broker(run)["pid"]
        acting = subprocess.Popen(
            [sys.executable, str(ASSAY_CLI), "--run-dir", str(run),
             "act", "SLEEP", "seconds=3", "--predict", "change"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        time.sleep(1.0)
        os.kill(pid, 15)
        time.sleep(0.3)
        assert _alive(pid)  # still inside the step
        out, err = acting.communicate(timeout=60)
        assert acting.returncode == 0, err
        assert "RESULT | PREDICTED" in out
        assert _wait_gone(pid)
        assert _broker(run)["status"] == "STOPPED"
        events = _events(run)
        assert events[-1]["action"] == "SLEEP"
        assert "AUDIT | CLEAN" in run_cli(run, "audit").stdout
        assert "RECOVERED" in _start(run).stdout
    finally:
        stop_run(run)


def test_orphan_daemon_blocks_a_fresh_start(tmp_path):
    """`.assay` deleted by hand while the daemon lives: start refuses and names
    `assay stop`, which works without any run state."""
    run = tmp_path / "orphan"
    _prepare(run)
    try:
        assert _start(run).returncode == 0
        pid = _broker(run)["pid"]
        import shutil

        shutil.rmtree(run / ".assay")
        refused = _start(run)
        assert refused.returncode == 2
        assert "still serves this directory" in refused.stderr and f"pid {pid}" in refused.stderr
        assert _alive(pid)
        stopped = run_cli(run, "stop")
        assert stopped.returncode == 0, stopped.stderr
        assert f"pid {pid}" in stopped.stdout and not _alive(pid)
        assert _start(run).returncode == 0
    finally:
        stop_run(run)


def test_sigterm_idle_daemon_exits_at_once(tmp_path):
    run = tmp_path / "idle"
    _prepare(run)
    try:
        assert _start(run).returncode == 0
        pid = _broker(run)["pid"]
        os.kill(pid, 15)
        assert _wait_gone(pid, seconds=2.0)
        assert _broker(run)["status"] == "STOPPED"
    finally:
        stop_run(run)


def test_resume_compares_the_pinned_registry_under_the_current_module_name(tmp_path):
    run = tmp_path / "renamed"
    _prepare(run)
    try:
        assert _start(run).returncode == 0
        assert run_cli(run, "stop").returncode == 0
        # A copy pinned before 1.2.0 names the module `sharpness`. A resume
        # with --registry FILE compares it under the current name, so FILE
        # may say either and the run is recovered, not refused.
        pinned = run / ".assay" / "registry.json"
        pinned.write_text(
            json.dumps({**json.loads(pinned.read_text()), "module_modes": {"sharpness": "block"}})
        )
        for name in ("sharpness", "specificity"):
            (run / "reg.json").write_text(
                json.dumps({"actions": ACTIONS, "budget": {"actions": 30}, "module_modes": {name: "block"}})
            )
            resumed = _start(run)
            assert resumed.returncode == 0, resumed.stderr
            assert "RECOVERED" in resumed.stdout and "replayed 0 paid actions" in resumed.stdout
            assert run_cli(run, "stop").returncode == 0
    finally:
        stop_run(run)
