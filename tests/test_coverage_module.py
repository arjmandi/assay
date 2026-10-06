"""The coverage-audit module (bench/arcagi/modules/coverage_audit.py) loaded as
an EXTERNAL module through the registry "modules" list in advise mode, with the
six built-ins untouched. One scenario drives the real CLI + broker + fake
adapter; one replays the module's trigger/telemetry over a real archived ARC
journal (read-only source, copied to a scratch directory) when it is present."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from conftest import FAKE_ADAPTER, run_cli, stop_run

REPO = Path(__file__).resolve().parents[1]
MODULE = REPO / "bench" / "arcagi" / "modules" / "coverage_audit.py"
ARCHIVED_RUN = Path("/Users/mohsenarjmandi/workspace/assay-runs/sweep1500/s5i5")

ACTIONS = [
    {"name": "INC", "params": {"amount": {"type": "int", "min": 1, "max": 2}}},
    {"name": "SET_LAMP", "params": {"state": {"type": "str", "enum": ["on", "off"]}}},
    {"name": "NOOP", "params": {}},
]


def test_coverage_module_loads_via_registry_and_advises(tmp_path):
    run = tmp_path / "cov"
    run.mkdir()
    registry = run / "reg.json"
    registry.write_text(json.dumps({
        "actions": ACTIONS,
        "budget": {"actions": 30},
        "modules": [str(MODULE)],
        "module_modes": {"coverage_audit": "advise"},
    }))
    try:
        started = run_cli(
            run, "start", "fake1", "--adapter", f"{FAKE_ADAPTER}:factory",
            "--registry", str(registry),
        )
        assert started.returncode == 0, started.stderr
        # The kernel pinned the file at start (pack-tier trust).
        pinned = run / ".assay" / "modules" / "coverage_audit.py"
        assert pinned.exists()
        assert pinned.read_bytes() == MODULE.read_bytes()
        # Three paid actions, then the status meter line appears.
        for _ in range(3):
            acted = run_cli(run, "act", "NOOP", "--predict", "noop")
            assert acted.returncode == 0, acted.stderr
        status = run_cli(run, "status")
        assert status.returncode == 0, status.stderr
        meter = [line for line in status.stdout.splitlines()
                 if line.startswith("MODULE coverage_audit | coverage level 1")]
        assert meter, status.stdout
        assert "untried [BOMB, INC, SET_LAMP]" in meter[0]
        # Re-issuing the exact move that just graded FALSE fires the halt as an
        # advisory line on the receipt (advise mode never refuses).
        missed = run_cli(run, "act", "NOOP", "--predict", "change")
        assert "OUTCOME | SURPRISE" in missed.stdout, missed.stdout
        reissued = run_cli(run, "act", "NOOP", "--predict", "change")
        assert reissued.returncode == 0, reissued.stderr
        assert "MODULE coverage_audit | re-issuing NOOP unmodified" in reissued.stdout
        # Built-ins still run alongside and the journal stays CLEAN.
        assert "MODULE miss_streak" in reissued.stdout or "MODULE null_forensics" in reissued.stdout
        audited = run_cli(run, "audit")
        assert "AUDIT | CLEAN" in audited.stdout
    finally:
        stop_run(run)


def test_coverage_module_trigger_and_telemetry_on_archived_journal(tmp_path):
    source = ARCHIVED_RUN / ".assay" / "events.jsonl"
    if not source.exists():
        pytest.skip(f"archived journal not present at {source}")
    from assay.core import RunPaths, load_events
    from assay.modules import JournalView, import_path

    scratch = tmp_path / "s5i5"
    (scratch / ".assay").mkdir(parents=True)
    shutil.copy2(source, scratch / ".assay" / "events.jsonl")
    paths = RunPaths(scratch)
    events = load_events(paths)
    assert len(events) > 273
    with import_path(MODULE, "assay_module") as module:
        candidate = module.MODULE
        for attr in ("NAME", "CONSTITUTION", "MODE", "trigger", "demand", "telemetry"):
            assert hasattr(candidate, attr)
        assert candidate.MODE == "advise"
        prefix = events[:274]  # the E2 cut point for s5i5 (e273 inclusive)
        view = JournalView(paths=paths, events=prefix, registry=None)
        line = candidate.trigger(view, None)
        assert line and line.startswith("coverage level 7:"), line
        assert "regions probed" in line
        telemetry = candidate.telemetry(view)
        assert telemetry["level"] == 6
        assert telemetry["regions_total"] == 64
        assert 0 < telemetry["regions_probed"] <= 64
        assert candidate.demand(view, None) is None
