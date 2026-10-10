"""A frame world through the real CLI and daemon: rendering, the frame grader,
the grid refusal on registry runs, view and the offline namespace. This is the
behavior the frame-world extra must keep when it leaves the kernel. Then the
transition story's stated order, and the view as a function of the records
across processes (#58)."""

from __future__ import annotations

import gzip
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from conftest import ASSAY_CLI, event_of, run_cli, stop_run

GRID_ADAPTER = Path(__file__).resolve().parent / "grid_adapter.py"
# The published ar25 journal, the run whose view printed three texts in three
# processes (#58); event 264, its last, is the one the issue rendered.
JOURNAL = Path(__file__).resolve().parents[1] / "evidence" / "arcagi" / "journal-ar25.jsonl.gz"
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
        # Grid outcome forms are recognized and refused (owner decision O1);
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


# A settled-frame pair whose story ties on every key but position: three
# color-3 shapes of size 3 and two color-5 shapes of size 2 vanish, two
# color-6 shapes of size 3 appear, a color-7 block moves, and the color-4 bar
# that appears overlaps two vanished color-4 components (the domino and the
# single cell), so the resize could pair with either. These are the lines
# whose order and content depended on the process's string hashing (#58).
BEFORE = [
    "3330000003",
    "0000000003",
    "0044040003",
    "0000000000",
    "3000770000",
    "3300770000",
    "0000000000",
    "0005500050",
    "0000000050",
    "0000000000",
]
AFTER = [
    "0000000000",
    "0000000000",
    "0044440000",
    "0000000000",
    "0000007700",
    "0000007700",
    "0000000000",
    "0000000000",
    "0666006000",
    "0000006600",
]
# The story of that pair in its stated order: in every block the largest
# first, then the top-left first (rows before columns), then the color; the
# resize pairs the bar with the domino, the larger of the two vanished
# color-4 components overlapping it, and the single cell is vanished.
STORY = [
    "28 cells changed in rows 0..9, cols 0..9",
    "color 7 size 4 moved (x,y) (4,4)->(6,4) [dx=+2,dy=+0]",
    "color 4 resized 2->4 near (x,y) (3,2)",
    "color 3 size 3 vanished at (x,y) (1,0)",
    "color 3 size 3 vanished at (x,y) (9,1)",
    "color 3 size 3 vanished at (x,y) (0,5)",
    "color 5 size 2 vanished at (x,y) (3,7)",
    "color 5 size 2 vanished at (x,y) (8,7)",
    "color 4 size 1 vanished at (x,y) (5,2)",
    "color 6 size 3 appeared at (x,y) (2,8)",
    "color 6 size 3 appeared at (x,y) (6,9)",
]


def _rows(text: list[str]) -> list[list[int]]:
    return [[int(cell, 16) for cell in row] for row in text]


def _frame_run(root: Path, name: str, frames: list[list[str]]) -> Path:
    """A run directory `assay view` reads with no daemon: a config and a
    journal of one frame-world event per settled frame, given as the hex
    rows the journal stores."""
    run = root / name
    (run / ".assay").mkdir(parents=True)
    (run / ".assay" / "config.json").write_text(json.dumps({"game_id": name, "mode": "local"}))
    events = [
        event_of(
            id=index,
            action="RESET" if index == 0 else "ACTION1",
            data=None,
            counts_action=bool(index),
            level_before=None if index == 0 else 0,
            available_actions=[1, 2, 6],
            frames=[frame],
        ).to_json()
        for index, frame in enumerate(frames)
    ]
    (run / ".assay" / "events.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events)
    )
    return run


def _view_under_seeds(run: Path, event: int) -> list[str]:
    """`assay view --event N` on the run once per hash seed, each in a
    process of its own."""
    texts = []
    for seed in ("0", "1", "2"):
        completed = subprocess.run(
            [sys.executable, str(ASSAY_CLI), "--run-dir", str(run), "view", "--event", str(event)],
            capture_output=True,
            text=True,
            timeout=180,
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        assert completed.returncode == 0, completed.stderr
        texts.append(completed.stdout)
    return texts


def test_transition_story_is_in_its_stated_order():
    from assay_grid.perception import transition_story

    assert transition_story(_rows(BEFORE), _rows(AFTER))["lines"] == STORY


def test_view_text_is_a_function_of_the_records(tmp_path):
    """The same event renders byte for byte the same under three hash seeds
    in three processes (#58): on the crafted pair, and on the last event of
    the published ar25 journal."""
    crafted = _frame_run(tmp_path, "crafted", [BEFORE, AFTER])
    texts = _view_under_seeds(crafted, 1)
    assert texts[0] == texts[1] == texts[2]
    assert "\n".join(f"  {line}" for line in STORY) in texts[0]
    published = tmp_path / "ar25"
    (published / ".assay").mkdir(parents=True)
    (published / ".assay" / "config.json").write_text(
        json.dumps({"game_id": "ar25", "mode": "local"})
    )
    with gzip.open(JOURNAL, "rb") as source, (published / ".assay" / "events.jsonl").open(
        "wb"
    ) as sink:
        shutil.copyfileobj(source, sink)
    texts = _view_under_seeds(published, 264)
    assert texts[0] == texts[1] == texts[2]
    assert "TRANSITION | since previous settled board" in texts[0]
