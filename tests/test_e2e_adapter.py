"""End-to-end: the fake dict-observation adapter driven through the real CLI.

Each scenario runs the actual `assay_cli.py` as a subprocess, which spawns the
actual broker subprocess, which loads the fake adapter — the full seam a crux
environment will use.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import time
from pathlib import Path

from conftest import FAKE_ADAPTER, run_cli, stop_run

REGISTRY = {
    "actions": [
        {"name": "INC", "params": {"amount": {"type": "int", "min": 1, "max": 2}}},
        {"name": "SET_LAMP", "params": {"state": {"type": "str", "enum": ["on", "off"]}}},
        {"name": "NOOP", "params": {}},
    ],
    "budget": {"actions": 10},
    "mode_note": "fake counter world",
}

INC_BY_AMOUNT = """\
def verify(before, after):
    b = before["data"]["counter"]
    a = after["data"]["counter"]
    return a == b + 1, f"counter {b} -> {a}"
"""

CRASHER = """\
def verify(before, after):
    raise RuntimeError("boom")
"""

ALWAYS_TRUE = """\
def verify(before, after):
    return True, "always fine"
"""

LAMP_FLIP = """\
def verify(before, after):
    b = before["data"]["lamp"]
    a = after["data"]["lamp"]
    return a != b, f"lamp {b} then {a}"
"""


def _prepare(run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "reg.json").write_text(json.dumps(REGISTRY))
    checks = run_dir / "checks"
    checks.mkdir(exist_ok=True)
    (checks / "inc_by_one.py").write_text(INC_BY_AMOUNT)
    (checks / "crash.py").write_text(CRASHER)
    (checks / "always.py").write_text(ALWAYS_TRUE)
    (checks / "lamp_flip.py").write_text(LAMP_FLIP)


def _start(run_dir: Path):
    return run_cli(
        run_dir,
        "start",
        "fake1",
        "--adapter",
        f"{FAKE_ADAPTER}:factory",
        "--registry",
        str(run_dir / "reg.json"),
    )


def _events(run_dir: Path) -> list[dict]:
    lines = (run_dir / ".assay" / "events.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def test_full_general_run(tmp_path):
    run = tmp_path / "run1"
    _prepare(run)
    try:
        # -- start -----------------------------------------------------------
        started = _start(run)
        assert started.returncode == 0, started.stderr
        assert "STARTED" in started.stdout
        assert "REGISTRY | 3 registered actions" in started.stdout
        assert "BUDGET | paid actions 0/10" in started.stdout
        assert "OBSERVATION |" in started.stdout
        assert '"counter": 0' in started.stdout
        assert "IMAGE |" not in started.stdout  # no PNG path for dict runs

        # -- act with a verifier claim ----------------------------------------
        acted = run_cli(
            run,
            "act",
            "INC",
            "amount=1",
            "--predict",
            "verify:checks/inc_by_one.py; change",
            "--because",
            "probe INC",
        )
        assert acted.returncode == 0, acted.stderr
        assert "OUTCOME | PREDICTED" in acted.stdout
        assert "✓ verify:checks/inc_by_one.py" in acted.stdout
        assert "KEY DELTA" in acted.stdout
        assert "~ counter: 0 → 1" in acted.stdout
        event = _events(run)[-1]
        assert event["predict_ok"] is True
        kinds = {item["kind"] for item in event["grade"]}
        assert kinds == {"verify", "change"}
        verify_record = next(
            item for item in event["grade"] if item["kind"] == "verify"
        )
        assert verify_record["verifier"] is True
        assert len(verify_record["verifier_hash"]) == 64
        assert verify_record["bucket"] == "world_model"
        assert verify_record["identity_verdict"] is False

        # -- free refusals: no spend ------------------------------------------
        spent_before = len(_events(run))
        for token, message in [
            (["INC", "amount=9"], "above max"),
            (["BOGUS"], "unknown action"),
            (["INC"], "missing parameter"),
            (["SET_LAMP", "state=blue"], "not one of"),
        ]:
            refused = run_cli(run, "act", *token, "--predict", "change")
            assert refused.returncode == 2
            assert message in refused.stderr
        assert len(_events(run)) == spent_before  # validation preceded spend

        # -- commit batch halts on the first miss ------------------------------
        committed = run_cli(
            run,
            "commit",
            "--step",
            "SET_LAMP state=on :: change",
            "--step",
            "SET_LAMP state=on :: change",  # lamp already on: no change -> miss
            "--step",
            "NOOP :: noop",  # must be discarded
        )
        assert committed.returncode == 0, committed.stderr
        assert "OUTCOME | SURPRISE" in committed.stdout
        assert "step 2 missed" in committed.stdout
        assert "1 remaining steps were discarded" in committed.stdout
        events = _events(run)
        assert len(events) == spent_before + 2  # two steps paid, third discarded
        assert events[-1]["predict_ok"] is False

        # -- crash verifier: INVALID_CLAIM, not a miss --------------------------
        invalid = run_cli(
            run, "act", "NOOP", "--predict", "verify:checks/crash.py"
        )
        assert invalid.returncode == 0, invalid.stderr
        assert "OUTCOME | INVALID_CLAIM" in invalid.stdout
        event = _events(run)[-1]
        assert event["predict_ok"] is None
        assert event["grade"][0]["invalid"] is True

        # -- status renders and carries the meters ------------------------------
        status = run_cli(run, "status")
        assert status.returncode == 0, status.stderr
        assert "CLAIMS | world-model misses 1/4 (25.0%)" in status.stdout
        assert "invalid 1" in status.stdout
        assert "BUDGET | paid actions 4/10 | remaining 6" in status.stdout
        assert "OBSERVATION |" in status.stdout
        assert "RECENT |" in status.stdout

        # -- view and python never crash on dict observations -------------------
        viewed = run_cli(run, "view")
        assert viewed.returncode == 0, viewed.stderr
        assert "OBSERVATION |" in viewed.stdout
        computed = run_cli(run, "python", "observations[-1]['counter']")
        assert computed.returncode == 0, computed.stderr
        assert computed.stdout.strip() == "1"
        computed = run_cli(run, "python", "actions[0]")
        assert "INC amount=1" in computed.stdout

        # -- budget exhaustion ----------------------------------------------------
        for _ in range(6):  # spend 5..10
            spent = run_cli(run, "act", "NOOP", "--predict", "noop")
            assert spent.returncode == 0, spent.stderr
        refused = run_cli(run, "act", "NOOP", "--predict", "noop")
        assert refused.returncode == 2
        assert "BUDGET_EXHAUSTED" in refused.stderr
        assert "remaining=0" in refused.stderr
        refused = run_cli(run, "reset", "--because", "out of ideas")
        assert refused.returncode == 2 and "BUDGET_EXHAUSTED" in refused.stderr
        refused = run_cli(run, "commit", "--step", "NOOP :: noop")
        assert refused.returncode == 2 and "BUDGET_EXHAUSTED" in refused.stderr
        status = run_cli(run, "status")
        assert "BUDGET | paid actions 10/10 | remaining 0" in status.stdout
    finally:
        stop_run(run)


def test_vacuous_resume_and_win(tmp_path):
    run = tmp_path / "run2"
    _prepare(run)
    try:
        started = _start(run)
        assert started.returncode == 0, started.stderr

        # -- five gradings: the constant verifier is VACUOUS, the one that reads
        #    the transition and holds is an advisory -----------------------------
        for index in range(5):
            acted = run_cli(
                run,
                "act",
                "SET_LAMP",
                f"state={'on' if index % 2 == 0 else 'off'}",
                "--predict",
                "verify:checks/always.py; verify:checks/lamp_flip.py",
            )
            assert acted.returncode == 0, acted.stderr
            assert "OUTCOME | PREDICTED" in acted.stdout
        constant = hashlib.sha256(ALWAYS_TRUE.encode()).hexdigest()
        flip = hashlib.sha256(LAMP_FLIP.encode()).hexdigest()
        status = run_cli(run, "status")
        assert (
            f"VACUOUS | verifier {constant[:12]} graded 5, identity verdict matched "
            "the real verdict every time; it does not use the transition, its "
            "passes are excluded from the meter"
        ) in status.stdout
        assert (
            f"VERIFIER | {flip[:12]} graded 5, never failed (advisory, not a flag)"
        ) in status.stdout
        assert f"VACUOUS | verifier {flip[:12]}" not in status.stdout
        graded = {item["verifier_hash"]: item for item in _events(run)[-1]["grade"]}
        assert graded[constant].get("excluded_from_meter") is True
        assert "excluded_from_meter" not in graded[flip]
        stats = json.loads((run / ".assay" / "verifiers" / "stats.json").read_text())
        assert stats["rule"] == "identity"
        assert stats["verifiers"][constant] == {
            "graded": 5,
            "passed": 5,
            "failed": 0,
            "invalid": 0,
            "identity_same_verdict": 5,
        }
        assert stats["verifiers"][flip] == {
            "graded": 5,
            "passed": 5,
            "failed": 0,
            "invalid": 0,
            "identity_same_verdict": 0,
        }

        # -- kill the broker; start replays the journal through the adapter ------
        broker = json.loads((run / ".assay" / "broker.json").read_text())
        os.kill(broker["pid"], signal.SIGTERM)
        time.sleep(0.3)
        resumed = _start(run)
        assert resumed.returncode == 0, resumed.stderr
        assert "RECOVERED" in resumed.stdout
        assert "replayed 5 paid actions" in resumed.stdout

        # -- win ------------------------------------------------------------------
        acted = run_cli(run, "act", "INC", "amount=2", "--predict", "change")
        assert acted.returncode == 0 and "OUTCOME | PREDICTED" in acted.stdout
        won = run_cli(run, "act", "INC", "amount=1", "--predict", "win")
        assert won.returncode == 0, won.stderr
        assert "OUTCOME | GAME_COMPLETE" in won.stdout
        assert "WIN" in won.stdout

        # -- status still renders after the win, broker gone ----------------------
        status = run_cli(run, "status")
        assert status.returncode == 0, status.stderr
        assert "WIN" in status.stdout
        assert "gamble misses 0/1" in status.stdout
        finished = _start(run)
        assert finished.returncode == 0, finished.stderr
        assert "completed run" in finished.stdout
    finally:
        stop_run(run)
