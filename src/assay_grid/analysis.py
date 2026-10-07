"""The grid namespace of `assay python`: settled grids, animation frames,
transitions, and the perception and search helpers a frame world's offline
analysis uses."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

import numpy as np

from assay.analysis import astar, bfs, show
from assay.core import canonical_action, frame_at

from .perception import (
    connected_components,
    frame_delta,
    infer_lattice,
    line_graph,
    motion_trace,
    repeated_shapes,
)


def crop(grid: np.ndarray, rows: tuple[int, int], cols: tuple[int, int]) -> np.ndarray:
    return np.asarray(grid)[rows[0] : rows[1], cols[0] : cols[1]].copy()


def sample(grid: np.ndarray, coordinates: Iterable[tuple[int, int]]) -> list[int]:
    array = np.asarray(grid)
    return [int(array[row, column]) for row, column in coordinates]


def _neighbors(
    cell: tuple[int, int], diagonal: bool = False
) -> tuple[tuple[int, int], ...]:
    row, column = cell
    steps = ((-1, 0), (1, 0), (0, -1), (0, 1))
    if diagonal:
        steps += ((-1, -1), (-1, 1), (1, -1), (1, 1))
    return tuple((row + dr, column + dc) for dr, dc in steps)


def shortest_path(
    start: tuple[int, int],
    goal: tuple[int, int],
    passable: np.ndarray | Callable[[tuple[int, int]], bool],
    *,
    diagonal: bool = False,
) -> list[tuple[int, int]] | None:
    """BFS over a caller-supplied passability mask. Coordinates are (row, col)."""
    if callable(passable):
        allowed = passable
    else:
        mask = np.asarray(passable, dtype=bool)
        allowed = lambda cell: (
            0 <= cell[0] < mask.shape[0]
            and 0 <= cell[1] < mask.shape[1]
            and bool(mask[cell])
        )
    queue = deque([start])
    parent: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
    while queue:
        current = queue.popleft()
        if current == goal:
            route: list[tuple[int, int]] = []
            cursor: tuple[int, int] | None = current
            while cursor is not None:
                route.append(cursor)
                cursor = parent[cursor]
            return route[::-1]
        for successor in _neighbors(current, diagonal):
            if successor not in parent and allowed(successor):
                parent[successor] = current
                queue.append(successor)
    return None


def namespace(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    settled = [frame_at(event) for event in events]
    frames = [
        [frame_at(event, index) for index in range(len(event["frames"]))]
        for event in events
    ]
    transitions = [
        {
            "event": int(event["id"]),
            "action": canonical_action(event),
            "before": settled[index - 1],
            "after": settled[index],
            "frames": frames[index],
            "state": event["state"],
            "level": int(event["levels_completed"]) + 1,
        }
        for index, event in enumerate(events)
        if index
    ]
    return {
        "np": np,
        "grid": settled[-1],
        "previous": settled[-2] if len(settled) > 1 else None,
        "settled": settled,
        "frames": frames,
        "events": list(events),
        "transitions": transitions,
        "actions": [canonical_action(event) for event in events[1:]],
        "show": show,
        "crop": crop,
        "sample": sample,
        "connected_components": connected_components,
        "frame_delta": frame_delta,
        "infer_lattice": infer_lattice,
        "line_graph": line_graph,
        "motion_trace": motion_trace,
        "repeated_shapes": repeated_shapes,
        "shortest_path": shortest_path,
        "bfs": bfs,
        "astar": astar,
    }
