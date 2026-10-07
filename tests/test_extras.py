"""The frame-world extra is selected by observation shape and never touches a
dict run. The frame behavior itself is covered end to end by
test_grid_world.py; these tests pin the seam."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from conftest import FAKE_ADAPTER, SRC_DIR, run_cli, stop_run

GRID_ADAPTER = Path(__file__).resolve().parent / "grid_adapter.py"
ACTIONS = [
    {"name": "INC", "params": {"amount": {"type": "int", "min": 1, "max": 2}}},
    {"name": "NOOP", "params": {}},
]


def test_kernel_imports_neither_the_extra_nor_pillow():
    probe = (
        "import sys, assay.live, assay.inspect, assay.cli, assay.predictions, "
        "assay.analysis, assay.evidence; "
        "print(sorted(name for name in sys.modules if name.startswith(('PIL', 'assay_grid'))))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True,
        cwd=str(SRC_DIR), timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "[]"


def test_dict_run_never_loads_the_extra(tmp_path):
    run = tmp_path / "dict"
    run.mkdir()
    (run / "reg.json").write_text(json.dumps({"actions": ACTIONS, "budget": {"actions": 20}}))
    try:
        started = run_cli(
            run, "start", "fake1", "--adapter", f"{FAKE_ADAPTER}:factory",
            "--registry", str(run / "reg.json"),
        )
        assert started.returncode == 0, started.stderr
        assert run_cli(run, "act", "INC", "amount=1", "--predict", "change").returncode == 0
        assert run_cli(run, "status").returncode == 0
        assert run_cli(run, "view", "--history", "2").returncode == 0
        loaded = run_cli(
            run, "python",
            "print(sorted(m for m in __import__('sys').modules if m.startswith(('PIL', 'assay_grid'))))",
        )
        assert loaded.returncode == 0, loaded.stderr
        assert loaded.stdout.strip() == "[]"
        # Frame-only flags and commands answer honestly on a dict run.
        viewed = run_cli(run, "view", "--frames")
        assert viewed.returncode == 0 and "--frames/--crop do not apply" in viewed.stdout
        rules = run_cli(run, "rules", "help")
        assert rules.returncode == 2 and "applies to frame worlds" in rules.stderr
        # A grid claim form is named as such, and refused before any spend.
        before = len((run / ".assay" / "events.jsonl").read_text().splitlines())
        refused = run_cli(run, "act", "NOOP", "--predict", "cell 1,1=5")
        assert refused.returncode == 2
        assert "frames-world form" in refused.stderr and "refused on registry runs" in refused.stderr
        assert len((run / ".assay" / "events.jsonl").read_text().splitlines()) == before
    finally:
        stop_run(run)


def test_frame_run_loads_the_extra_and_keeps_the_legacy_forwarders(tmp_path):
    run = tmp_path / "frames"
    run.mkdir()
    (run / "reg.json").write_text(json.dumps({
        "actions": [{"name": "ACTION1", "params": {}}, {"name": "ACTION2", "params": {}}],
        "budget": {"actions": 20},
    }))
    try:
        started = run_cli(
            run, "start", "grid1", "--adapter", f"{GRID_ADAPTER}:factory",
            "--registry", str(run / "reg.json"),
        )
        assert started.returncode == 0, started.stderr
        loaded = run_cli(
            run, "python",
            "print('assay_grid' in __import__('sys').modules, 'PIL' in __import__('sys').modules)",
        )
        assert loaded.stdout.strip() == "True True"
        # The historical import path for rules files and scripts still works.
        forwarded = run_cli(
            run, "python",
            "from assay import connected_components as cc; print(len(cc(grid)))",
        )
        assert forwarded.returncode == 0, forwarded.stderr
        assert forwarded.stdout.strip().isdigit()
        assert run_cli(run, "view", "--crop", "0:2,0:2").returncode == 0
    finally:
        stop_run(run)


def test_numbered_vocabulary_lives_in_the_extra():
    from assay.core import AssayError, parse_action
    from assay_grid import legacy

    assert parse_action("ACTION6:3,14") == ("ACTION6", {"x": 3, "y": 14})
    assert legacy.action_token("action6", ["1", "2"]) == "ACTION6:1,2"
    assert legacy.advertised_names({"available_actions": [1, 6]}) == ["ACTION1", "ACTION6"]
    import pytest

    with pytest.raises(AssayError, match="0..63"):
        legacy.parse_action("ACTION6:99,2")
