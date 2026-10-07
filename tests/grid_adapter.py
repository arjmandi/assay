"""A tiny deterministic frame world for end-to-end tests.

An 8x8 grid of color 0 with a 2x2 block of color 3 that starts at the left
edge. ACTION1 moves the block one column right, ACTION2 does nothing, ACTION6
x,y paints one cell color 5. The level is complete when the block reaches the
right edge (six moves), and that is the win. The observation is the frame
shape the ARC adapter produces (a mapping with `frame`, state, progress and
integer action ids), so every frame code path in the kernel is exercised
without any benchmark dependency.
"""

from __future__ import annotations

from typing import Any

SIZE = 8
BLOCK = 3
PAINT = 5


class GridSession:
    def __init__(self, root: Any, config: dict[str, Any]):
        self.column = 0
        self.painted: set[tuple[int, int]] = set()
        self.state = "NOT_FINISHED"
        self.level = 0

    def _grid(self) -> list[list[int]]:
        grid = [[0] * SIZE for _ in range(SIZE)]
        for row in (3, 4):
            for col in (self.column, self.column + 1):
                grid[row][col] = BLOCK
        for row, col in self.painted:
            if grid[row][col] == 0:
                grid[row][col] = PAINT
        return grid

    @property
    def observation(self) -> dict[str, Any]:
        return {
            "frame": [self._grid()],
            "state": self.state,
            "levels_completed": self.level,
            "win_levels": 1,
            "available_actions": [1, 2, 6],
        }

    def step(
        self,
        action: str,
        data: dict[str, Any] | None,
        reasoning: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if action == "RESET":
            self.column = 0
            self.painted = set()
            self.state = "NOT_FINISHED"
        elif action == "ACTION1":
            self.column = min(SIZE - 2, self.column + 1)
        elif action == "ACTION6":
            self.painted.add((int((data or {})["y"]), int((data or {})["x"])))
        elif action != "ACTION2":
            raise ValueError(f"grid adapter got an unknown action {action!r}")
        if self.column == SIZE - 2 and self.state != "WIN":
            self.state = "WIN"
            self.level = 1
        return self.observation


def factory(root: Any, config: dict[str, Any]) -> GridSession:
    return GridSession(root, config)
