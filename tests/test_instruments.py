"""The instruments of the sonnet-haiku-5-5 experiment, end to end on the
grid test world and by unit: the settled diff on a receipt's first line, the
frame forms admitted by the registry (`frame_forms: admit`) with `region`
grading a local change, and the state cap as a registry field with a retire
operation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import FAKE_ADAPTER, run_cli, stop_run

GRID_ADAPTER = Path(__file__).resolve().parent / "grid_adapter.py"
GRID_REGISTRY = {
    "actions": [
        {"name": "ACTION1", "params": {}},
        {"name": "ACTION2", "params": {}},
        {"name": "ACTION6", "params": {"x": {"type": "int", "min": 0, "max": 7},
                                       "y": {"type": "int", "min": 0, "max": 7}}},
    ],
    "budget": {"actions": 30},
}


def _grid_run(tmp_path: Path, **registry) -> Path:
    run = tmp_path / "grid"
    run.mkdir()
    (run / "reg.json").write_text(json.dumps({**GRID_REGISTRY, **registry}))
    started = run_cli(run, "start", "grid1", "--adapter", f"{GRID_ADAPTER}:factory",
                      "--registry", str(run / "reg.json"))
    assert started.returncode == 0, started.stderr
    return run


def test_the_receipt_first_line_carries_the_settled_diff(tmp_path):
    pytest.importorskip("PIL")
    run = _grid_run(tmp_path)
    try:
        painted = run_cli(run, "act", "ACTION6", "x=6", "y=1", "--predict", "change")
        first = painted.stdout.splitlines()[0]
        assert first == "RESULT | PREDICTED | result matched the prediction | settled diff 1 cell (x 6..6, y 1..1)"
        idle = run_cli(run, "act", "ACTION2", "--predict", "noop")
        assert idle.stdout.splitlines()[0].endswith("| settled diff 0 cells")
        moved = run_cli(run, "act", "ACTION1", "--predict", "change")
        # The 2x2 block one column right: its old column clears, its new one fills.
        assert moved.stdout.splitlines()[0].endswith("| settled diff 4 cells (x 0..2, y 3..4)")
    finally:
        stop_run(run)


def test_frame_forms_are_refused_unless_the_registry_admits_them(tmp_path):
    pytest.importorskip("PIL")
    run = _grid_run(tmp_path)
    try:
        refused = run_cli(run, "act", "ACTION6", "x=6", "y=1", "--predict", "cell 6,1=5")
        assert refused.returncode == 2
        assert "frame-world forms are admitted only when the registry sets frame_forms: admit" in refused.stderr
    finally:
        stop_run(run)


def test_admitted_frame_forms_grade_and_region_is_local(tmp_path):
    pytest.importorskip("PIL")
    run = _grid_run(tmp_path, frame_forms="admit")
    try:
        cell = run_cli(run, "act", "ACTION6", "x=6", "y=1", "--predict", "cell 6,1=5")
        assert cell.returncode == 0, cell.stderr
        assert "RESULT | PREDICTED" in cell.stdout and "✓ cell 6,1=5" in cell.stdout
        # Something changes inside the box: held, whatever changes outside.
        inside = run_cli(run, "act", "ACTION6", "x=1", "y=6", "--predict", "region 0:3,5:8")
        assert "RESULT | PREDICTED" in inside.stdout, inside.stdout
        missed = run_cli(run, "act", "ACTION6", "x=2", "y=7", "--predict", "region 5:8,0:3")
        assert "RESULT | SURPRISE" in missed.stdout
        assert "no change inside the box (1 outside)" in missed.stdout
        events = [json.loads(line) for line in (run / ".assay" / "events.jsonl").read_text().splitlines()]
        kinds = [item["kind"] for event in events for item in event.get("grade") or []]
        assert kinds == ["cell", "region", "region"]
        assert all(item["bucket"] == "world_model" for event in events for item in event.get("grade") or [])
        assert "AUDIT | CLEAN" in run_cli(run, "audit").stdout
    finally:
        stop_run(run)


def test_region_grades_inside_the_box_only():
    import numpy as np

    from assay_grid.outcomes import grade_outcomes
    from assay.records import Outcome
    from conftest import event_of

    before = np.zeros((4, 4), dtype=np.int16)
    after = before.copy()
    after[0, 3] = 2  # a ticker outside the box
    after[2, 1] = 5  # the change inside it
    event = event_of(id=1, frames=[["0002", "0000", "0500", "0000"]])
    box = Outcome(kind="region", text="region 0:2,2:4", extra={"x0": 0, "x1": 2, "y0": 2, "y1": 4})
    far = Outcome(kind="region", text="region 2:4,2:4", extra={"x0": 2, "x1": 4, "y0": 2, "y1": 4})
    held, missed = grade_outcomes([box, far], before, event)
    assert held.ok and held.actual == "1 changes inside the box (1 outside, not graded)"
    assert not missed.ok and missed.actual == "no change inside the box (2 outside)"


def test_registry_validates_the_new_keys():
    from assay.core import AssayError
    from assay.registry import frame_forms_admitted, state_cap, validate_registry

    base = {"actions": [{"name": "INC", "params": {}}]}
    assert validate_registry({**base, "frame_forms": "admit", "state_cap": 40}) == {
        **validate_registry(base), "frame_forms": "admit", "state_cap": 40,
    }
    for bad in ({"frame_forms": "yes"}, {"state_cap": 0}, {"state_cap": True}):
        with pytest.raises(AssayError):
            validate_registry({**base, **bad})
    assert frame_forms_admitted({"frame_forms": "admit"}) and not frame_forms_admitted(base)
    assert state_cap(base) == 16 and state_cap({"state_cap": 3}) == 3


def test_the_state_cap_is_the_registry_s_and_retire_frees_a_place(tmp_path):
    run = tmp_path / "states"
    run.mkdir()
    (run / "reg.json").write_text(json.dumps({
        "actions": [{"name": "INC", "params": {"amount": {"type": "int", "min": 1, "max": 2}}}],
        "budget": {"actions": 10},
        "state_cap": 2,
    }))
    try:
        started = run_cli(run, "start", "fake1", "--adapter", f"{FAKE_ADAPTER}:factory",
                          "--registry", str(run / "reg.json"))
        assert started.returncode == 0, started.stderr
        assert run_cli(run, "state", "declare", "one", "--path", "counter").returncode == 0
        assert run_cli(run, "state", "declare", "two", "--path", "counter").returncode == 0
        full = run_cli(run, "state", "declare", "three", "--path", "counter")
        assert full.returncode == 2 and "at most 2 declared states at a time" in full.stderr
        assert "assay state retire NAME" in full.stderr
        retired = run_cli(run, "state", "retire", "one")
        assert retired.returncode == 0 and "STATE | one retired" in retired.stdout
        assert run_cli(run, "state", "declare", "three", "--path", "counter").returncode == 0
        # A dict world's receipt names the changed keys on its first line.
        counted = run_cli(run, "act", "INC", "amount=1", "--predict", "ch two delta = 1")
        assert counted.stdout.splitlines()[0].startswith("RESULT | PREDICTED | result matched the prediction | settled diff ")
        # A retired state no longer parses; declaring it again needs a place.
        named = run_cli(run, "act", "INC", "amount=1", "--predict", "ch one delta = 1")
        assert named.returncode == 2 and "unregistered state 'one'" in named.stderr
        assert run_cli(run, "state", "declare", "one", "--path", "counter").returncode == 2
        assert run_cli(run, "state", "retire", "missing").returncode == 2
        kinds = [json.loads(line)["kind"] for line in (run / ".assay" / "activity.jsonl").read_text().splitlines()]
        assert "channel_retired" in kinds
    finally:
        stop_run(run)
