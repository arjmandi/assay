"""Channels first-class in code: budget_remaining grades, status shows every
channel's reading without spawning an extractor, `channel list --read`
computes fresh, and receipts show path channels that changed."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import FAKE_ADAPTER, run_cli, stop_run

ACTIONS = [
    {"name": "INC", "params": {"amount": {"type": "int", "min": 1, "max": 2}}},
    {"name": "SET_LAMP", "params": {"state": {"type": "str", "enum": ["on", "off"]}}},
    {"name": "NOOP", "params": {}},
]


def _prepare(run: Path, **extra) -> None:
    run.mkdir()
    (run / "reg.json").write_text(json.dumps({"actions": ACTIONS, **extra}))


def _start(run: Path):
    return run_cli(
        run, "start", "fake1", "--adapter", f"{FAKE_ADAPTER}:factory",
        "--registry", str(run / "reg.json"),
    )


def _events(run: Path) -> list[dict]:
    lines = (run / ".assay" / "events.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def test_budget_remaining_grades(tmp_path):
    run = tmp_path / "budget"
    _prepare(run, budget={"actions": 10})
    try:
        assert _start(run).returncode == 0
        held = run_cli(run, "act", "NOOP", "--predict", "ch budget_remaining = 9")
        assert held.returncode == 0 and "OUTCOME | PREDICTED" in held.stdout, held.stdout
        moved = run_cli(run, "act", "NOOP", "--predict", "ch budget_remaining delta = -1")
        assert moved.returncode == 0 and "OUTCOME | PREDICTED" in moved.stdout, moved.stdout
        missed = run_cli(run, "act", "NOOP", "--predict", "ch budget_remaining = 99")
        assert missed.returncode == 0 and "OUTCOME | SURPRISE" in missed.stdout
        assert "ch budget_remaining = 7" in missed.stdout
        grade = _events(run)[-1]["grade"][0]
        assert grade["bucket"] == "world_model" and grade["ok"] is False
    finally:
        stop_run(run)
    run2 = tmp_path / "nocap"
    _prepare(run2)
    try:
        assert _start(run2).returncode == 0
        ungradable = run_cli(run2, "act", "NOOP", "--predict", "ch budget_remaining = 1")
        assert ungradable.returncode == 0 and "OUTCOME | INVALID_CLAIM" in ungradable.stdout
        assert "needs a registered action cap" in ungradable.stdout
    finally:
        stop_run(run2)


EXTRACTOR = """\
def extract(obs):
    return obs["data"]["counter"] * 10 + (1 if obs["data"]["lamp"] == "on" else 0)
"""


def test_status_shows_readings_without_spawning_extractors(tmp_path):
    """Why the count goes through the cache: an extractor leaves no trace
    outside its scratch directory (the sandbox refuses the marker file the
    old test had it write), so its runs are counted through what the daemon
    caches at grade time in `.assay/channel_readings.json`. Why 20 against
    21: the extractor reads counter times ten plus one when the lamp is on,
    so after the graded act the cache holds 20 at e1, and once the lamp is
    on a fresh run can only read 21; a status line that says 20 came from
    the cache, a `--read` line that says 21 came from a spawn. What this can
    no longer catch: a status that spawned the extractor and discarded the
    reading, which the marker file counted and the cache cannot see; the
    code path (`channel_lines` without `fresh`) never calls an extractor."""
    run = tmp_path / "readings"
    _prepare(run, budget={"actions": 20})
    (run / "tens.py").write_text(EXTRACTOR)
    cache = run / ".assay" / "channel_readings.json"

    def graded_readings() -> dict:
        return json.loads(cache.read_text()) if cache.exists() else {}

    try:
        assert _start(run).returncode == 0
        status = run_cli(run, "status")
        assert "CHANNELS | registered: goal · level · budget_remaining" in status.stdout
        assert "CHANNELS | host: goal=false · level=0 · budget_remaining=20" in status.stdout
        assert run_cli(run, "channel", "declare", "counter", "--path", "counter").returncode == 0
        assert run_cli(run, "channel", "declare", "tens", "--file", "tens.py").returncode == 0
        status = run_cli(run, "status")
        assert "counter=0 (path)" in status.stdout
        assert "tens=not yet graded (extractor)" in status.stdout
        assert graded_readings() == {}  # no extractor has run
        # A graded claim runs the extractor in the daemon and caches the reading.
        acted = run_cli(run, "act", "INC", "amount=2", "--predict", "ch tens = 20")
        assert acted.returncode == 0 and "OUTCOME | PREDICTED" in acted.stdout, acted.stdout
        assert graded_readings() == {"tens": {"event": 1, "value": 20}}
        # Change the world without a tens claim: a fresh run would now read
        # 21 while the cache still says 20 at e1, so the two are told apart.
        moved = run_cli(run, "act", "SET_LAMP", "state=on", "--predict", "change")
        assert moved.returncode == 0 and "OUTCOME | PREDICTED" in moved.stdout, moved.stdout
        assert graded_readings() == {"tens": {"event": 1, "value": 20}}  # no extractor ran
        status = run_cli(run, "status")
        assert "counter=2 (path)" in status.stdout
        assert "tens=20 @e1 (extractor, last graded)" in status.stdout  # cached; a spawn would show 21
        listed = run_cli(run, "channel", "list")
        assert "tens=20 @e1" in listed.stdout
        assert graded_readings() == {"tens": {"event": 1, "value": 20}}  # status and list spawned nothing
        fresh = run_cli(run, "channel", "list", "--read")
        assert fresh.returncode == 0, fresh.stderr
        assert "tens=21 (extractor)" in fresh.stdout  # computed fresh, one spawn
        assert graded_readings() == {"tens": {"event": 1, "value": 20}}  # the CLI never writes the cache
    finally:
        stop_run(run)


def test_receipts_show_path_channels_that_changed(tmp_path):
    run = tmp_path / "receipt"
    _prepare(run, budget={"actions": 20})
    try:
        assert _start(run).returncode == 0
        assert run_cli(run, "channel", "declare", "counter", "--path", "counter").returncode == 0
        assert run_cli(run, "channel", "declare", "lamp", "--path", "lamp").returncode == 0
        acted = run_cli(run, "act", "INC", "amount=1", "--predict", "change")
        assert acted.returncode == 0
        assert "CHANNELS | counter: 0 -> 1" in acted.stdout
        assert "CHANNELS | lamp:" not in acted.stdout
        quiet = run_cli(run, "act", "NOOP", "--predict", "noop")
        assert quiet.returncode == 0 and "CHANNELS |" not in quiet.stdout
        batched = run_cli(
            run, "commit", "--step", "INC amount=1 :: change", "--step", "SET_LAMP state=on :: change",
        )
        assert batched.returncode == 0, batched.stderr
        assert "CHANNELS | counter: 1 -> 2" in batched.stdout
        assert 'CHANNELS | lamp: "off" -> "on"' in batched.stdout
        receipt = json.loads(sorted((run / ".assay" / "receipts").glob("*.json"))[-1].read_text())
        assert receipt["channels"] == ['CHANNELS | counter: 1 -> 2', 'CHANNELS | lamp: "off" -> "on"']
    finally:
        stop_run(run)
