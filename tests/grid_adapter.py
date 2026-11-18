"""A small deterministic frame world for the tests.

An 8x8 grid of color 0 with a 2x2 block of color 3 at the left edge. ACTION1
moves the block one column right, ACTION2 does nothing, ACTION6 x,y paints
one cell color 5. Two levels: the first ends when the block reaches the
right edge, which sends it back to the left, and the second time is the win.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

SIZE = 8


class GridWorld:
    def __init__(self, root: Path, config: dict[str, Any]) -> None:
        self.column, self.level, self.painted = 0, 0, set[tuple[int, int]]()
        self.state = "NOT_FINISHED"

    @property
    def observation(self) -> dict[str, Any]:
        grid = [[0] * SIZE for _ in range(SIZE)]
        for row, col in self.painted:
            grid[row][col] = 5
        for row in (3, 4):
            for col in (self.column, self.column + 1):
                grid[row][col] = 3
        return {
            "frames": [grid],
            "state": self.state,
            "levels_completed": self.level,
            "win_levels": 2,
            "available_actions": ["ACTION1", "ACTION2", "ACTION6"],
        }

    def step(self, action: str, params: dict[str, Any] | None) -> dict[str, Any]:
        if action == "RESET":
            self.column, self.painted = 0, set()
        elif action == "ACTION1":
            self.column += 1
        elif action == "ACTION6" and params:
            self.painted.add((params["y"], params["x"]))
        if self.column == SIZE - 2:
            self.level += 1
            self.column = 0
            self.state = "WIN" if self.level == 2 else "NOT_FINISHED"
        return self.observation


def factory(root: Path, config: dict[str, Any]) -> GridWorld:
    return GridWorld(root, config)


REGISTRY = {
    "actions": [
        {"name": "ACTION1", "params": {}, "description": "direction 1"},
        {"name": "ACTION2", "params": {}},
        {
            "name": "ACTION6",
            "params": {
                "x": {"type": "integer", "minimum": 0, "maximum": 7},
                "y": {"type": "integer", "minimum": 0, "maximum": 7},
            },
        },
    ],
    "budget": {"actions": 40},
}
