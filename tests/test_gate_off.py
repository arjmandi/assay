"""`gate: off`, the second control-arm mode. The instrument is removed: no
prediction is accepted or graded, every paid action is journaled UNGATED with
the marker gate_off, status shows a neutral `GATE | off | n action(s)` line,
the audit keeps the run INVALID FOR SCORING and names the mode, and the
independent checker agrees. Not the default, never scorable."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import FAKE_ADAPTER, run_cli, stop_run

ACTIONS = [
    {"name": "INC", "params": {"amount": {"type": "int", "min": 1, "max": 2}}},
    {"name": "NOOP", "params": {}},
]


def _registry(run: Path, **extra) -> Path:
    spec = {"actions": [dict(item) for item in ACTIONS], "budget": {"actions": 20}, **extra}
    target = run / "reg.json"
    target.write_text(json.dumps(spec))
    return target


def _start(run: Path, registry: Path):
    return run_cli(
        run, "start", "fake1", "--adapter", f"{FAKE_ADAPTER}:factory",
        "--registry", str(registry),
    )


def _events(run: Path) -> list[dict]:
    lines = (run / ".assay" / "events.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _assay_verify() -> Path | None:
    configured = os.getenv("ASSAY_VERIFY")
    candidates = [Path(configured)] if configured else []
    candidates.append(Path(__file__).resolve().parents[2] / "assay-verify" / "assay_verify.py")
    return next((c for c in candidates if c.is_file()), None)


def test_off_is_a_valid_value_and_the_default_is_required():
    from assay.registry import gate_mode, gate_off, gate_optional, validate_registry

    assert gate_mode(validate_registry({"actions": ACTIONS})) == "required"
    assert gate_mode(None) == "required"
    spec = validate_registry({"actions": ACTIONS, "gate": "off"})
    assert spec["gate"] == "off" and gate_off(spec) and not gate_optional(spec)


def test_off_removes_the_instrument_and_audits_invalid(tmp_path):
    run = tmp_path / "off"
    run.mkdir()
    registry = _registry(run, gate="off")
    try:
        started = _start(run, registry)
        assert started.returncode == 0, started.stderr
        assert "USE | gate: off" in started.stdout
        assert "GATE | off | 0 action(s)" in started.stdout
        # A prediction is refused free: the instrument is removed, not voluntary.
        refused = run_cli(run, "act", "INC", "amount=1", "--predict", "change")
        assert refused.returncode == 2 and "gate is off" in refused.stderr
        assert len(_events(run)) == 1
        refused = run_cli(run, "commit", "--step", "NOOP :: noop")
        assert refused.returncode == 2 and "gate is off" in refused.stderr
        assert len(_events(run)) == 1
        # Bare acts and bare steps run and are journaled UNGATED with the marker.
        bare = run_cli(run, "act", "INC", "amount=1")
        assert bare.returncode == 0, bare.stderr
        assert "OUTCOME | UNGATED | no prediction (gate: off)" in bare.stdout
        batch = run_cli(run, "commit", "--step", "NOOP", "--step", "INC amount=1")
        assert batch.returncode == 0, batch.stderr
        assert "e0002 NOOP ·" in batch.stdout and "e0003 INC amount=1 ·" in batch.stdout
        events = _events(run)
        for event in events[1:]:
            assert event["counts_action"] is True
            assert event["predict"] is None and event["predict_ok"] is None and event["grade"] == []
            assert event["gate_off"] is True and "gate_optional" not in event
        assert events[-1]["observation"]["counter"] == 2
        status = run_cli(run, "status")
        assert status.returncode == 0, status.stderr
        # The status line is neutral: the mode and a count. The verdict is the audit's.
        assert "GATE | off | 3 action(s)" in status.stdout
        assert "CLAIMS |" not in status.stdout  # nothing was ever graded
        assert "INTEGRITY" not in status.stdout
        audited = run_cli(run, "audit")
        assert "INVALID FOR SCORING" in audited.stdout
        assert "UNGATED events [1, 2, 3]" in audited.stdout
        assert "3 of them permitted by `gate: off` (control arm)" in audited.stdout
        report = json.loads((run / ".assay" / "audit.json").read_text())
        assert report["ungated_permitted"] == [1, 2, 3]
        assert report["ungated_permitted_by"] == ["off"]
        assert report["chain"] == "intact"
    finally:
        stop_run(run)
    checker = _assay_verify()
    if checker is None:
        pytest.skip("independent checker not found (set ASSAY_VERIFY or check out assay-verify beside this repo)")
    checked = subprocess.run(
        [sys.executable, str(checker), str(run), "--json"],
        capture_output=True, text=True, timeout=60,
    )
    assert checked.returncode == 1, checked.stdout + checked.stderr
    verdict = json.loads(checked.stdout)
    assert verdict["verdict"] == "INVALID FOR SCORING" and verdict["ungated"] == [1, 2, 3]


def test_required_and_optional_are_unchanged(tmp_path):
    run = tmp_path / "opt"
    run.mkdir()
    registry = _registry(run, gate="optional")
    try:
        assert _start(run, registry).returncode == 0
        assert run_cli(run, "act", "INC", "amount=1").returncode == 0
        assert run_cli(run, "act", "NOOP", "--predict", "noop").returncode == 0
        events = _events(run)
        assert events[1]["gate_optional"] is True and "gate_off" not in events[1]
        assert events[2]["predict_ok"] is True
        audited = run_cli(run, "audit")
        assert "1 of them permitted by `gate: optional` (control arm)" in audited.stdout
    finally:
        stop_run(run)
