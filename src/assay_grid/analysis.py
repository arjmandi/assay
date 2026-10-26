"""The grid namespace of `assay python`: settled grids, animation frames,
transitions, and the perception and search helpers a frame world's offline
analysis uses.

The names the manual documents for every world (`observations`,
`transitions`, `actions`, `key_delta`, `delta_lines`) exist here too, in the
grid's terms: an observation is the settled grid, and a key of `key_delta` is
a cell `(x, y)`, x the column and y the row."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Sequence
from typing import Any

import numpy as np

from assay.analysis import astar, bfs, show
from assay.core import canonical_action, frame_at
from assay.records import Event

from .perception import (
    connected_components,
    frame_delta,
    infer_lattice,
    line_graph,
    motion_trace,
    repeated_shapes,
)


def key_delta(before: Any, after: Any) -> dict[str, list[Any]]:
    """The cells that differ between two grids, as the dict world's
    `key_delta` shapes its answer: `changed` holds `((x, y), old, new)`;
    `added` and `removed` stay empty unless the board's size changed, when
    every cell of the other shape is listed there."""
    left, right = np.asarray(before), np.asarray(after)
    if left.shape != right.shape:
        return {
            "added": [((int(x), int(y)), int(right[y, x])) for y, x in np.argwhere(np.ones(right.shape, dtype=bool))],
            "removed": [((int(x), int(y)), int(left[y, x])) for y, x in np.argwhere(np.ones(left.shape, dtype=bool))],
            "changed": [],
        }
    changed = [
        ((int(x), int(y)), int(left[y, x]), int(right[y, x]))
        for y, x in np.argwhere(left != right)
    ]
    return {"added": [], "removed": [], "changed": changed}


def delta_lines(before: Any, after: Any, limit: int = 24) -> list[str]:
    """One line per changed cell, `~ (x,y): old -> new`, as the dict world
    words a transition."""
    delta = key_delta(before, after)
    if delta["added"] or delta["removed"]:
        return [f"board size changed: {np.asarray(before).shape} -> {np.asarray(after).shape}"]
    lines = [f"~ ({x},{y}): {old:x} -> {new:x}" for (x, y), old, new in delta["changed"]]
    if not lines:
        return ["no observed change"]
    if len(lines) > limit:
        lines = lines[:limit] + [f"... {len(lines) - limit} more changed cells"]
    return lines


def crop(grid: np.ndarray[Any, Any], rows: tuple[int, int], cols: tuple[int, int]) -> np.ndarray[Any, Any]:
    return np.asarray(grid)[rows[0] : rows[1], cols[0] : cols[1]].copy()


def sample(grid: np.ndarray[Any, Any], coordinates: Iterable[tuple[int, int]]) -> list[int]:
    array = np.asarray(grid)
    return [int(array[row, column]) for row, column in coordinates]


def _neighbors(
    cell: tuple[int, int], diagonal: bool = False
) -> tuple[tuple[int, int], ...]:
    row, column = cell
    steps: tuple[tuple[int, int], ...] = ((-1, 0), (1, 0), (0, -1), (0, 1))
    if diagonal:
        steps += ((-1, -1), (-1, 1), (1, -1), (1, 1))
    return tuple((row + dr, column + dc) for dr, dc in steps)


def shortest_path(
    start: tuple[int, int],
    goal: tuple[int, int],
    passable: np.ndarray[Any, Any] | Callable[[tuple[int, int]], bool],
    *,
    diagonal: bool = False,
) -> list[tuple[int, int]] | None:
    """BFS over a caller-supplied passability mask. Coordinates are (row, col)."""
    if callable(passable):
        allowed = passable
    else:
        mask = np.asarray(passable, dtype=bool)

        def allowed(cell: tuple[int, int]) -> bool:
            return (
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


def namespace(events: Sequence[Event]) -> dict[str, Any]:
    settled = [frame_at(event) for event in events]
    frames = [
        [frame_at(event, index) for index in range(len(event.frames or ()))]
        for event in events
    ]
    transitions = [
        {
            "event": event.id,
            "action": canonical_action(event),
            "before": settled[index - 1],
            "after": settled[index],
            "frames": frames[index],
            "state": event.state,
            "level": event.levels_completed + 1,
        }
        for index, event in enumerate(events)
        if index
    ]
    return {
        "np": np,
        "grid": settled[-1],
        "previous": settled[-2] if len(settled) > 1 else None,
        "settled": settled,
        "observations": settled,
        "key_delta": key_delta,
        "delta_lines": delta_lines,
        "frames": frames,
        "events": [event.to_json() for event in events],
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
