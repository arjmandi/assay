"""The coverage-audit module (bench/arcagi/modules/coverage_audit.py) loaded as
an EXTERNAL module through the registry "modules" list in advise mode, with the
six built-ins untouched. One scenario drives the real CLI + broker + fake
adapter; one unit test pins the settled-frame comparison."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import FAKE_ADAPTER, run_cli, stop_run

REPO = Path(__file__).resolve().parents[1]
MODULE = REPO / "bench" / "arcagi" / "modules" / "coverage_audit.py"

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


def test_changed_compares_settled_frames_only():
    """Review finding 4: two events whose settled frames are equal but whose
    animation lists differ in length are NOT a change (an inert-click pulse, a
    per-action HUD animation). Only frames[-1] is compared."""
    from assay.modules import import_path

    settled = [[0] * 4 for _ in range(4)]
    pulse = [[1] * 4 for _ in range(4)]
    base = {"counts_action": True, "levels_completed": 0, "level_before": 0,
            "state": "NOT_FINISHED", "grade": []}
    events = [
        {**base, "id": 0, "action": "START", "counts_action": False, "frames": [settled]},
        {**base, "id": 1, "action": "ACTION6", "data": {"x": 1, "y": 1},
         "frames": [pulse, pulse, pulse, pulse, settled]},
        {**base, "id": 2, "action": "ACTION6", "data": {"x": 1, "y": 1},
         "frames": [pulse, settled, pulse]},
    ]
    with import_path(MODULE, "assay_module") as module:
        assert module._changed(events, 1) is False  # five-frame pulse, same settled frame
        assert module._changed(events, 2) is True   # settled frame differs
