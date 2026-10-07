"""A frame world through the real CLI and daemon: rendering, the frame grader,
the grid refusal on registry runs, view and the offline namespace. This is the
behavior the frame-world extra must keep when it leaves the kernel."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import run_cli, stop_run

GRID_ADAPTER = Path(__file__).resolve().parent / "grid_adapter.py"
REGISTRY = {
    "actions": [
        {"name": "ACTION1", "params": {}},
        {"name": "ACTION2", "params": {}},
        {"name": "ACTION6", "params": {"x": {"type": "int", "min": 0, "max": 7},
                                       "y": {"type": "int", "min": 0, "max": 7}}},
    ],
    "budget": {"actions": 30},
    "batching": {"hand_cap": None},
}


def _prepare(run: Path) -> None:
    run.mkdir()
    (run / "reg.json").write_text(json.dumps(REGISTRY))


def _start(run: Path):
    return run_cli(
        run, "start", "grid1", "--adapter", f"{GRID_ADAPTER}:factory",
        "--registry", str(run / "reg.json"),
    )


def _events(run: Path) -> list[dict]:
    lines = (run / ".assay" / "events.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def test_frame_world_end_to_end(tmp_path):
    run = tmp_path / "grid"
    _prepare(run)
    try:
        started = _start(run)
        assert started.returncode == 0, started.stderr
        assert "IMAGE |" in started.stdout
        assert "ACTIONS | available: 1 · 2 · 6 · RESET (built-in)" in started.stdout
        event0 = _events(run)[0]
        assert event0["n_frames"] == 1 and len(event0["frames"][0]) == 8
        assert (run / ".assay" / "images" / "event-00000-settled.png").exists()
        assert (run / ".assay" / "dossier.json").exists()
        # The frame grader: noop holds on ACTION2, change holds on ACTION1.
        held = run_cli(run, "act", "ACTION2", "--predict", "noop")
        assert held.returncode == 0 and "OUTCOME | PREDICTED" in held.stdout
        assert "TRANSITION |" in held.stdout
        moved = run_cli(run, "act", "ACTION1", "--predict", "change")
        assert moved.returncode == 0 and "OUTCOME | PREDICTED" in moved.stdout
        assert "cells changed" in moved.stdout
        missed = run_cli(run, "act", "ACTION2", "--predict", "change")
        assert missed.returncode == 0 and "OUTCOME | SURPRISE" in missed.stdout
        # Grid claim forms are recognized and refused (owner decision O1);
        # nothing is spent.
        before = len(_events(run))
        refused = run_cli(run, "act", "ACTION6", "x=1", "y=1", "--predict", "cell 1,1=5")
        assert refused.returncode == 2, refused.stdout
        assert len(_events(run)) == before
        # A point action with typed parameters grades like any other.
        painted = run_cli(run, "act", "ACTION6", "x=1", "y=1", "--predict", "change")
        assert painted.returncode == 0 and "OUTCOME | PREDICTED" in painted.stdout
        assert _events(run)[-1]["data"] == {"x": 1, "y": 1}
        # view and the offline namespace see grids.
        viewed = run_cli(run, "view", "--grid")
        assert viewed.returncode == 0, viewed.stderr
        assert "BOARD | color indices 0-f" in viewed.stdout
        assert "SCENE | 8x8" in viewed.stdout
        shape = run_cli(run, "python", "grid.shape")
        assert shape.returncode == 0 and shape.stdout.strip() == "(8, 8)"
        components = run_cli(run, "python", "len(connected_components(grid))")
        assert components.returncode == 0 and components.stdout.strip().isdigit()
        status = run_cli(run, "status")
        assert "IMAGE |" in status.stdout and "PROGRESS |" in status.stdout
        assert "STATUS | grid1 | event 4 | progress 1/1 |" in status.stdout
        # Finish the level: five more moves reach the right edge.
        for _ in range(4):
            assert run_cli(run, "act", "ACTION1", "--predict", "change").returncode == 0
        won = run_cli(run, "act", "ACTION1", "--predict", "win; level+1")
        assert won.returncode == 0 and "GAME_COMPLETE" in won.stdout
        audited = run_cli(run, "audit")
        assert "AUDIT | CLEAN" in audited.stdout and "chain intact" in audited.stdout
        assert "anchors intact (1)" in audited.stdout
        # Resume replays the frame journal through the adapter.
        resumed = _start(run)
        assert resumed.returncode == 0 and "completed run" in resumed.stdout
    finally:
        stop_run(run)
