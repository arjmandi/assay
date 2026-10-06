"""The `gate` registry key (exp/2026-10, control-arm switch).

Default (absent or "required"): a bare `assay act` without --predict is refused
before any spend, exactly as before. Under "optional" the daemon accepts it,
executes it, and journals it UNGATED (predict null, predict_ok null, grade [],
marker gate_optional true). The audit counts those events separately as
ungated_permitted while keeping the run INVALID FOR SCORING, and the
independent assay-verify checker agrees. Everything runs through the real CLI,
broker and fake adapter."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import FAKE_ADAPTER, run_cli, stop_run

ACTIONS = [
    {"name": "INC", "params": {"amount": {"type": "int", "min": 1, "max": 2}}},
    {"name": "NOOP", "params": {}},
]
ASSAY_VERIFY = Path("/Users/mohsenarjmandi/workspace/assay-verify/assay_verify.py")


def _registry(run_dir: Path, **extra) -> Path:
    spec = {"actions": [dict(item) for item in ACTIONS], "budget": {"actions": 20}, **extra}
    target = run_dir / "reg.json"
    target.write_text(json.dumps(spec))
    return target


def _start(run_dir: Path, registry: Path):
    return run_cli(
        run_dir, "start", "fake1", "--adapter", f"{FAKE_ADAPTER}:factory",
        "--registry", str(registry),
    )


def _events(run_dir: Path) -> list[dict]:
    lines = (run_dir / ".assay" / "events.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def test_unknown_gate_value_is_refused_like_any_bad_key():
    from assay.core import AssayError
    from assay.registry import validate_registry

    with pytest.raises(AssayError, match="gate must be one of"):
        validate_registry({"actions": ACTIONS, "gate": "maybe"})
    # The key is only canonicalized when present: absent stays absent, so the
    # registry_hash of every existing pinned registry is unchanged.
    assert "gate" not in validate_registry({"actions": ACTIONS})
    assert validate_registry({"actions": ACTIONS, "gate": "required"})["gate"] == "required"
    assert validate_registry({"actions": ACTIONS, "gate": "optional"})["gate"] == "optional"


@pytest.mark.parametrize("gate", [None, "required"])
def test_required_gate_still_refuses_a_bare_act(tmp_path, gate):
    run = tmp_path / "req"
    run.mkdir()
    registry = _registry(run, **({"gate": gate} if gate else {}))
    try:
        started = _start(run, registry)
        assert started.returncode == 0, started.stderr
        assert "GATE | optional" not in started.stdout
        bare = run_cli(run, "act", "NOOP")
        assert bare.returncode != 0
        assert "empty prediction predicts nothing" in bare.stderr
        assert len(_events(run)) == 1  # START only: nothing was spent
        bare_commit = run_cli(run, "commit", "--step", "NOOP")
        assert bare_commit.returncode != 0
        assert "each step needs its own prediction" in bare_commit.stderr
        assert len(_events(run)) == 1
        audited = run_cli(run, "audit")
        assert "AUDIT | CLEAN" in audited.stdout
    finally:
        stop_run(run)


def test_optional_gate_accepts_journals_and_audits_as_ungated(tmp_path):
    run = tmp_path / "opt"
    run.mkdir()
    registry = _registry(run, gate="optional")
    try:
        started = _start(run, registry)
        assert started.returncode == 0, started.stderr
        assert "USE | gate: optional" in started.stdout
        # e1: bare act, accepted and executed.
        bare = run_cli(run, "act", "INC", "amount=1")
        assert bare.returncode == 0, bare.stderr
        assert "OUTCOME | UNGATED" in bare.stdout
        # e2: a predicted act still grades as before.
        predicted = run_cli(run, "act", "NOOP", "--predict", "noop")
        assert predicted.returncode == 0, predicted.stderr
        assert "OUTCOME | PREDICTED" in predicted.stdout
        # e3 bare step + e4 predicted step inside one batch.
        batch = run_cli(run, "commit", "--step", "NOOP", "--step", "NOOP :: noop")
        assert batch.returncode == 0, batch.stderr
        assert "e0003 NOOP ·" in batch.stdout
        assert "e0004 NOOP ✓" in batch.stdout

        events = _events(run)
        assert [event["action"] for event in events[1:]] == ["INC", "NOOP", "NOOP", "NOOP"]
        for event in (events[1], events[3]):
            assert event["counts_action"] is True
            assert event["predict"] is None
            assert event["predict_ok"] is None
            assert event["grade"] == []
            assert event["gate_optional"] is True
        for event in (events[2], events[4]):
            assert event["predict"] == "noop"
            assert event["predict_ok"] is True
            assert "gate_optional" not in event
        assert int(events[1]["observation"]["counter"]) == 1  # the bare act ran

        status = run_cli(run, "status")
        assert status.returncode == 0, status.stderr
        assert "GATE | optional" in status.stdout

        audited = run_cli(run, "audit")
        assert audited.returncode == 0, audited.stderr
        assert "INVALID FOR SCORING" in audited.stdout
        assert "UNGATED events [1, 3]" in audited.stdout
        assert "2 of them permitted by `gate: optional`" in audited.stdout
        report = json.loads((run / ".assay" / "audit.json").read_text())
        assert report["ungated"] == [1, 3]
        assert report["ungated_permitted"] == [1, 3]
        assert report["invalid_for_scoring"] is True
        assert report["chain"] == "intact"
    finally:
        stop_run(run)


def test_assay_verify_reports_an_optional_gate_journal_invalid(tmp_path):
    if not ASSAY_VERIFY.exists():
        pytest.skip(f"independent checker not present at {ASSAY_VERIFY}")
    run = tmp_path / "ver"
    run.mkdir()
    registry = _registry(run, gate="optional")
    try:
        started = _start(run, registry)
        assert started.returncode == 0, started.stderr
        assert run_cli(run, "act", "INC", "amount=1").returncode == 0
        assert run_cli(run, "act", "INC", "amount=1", "--predict", "change").returncode == 0
    finally:
        stop_run(run)
    checked = subprocess.run(
        [sys.executable, str(ASSAY_VERIFY), str(run), "--json"],
        capture_output=True, text=True, timeout=60,
    )
    assert checked.returncode == 1, checked.stdout + checked.stderr
    report = json.loads(checked.stdout)
    assert report["verdict"] == "INVALID FOR SCORING"
    assert report["ungated"] == [1]
    assert report["stored_chain"] == "intact"
